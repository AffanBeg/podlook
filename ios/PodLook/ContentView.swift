import SwiftUI

struct ContentView: View {
    @ObservedObject var tracker: HeadphoneTracker
    private enum ConnectionField: Hashable { case address, port }
    @FocusState private var editingConnection: ConnectionField?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label("AIRPODS → PC", systemImage: "airpodspro")
                        .font(.caption.weight(.semibold)).foregroundStyle(.mint)
                    Text("Look into the corner.")
                        .font(.title2.bold())
                    Text("Your head controls the view. Keep PodLook open while driving.")
                        .foregroundStyle(.secondary)
                }
                Section("Windows PC") {
                    TextField("PC IPv4 address", text: $tracker.host)
                        .keyboardType(.decimalPad).autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .accessibilityIdentifier("pcAddress")
                        .focused($editingConnection, equals: .address)
                        .disabled(tracker.running)
                    TextField("UDP port", text: $tracker.portText)
                        .keyboardType(.numberPad).disabled(tracker.running)
                        .accessibilityIdentifier("pcPort")
                        .focused($editingConnection, equals: .port)
                    Text("Windows PodLook: port 4243. Direct to OpenTrack: port 4242.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Tracking") {
                    Text(tracker.status).accessibilityIdentifier("trackingStatus")
                    Text(tracker.motionDetails).font(.caption).foregroundStyle(.secondary)
                        .accessibilityIdentifier("motionDetails")
                    HStack {
                        metric("Yaw", tracker.pose.yaw)
                        Spacer()
                        metric("Pitch", tracker.pose.pitch)
                        Spacer()
                        metric("Roll", tracker.pose.roll)
                    }
                    Text("\(tracker.sampleRate) samples/s · \(tracker.sentPackets) packets sent")
                        .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    Button(tracker.running ? "Stop tracking" : "Start tracking") {
                        if tracker.running { tracker.stop() } else { tracker.start() }
                    }
                    .accessibilityIdentifier("startStop")
                    .buttonStyle(.borderedProminent).tint(.mint)
                    Button("Recenter — look straight ahead") { tracker.recenter() }
                        .disabled(!tracker.running)
                    Button("Restart AirPods sensor") { tracker.restartAirPods() }
                        .disabled(!tracker.running).accessibilityIdentifier("restartMotion")
                    Text("\(tracker.receivedSamples) sensor samples received")
                        .font(.caption).accessibilityIdentifier("receivedSamples")
                    Text(tracker.networkStatus).font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("networkStatus")
                    Text(tracker.diagnostics).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        .accessibilityIdentifier("streamDiagnostics")
                }
                Section("Direction") {
                    Toggle("Invert left / right", isOn: $tracker.invertYaw)
                    Toggle("Invert up / down", isOn: $tracker.invertPitch)
                    Toggle("Enable head tilt", isOn: $tracker.includeRoll)
                    if tracker.includeRoll {
                        Toggle("Invert tilt", isOn: $tracker.invertRoll)
                    }
                    Text("Tune sensitivity in Windows PodLook, or in OpenTrack when using it directly. Start with yaw and pitch only for F1 25.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Before you drive") {
                    Text("Connect the AirPods to this iPhone. Put both devices on the same home network. Use PC speakers or another headset for game audio.")
                    Text("F1 25: Settings → Eye and Head Tracking → Head Tracking with TrackIR.")
                    Text("Motion is sent only to the PC address you enter. No account, ads, or camera access.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("PodLook \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown") · Dedicated motion stream")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("PodLook")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { editingConnection = nil }
                        .accessibilityIdentifier("dismissKeyboard")
                }
            }
            .scrollDismissesKeyboard(.interactively)
        }
    }

    private func metric(_ title: String, _ value: Double) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(String(format: "%.1f°", value)).font(.title3.monospacedDigit())
        }
    }
}
