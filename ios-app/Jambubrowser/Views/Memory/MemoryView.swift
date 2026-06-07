// Memory — search, browse, and add persistent memories.
// Integrates: SwiftData caching, Spotlight indexing, TipKit.

import SwiftUI
import SwiftData
import JambubrowserKit

struct MemoryView: View {
    @Environment(AppState.self) private var appState
    @Environment(GatewayClient.self) private var client
    @Environment(SpotlightService.self) private var spotlightService
    @Environment(\.modelContext) private var modelContext

    @State private var searchText = ""
    @State private var showAddSheet = false
    @State private var isSearching = false

    @State private var newCategory = "general"
    @State private var newKey = ""
    @State private var newValue = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Search bar
                HStack {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                    TextField("Search memories...", text: $searchText)
                        .textFieldStyle(.plain)
                        .onSubmit { Task { await search() } }

                    if !searchText.isEmpty {
                        Button {
                            searchText = ""
                            appState.searchResults = []
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(12)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .padding()
                .popoverTip(MemoryTip())

                // Results
                if isSearching {
                    ProgressView("Searching...")
                        .padding()
                    Spacer()
                } else if appState.searchResults.isEmpty && !searchText.isEmpty {
                    ContentUnavailableView(
                        "No Results",
                        systemImage: "magnifyingglass",
                        description: Text("No memories match \"\(searchText)\".")
                    )
                } else if appState.searchResults.isEmpty {
                    ContentUnavailableView(
                        "Memory Store",
                        systemImage: "brain",
                        description: Text("Jambubrowser remembers context across sessions. Search or add memories.")
                    )
                } else {
                    List(appState.searchResults) { entry in
                        MemoryEntryRow(entry: entry)
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Memory")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showAddSheet = true
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showAddSheet) {
                addMemorySheet
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

    // MARK: - Add Memory Sheet

    private var addMemorySheet: some View {
        NavigationStack {
            Form {
                Section("Category") {
                    Picker("Category", selection: $newCategory) {
                        Text("General").tag("general")
                        Text("Code").tag("code")
                        Text("Preference").tag("preference")
                        Text("Context").tag("context")
                    }
                }

                Section("Key") {
                    TextField("Key (e.g., 'preferred-language')", text: $newKey)
                }

                Section("Value") {
                    TextEditor(text: $newValue)
                        .frame(minHeight: 80)
                }
            }
            .navigationTitle("Add Memory")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showAddSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await addMemory() }
                    }
                    .disabled(newKey.isEmpty || newValue.isEmpty)
                }
            }
        }
    }

    // MARK: - Actions

    private func search() async {
        guard !searchText.isEmpty else { return }
        isSearching = true
        do {
            let results = try await client.searchMemory(query: searchText)
            appState.searchResults = results

            // Cache results
            for entry in results {
                let eid = entry.id
                let descriptor = FetchDescriptor<CachedMemoryEntry>(
                    predicate: #Predicate { $0.entryId == eid }
                )
                if (try? modelContext.fetch(descriptor))?.isEmpty ?? true {
                    modelContext.insert(CachedMemoryEntry(from: entry))
                }
            }
            try? modelContext.save()

            // Index in Spotlight
            spotlightService.indexMemories(results)

            // Mark tip as used
            MemoryTip.hasUsedMemory = true
        } catch {
            appState.errorMessage = error.localizedDescription
        }
        isSearching = false
    }

    private func addMemory() async {
        do {
            try await client.addMemory(category: newCategory, key: newKey, value: newValue)
            showAddSheet = false
            newKey = ""
            newValue = ""

            // Re-search to show the new entry
            if !searchText.isEmpty {
                await search()
            }
        } catch {
            appState.errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Entry Row

struct MemoryEntryRow: View {
    let entry: MemoryEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(entry.key)
                    .font(.subheadline.bold())
                Spacer()
                Text(entry.category)
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.indigo.opacity(0.2))
                    .clipShape(Capsule())
            }

            Text(entry.value)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(3)

            if let tags = entry.tags, !tags.isEmpty {
                HStack(spacing: 4) {
                    ForEach(tags, id: \.self) { tag in
                        Text("#\(tag)")
                            .font(.caption2)
                            .foregroundStyle(.indigo)
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

#Preview {
    MemoryView()
        .environment(AppState())
        .environment(GatewayClient(baseURL: URL(string: "http://localhost:8001")!))
        .environment(SpotlightService())
}
