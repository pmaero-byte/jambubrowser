// Settings — gateway URL, connection test, Keychain, notifications, connectors, models.

import SwiftUI
import SwiftData
import JambubrowserKit

struct SettingsView: View {
    @Environment(AppState.self) private var appState
    @Environment(GatewayClient.self) private var client
    @Environment(NotificationService.self) private var notificationService
    @Environment(\.modelContext) private var modelContext

    @State private var gatewayURLText = ""
    @State private var healthResult: HealthResponse?
    @State private var isChecking = false
    @State private var showResetAlert = false
    @State private var apiKeyText = ""
    @State private var mcpServers: [MCPServerStatus] = []
    @State private var showMCPAddSheet = false
    @State private var mcpNewName = ""
    @State private var mcpNewTransport = "stdio"
    @State private var mcpNewCommand = ""
    @State private var mcpNewUrl = ""

    var body: some View {
        NavigationStack {
            Form {
                // Gateway connection
                Section {
                    HStack {
                        Image(systemName: "server.rack")
                            .foregroundStyle(.indigo)
                        TextField("Gateway URL", text: $gatewayURLText)
                            .textContentType(.URL)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                    }

                    Button {
                        Task { await testConnection() }
                    } label: {
                        HStack {
                            if isChecking {
                                ProgressView()
                                    .scaleEffect(0.8)
                            } else {
                                Image(systemName: "antenna.radiowaves.left.and.right")
                            }
                            Text("Test Connection")
                        }
                    }
                    .disabled(gatewayURLText.isEmpty || isChecking)

                    if let health = healthResult {
                        HStack {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                            Text("Connected — v\(health.version)")
                                .font(.subheadline)
                        }
                    }
                } header: {
                    Text("Gateway")
                } footer: {
                    Text("The URL where your Jambubrowser gateway is running (e.g. http://192.168.1.100:8080)")
                }

                // Appearance
                Section {
                    Picker("Theme", selection: Binding(
                        get: { appState.colorScheme },
                        set: { appState.colorScheme = $0 }
                    )) {
                        ForEach(AppColorScheme.allCases, id: \.self) { scheme in
                            Text(scheme.displayName).tag(scheme)
                        }
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Appearance")
                } footer: {
                    Text("Choose between system, light, and dark appearance.")
                }

                // Security — Keychain
                Section {
                    HStack {
                        Image(systemName: "lock.shield")
                            .foregroundStyle(.green)
                        Text("API Keys")
                        Spacer()
                        Text(KeychainService.hasStoredAPIKeys() ? "Stored" : "None")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    SecureField("API Key", text: $apiKeyText)
                        .textContentType(.password)

                    HStack {
                        Button("Save to Keychain") {
                            try? KeychainService.saveAPIKey(apiKeyText, identifier: "jambubrowser")
                            apiKeyText = ""
                        }
                        .disabled(apiKeyText.isEmpty)

                        Spacer()

                        if KeychainService.hasStoredAPIKeys() {
                            Button("Clear", role: .destructive) {
                                try? KeychainService.deleteAPIKey(identifier: "jambubrowser")
                            }
                        }
                    }
                } header: {
                    Text("Security")
                } footer: {
                    Text("API keys are stored in the iOS Keychain with App Group sharing for widget access.")
                }

                // Notifications
                Section {
                    HStack {
                        Image(systemName: notificationService.isAuthorized ? "bell.fill" : "bell.slash")
                            .foregroundStyle(notificationService.isAuthorized ? .green : .red)
                        Text("Push Notifications")
                        Spacer()
                        Text(notificationService.isAuthorized ? "Enabled" : "Disabled")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if !notificationService.isAuthorized {
                        Button("Enable Notifications") {
                            Task { await notificationService.requestPermission() }
                        }
                    }
                } header: {
                    Text("Notifications")
                } footer: {
                    Text("Get notified when tasks complete or connectors go offline.")
                }

                // Connector status
                Section("Connector Status") {
                    ForEach(appState.connectors) { connector in
                        HStack {
                            Circle()
                                .fill(connector.available ? .green : .red)
                                .frame(width: 8, height: 8)
                            Text(connector.name)
                                .font(.subheadline)
                            Spacer()
                            if connector.available {
                                Text("\(connector.capabilities.count) caps")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            } else {
                                Text("Offline")
                                    .font(.caption)
                                    .foregroundStyle(.red)
                            }
                        }
                    }
                }

                // Models
                Section("Available Models") {
                    ForEach(appState.models) { model in
                        HStack {
                            Image(systemName: "cpu")
                                .foregroundStyle(.orange)
                            Text(model.id)
                                .font(.subheadline)
                            Spacer()
                            Text(model.ownedBy)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                // MCP Servers
                Section {
                    if mcpServers.isEmpty {
                        HStack {
                            Image(systemName: "plug")
                                .foregroundStyle(.secondary)
                            Text("No MCP servers configured")
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        ForEach(mcpServers) { server in
                            HStack {
                                Circle()
                                    .fill(server.connected ? .green : .gray)
                                    .frame(width: 8, height: 8)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(server.name)
                                        .font(.subheadline)
                                    Text(server.connected ? "\(server.toolCount) tools" : "Disconnected")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if server.connected {
                                    Button("Disconnect") {
                                        Task { await disconnectMCPServer(server.name) }
                                    }
                                    .font(.caption)
                                    .foregroundStyle(.red)
                                } else {
                                    Button("Connect") {
                                        Task { await connectMCPServer(server.name) }
                                    }
                                    .font(.caption)
                                }
                            }
                        }
                        .onDelete { indexSet in
                            for index in indexSet {
                                Task { await removeMCPServer(mcpServers[index].name) }
                            }
                        }
                    }

                    Button {
                        showMCPAddSheet = true
                    } label: {
                        Label("Add MCP Server", systemImage: "plus.circle")
                    }
                } header: {
                    Text("MCP Servers")
                } footer: {
                    Text("Connect to Model Context Protocol servers to extend Jambubrowser with external tools. Supports stdio (subprocess) and HTTP transports.")
                }

                // About
                Section("About") {
                    HStack {
                        Text("Version")
                        Spacer()
                        Text("1.0.0")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Platform")
                        Spacer()
                        Text("iOS 17+")
                            .foregroundStyle(.secondary)
                    }
                    Link(destination: URL(string: "https://github.com/pmaero-byte/jambubrowser")!) {
                        HStack {
                            Text("GitHub")
                            Spacer()
                            Image(systemName: "arrow.up.right.square")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                // Danger zone
                Section {
                    Button("Reset Gateway URL") {
                        showResetAlert = true
                    }
                    .foregroundStyle(.red)
                }
            }
            .navigationTitle("Settings")
            .onAppear {
                gatewayURLText = appState.gatewayURL
                Task { await loadMCPServers() }
            }
            .sheet(isPresented: $showMCPAddSheet) {
                mcpAddSheet
            }
            .alert("Reset Gateway URL?", isPresented: $showResetAlert) {
                Button("Cancel", role: .cancel) { }
                Button("Reset", role: .destructive) {
                    gatewayURLText = "http://localhost:8001"
                    appState.gatewayURL = gatewayURLText
                    client.updateBaseURL(URL(string: gatewayURLText)!)
                    let prefs = UserPreferences.fetchOrCreate(in: modelContext)
                    prefs.gatewayURL = gatewayURLText
                    try? modelContext.save()
                }
            } message: {
                Text("This will reset the gateway URL to localhost:8080")
            }
            .alert("Connection Error", isPresented: Binding(
                get: { appState.errorMessage != nil },
                set: { if !$0 { appState.clearError() } }
            )) {
                Button("OK") { appState.clearError() }
            } message: {
                Text(appState.errorMessage ?? "")
            }
        }
    }

    private func testConnection() async {
        isChecking = true
        appState.clearError()

        guard let url = URL(string: gatewayURLText) else {
            appState.errorMessage = "Invalid URL"
            isChecking = false
            return
        }

        appState.gatewayURL = gatewayURLText
        client.updateBaseURL(url)

        do {
            healthResult = try await client.health()
            appState.isConnected = true

            async let connectorsTask = client.listConnectors()
            async let modelsTask = client.listModels()
            appState.connectors = try await connectorsTask
            appState.models = try await modelsTask.data

            // Persist URL
            let prefs = UserPreferences.fetchOrCreate(in: modelContext)
            prefs.gatewayURL = gatewayURLText
            try? modelContext.save()

        } catch {
            healthResult = nil
            appState.isConnected = false
            appState.errorMessage = error.localizedDescription
        }

        isChecking = false
    }

    // MARK: - MCP Actions

    private var mcpAddSheet: some View {
        NavigationStack {
            Form {
                Section("Server Name") {
                    TextField("e.g., filesystem, web-search", text: $mcpNewName)
                        .autocapitalization(.none)
                }

                Section("Transport") {
                    Picker("Transport", selection: $mcpNewTransport) {
                        Text("stdio (subprocess)").tag("stdio")
                        Text("HTTP (remote)").tag("http")
                    }
                    .pickerStyle(.segmented)
                }

                if mcpNewTransport == "stdio" {
                    Section("Command") {
                        TextField("e.g., npx, python, node", text: $mcpNewCommand)
                            .autocapitalization(.none)
                    }
                } else {
                    Section("URL") {
                        TextField("https://mcp-server.example.com", text: $mcpNewUrl)
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                    }
                }
            }
            .navigationTitle("Add MCP Server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showMCPAddSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        Task { await addMCPServer() }
                    }
                    .disabled(mcpNewName.isEmpty || (mcpNewTransport == "stdio" && mcpNewCommand.isEmpty) || (mcpNewTransport == "http" && mcpNewUrl.isEmpty))
                }
            }
        }
    }

    private func loadMCPServers() async {
        do {
            mcpServers = try await client.listMCPServers()
        } catch {
            // Silently handle — MCP may not be available
        }
    }

    private func connectMCPServer(_ name: String) async {
        do {
            let _ = try await client.connectMCPServer(name: name)
            await loadMCPServers()
        } catch {
            appState.errorMessage = "Failed to connect: \(error.localizedDescription)"
        }
    }

    private func disconnectMCPServer(_ name: String) async {
        do {
            try await client.disconnectMCPServer(name: name)
            await loadMCPServers()
        } catch {
            appState.errorMessage = "Failed to disconnect: \(error.localizedDescription)"
        }
    }

    private func removeMCPServer(_ name: String) async {
        do {
            try await client.removeMCPServer(name: name)
            await loadMCPServers()
        } catch {
            appState.errorMessage = "Failed to remove: \(error.localizedDescription)"
        }
    }

    private func addMCPServer() async {
        guard !mcpNewName.isEmpty else { return }
        do {
            let config = MCPServerConfig(
                name: mcpNewName,
                transport: mcpNewTransport,
                command: mcpNewTransport == "stdio" ? mcpNewCommand : nil,
                url: mcpNewTransport == "http" ? mcpNewUrl : nil
            )
            let _ = try await client.addMCPServer(config)
            await loadMCPServers()
            showMCPAddSheet = false
            mcpNewName = ""
            mcpNewCommand = ""
            mcpNewUrl = ""
        } catch {
            appState.errorMessage = "Failed to add MCP server: \(error.localizedDescription)"
        }
    }
}

#Preview {
    SettingsView()
        .environment(AppState())
        .environment(GatewayClient(baseURL: URL(string: "http://localhost:8001")!))
        .environment(NotificationService())
}
