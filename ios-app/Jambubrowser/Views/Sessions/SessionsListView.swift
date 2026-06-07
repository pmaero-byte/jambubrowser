// Sessions list — searchable, cached via SwiftData, indexed in Spotlight.

import SwiftUI
import SwiftData
import JambubrowserKit

struct SessionsListView: View {
    @Environment(AppState.self) private var appState
    @Environment(GatewayClient.self) private var client
    @Environment(SpotlightService.self) private var spotlightService
    @Environment(\.modelContext) private var modelContext

    @State private var searchText = ""
    @State private var selectedSession: Session?
    @State private var isLoading = false

    private var filteredSessions: [Session] {
        if searchText.isEmpty {
            return appState.sessions
        }
        return appState.sessions.filter {
            $0.description.localizedCaseInsensitiveContains(searchText) ||
            $0.id.localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                if filteredSessions.isEmpty && !isLoading {
                    ContentUnavailableView(
                        "No Sessions",
                        systemImage: "clock",
                        description: Text("Run a task to create your first session.")
                    )
                } else {
                    ForEach(filteredSessions) { session in
                        SessionListRow(session: session)
                            .onTapGesture {
                                selectedSession = session
                            }
                    }
                }
            }
            .searchable(text: $searchText, prompt: "Search sessions")
            .navigationTitle("Sessions")
            .sheet(item: $selectedSession) { session in
                SessionDetailView(sessionId: session.id)
            }
            .refreshable {
                await loadSessions()
            }
            .task {
                // Load from cache first
                appState.loadCachedSessions(from: modelContext)
                await loadSessions()
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

    private func loadSessions() async {
        isLoading = true
        do {
            let sessions = try await client.listSessions(limit: 50)
            withAnimation {
                appState.sessions = sessions
            }
            // Cache for offline
            appState.cacheSessions(in: modelContext)
            // Index in Spotlight
            spotlightService.indexSessions(sessions)
        } catch {
            appState.errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

// MARK: - Row

struct SessionListRow: View {
    let session: Session

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(session.description)
                    .font(.subheadline)
                    .lineLimit(2)
                HStack(spacing: 8) {
                    Text(session.id.prefix(8).lowercased())
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if let count = session.entryCount {
                        Text("\(count) entries")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
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
        .padding(.vertical, 2)
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

// MARK: - Detail

struct SessionDetailView: View {
    @Environment(GatewayClient.self) private var client
    let sessionId: String
    @State private var detail: SessionDetail?
    @State private var isLoading = true
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if isLoading {
                        ProgressView("Loading entries...")
                            .frame(maxWidth: .infinity)
                            .padding()
                    } else if let entries = detail?.entries, !entries.isEmpty {
                        Text("Context Entries")
                            .font(.headline)

                        ForEach(entries) { entry in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(entry.key)
                                        .font(.subheadline.bold())
                                    Spacer()
                                    if let type = entry.entryType {
                                        Text(type)
                                            .font(.caption2)
                                            .padding(.horizontal, 6)
                                            .padding(.vertical, 2)
                                            .background(.indigo.opacity(0.2))
                                            .clipShape(Capsule())
                                    }
                                }
                                Text(entry.value)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(4)
                            }
                            .padding(8)
                            .background(.ultraThinMaterial)
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    } else {
                        Text("No entries")
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity)
                            .padding()
                    }
                }
                .padding()
            }
            .navigationTitle("Session \(sessionId.prefix(8))")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .task { await loadDetail() }
        }
    }

    private func loadDetail() async {
        do {
            detail = try await client.getSessionDetail(sessionId)
        } catch {
            // Silently handle
        }
        isLoading = false
    }
}

#Preview {
    SessionsListView()
        .environment(AppState())
        .environment(GatewayClient(baseURL: URL(string: "http://localhost:8001")!))
        .environment(SpotlightService())
}
