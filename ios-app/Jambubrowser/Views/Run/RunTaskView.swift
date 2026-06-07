// Run Task — prompt input, connector selection, streaming results, multi-connector comparison.
// Integrates: Live Activity, Handoff, Smart Paste, TipKit, Notifications, Network banner.

import SwiftUI
import SwiftData
import WidgetKit
import UIKit
import JambubrowserKit

struct RunTaskView: View {
    @Environment(AppState.self) private var appState
    @Environment(GatewayClient.self) private var client
    @Environment(NetworkMonitor.self) private var networkMonitor
    @Environment(NotificationService.self) private var notificationService
    @Environment(LiveActivityService.self) private var liveActivityService
    @Environment(SpotlightService.self) private var spotlightService
    @Environment(HandoffService.self) private var handoffService
    @Environment(\.modelContext) private var modelContext
    @Environment(\.scenePhase) private var scenePhase

    @State private var selectedTool = "auto"
    @State private var useMulti = false
    @State private var selectedConnectors: Set<String> = []
    @State private var showResult = false
    @State private var taskStartTime: Date?

    private let tools = ["auto", "claude", "codex", "hermes", "opencode"]

    var body: some View {
        @Bindable var appState = appState
        NavigationStack {
            ZStack(alignment: .top) {
                ScrollView {
                    VStack(spacing: 20) {
                        // Network status
                        if !networkMonitor.isConnected {
                            NetworkStatusBanner()
                        }

                        // Prompt input
                        promptSection

                        // Tool selector
                        toolSection

                        // Multi-connector toggle
                        multiSection

                        // Run button
                        runButton

                        // Results in Codex-style terminal
                        if !appState.runOutput.isEmpty || appState.isRunning {
                            TerminalView(
                                output: $appState.runOutput,
                                isRunning: $appState.isRunning,
                                showPrompt: false,
                                promptText: "",
                                promptInput: .constant("")
                            )
                            .frame(minHeight: 300)
                        }

                        if let error = appState.errorMessage {
                            errorSection(error)
                        }
                    }
                    .padding()
                }

                NetworkStatusBanner()
            }
            .navigationTitle("Run Task")
            .alert("Task Error", isPresented: Binding(
                get: { appState.errorMessage != nil },
                set: { if !$0 { appState.clearError() } }
            )) {
                Button("OK") { appState.clearError() }
            } message: {
                Text(appState.errorMessage ?? "")
            }
        }
    }

    // MARK: - Prompt

