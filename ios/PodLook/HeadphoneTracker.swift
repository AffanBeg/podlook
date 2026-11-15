import Foundation
import Combine
import CoreMotion
import Network
import UIKit

@MainActor
final class HeadphoneTracker: NSObject, ObservableObject {
    @Published var host = UserDefaults.standard.string(forKey: "pcAddress") ?? ""
    @Published var portText = UserDefaults.standard.string(forKey: "pcPort") ?? "4243"
    @Published var invertYaw = true { didSet { updateOptions() } }
    @Published var invertPitch = false { didSet { updateOptions() } }
    @Published var invertRoll = false { didSet { updateOptions() } }
    @Published var includeRoll = false { didSet { updateOptions() } }
    @Published private(set) var running = false
    @Published private(set) var status = "Connect your AirPods to this iPhone, then enter your PC's IPv4 address."
    @Published private(set) var pose = Pose.zero
    @Published private(set) var sentPackets = 0
    @Published private(set) var sampleRate = 0
    @Published private(set) var networkStatus = "PC connection stopped."
    @Published private(set) var motionDetails = "Motion sensor not started."
    @Published private(set) var receivedSamples = 0
    @Published private(set) var diagnostics = "Stream diagnostics appear after Start."

    private let stream = MotionStream()
    private var displayTimer: Timer?
    private var session: UUID?

    override init() {
        super.init()
        if ProcessInfo.processInfo.arguments.contains("--uitest-reset") {
            host = ""
            portText = "4243"
        }
    }

    private var options: MotionStream.Options {
        .init(invertYaw: invertYaw, invertPitch: invertPitch,
              invertRoll: invertRoll, includeRoll: includeRoll)
    }

    private func updateOptions() { stream.configure(options) }

    func start() {
        guard !running else { return }
        let destination = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard IPv4Address(destination) != nil else {
            status = "Enter the PC's IPv4 address, for example 192.168.1.20."
            return
        }
        guard let portNumber = UInt16(portText), portNumber > 0,
              let port = NWEndpoint.Port(rawValue: portNumber) else {
            status = "Enter a port from 1 to 65535. Windows PodLook normally uses 4243."
            return
        }
        UserDefaults.standard.set(destination, forKey: "pcAddress")
        UserDefaults.standard.set(portText, forKey: "pcPort")
        let token = UUID()
        session = token
        running = true
        status = "Starting AirPods motion. Allow Motion access when asked."
        UIApplication.shared.isIdleTimerDisabled = true
        stream.start(session: token, host: destination, port: port, options: options)
        // UI reads a latest-value snapshot. A blocked UI never queues sensor work
        // or forces the background worker to wait for a rendering completion.
        let timer = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.refreshDisplay(session: token) }
        }
        displayTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func refreshDisplay(session token: UUID) {
        guard session == token else { return }
        let value = stream.snapshot()
        guard value.session == token else { return }
        if status != value.status { status = value.status }
        if pose != value.pose { pose = value.pose }
        if sentPackets != value.sentPackets { sentPackets = value.sentPackets }
        if receivedSamples != value.receivedSamples { receivedSamples = value.receivedSamples }
        if sampleRate != value.sampleRate { sampleRate = value.sampleRate }
        if networkStatus != value.networkStatus { networkStatus = value.networkStatus }
        if motionDetails != value.motionDetails { motionDetails = value.motionDetails }
        if diagnostics != value.diagnostics { diagnostics = value.diagnostics }
        if running != value.running { running = value.running }
        if !value.running {
            displayTimer?.invalidate()
            displayTimer = nil
            UIApplication.shared.isIdleTimerDisabled = false
        }
    }

    func restartAirPods() { if running { stream.restartAirPods() } }
    func recenter() { if running { stream.recenter() } }

    func stop(message: String = "Stopped. Stop tracking in OpenTrack too when you finish.") {
        session = nil
        displayTimer?.invalidate()
        displayTimer = nil
        stream.stop(message: message)
        running = false
        pose = .zero
        sampleRate = 0
        status = message
        networkStatus = "PC connection stopped."
        motionDetails = "Motion sensor stopped."
        UIApplication.shared.isIdleTimerDisabled = false
    }
}
