import SwiftUI

@main
struct PodLookApp: App {
    @StateObject private var tracker = HeadphoneTracker()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView(tracker: tracker)
                .preferredColorScheme(.dark)
                .onChange(of: scenePhase) { phase in
                    if phase == .background { tracker.stop(message: "Stopped while in background. Tap Start to resume.") }
                }
        }
    }
}
