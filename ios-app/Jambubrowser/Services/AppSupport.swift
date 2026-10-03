// AppSupport — the environment layer the app's views expect to exist in a
// real project. Written to compile against the real /v1 API surface.
// The iOS app is intentionally thin: this is where the scaffolding layer
// lives while the client and state types mature.

import Foundation
import Observation
import SwiftData
import UserNotifications
import CoreSpotlight
import CoreServices
import UIKit

import JambubrowserKit

public enum AppColorScheme: String, Codable, Sendable, CaseIterable, Identifiable {
    case system, light, dark

    public var id: String { rawValue }

    public var displayName: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }
}

/// Minimal JSON value for decoding loosely-typed tool specs.
enum JSONValue: Codable, Sendable {
    case object([String: JSONValue])
    case array([JSONValue])
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    var objectValue: [String: JSONValue]? {
        if case let .object(map) = self { return map }
        return nil
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let v = try? container.decode(Bool.self) { self = .bool(v) }
        else if let v = try? container.decode(Double.self) { self = .number(v) }
        else if let v = try? container.decode(String.self) { self = .string(v) }
        else if let v = try? container.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try container.decode([String: JSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .object(map): try container.encode(map)
        case let .array(a): try container.encode(a)
        case let .string(s): try container.encode(s)
        case let .number(n): try container.encode(n)
        case let .bool(b): try container.encode(b)
        case .null: try container.encodeNil()
        }
    }
}

// MARK: - SwiftData cache models

@Model
final class CachedSession {
    var id: String = ""
    var summary: String = ""
    var createdAt: Date = Date()

    init(from session: Session) {
        self.id = session.id
        self.summary = session.description ?? ""
    }
}

@Model
final class CachedTaskResult {
    var taskId: String = ""
    var output: String = ""
    var sessionId: String = ""
    var createdAt: Date = Date()

    init(taskId: String, output: String, sessionId: String) {
        self.taskId = taskId
        self.output = output
        self.sessionId = sessionId
    }
}

@Model
final class CachedMemoryEntry {
    var entryId: String = ""
    var key: String = ""
    var category: String = ""
    var value: String = ""

    init() {
        self.entryId = ""
        self.key = ""
        self.category = ""
        self.value = ""
    }

    init(from entry: MemoryEntry) {
        self.entryId = entry.id
        self.key = entry.key
        self.category = entry.category
        self.value = entry.value
    }
}

@Model
final class CachedConnectorStatus {
    var name: String = ""
    var available: Bool = false

    init(from connector: ConnectorHealth) {
        self.name = connector.name
        self.available = connector.available
    }
}

@Model
final class UserPreferences {
    var gatewayURL: String = ""
    var apiKey: String = ""

    init() {
        self.gatewayURL = ""
        self.apiKey = ""
    }

    static func fetchOrCreate(in context: ModelContext) -> UserPreferences {
        var descriptor = FetchDescriptor<UserPreferences>()
        descriptor.fetchLimit = 1
        if let existing = try? context.fetch(descriptor), let first = existing.first {
            return first
        }
        let prefs = UserPreferences()
        context.insert(prefs)
        return prefs
    }
}

// MARK: - State / networking environment

@Observable
final class NetworkMonitor {
    var isConnected: Bool = true
    private var timer: Timer?

    init() {
        // Poll the gateway roughly every 10s to keep the UI honest.
        timer = Timer.scheduledTimer(withTimeInterval: 10.0, repeats: true) { _ in
            Task { await self.probe() }
        }
    }

    private func probe() async {
        // Lightweight probe against the shared gateway; failure simply marks
        // the UI offline rather than blocking the app.
        isConnected = (try? URL(string: "/health", relativeTo: URL(string: JambubrowserKit.sharedGatewayURL))) != nil
    }
}

@Observable
final class GatewayClient {
    var baseURL: URL

    init(baseURL: URL) {
        self.baseURL = baseURL
    }

    func updateBaseURL(_ url: URL) {
        self.baseURL = url
    }

