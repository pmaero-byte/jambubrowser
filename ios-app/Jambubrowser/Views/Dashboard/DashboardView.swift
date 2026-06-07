// Dashboard — connection status, stat cards, connectors, recent sessions.
// Integrates: SwiftData caching, NetworkMonitor, Spotlight indexing, Widget refresh, TipKit.

import SwiftUI
import SwiftData
import WidgetKit
import JambubrowserKit

struct DashboardView: View {
    @Environment(AppState.self) private var appState
    @Environment(GatewayClient.self) private var client
    @Environment(NetworkMonitor.self) private var networkMonitor
    @Environment(NotificationService.self) private var notificationService
    @Environment(SpotlightService.self) private var spotlightService
    @Environment(\.modelContext) private var modelContext

    @State private var previousConnectors: [ConnectorHealth] = []

    var body: some View {
        NavigationStack {
            ZStack(alignment: .top) {
                ScrollView {
                    VStack(spacing: 20) {
                        // Connection status
                        connectionBanner

                        // Stat cards
                        statCards

                        // Connector status
                        connectorSection

                        // Recent sessions
                        recentSessionsSection
                    }
                    .padding()
                }

                // Offline banner overlay
                NetworkStatusBanner()
            }
            .navigationTitle("🐴 Jambubrowser")
            .refreshable {
                await loadData()
            }
            .task {
                // Load from cache first for instant display
                appState.loadCachedSessions(from: modelContext)
                await loadData()
            }
            .onChange(of: appState.selectedSessionId) { _, newId in
                if newId != nil {
                    appState.selectedTab = 2 // Switch to Sessions tab
                }
            }
            .alert("Error", isPresented: Binding(
                get: { appState.errorMessage != nil },
                set: { if !$0 { appState.clearError() } }
            )) {
                Button("OK") { appState.clearError() }
            } message: {
                Text(appState.errorMessage ?? "")
            }
        }
    }

    // MARK: - Connection Banner

    private var connectionBanner: some View {
        HStack {
            Circle()
                .fill(appState.isConnected ? .green : .red)
                .frame(width: 10, height: 10)
            Text(appState.isConnected ? "Connected to \(appState.gatewayURL)" : "Disconnected")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            if !networkMonitor.isConnected {
                Label("Offline", systemImage: "wifi.slash")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.horizontal)
    }

    // MARK: - Stat Cards

    private var statCards: some View {
        HStack(spacing: 12) {
            StatCard(
                title: "Sessions",
                value: "\(appState.sessions.count)",
                icon: "clock.fill",
                color: .blue
            )
            StatCard(
                title: "Connectors",
                value: "\(appState.connectors.filter(\.available).count)/\(appState.connectors.count)",
                icon: "link.circle.fill",
                color: .green
            )
            StatCard(
                title: "Models",
                value: "\(appState.models.count)",
                icon: "cpu.fill",
                color: .orange
            )
        }
    }

    // MARK: - Connectors

    private var connectorSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Connectors")
                .font(.headline)

            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible())
            ], spacing: 12) {
                ForEach(appState.connectors) { connector in
                    ConnectorCard(connector: connector)
                }
            }
        }
    }

    // MARK: - Recent Sessions

    private var recentSessionsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recent Sessions")
                .font(.headline)

            if appState.sessions.isEmpty {
                Text("No sessions yet")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(appState.sessions.prefix(5)) { session in
                    SessionRow(session: session)
                }
            }
        }
    }

    // MARK: - Data Loading

    private func loadData() async {
        do {
            async let healthTask = client.health()
            async let sessionsTask = client.listSessions(limit: 10)
            async let connectorsTask = client.listConnectors()
            async let modelsTask = client.listModels()

            let health = try await healthTask
            let sessions = try await sessionsTask
            let connectors = try await connectorsTask
            let models = try await modelsTask

            withAnimation {
                // Detect connector status changes for notifications
                detectConnectorChanges(old: appState.connectors, new: connectors)

                appState.isConnected = health.status == "ok"
                appState.sessions = sessions
                appState.connectors = connectors
                appState.models = models.data
                appState.clearError()
            }

            // Cache to SwiftData for offline access
            appState.cacheSessions(in: modelContext)
            appState.cacheConnectors(in: modelContext)

            // Index in Spotlight for system-wide search
            spotlightService.indexSessions(sessions)

            // Refresh widgets with latest data
            refreshWidgets(connectors: connectors, sessions: sessions)

        } catch {
            withAnimation {
                appState.isConnected = false
                appState.errorMessage = error.localizedDescription
            }
        }
    }

    // MARK: - Connector Change Detection

    private func detectConnectorChanges(old: [ConnectorHealth], new: [ConnectorHealth]) {
        let oldDict = Dictionary(uniqueKeysWithValues: old.map { ($0.name, $0.available) })
        for connector in new {
            if let wasAvailable = oldDict[connector.name] {
                if wasAvailable && !connector.available {
                    Task {
                        await notificationService.scheduleConnectorDownAlert(connectorName: connector.name)
                    }
                } else if !wasAvailable && connector.available {
                    Task {
                        await notificationService.scheduleConnectorUpAlert(connectorName: connector.name)
                    }
                }
            }
        }
    }

    // MARK: - Widget Refresh

    private func refreshWidgets(connectors: [ConnectorHealth], sessions: [Session]) {
        // Write connector data to App Group for widget access
        if let data = try? JSONEncoder().encode(connectors) {
            UserDefaults(suiteName: JambubrowserKit.appGroupIdentifier)?
                .set(data, forKey: "cachedConnectors")
        }
        if let data = try? JSONEncoder().encode(sessions) {
            UserDefaults(suiteName: JambubrowserKit.appGroupIdentifier)?
                .set(data, forKey: "cachedSessions")
        }

        // Tell widgets to refresh
        WidgetCenter.shared.reloadTimelines(ofKind: "ConnectorStatus")
        WidgetCenter.shared.reloadTimelines(ofKind: "SessionSummary")
    }
}

// MARK: - Subviews

struct StatCard: View {
    @Environment(\.colorScheme) private var colorScheme
    let title: String
    let value: String
    let icon: String
    let color: Color

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundStyle(color)
            Text(value)
                .font(.title2.bold())
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}

struct ConnectorCard: View {
    let connector: ConnectorHealth

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle()
                    .fill(connector.available ? .green : .red)
                    .frame(width: 8, height: 8)
                Text(connector.name)
                    .font(.subheadline.bold())
                Spacer()
            }

            if connector.available {
                Text("\(connector.capabilities.count) capabilities")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                Text("Unavailable")
                    .font(.caption2)
                    .foregroundStyle(.red)
            }
        }
        .padding(12)
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct SessionRow: View {
    let session: Session

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.description)
                    .font(.subheadline)
                    .lineLimit(1)
                Text(session.id.prefix(8).lowercased())
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(session.status)
                .font(.caption2)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(statusColor.opacity(0.2))
                .foregroundStyle(statusColor)
                .clipShape(Capsule())
        }
        .padding(.vertical, 4)
    }

    private var statusColor: Color {
        switch session.status {
        case "active": return .green
        case "completed": return .blue
        case "failed": return .red
        default: return .gray
        }
    }
}

#Preview {
    DashboardView()
        .environment(AppState())
        .environment(GatewayClient(baseURL: URL(string: "http://localhost:8001")!))
        .environment(NetworkMonitor())
        .environment(NotificationService())
        .environment(SpotlightService())
}
