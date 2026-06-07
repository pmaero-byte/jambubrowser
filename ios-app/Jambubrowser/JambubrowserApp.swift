// Jambubrowser iOS — AI Orchestration Platform
// Integrates SwiftData, TipKit, BackgroundTasks, Live Activities, and all Apple frameworks.

import SwiftUI
import SwiftData
import TipKit
import JambubrowserKit

@main
struct JambubrowserApp: App {
    @Environment(\.scenePhase) private var scenePhase

    @State private var appState = AppState()
    @State private var networkMonitor = NetworkMonitor()
    @State private var notificationService = NotificationService()
    @State private var liveActivityService = LiveActivityService()
    @State private var spotlightService = SpotlightService()
    @State private var handoffService = HandoffService()
    @State private var gatewayClient: GatewayClient?

    init() {
        // Configure TipKit
        do {
            try Tips.configure([
                .datastoreLocation(.applicationDefault)
            ])
        } catch {
            print("TipKit configuration: \(error)")
        }

        // Register background tasks
        BackgroundTaskManager.register()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(appState)
                .environment(networkMonitor)
                .environment(notificationService)
                .environment(liveActivityService)
                .environment(spotlightService)
                .environment(handoffService)
                .environment(gatewayClient ?? createClient())
                .task {
                    // Request notification permission on first launch
                    await notificationService.requestPermission()
                }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .background {
                        BackgroundTaskManager.scheduleConnectorHealthRefresh()
                        BackgroundTaskManager.scheduleSessionSync()
                    }
                }
                .onContinueUserActivity(HandoffService.activityType) { activity in
                    if let prompt = activity.userInfo?["prompt"] as? String {
                        appState.currentPrompt = prompt
                    }
                }
                .onOpenURL { url in
                    // Handle Spotlight deep links
                    handleSpotlightURL(url)
                }
                .preferredColorScheme(colorScheme(from: appState.colorScheme))
        }
        .modelContainer(for: [
            CachedSession.self,
            CachedTaskResult.self,
            CachedMemoryEntry.self,
            CachedConnectorStatus.self,
            UserPreferences.self
        ])
    }

    private func createClient() -> GatewayClient {
        let client = GatewayClient(baseURL: appState.baseURL ?? URL(string: "http://localhost:8001")!)
        gatewayClient = client
        return client
    }

    private func handleSpotlightURL(_ url: URL) {
        // Spotlight deep links open the app and navigate to the relevant content
        if url.absoluteString.contains("jambubrowser-session-") {
            let sessionId = url.absoluteString.replacingOccurrences(of: "jambubrowser-session-", with: "")
            appState.selectedSessionId = sessionId
        }
    }

    private func colorScheme(from scheme: AppColorScheme) -> ColorScheme? {
        switch scheme {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}