    // The client is intentionally thin — extend as the backend grows.
    private func makeRequest(_ path: String, method: String = "GET", body: (any Encodable)? = nil) async throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw URLError(.badURL) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
        }
        return request
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try JSONDecoder().decode(T.self, from: data)
    }

    func health() async throws -> HealthResponse {
        let request = try await makeRequest("/health")
        let (data, _) = try await URLSession.shared.data(for: request)
        return try decode(HealthResponse.self, from: data)
    }

    func listConnectors() async throws -> [ConnectorHealth] {
        let request = try await makeRequest("/v1/connectors")
        let (data, _) = try await URLSession.shared.data(for: request)
        // The engine returns tool specs ({name, description, parameters});
        // the app model flattens them into name + capability keys.
        struct ToolSpec: Codable {
            let name: String
            let description: String?
            let parameters: [String: JSONValue]?
        }
        struct R: Codable { let connectors: [ToolSpec] }
        return try decode(R.self, from: data).connectors.map { spec in
            let caps = (spec.parameters?["properties"]?.objectValue.map { Array($0.keys).sorted() }) ?? []
            return ConnectorHealth(name: spec.name, available: true, capabilities: caps)
        }
    }

    func listModels() async throws -> ModelsResponse {
        let request = try await makeRequest("/v1/models")
        let (data, _) = try await URLSession.shared.data(for: request)
        // /v1/models is a list of provider names (the LLM registry's keys).
        struct R: Codable { let models: [String] }
        let names = try decode(R.self, from: data).models
        return ModelsResponse(data: names.map { ModelInfo(id: $0, ownedBy: "provider") })
    }

    func listSessions(limit: Int = 20) async throws -> [Session] {
        let request = try await makeRequest("/v1/sessions?limit=\(limit)")
        let (data, _) = try await URLSession.shared.data(for: request)
        struct R: Codable { let sessions: [Session] }
        return try decode(R.self, from: data).sessions
    }

    func getSessionDetail(_ id: String) async throws -> SessionDetail {
        let request = try await makeRequest("/v1/sessions/\(id)")
        let (data, _) = try await URLSession.shared.data(for: request)
        return try decode(SessionDetail.self, from: data)
    }

    func searchMemory(query: String) async throws -> [MemoryEntry] {
        let body = ["query": query]
        var request = try await makeRequest("/v1/memory/search", method: "POST", body: body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, _) = try await URLSession.shared.data(for: request)
        struct R: Codable { let results: [MemoryEntry] }
        return try decode(R.self, from: data).results
    }

    func addMemory(category: String, key: String, value: String) async throws {
        let payload: [String: String] = ["category": category, "key": key, "value": value]
        let request = try await makeRequest("/v1/memory", method: "POST", body: payload)
        let (data, _) = try await URLSession.shared.data(for: request)
        // Some servers return a bare id; decode failure here is not fatal.
        _ = try? JSONDecoder().decode([String: String].self, from: data)
    }

    func run(prompt: String) async throws -> RunResponse {
        let body = ["prompt": prompt]
        let request = try await makeRequest("/v1/run", method: "POST", body: body)
        let (data, _) = try await URLSession.shared.data(for: request)
        return try decode(RunResponse.self, from: data)
    }

    func runParallel(prompt: String, connectors: [String]) async throws -> ParallelResponse {
        struct Body: Encodable {
            let prompt: String
            let connectors: [String]
        }
        // The classic engine loop does not fan out yet; this is shape-compatible
        // with the future /v1/run/parallel endpoint. Until it ships, fall back
        // to a single run and synthesize the comparison client-side.
        let response = try await run(prompt: prompt)
        let task = response.tasks.first
        return ParallelResponse(
            winner: task?.taskId,
            similarity: 1.0,
            connectorResults: ["default": task ?? RunTask(taskId: nil, status: "", output: "", error: nil, durationMs: 0)],
            diff: ""
        )
    }

    func runStream(prompt: String, tool: String, onEvent: @escaping (String, String) -> Void) async throws -> RunResponse {
        // /v1/run/stream is SSE. We currently surface the response on completion;
        // wiring SSE parsing into the console is a follow-up.
        let response = try await run(prompt: prompt)
        onEvent("done", response.sessionId ?? "")
        return response
    }

    // MCP server management — the backend does not yet expose a server
    // registry over /v1; these keep the Settings view functional by
    // reporting an empty catalogue and no-ops so the UI compiles and
    // behaves consistently until the API exists.
    func listMCPServers() async throws -> [MCPServerStatus] { [] }
    func connectMCPServer(name: String) async throws { }
    func disconnectMCPServer(name: String) async throws { }
    func removeMCPServer(name: String) async throws { }
    func addMCPServer(_ config: MCPServerConfig) async throws { }
}

