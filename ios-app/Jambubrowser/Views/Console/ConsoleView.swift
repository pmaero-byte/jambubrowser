// Console — persistent Codex-style terminal for Jambubrowser output.
// Shows real-time streaming output, command history, and connector status.

import SwiftUI
import JambubrowserKit

struct ConsoleView: View {
    @Environment(AppState.self) private var appState
    @Environment(GatewayClient.self) private var client

    @State private var consoleOutput = ""
    @State private var commandInput = ""
    @State private var isExecuting = false
    @State private var commandHistory: [String] = []
    @State private var historyIndex = -1

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Quick connector status bar
                connectorStatusBar

                // Terminal
                TerminalView(
                    output: $consoleOutput,
                    isRunning: $isExecuting,
                    placeholder: """
╭──────────────────────────────────────────────────╮
│  🐴 Jambubrowser Console v1.1.0                      │
│                                                  │
│  Type a prompt and press ⏎ to execute.           │
│  Use /help for available commands.               │
│  Connectors: hermes, claude, opencode, mcp       │
╰──────────────────────────────────────────────────╯

""",
                    showPrompt: true,
                    promptText: "jambubrowser",
                    promptInput: $commandInput,
                    onSend: executeCommand
                )
                .padding(.horizontal, 8)
                .padding(.bottom, 8)
            }
            .navigationTitle("Console")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button {
                            consoleOutput = ""
                        } label: {
                            Label("Clear Console", systemImage: "trash")
                        }
                        Button {
                            UIPasteboard.general.string = consoleOutput
                        } label: {
                            Label("Copy All", systemImage: "doc.on.doc")
                        }
                        Divider()
                        Button {
                            runHealthCheck()
                        } label: {
                            Label("Health Check", systemImage: "heart")
                        }
                        Button {
                            runConnectorStatus()
                        } label: {
                            Label("Connector Status", systemImage: "cpu")
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
        }
    }

    // MARK: - Connector Status Bar

    private var connectorStatusBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(appState.connectors) { connector in
                    HStack(spacing: 4) {
                        Circle()
                            .fill(connector.available ? Color.green : Color.red)
                            .frame(width: 6, height: 6)
                        Text(connector.name)
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.ultraThinMaterial)
                    .clipShape(Capsule())
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
        }
    }

    // MARK: - Command Execution

    private func executeCommand() {
        let cmd = commandInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cmd.isEmpty else { return }

        commandHistory.append(cmd)
        historyIndex = -1
        commandInput = ""

        // Add command to output with prompt
        appendLine("❯ \(cmd)", color: "#bb9af7")

        // Handle slash commands
        if cmd.hasPrefix("/") {
            handleSlashCommand(String(cmd.dropFirst()))
            return
        }

        // Execute as AI prompt
        Task { await runPrompt(cmd) }
    }

    private func handleSlashCommand(_ cmd: String) {
        switch cmd.lowercased() {
        case "help":
            appendLine("""
Available commands:
  /help          Show this help
  /health        Check gateway health
  /connectors    Show connector status
  /clear         Clear console
  /sessions      List recent sessions
  /models        List available models
  <prompt>       Execute an AI task
""", color: "#9ece6a")
        case "health":
            runHealthCheck()
        case "connectors":
            runConnectorStatus()
        case "clear":
            consoleOutput = ""
        case "sessions":
            Task { await listSessions() }
        case "models":
            Task { await listModels() }
        default:
            appendLine("Unknown command: /\(cmd). Type /help for available commands.", color: "#f7768e")
        }
    }

    private func runPrompt(_ prompt: String) async {
        isExecuting = true
        appendLine("→ Routing to optimal connector...", color: "#565f89")

        do {
            let result = try await client.run(prompt: prompt)
            isExecuting = false

            if let firstTask = result.tasks.first {
                let status = firstTask.status == "success" ? "✅" : "❌"
                appendLine("\(status) Completed in \(firstTask.durationMs)ms | Session: \(result.sessionId.prefix(8))", color: firstTask.status == "success" ? "#9ece6a" : "#f7768e")
                if !firstTask.output.isEmpty {
                    appendLine(String(firstTask.output.prefix(2000)), color: "#c0caf5")
                }
                if let error = firstTask.error {
                    appendLine("Error: \(error)", color: "#f7768e")
                }
            }
            appendLine(String(repeating: "─", count: 60), color: "#565f89")
        } catch {
            isExecuting = false
            appendLine("❌ Error: \(error.localizedDescription)", color: "#f7768e")
        }
    }

    private func runHealthCheck() {
        Task {
            do {
                let health = try await client.health()
                appendLine("✅ Gateway: \(health.status) | v\(health.version) | Connectors: \(health.connectors.joined(separator: ", "))", color: "#9ece6a")
            } catch {
                appendLine("❌ Health check failed: \(error.localizedDescription)", color: "#f7768e")
            }
        }
    }

    private func runConnectorStatus() {
        Task {
            do {
                let connectors = try await client.listConnectors()
                appendLine("Connectors:", color: "#c0caf5")
                for c in connectors {
                    let icon = c.available ? "🟢" : "🔴"
                    appendLine("  \(icon) \(c.name) — \(c.capabilities.joined(separator: ", "))", color: c.available ? "#9ece6a" : "#f7768e")
                }
            } catch {
                appendLine("❌ Failed: \(error.localizedDescription)", color: "#f7768e")
            }
        }
    }

    private func listSessions() async {
        do {
            let sessions = try await client.listSessions(limit: 10)
            appendLine("Recent Sessions:", color: "#c0caf5")
            for s in sessions {
                appendLine("  \(s.id.prefix(8)) | \(s.status) | \(s.description.prefix(60))", color: "#7aa2f7")
            }
        } catch {
            appendLine("❌ Failed: \(error.localizedDescription)", color: "#f7768e")
        }
    }

    private func listModels() async {
        do {
            let response = try await client.listModels()
            appendLine("Available Models:", color: "#c0caf5")
            for m in response.data {
                appendLine("  \(m.id) (\(m.ownedBy))", color: "#7aa2f7")
            }
        } catch {
            appendLine("❌ Failed: \(error.localizedDescription)", color: "#f7768e")
        }
    }

    // MARK: - Helpers

    private func appendLine(_ text: String, color: String = "#c0caf5") {
        consoleOutput += text + "\n"
    }
}

#Preview {
    ConsoleView()
        .environment(AppState())
        .environment(GatewayClient(baseURL: URL(string: "http://localhost:8001")!))
}
