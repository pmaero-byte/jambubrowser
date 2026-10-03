// Shared response models for the Jambubrowser gateway API.
// Both the app target and the widget/intents kit decode the same payloads.

import Foundation

// MARK: - /health

public struct HealthResponse: Codable, Sendable {
    public init(status: String? = nil, message: String? = nil, version: String? = nil, connectors: [String] = []) {
        self.status = status; self.message = message; self.version = version; self.connectors = connectors
    }

    public let status: String?
    public let message: String?
    public let version: String?
    public let connectors: [String]

    enum CodingKeys: String, CodingKey {
        case status, message, version, connectors
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        message = try c.decodeIfPresent(String.self, forKey: .message)
        version = try c.decodeIfPresent(String.self, forKey: .version)
        connectors = (try? c.decode([String].self, forKey: .connectors)) ?? []
    }
}

// MARK: - /v1/connectors

public struct ConnectorHealth: Codable, Sendable, Identifiable {
    public init(name: String, available: Bool, capabilities: [String]) {
        self.name = name; self.available = available; self.capabilities = capabilities
    }

    public let name: String
    public let available: Bool
    public let capabilities: [String]

    public var id: String { name }
}

// MARK: - /v1/sessions

public struct Session: Codable, Sendable, Identifiable {
    public init(id: String, description: String = "", status: String = "unknown", entryCount: Int? = nil, name: String? = nil, createdAt: String? = nil, lastActive: String? = nil) {
        self.id = id; self.name = name ?? description; self.status = status; self.entryCount = entryCount; self.createdAt = createdAt; self.lastActive = lastActive
    }

    public let id: String
    public let name: String?
    public let createdAt: String?
    public let lastActive: String?
    public let status: String
    public let entryCount: Int?

    public var description: String { name ?? "" }

    enum CodingKeys: String, CodingKey {
        case id, name, status
        case entryCount = "task_count"
        case createdAt = "created_at"
        case lastActive = "last_active"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decodeIfPresent(String.self, forKey: .name)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "unknown"
        entryCount = try c.decodeIfPresent(Int.self, forKey: .entryCount)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
        lastActive = try c.decodeIfPresent(String.self, forKey: .lastActive)
    }
}

public struct SessionsResponse: Codable, Sendable {
    public init(sessions: [Session]) { self.sessions = sessions }

    public let sessions: [Session]
}

public struct SessionDetail: Codable, Sendable {
    public init(id: String, name: String? = nil, status: String? = nil, steps: [String]? = nil, summary: String? = nil, entries: [SessionContextEntry]? = nil) {
        self.id = id; self.name = name; self.status = status; self.steps = steps; self.summary = summary; self.entries = entries
    }

    public let id: String
    public let name: String?
    public let status: String?
    public let steps: [String]?
    public let summary: String?
    public let entries: [SessionContextEntry]?
}

public struct SessionContextEntry: Codable, Sendable, Identifiable {
    public init(key: String, entryType: String? = nil, value: String = "") {
        self.key = key; self.entryType = entryType; self.value = value
    }

    public let key: String
    public let entryType: String?
    public let value: String

    public var id: String { key }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decodeIfPresent(String.self, forKey: .key) ?? ""
        entryType = try c.decodeIfPresent(String.self, forKey: .entryType)
        value = try c.decodeIfPresent(String.self, forKey: .value) ?? ""
    }

    enum CodingKeys: String, CodingKey {
        case key, entryType, value
    }
}

// MARK: - /v1/models

public struct ModelInfo: Codable, Sendable, Identifiable {
    public init(id: String, ownedBy: String = "") { self.id = id; self.ownedBy = ownedBy }

    public let id: String
    public let ownedBy: String
}

// MARK: - /v1/memory

public struct MemoryEntry: Codable, Sendable, Identifiable {
    public init(id: String, category: String, key: String, value: String, tags: [String]? = nil) {
        self.id = id; self.category = category; self.key = key; self.value = value; self.tags = tags
    }