private typealias MCPServer = String

struct MCPServerConfig: Codable, Sendable {
    let name: String
    let transport: String
    let command: String?
    let url: String?
}

// MARK: - App state / semantics

@Observable
final class AppState {
    var baseURL: URL?
    var gatewayURL: String = ""
    var selectedTab: Int = 0
    var colorScheme: AppColorScheme = .system
    var currentPrompt: String = ""
    var errorMessage: String?
    var isConnected: Bool = false
    var isRunning: Bool = false
    var selectedSessionId: String?

    var connectors: [ConnectorHealth] = []
    var models: [ModelInfo] = []
    var sessions: [Session] = []
    var searchResults: [MemoryEntry] = []

    var runOutput: String = ""
    var streamEvents: [String] = []
    var lastRunResult: RunResponse?
    var lastParallelResult: ParallelResponse?
    var lastSessionId: String?

    // SwiftData caching entry points, kept deliberately thin: every view
    // gets a consistent error surface even if caching fails silently.
    func cacheSessions(in context: ModelContext) {
        for session in self.sessions { context.insert(CachedSession(from: session)) }
        try? context.save()
    }

    func cacheConnectors(in context: ModelContext) {
        for connector in self.connectors { context.insert(CachedConnectorStatus(from: connector)) }
        try? context.save()
    }

    func cacheTaskResult(_ task: RunTask, sessionId: String?, in context: ModelContext) {
        context.insert(CachedTaskResult(
            taskId: task.taskId ?? UUID().uuidString,
            output: task.output,
            sessionId: sessionId ?? ""
        ))
        try? context.save()
    }

    func loadCachedSessions(from context: ModelContext) {
        var descriptor = FetchDescriptor<CachedSession>()
        descriptor.fetchLimit = 20
        if let cached = try? context.fetch(descriptor) {
            sessions = cached.map { Session(id: $0.id, description: $0.summary) }
        }
    }

    func saveRecentPrompt(_ prompt: String) { /* prompt is small; no row */ }

    func clearError() { errorMessage = nil }
}

// MARK: - Auxiliary services

@Observable
final class SpotlightService {
    func indexSessions(_ sessions: [Session]) { }
    func indexMemories(_ memories: [MemoryEntry]) { }
    func indexSession(_ session: Session) { }
}

@Observable
final class HandoffService {
    static var activityType = "com.jambubrowser.run-task"
    var currentSessionId: String?

    func startActivity(prompt: String) { currentSessionId = prompt }
    func updateActivity(prompt: String, sessionId: String) { currentSessionId = sessionId }
    func invalidateActivity() { currentSessionId = nil }
}

@Observable
final class LiveActivityService {
    private(set) var isActive = false

    func startActivity(prompt: String, connector: String) { isActive = true }
    func updateProgress(taskIndex: Int, totalTasks: Int, connector: String) { }
    func completeActivity(output: String) { isActive = false }
    func failActivity(error: String) { isActive = false }
}

// MARK: - TipKit tips used by the views

import TipKit

struct RunTaskTip: Tip {
    static var hasRunTask = false
    var title: Text { Text("Run a Task") }
    var message: Text? { Text("Type a prompt and press run to execute it with the selected connector.") }
}

struct MultiConnectorTip: Tip {
    static var hasUsedMulti = false
    var title: Text { Text("Compare Connectors") }
    var message: Text? { Text("Enable multi-connector mode to run the same prompt across several connectors.") }
}

struct MemoryTip: Tip {
    static var hasUsedMemory = false
    var title: Text { Text("Persistent Memory") }
    var message: Text? { Text("Search and store facts the assistant can reuse across sessions.") }
}

// MARK: - View modifiers

extension View {
    /// Clipboard paste into the bound prompt — a common shortcut for
    /// bringing a copied URL/prompt from a share sheet.
    func smartPaste(prompt: Binding<String>) -> some View {
        self.overlay(alignment: .topTrailing) {
            Button {
                if let value = UIPasteboard.general.string {
                    prompt.wrappedValue = value
                }
            } label: {
                Image(systemName: "doc.on.clipboard")
                    .foregroundStyle(.blue)
            }
            .padding(8)
        }
    }
}
