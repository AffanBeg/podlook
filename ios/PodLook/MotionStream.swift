import Foundation
import CoreMotion
import Network

/// The worker owns sensor and network state on `queue`. Only this small value
/// snapshot crosses to the UI; snapshotLock never protects sensor/API calls.
final class MotionStream: NSObject, CMHeadphoneMotionManagerDelegate {
    struct Options {
        var invertYaw = true
        var invertPitch = false
        var invertRoll = false
        var includeRoll = false
    }
    struct Snapshot {
        var session: UUID?
        var running = false
        var status = "Motion sensor not started."
        var pose = Pose.zero
        var sentPackets = 0
        var receivedSamples = 0
        var sampleRate = 0
        var networkStatus = "PC connection stopped."
        var motionDetails = "Motion sensor not started."
        var diagnostics = "Stream diagnostics appear after Start."
    }

    private let queue = DispatchQueue(label: "PodLook.MotionStream", qos: .userInteractive)
    private let motionQueue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "PodLook.CoreMotionCallbacks"
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .userInteractive
        return queue
    }()
    private struct CallbackValue {
        let sample: CMDeviceMotion?
        let error: String?
    }
    private let inbox = StreamMailbox<CallbackValue>()
    private let snapshotLock = NSLock()
    private var published = Snapshot()
    private var value = Snapshot()
    private var options = Options()
    private var motion: CMHeadphoneMotionManager?
    private var motionGeneration = UUID()
    private var connection: NWConnection?
    private var watchdog: DispatchSourceTimer?
    private var recovery = MotionRecovery()
    private var tracker = PoseTracker(smoothingSeconds: 0, resetOnGap: false)
    private var ready = false
    private var pendingSince: TimeInterval?
    private var pendingID = UUID()
    private var polling = false
    private var awaitingPermission = false
    private var stale = true
    private var rateStart: TimeInterval = 0
    private var rateSamples = 0
    private var lastPublish: TimeInterval = 0
    private var lastProcessedAt: TimeInterval?
    private var maxProcessingGap: TimeInterval = 0
    private var maxQueueDelay: TimeInterval = 0
    private var lastUniqueAt: TimeInterval?
    private var lastSensorTimestamp: TimeInterval?
    private var maxSensorGap: TimeInterval = 0
    private var lastSendCompletedAt: TimeInterval?
    private var rescuedSamples = 0
    private var duplicateSamples = 0
    private var delayedCallbacks = 0
    private var disconnects = 0
    private var motionError: String?

    func snapshot() -> Snapshot {
        snapshotLock.lock()
        defer { snapshotLock.unlock() }
        return published
    }

    func configure(_ options: Options) { queue.async { self.options = options } }

    func start(session: UUID, host: String, port: NWEndpoint.Port, options: Options) {
        queue.async { self.startOnQueue(session: session, host: host, port: port, options: options) }
    }

    private func startOnQueue(session: UUID, host: String, port: NWEndpoint.Port, options: Options) {
        stopOnQueue(message: "Stopped.")
        value = Snapshot()
        value.session = session
        self.options = options
        let authorization = CMHeadphoneMotionManager.authorizationStatus()
        guard authorization != .denied && authorization != .restricted else {
            value.status = "Motion access is disabled. Allow PodLook in Settings > Privacy & Security > Motion & Fitness."
            publish(force: true)
            return
        }
        let manager = CMHeadphoneMotionManager()
        guard manager.isDeviceMotionAvailable else {
            value.status = "No motion-capable headphones available. Connect and wear your AirPods, then try again."
            publish(force: true)
            return
        }
        motion = manager
        value.running = true
        value.status = "Waiting for AirPods motion. Allow Motion access when asked."
        value.networkStatus = "Opening UDP connection. Allow Local Network access when asked."
        let now = ProcessInfo.processInfo.systemUptime
        recovery.start(now: now)
        tracker.recenter()
        awaitingPermission = authorization == .notDetermined
        rateStart = now
        rateSamples = 0
        lastPublish = 0
        lastProcessedAt = nil
        maxProcessingGap = 0
        maxQueueDelay = 0
        lastUniqueAt = nil
        lastSensorTimestamp = nil
        maxSensorGap = 0
        lastSendCompletedAt = nil
        rescuedSamples = 0
        duplicateSamples = 0
        delayedCallbacks = 0
        disconnects = 0
        motionError = nil
        pendingSince = nil
        stale = true
        ready = false
        let udp = NWConnection(host: NWEndpoint.Host(host), port: port, using: .udp)
        connection = udp
        udp.stateUpdateHandler = { [weak self] state in
            guard let self = self, self.value.running, self.value.session == session else { return }
            switch state {
            case .ready:
                self.ready = true
                self.value.networkStatus = "UDP ready to send; receipt must be checked in Windows."
            case .waiting(let error):
                self.ready = false
                self.value.networkStatus = "Network unavailable: \(error.localizedDescription). Sensor processing continues."
            case .failed(let error):
                self.ready = false
                self.value.networkStatus = "Network failed: \(error.localizedDescription). Restart both apps to reconnect."
            default: break
            }
            self.publish(force: true)
        }
        udp.start(queue: queue)
        beginMotionAttempt(usePolling: false)
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: 1.0 / 60.0, leeway: .milliseconds(2))
        timer.setEventHandler { [weak self] in self?.tick() }
        watchdog = timer
        timer.resume()
        publish(force: true)
    }

    private func beginMotionAttempt(usePolling: Bool) {
        motionGeneration = UUID()
        let generation = motionGeneration
        let session = value.session
        inbox.reset(generation: generation)
        motion?.delegate = nil
        motion?.stopDeviceMotionUpdates()
        motion?.stopConnectionStatusUpdates()
        let manager = CMHeadphoneMotionManager()
        motion = manager
        polling = usePolling
        tracker.recenter()
        value.pose = .zero
        stale = true
        motionError = nil
        value.sampleRate = 0
        rateSamples = 0
        rateStart = ProcessInfo.processInfo.systemUptime
        manager.delegate = self
        manager.startConnectionStatusUpdates()
        if usePolling {
            manager.startDeviceMotionUpdates()
        } else {
            // The callback queue only captures arrival and replaces a one-item
            // mailbox. A busy worker cannot accumulate stale sample closures.
            manager.startDeviceMotionUpdates(to: motionQueue) { [weak self] sample, error in
                guard let self = self else { return }
                let shouldSchedule = self.inbox.put(value: CallbackValue(sample: sample, error: error?.localizedDescription),
                                                    generation: generation,
                                                    arrival: ProcessInfo.processInfo.systemUptime)
                if shouldSchedule {
                    self.queue.async { [weak self] in
                        guard let self = self, self.value.running, self.value.session == session,
                              self.motionGeneration == generation else { return }
                        self.consumeCallback()
                    }
                }
            }
        }
    }

    private func consumeCallback() {
        guard let event = inbox.take(), event.generation == motionGeneration else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let delay = max(0, now - event.arrival)
        maxQueueDelay = max(maxQueueDelay, delay)
        if let previous = lastProcessedAt { maxProcessingGap = max(maxProcessingGap, now - previous) }
        lastProcessedAt = now
        if let error = event.value.error { motionError = error }
        // This compares two host-uptime measurements we own, not an assumed
        // relationship between the device's sensor clock and the host clock.
        guard delay <= 0.25 else { delayedCallbacks += 1; return }
        if let sample = event.value.sample { receive(sample, rescued: false, now: now) }
    }

    private func receive(_ sample: CMDeviceMotion, rescued: Bool, now: TimeInterval) {
        let q = sample.attitude.quaternion
        guard let attitude = Quaternion(w: q.w, x: q.x, y: q.y, z: q.z) else { return }
        guard recovery.received(now: now, sampleTimestamp: sample.timestamp) else {
            duplicateSamples += 1
            return
        }
        guard let raw = tracker.update(attitude, timestamp: sample.timestamp) else { return }
        if let previous = lastSensorTimestamp { maxSensorGap = max(maxSensorGap, sample.timestamp - previous) }
        lastSensorTimestamp = sample.timestamp
        lastUniqueAt = now
        if rescued { rescuedSamples += 1 }
        stale = false
        motionError = nil
        value.receivedSamples += 1
        rateSamples += 1
        value.pose = Pose(yaw: raw.yaw * (options.invertYaw ? -1 : 1),
                          pitch: raw.pitch * (options.invertPitch ? -1 : 1),
                          roll: options.includeRoll ? raw.roll * (options.invertRoll ? -1 : 1) : 0)
        value.status = rescued ? "AirPods motion active (latest-sample rescue)." : "AirPods motion active."
        send(value.pose)
    }

    private func send(_ pose: Pose) {
        guard ready, pendingSince == nil, let udp = connection,
              let packet = try? pose.openTrackPacket() else { return }
        pendingSince = ProcessInfo.processInfo.systemUptime
        pendingID = UUID()
        let id = pendingID
        let session = value.session
        udp.send(content: packet, completion: .contentProcessed { [weak self] error in
            guard let self = self, self.value.running, self.value.session == session,
                  self.pendingID == id else { return }
            self.pendingSince = nil
            self.lastSendCompletedAt = ProcessInfo.processInfo.systemUptime
            if let error = error {
                self.ready = false
                self.value.networkStatus = "UDP send failed: \(error.localizedDescription). Sensor processing continues; restart both apps to reconnect."
            } else {
                self.value.sentPackets += 1
                if self.ready { self.value.networkStatus = "UDP ready to send; receipt must be checked in Windows." }
            }
        })
    }

    private func tick() {
        guard value.running else { return }
        let authorization = CMHeadphoneMotionManager.authorizationStatus()
        guard authorization != .denied && authorization != .restricted else {
            stopOnQueue(message: "Motion access is disabled. Allow PodLook in Settings > Privacy & Security > Motion & Fitness.")
            return
        }
        let now = ProcessInfo.processInfo.systemUptime
        if authorization != .notDetermined {
            if awaitingPermission {
                awaitingPermission = false
                if value.receivedSamples == 0 { recovery.start(now: now) }
            }
            let callback = inbox.statistics()
            if polling || (callback.lastArrival.map({ now - $0 >= 0.15 }) ?? true)
                || (lastUniqueAt.map({ now - $0 >= 0.15 }) ?? true) {
                // deviceMotion is Apple's latest-sample API. Timestamp gating in
                // MotionRecovery prevents cached or duplicate data being sent.
                if let sample = motion?.deviceMotion { receive(sample, rescued: !polling, now: now) }
            }
            if recovery.isStale(now: now) {
                stale = true
                value.sampleRate = 0
                // No fake neutral packet and no automatic reference reset here.
                value.status = motionError.map { "Motion callback error: \($0). Recovery will retry." }
                    ?? "No fresh AirPods sensor samples. Waiting for motion recovery."
            }
            switch recovery.action(now: now) {
            case .retry(let attempt):
                beginMotionAttempt(usePolling: true)
                value.status = "No sensor progress. Restarting motion (\(attempt)/3); next sample establishes center."
            case .exhausted:
                value.status = "No fresh sensor samples after 3 retries. Check diagnostics or tap Restart AirPods."
            default: break
            }
        }
        if now - rateStart >= 1 {
            value.sampleRate = stale ? 0 : Int(Double(rateSamples) / (now - rateStart))
            rateSamples = 0
            rateStart = now
        }
        if let pending = pendingSince, now - pending >= 1 {
            value.networkStatus = "UDP completion delayed; sensor processing continues. No additional sends are queued."
        }
        publish(force: false)
    }

    private func age(_ time: TimeInterval?, now: TimeInterval) -> String {
        guard let time = time else { return "none" }
        return String(format: "%.2fs", max(0, now - time))
    }

    private func publish(force: Bool) {
        let now = ProcessInfo.processInfo.systemUptime
        guard force || now - lastPublish >= 0.1 else { return }
        lastPublish = now
        let callbacks = inbox.statistics()
        value.motionDetails = "Permission: \(CMHeadphoneMotionManager.authorizationStatus() == .authorized ? "allowed" : "not granted") | available: \(motion?.isDeviceMotionAvailable == true ? "yes" : "no") | active: \(motion?.isDeviceMotionActive == true ? "yes" : "no") | \(polling ? "polling" : "callback + rescue")"
        value.diagnostics = "Age - callback: \(age(callbacks.lastArrival, now: now)) | processed: \(age(lastProcessedAt, now: now)) | fresh sensor: \(age(lastUniqueAt, now: now)) | send completion: \(age(lastSendCompletedAt, now: now)) | pending: \(age(pendingSince, now: now))\nMax gaps - callback \(String(format: "%.2f", callbacks.maxGap))s | processing \(String(format: "%.2f", maxProcessingGap))s | sensor timestamps \(String(format: "%.2f", maxSensorGap))s | worker delay \(String(format: "%.2f", maxQueueDelay))s\nCallbacks (attempt): \(callbacks.count) | rescued: \(rescuedSamples) | duplicate/old: \(duplicateSamples) | delayed callbacks dropped: \(delayedCallbacks) | disconnects: \(disconnects)"
        snapshotLock.lock()
        published = value
        snapshotLock.unlock()
    }

    func recenter() {
        queue.async {
            guard self.value.running else { return }
            self.tracker.recenter()
            self.value.pose = .zero
            self.send(.zero)
            self.value.status = "Face forward. The next motion sample becomes the center."
            self.publish(force: true)
        }
    }

    func restartAirPods() {
        queue.async {
            guard self.value.running else { return }
            self.recovery.start(now: ProcessInfo.processInfo.systemUptime)
            self.beginMotionAttempt(usePolling: true)
            self.value.status = "Restarting AirPods sensor. Face forward; next sample becomes center."
            self.publish(force: true)
        }
    }

    func stop(message: String) { queue.async { self.stopOnQueue(message: message) } }

    private func stopOnQueue(message: String) {
        let old = connection
        let sendNeutral = ready && pendingSince == nil
        value.running = false
        ready = false
        pendingSince = nil
        pendingID = UUID()
        watchdog?.cancel()
        watchdog = nil
        motionGeneration = UUID()
        inbox.reset(generation: motionGeneration)
        motion?.delegate = nil
        motion?.stopDeviceMotionUpdates()
        motion?.stopConnectionStatusUpdates()
        motion = nil
        recovery.stop()
        connection = nil
        old?.stateUpdateHandler = nil
        if sendNeutral, let udp = old, let packet = try? Pose.zero.openTrackPacket() {
            udp.send(content: packet, completion: .contentProcessed { _ in udp.cancel() })
            queue.asyncAfter(deadline: .now() + 0.5) { udp.cancel() }
        } else { old?.cancel() }
        tracker.recenter()
        value.pose = .zero
        value.sampleRate = 0
        value.status = message
        value.networkStatus = "PC connection stopped."
        publish(force: true)
    }

    func headphoneMotionManagerDidConnect(_ manager: CMHeadphoneMotionManager) {
        queue.async {
            guard self.value.running, self.motion === manager else { return }
            if self.stale { self.value.status = "AirPods connected; waiting for fresh sensor samples." }
            self.publish(force: true)
        }
    }

    func headphoneMotionManagerDidDisconnect(_ manager: CMHeadphoneMotionManager) {
        queue.async {
            guard self.value.running, self.motion === manager else { return }
            self.disconnects += 1
            self.value.status = "AirPods motion connection lost. Waiting for sensor recovery."
            self.publish(force: true)
        }
    }
}

