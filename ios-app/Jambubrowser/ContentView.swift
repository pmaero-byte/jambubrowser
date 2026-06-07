// Root view — TabView with 6 tabs, all environment injection, Handoff + Spotlight handling.

import SwiftUI
import SwiftData
import JambubrowserKit

struct ContentView: View {
    @Environment(AppState.self) private var appState
    @Environment(NetworkMonitor.self) private var networkMonitor
    @Environment(\.modelContext) private var modelContext

    @State private var gatewayClient: GatewayClient?

    var body: some View {
        @Bindable var appState = appState
        TabView(selection: $appState.selectedTab) {
            DashboardView()
                .tabItem {
                    Label("Dashboard", systemImage: "chart.bar.fill")
                }
                .tag(0)

            RunTaskView()
                .tabItem {
                    Label("Run", systemImage: "play.circle.fill")
                }
                .tag(1)

            ConsoleView()
                .tabItem {
                    Label("Console", systemImage: "terminal.fill")
                }
                .tag(5)

            SessionsListView()
                .tabItem {
                    Label("Sessions", systemImage: "clock.fill")
                }
                .tag(2)

            MemoryView()
                .tabItem {
                    Label("Memory", systemImage: "brain")
                }
                .tag(3)

            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gear")
                }
                .tag(4)
        }
        .tint(.indigo)
        .environment(gatewayClient ?? createClient())
        .onAppear {
            if gatewayClient == nil {
                gatewayClient = createClient()
            }
            // Load user preferences
            let prefs = UserPreferences.fetchOrCreate(in: modelContext)
            if appState.gatewayURL != prefs.gatewayURL {
                appState.gatewayURL = prefs.gatewayURL
            }
        }
    }

    private func createClient() -> GatewayClient {
        let client = GatewayClient(baseURL: appState.baseURL ?? URL(string: "http://localhost:8001")!)
        gatewayClient = client
        return client
    }
}

#Preview {
    ContentView()
}
