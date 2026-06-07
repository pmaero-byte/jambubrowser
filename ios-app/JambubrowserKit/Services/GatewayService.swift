// Lightweight, Sendable gateway API client for use in widgets and intents.
// This is a stripped-down version of GatewayClient that works in extension contexts.

import Foundation

public final class GatewayService: Sendable {
    private let baseURL: URL
    private let session: URLSession
    private let decoder = JSONDecoder()

    public init(baseURL: URL) {
        self.baseURL = baseURL
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 60
        self.session = URLSession(configuration: config)
    }

    // MARK: - Health

    public func health() async throws -> HealthResponse {
        try await get(path: "/health")
    }

    // MARK: - Connectors

    public func listConnectors() async throws -> [ConnectorHealth] {
        let response: ConnectorsResponse = try await get(path: "/v1/connectors")
        return response.connectors
    }

    // MARK: - Sessions

    public func listSessions(limit: Int = 10) async throws -> [Session] {
        let response: SessionsResponse = try await get(path: "/v1/sessions?limit=\(limit)")
        return response.sessions
    }

    // MARK: - Run

    public func run(prompt: String, tool: String = "") async throws -> RunResponse {
        let body = RunRequest(prompt: prompt, tool: tool)
        return try await post(path: "/v1/run", body: body)
    }

    // MARK: - Memory

    public func searchMemory(query: String, limit: Int = 10) async throws -> [MemoryEntry] {
        let body = MemorySearchRequest(query: query, limit: limit)
        let response: MemorySearchResponse = try await post(path: "/v1/memory/search", body: body)
        return response.results
    }

    public func addMemory(category: String = "general", key: String, value: String) async throws {
        let body = MemoryAddRequest(category: category, key: key, value: value)
        let _: MemoryAddResponse = try await post(path: "/v1/memory", body: body)
    }

    // MARK: - MCP

    public func listMCPServers() async throws -> [MCPServerStatus] {
        try await get(path: "/v1/mcp/servers")
    }

    public func listMCPTools() async throws -> [MCPToolInfo] {
        try await get(path: "/v1/mcp/tools")
    }

    // MARK: - Private

    private func get<T: Decodable>(path: String) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw URLError(.badURL)
        }
        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try decoder.decode(T.self, from: data)
    }

    private func post<T: Decodable, B: Encodable>(path: String, body: B) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try decoder.decode(T.self, from: data)
    }
}

// Internal response wrappers
struct ConnectorsResponse: Codable, Sendable {
    let connectors: [ConnectorHealth]
}

struct MemoryAddResponse: Codable, Sendable {
    let id: String
    let status: String
}