    private var promptSection: some View {
        @Bindable var appState = appState
        return VStack(alignment: .leading, spacing: 8) {
            Text("Prompt")
                .font(.headline)
                .popoverTip(RunTaskTip())

            TextEditor(text: $appState.currentPrompt)
                .frame(minHeight: 100)
                .padding(8)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(.quaternary, lineWidth: 1)
                )
                .smartPaste(prompt: $appState.currentPrompt)
                .onChange(of: appState.currentPrompt) { _, newValue in
                    // Start Handoff activity when user types a prompt
                    if !newValue.isEmpty {
                        handoffService.startActivity(prompt: newValue)
                    }
                }
        }
    }

    // MARK: - Tool Selection

    private var toolSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Connector")
                .font(.headline)

            Picker("Tool", selection: $selectedTool) {
                ForEach(tools, id: \.self) { tool in
                    Text(tool == "auto" ? "🤖 Auto (Smart Routing)" : tool.capitalized)
                        .tag(tool)
                }
            }
            .pickerStyle(.segmented)
        }
    }

    // MARK: - Multi-Connector

    private var multiSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("Compare across connectors", isOn: $useMulti)
                .font(.headline)
                .popoverTip(MultiConnectorTip())

            if useMulti {
                Text("Select connectors to compare:")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                LazyVGrid(columns: [
                    GridItem(.flexible()),
                    GridItem(.flexible())
                ], spacing: 8) {
                    ForEach(appState.connectors.filter(\.available), id: \.name) { connector in
                        let isSelected = selectedConnectors.contains(connector.name)
                        Button {
                            if isSelected {
                                selectedConnectors.remove(connector.name)
                            } else {
                                selectedConnectors.insert(connector.name)
                            }
                        } label: {
                            HStack {
                                Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                                Text(connector.name)
                            }
                            .font(.subheadline)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity)
                            .background(isSelected ? AnyShapeStyle(Color.indigo.opacity(0.2)) : AnyShapeStyle(.ultraThinMaterial))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    // MARK: - Run Button

    private var runButton: some View {
        Button {
            Task { await executeTask() }
        } label: {
            HStack {
                Image(systemName: "play.fill")
                Text("Execute")
            }
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(appState.currentPrompt.isEmpty || appState.isRunning ? .gray : .indigo)
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(appState.currentPrompt.isEmpty || appState.isRunning || !networkMonitor.isConnected)
    }

    // MARK: - Result

    private var resultSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Output")
                    .font(.headline)
                Spacer()
                if let result = appState.lastRunResult {
                    Text("\(result.tasks.first?.durationMs ?? 0)ms")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Text(appState.runOutput)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.ultraThinMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    // MARK: - Parallel Result

    private func parallelResultSection(_ result: ParallelResponse) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Comparison")
                .font(.headline)

            if let winner = result.winner {
                HStack {
                    Image(systemName: "trophy.fill")
                        .foregroundStyle(.yellow)
                    Text("Winner: \(winner)")
                        .font(.subheadline.bold())
                }
            }

            Text("Similarity: \(Int(result.similarity * 100))%")
                .font(.caption)

            ForEach(Array(result.connectorResults.keys.sorted()), id: \.self) { key in
                if let task = result.connectorResults[key] {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(key)
                                .font(.subheadline.bold())
                            Spacer()
                            Text(task.status)
                                .font(.caption2)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(task.status == "success" ? .green.opacity(0.2) : .red.opacity(0.2))
                                .clipShape(Capsule())
                        }
                        if !task.output.isEmpty {
                            Text(task.output.prefix(200))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(8)
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }

            if !result.diff.isEmpty {
                DisclosureGroup("Diff") {
                    Text(result.diff)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                }
            }
        }
    }

    // MARK: - Error

    private func errorSection(_ error: String) -> some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(error)
                .font(.subheadline)
        }
        .padding()
        .background(.red.opacity(0.1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Execution

    private func executeTask() async {
        appState.isRunning = true
        appState.runOutput = ""
        appState.lastRunResult = nil
        appState.lastParallelResult = nil
        appState.clearError()
        taskStartTime = Date()

        let prompt = appState.currentPrompt
        let connector = selectedTool == "auto" ? "" : selectedTool

        // Save prompt for widget and Spotlight
        appState.saveRecentPrompt(prompt)

        // Start Live Activity
        liveActivityService.startActivity(prompt: prompt, connector: selectedTool)

        // Update Handoff
        handoffService.startActivity(prompt: prompt)

        do {
            if useMulti && !selectedConnectors.isEmpty {
                // Parallel multi-connector execution
                let result = try await client.runParallel(
                    prompt: prompt,
                    connectors: Array(selectedConnectors)
                )
                appState.lastParallelResult = result
                appState.runOutput = result.connectorResults.values.first?.output ?? ""

                // Mark multi-connector tip as used
                MultiConnectorTip.hasUsedMulti = true

                // Complete Live Activity
                await liveActivityService.completeActivity(output: appState.runOutput)

            } else {
                // Single connector with SSE streaming
                var taskCount = 0
                let result = try await client.runStream(
                    prompt: prompt,
                    tool: connector
                ) { type, data in
                    DispatchQueue.main.async {
                        appState.streamEvents.append("[\(type)] \(data)")

                        if type == "task" {
                            taskCount += 1
                            Task {
                                await liveActivityService.updateProgress(
                                    taskIndex: taskCount,
                                    totalTasks: 1,
                                    connector: self.selectedTool
                                )
                            }
                        }
                    }
                }
                appState.lastRunResult = result
                appState.runOutput = result.tasks.first?.output ?? ""
                appState.lastSessionId = result.sessionId

                // Cache task results
                for task in result.tasks {
                    appState.cacheTaskResult(task, sessionId: result.sessionId, in: modelContext)
                }

                // Index session in Spotlight
                if !result.sessionId.isEmpty {
                    let session = Session(
                        id: result.sessionId,
                        description: String(prompt.prefix(100)),
                        status: "completed"
                    )
                    spotlightService.indexSession(session)
                }

                // Update Handoff with session ID
                handoffService.updateActivity(prompt: prompt, sessionId: result.sessionId)

                // Complete Live Activity
                await liveActivityService.completeActivity(output: appState.runOutput)
            }

            // Mark run task tip as used
            RunTaskTip.hasRunTask = true

            // Haptic feedback
            let generator = UIImpactFeedbackGenerator(style: .heavy)
            generator.impactOccurred()

            // Send notification if app is in background or task took > 5s
            let elapsed = Date().timeIntervalSince(taskStartTime ?? Date())
            if scenePhase == .background || elapsed > 5 {
                await notificationService.scheduleTaskCompletionNotification(
                    taskId: appState.lastRunResult?.sessionId ?? "unknown",
                    output: appState.runOutput
                )
            }

            // Refresh widgets
            WidgetCenter.shared.reloadTimelines(ofKind: "QuickPrompt")

        } catch {
            appState.errorMessage = error.localizedDescription
            await liveActivityService.failActivity(error: error.localizedDescription)
            // Error haptic
            let generator = UINotificationFeedbackGenerator()
            generator.notificationOccurred(.error)
        }

        appState.isRunning = false
        handoffService.invalidateActivity()
    }
}

#Preview {
    RunTaskView()
        .environment(AppState())
        .environment(GatewayClient(baseURL: URL(string: "http://localhost:8001")!))
        .environment(NetworkMonitor())
        .environment(NotificationService())
        .environment(LiveActivityService())
        .environment(SpotlightService())
        .environment(HandoffService())
}
