import Foundation
import Combine
import CoreMotion
import Network
import UIKit

@MainActor
final class HeadphoneTracker: NSObject, ObservableObject, CMHeadphoneMotionManagerDelegate {
    @Published var host = UserDefaults.standard.string(forKey: "pcAddress") ?? ""
    @Published var portText = UserDefaults.standard.string(forKey: "pcPort") ?? "4243"
    @Published var invertYaw = true
    @Published var invertPitch = false
    @Published var invertRoll = false
    @Published var includeRoll = false
    @Published private(set) var running = false
    @Published private(set) var status = "Connect your AirPods to this iPhone, then enter your PC's IPv4 address."
    @Published private(set) var pose = Pose.zero
    @Published private(set) var sentPackets = 0
    @Published private(set) var sampleRate = 0
    @Published private(set) var networkStatus = "PC connection stopped."
    @Published private(set) var motionDetails = "Motion sensor not started."
    @Published private(set) var receivedSamples = 0

    private var motion = CMHeadphoneMotionManager()
    private let motionQueue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "PodLook.HeadphoneMotion"
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .userInteractive
        return queue
    }()
    private var motionGeneration = UUID()
    private var recovery = MotionRecovery()
    private var polling = false
    private var awaitingMotionPermission = false
    private var connection: NWConnection?
    private var watchdog: Timer?
    private var tracker = PoseTracker(smoothingSeconds: 0)
    private var session = UUID()
    private var ready = false
    private var sendPending = false
    private var rateStart: TimeInterval = 0
    private var rateSamples = 0
    private var stale = true

    override init() {
        super.init()
        if ProcessInfo.processInfo.arguments.contains("--uitest-reset") {
            host = ""
            portText = "4243"
        }
    }

    func start() {
        guard !running else { return }
        let destination = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard IPv4Address(destination) != nil else {
            status = "Enter the PC's IPv4 address, for example 192.168.1.20."
            return
        }
        guard let portNumber = UInt16(portText), portNumber > 0,
              let port = NWEndpoint.Port(rawValue: portNumber) else {
            status = "Enter a port from 1 to 65535. OpenTrack normally uses 4242."
            return
        }
        let authorization = CMHeadphoneMotionManager.authorizationStatus()
        guard authorization != .denied && authorization != .restricted else {
            status = "Motion access is disabled. Allow PodLook in Settings > Privacy & Security > Motion & Fitness."
            return
        }
        motion = CMHeadphoneMotionManager()
        guard motion.isDeviceMotionAvailable else {
            status = "No motion-capable headphones available. Connect and wear your AirPods, then try again."
            return
        }
        UserDefaults.standard.set(destination, forKey: "pcAddress")
        UserDefaults.standard.set(portText, forKey: "pcPort")
        session = UUID()
        let currentSession = session
        tracker.recenter()
        pose = .zero
        sentPackets = 0
        rateSamples = 0
        sampleRate = 0
        receivedSamples = 0
        let now = ProcessInfo.processInfo.systemUptime
        recovery.start(now: now)
        awaitingMotionPermission = authorization == .notDetermined
        rateStart = now
        stale = true
        ready = false
        sendPending = false
        running = true
        UIApplication.shared.isIdleTimerDisabled = true
        status = "Waiting for AirPods motion. Allow Motion access when asked."
        networkStatus = "Opening UDP connection. Allow Local Network access when asked."

        let udp = NWConnection(host: NWEndpoint.Host(destination), port: port, using: .udp)
        connection = udp
        udp.stateUpdateHandler = { [weak self] state in
            Task { @MainActor [weak self] in
                guard let self = self, self.session == currentSession, self.running else { return }
                switch state {
                case .ready:
                    self.ready = true
                    self.networkStatus = "UDP ready to send. Check receipt in Windows PodLook; this is not a delivery acknowledgment."
                case .waiting(let error):
                    self.ready = false
                    self.networkStatus = "Network unavailable: \(error.localizedDescription). Motion preview remains active."
                case .failed(let error):
                    self.ready = false
                    self.networkStatus = "Network error: \(error.localizedDescription). Motion preview remains active. Stop and start both apps to reconnect."
                default: break
                }
            }
        }
        udp.start(queue: .main)
        beginMotionAttempt(usePolling: false)
        let timer = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self, self.session == currentSession, self.running else { return }
                if self.polling, let sample = self.motion.deviceMotion { self.receive(sample) }
                self.checkForStaleMotion()
            }
        }
        watchdog = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    nonisolated func headphoneMotionManagerDidConnect(_ manager: CMHeadphoneMotionManager) {
        Task { @MainActor [weak self] in
            guard let self = self, self.running, self.motion === manager else { return }
            if self.stale { self.status = "AirPods connected; waiting for sensor samples." }
        }
    }

    nonisolated func headphoneMotionManagerDidDisconnect(_ manager: CMHeadphoneMotionManager) {
        Task { @MainActor [weak self] in
            guard let self = self, self.running, self.motion === manager else { return }
            self.status = "AirPods motion connection lost. Attempting recovery; face forward."
            self.tracker.recenter()
            self.pose = .zero
            self.stale = true
            self.send(.zero)
        }
    }

    // Recreate the Core Motion session instead of leaving a silent stream running forever.
    // Each generation rejects queued callbacks from the previous manager.
    private func beginMotionAttempt(usePolling: Bool) {
        motionGeneration = UUID()
        let generation = motionGeneration
        let currentSession = session
        motion.delegate = nil
        motion.stopDeviceMotionUpdates()
        motion.stopConnectionStatusUpdates()
        motion = CMHeadphoneMotionManager()
        polling = usePolling
        tracker.recenter()
        pose = .zero
        stale = true
        sampleRate = 0
        rateSamples = 0
        rateStart = ProcessInfo.processInfo.systemUptime
        motion.delegate = self
        motion.startConnectionStatusUpdates()
        if polling {
            // Apple's second supported API exposes the latest sample via deviceMotion.
            motion.startDeviceMotionUpdates()
        } else {
            motion.startDeviceMotionUpdates(to: motionQueue) { [weak self] sample, error in
                Task { @MainActor [weak self] in
                    guard let self = self, self.running, self.session == currentSession,
                          self.motionGeneration == generation else { return }
                    if let error = error {
                        self.status = "Motion stream error: \(error.localizedDescription). Recovery will retry."
                        return
                    }
                    if let sample = sample { self.receive(sample) }
                }
            }
        }
        updateMotionDetails()
    }

    func restartAirPods() {
        guard running else { return }
        recovery.start(now: ProcessInfo.processInfo.systemUptime)
        send(.zero)
        beginMotionAttempt(usePolling: true)
        status = "Restarting AirPods sensor. Face forward; the next sample becomes center."
    }

    private func updateMotionDetails() {
        let permission: String
        switch CMHeadphoneMotionManager.authorizationStatus() {
        case .authorized: permission = "allowed"
        case .denied: permission = "denied"
        case .restricted: permission = "restricted"
        case .notDetermined: permission = "not yet granted"
        @unknown default: permission = "unknown"
        }
        let details = "Permission: \(permission) · available: \(motion.isDeviceMotionAvailable ? "yes" : "no") · active: \(motion.isDeviceMotionActive ? "yes" : "no") · \(polling ? "polling" : "callback")"
        if motionDetails != details { motionDetails = details }
    }

    private func receive(_ sample: CMDeviceMotion) {
        let q = sample.attitude.quaternion
        guard let attitude = Quaternion(w: q.w, x: q.x, y: q.y, z: q.z) else { return }
        let now = ProcessInfo.processInfo.systemUptime
        guard recovery.received(now: now, sampleTimestamp: sample.timestamp) else { return }
        guard let raw = tracker.update(attitude, timestamp: sample.timestamp) else { return }
        stale = false
        receivedSamples += 1
        rateSamples += 1
        if now - rateStart >= 1 {
            sampleRate = Int(Double(rateSamples) / (now - rateStart))
            rateStart = now
            rateSamples = 0
        }
        pose = Pose(yaw: raw.yaw * (invertYaw ? -1 : 1),
                    pitch: raw.pitch * (invertPitch ? -1 : 1),
                    roll: includeRoll ? raw.roll * (invertRoll ? -1 : 1) : 0)
        status = "AirPods motion active."
        if ready { send(pose) }
    }

    private func send(_ value: Pose) {
        guard ready, !sendPending, let udp = connection,
              let packet = try? value.openTrackPacket() else { return }
        sendPending = true
        let currentSession = session
        udp.send(content: packet, completion: .contentProcessed { [weak self] error in
            Task { @MainActor [weak self] in
                guard let self = self, self.session == currentSession, self.running else { return }
                self.sendPending = false
                if let error = error {
                    self.ready = false
                    self.networkStatus = "UDP send failed: \(error.localizedDescription). Motion preview remains active. Stop and start both apps to reconnect."
                } else {
                    self.sentPackets += 1
                }
            }
        })
    }

    private func checkForStaleMotion() {
        guard running else { return }
        updateMotionDetails()
        let authorization = CMHeadphoneMotionManager.authorizationStatus()
        if authorization == .denied || authorization == .restricted {
            stop(message: "Motion access is disabled. Allow PodLook in Settings > Privacy & Security > Motion & Fitness.")
            return
        }
        // Do not consume retries while the system is waiting for the user's consent.
        if authorization == .notDetermined { return }
        let now = ProcessInfo.processInfo.systemUptime
        if awaitingMotionPermission {
            awaitingMotionPermission = false
            if receivedSamples == 0 { recovery.start(now: now) }
        }
        if recovery.isStale(now: now) {
            if !stale { tracker.recenter(); send(.zero) }
            stale = true
            pose = .zero
            sampleRate = 0
        }
        switch recovery.action(now: now) {
        case .retry(let attempt):
            beginMotionAttempt(usePolling: true)
            status = "No sensor samples. Restarting AirPods motion (\(attempt)/3); face forward."
        case .exhausted:
            status = "iOS is not delivering AirPods motion after 3 retries. Check the sensor details below, or tap Restart AirPods."
        case .stale:
            status = "AirPods motion paused. Waiting briefly before restarting the sensor."
        default: break
        }
    }

    func recenter() {
        tracker.recenter()
        pose = .zero
        if running { send(.zero) }
        status = "Face forward. The next motion sample becomes the center."
    }

    func stop(message: String = "Stopped. Stop tracking in OpenTrack too when you finish.") {
        // Let the neutral datagram finish before closing the old UDP connection.
        let oldConnection = connection
        let canSendNeutral = ready
        session = UUID()
        running = false
        ready = false
        sendPending = false
        watchdog?.invalidate()
        watchdog = nil
        motion.stopDeviceMotionUpdates()
        motion.stopConnectionStatusUpdates()
        motion.delegate = nil
        motionGeneration = UUID()
        recovery.stop()
        connection = nil
        if canSendNeutral, let udp = oldConnection, let data = try? Pose.zero.openTrackPacket() {
            udp.stateUpdateHandler = nil
            udp.send(content: data, completion: .contentProcessed { _ in udp.cancel() })
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { udp.cancel() }
        } else { oldConnection?.cancel() }
        tracker.recenter()
        pose = .zero
        sampleRate = 0
        UIApplication.shared.isIdleTimerDisabled = false
        status = message
        networkStatus = "PC connection stopped."
        updateMotionDetails()
    }
}
