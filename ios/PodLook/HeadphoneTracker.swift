import Foundation
import Combine
import CoreMotion
import Network
import UIKit

@MainActor
final class HeadphoneTracker: NSObject, ObservableObject, CMHeadphoneMotionManagerDelegate {
    @Published var host = UserDefaults.standard.string(forKey: "pcAddress") ?? ""
    @Published var portText = UserDefaults.standard.string(forKey: "pcPort") ?? "4242"
    @Published var invertYaw = true
    @Published var invertPitch = false
    @Published var invertRoll = false
    @Published var includeRoll = false
    @Published private(set) var running = false
    @Published private(set) var status = "Connect your AirPods to this iPhone, then enter your PC's IPv4 address."
    @Published private(set) var pose = Pose.zero
    @Published private(set) var sentPackets = 0
    @Published private(set) var sampleRate = 0

    private let motion = CMHeadphoneMotionManager()
    private var connection: NWConnection?
    private var watchdog: Timer?
    private var tracker = PoseTracker(smoothingSeconds: 0)
    private var session = UUID()
    private var ready = false
    private var sendPending = false
    private var lastSample: TimeInterval?
    private var startedAt: TimeInterval = 0
    private var rateStart: TimeInterval = 0
    private var rateSamples = 0
    private var stale = true

    override init() {
        super.init()
        if ProcessInfo.processInfo.arguments.contains("--uitest-reset") {
            host = ""
            portText = "4242"
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
        lastSample = nil
        startedAt = ProcessInfo.processInfo.systemUptime
        rateStart = startedAt
        stale = true
        ready = false
        sendPending = false
        running = true
        UIApplication.shared.isIdleTimerDisabled = true
        status = "Opening UDP connection. Allow Local Network and Motion access when asked."

        let udp = NWConnection(host: NWEndpoint.Host(destination), port: port, using: .udp)
        connection = udp
        udp.stateUpdateHandler = { [weak self] state in
            Task { @MainActor [weak self] in
                guard let self = self, self.session == currentSession, self.running else { return }
                switch state {
                case .ready:
                    self.ready = true
                    self.status = "Waiting for AirPods motion. Face forward for automatic centering."
                case .waiting(let error):
                    self.ready = false
                    self.status = "Network unavailable: \(error.localizedDescription). Check Wi-Fi and Local Network permission."
                case .failed(let error):
                    self.stop(message: "Network error: \(error.localizedDescription)")
                default: break
                }
            }
        }
        udp.start(queue: .main)
        motion.delegate = self
        motion.startConnectionStatusUpdates()
        motion.startDeviceMotionUpdates(to: .main) { [weak self] sample, error in
            Task { @MainActor [weak self] in
                guard let self = self, self.session == currentSession, self.running else { return }
                if let error = error {
                    self.stop(message: "AirPods motion error: \(error.localizedDescription)")
                    return
                }
                guard let sample = sample else { return }
                self.receive(sample)
            }
        }
        watchdog = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self, self.session == currentSession else { return }
                self.checkForStaleMotion()
            }
        }
    }

    nonisolated func headphoneMotionManagerDidConnect(_ manager: CMHeadphoneMotionManager) {
        // A connection notification may precede the first sample. Centering is
        // established by PoseTracker from that sample, not from this callback.
    }

    nonisolated func headphoneMotionManagerDidDisconnect(_ manager: CMHeadphoneMotionManager) {
        Task { @MainActor [weak self] in
            guard let self = self, self.running else { return }
            self.stop(message: "AirPods disconnected. Reconnect them to this iPhone, then tap Start to recenter and resume.")
        }
    }

    private func receive(_ sample: CMDeviceMotion) {
        let q = sample.attitude.quaternion
        guard let attitude = Quaternion(w: q.w, x: q.x, y: q.y, z: q.z) else { return }
        guard let raw = tracker.update(attitude, timestamp: sample.timestamp) else { return }
        let now = ProcessInfo.processInfo.systemUptime
        lastSample = now
        stale = false
        rateSamples += 1
        if now - rateStart >= 1 {
            sampleRate = Int(Double(rateSamples) / (now - rateStart))
            rateStart = now
            rateSamples = 0
        }
        pose = Pose(yaw: raw.yaw * (invertYaw ? -1 : 1),
                    pitch: raw.pitch * (invertPitch ? -1 : 1),
                    roll: includeRoll ? raw.roll * (invertRoll ? -1 : 1) : 0)
        if ready {
            status = "Sending motion — verify receipt in OpenTrack on your PC."
            send(pose)
        }
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
                    self.stop(message: "UDP send failed: \(error.localizedDescription). Check your PC address and network.")
                } else {
                    self.sentPackets += 1
                }
            }
        })
    }

    private func checkForStaleMotion() {
        guard running else { return }
        let elapsed = ProcessInfo.processInfo.systemUptime - (lastSample ?? startedAt)
        guard elapsed > 1 else { return }
        if !stale { tracker.recenter() }
        stale = true
        pose = .zero
        sampleRate = 0
        if ready {
            status = "No recent AirPods motion. Wear both buds and check their iPhone connection."
            send(.zero)
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
    }
}