    public let id: String
    public let category: String
    public let key: String
    public let value: String
    public let tags: [String]?
}

public struct MemorySearchRequest: Codable, Sendable {
    public init(query: String, limit: Int) { self.query = query; self.limit = limit }

    public let query: String
    public let limit: Int
}

public struct MemorySearchResponse: Codable, Sendable {
    public init(results: [MemoryEntry]) { self.results = results }

    public let results: [MemoryEntry]
}

public struct MemoryAddRequest: Codable, Sendable {
    public init(category: String, key: String, value: String) { self.category = category; self.key = key; self.value = value }

    public let category: String
    public let key: String
    public let value: String
}

// MARK: - /mcp

public struct MCPServerStatus: Codable, Sendable, Identifiable {
    public init(name: String, status: String? = nil, transport: String? = nil, connected: Bool = false, toolCount: Int = 0) {
        self.name = name; self.status = status; self.transport = transport
        self.connected = connected; self.toolCount = toolCount
    }

    public let name: String
    public let status: String?
    public let transport: String?
    public let connected: Bool
    public let toolCount: Int

    public var id: String { name }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        status = try c.decodeIfPresent(String.self, forKey: .status)
        transport = try c.decodeIfPresent(String.self, forKey: .transport)
        connected = try c.decodeIfPresent(Bool.self, forKey: .connected) ?? false
        toolCount = try c.decodeIfPresent(Int.self, forKey: .toolCount) ?? 0
    }
}

public struct MCPToolInfo: Codable, Sendable {
    public init(name: String, description: String? = nil) { self.name = name; self.description = description }

    public let name: String
    public let description: String?
}

// MARK: - /v1/run

public struct RunRequest: Codable, Sendable {
    public init(prompt: String, tool: String) { self.prompt = prompt; self.tool = tool }

    public let prompt: String
    public let tool: String
}

public struct RunStreamRequest: Codable, Sendable {
    public init(prompt: String, tool: String) { self.prompt = prompt; self.tool = tool }

    public let prompt: String
    public let tool: String
}

public struct RunTask: Codable, Sendable {
    public init(taskId: String? = nil, status: String = "", output: String = "", error: String? = nil, durationMs: Int = 0) {
        self.taskId = taskId; self.status = status; self.output = output; self.error = error; self.durationMs = durationMs
    }

    public let taskId: String?
    public let status: String
    public let output: String
    public let error: String?
    public let durationMs: Int

    public var id: String? { taskId }

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case status, output, error
        case durationMs = "duration_ms"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        taskId = try c.decodeIfPresent(String.self, forKey: .taskId)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        output = try c.decodeIfPresent(String.self, forKey: .output) ?? ""
        error = try? c.decodeIfPresent(String.self, forKey: .error)
        durationMs = try c.decodeIfPresent(Int.self, forKey: .durationMs) ?? 0
    }
}

public struct RunResponse: Codable, Sendable {
    public init(sessionId: String = "", tasks: [RunTask] = []) { self.sessionId = sessionId; self.tasks = tasks }

    public let sessionId: String
    public let tasks: [RunTask]

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case tasks
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sessionId = try c.decodeIfPresent(String.self, forKey: .sessionId) ?? ""
        tasks = try c.decodeIfPresent([RunTask].self, forKey: .tasks) ?? []
    }
}

// MARK: - Parallel runs (future /v1/run/parallel — client synthesizes today)

public struct ParallelResponse: Codable, Sendable {
    public init(winner: String? = nil, similarity: Double = 1.0, connectorResults: [String: RunTask] = [:], diff: String = "") {
        self.winner = winner; self.similarity = similarity; self.connectorResults = connectorResults; self.diff = diff
    }

    public let winner: String?
    public let similarity: Double
    public let connectorResults: [String: RunTask]
    public let diff: String
}

public struct ModelsResponse: Codable, Sendable {
    public init(data: [ModelInfo]) { self.data = data }

    public let data: [ModelInfo]
}

public struct ConnectorResponse: Codable, Sendable {
    public let connectors: [ConnectorHealth]
}
