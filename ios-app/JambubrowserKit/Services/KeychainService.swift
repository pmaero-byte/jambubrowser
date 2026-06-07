// Keychain wrapper for secure credential storage with App Group sharing.
// Both the main app and widget extension can read/write via the shared access group.

import Foundation
import Security

public enum KeychainService {

    // MARK: - Errors

    public enum KeychainError: LocalizedError {
        case duplicateItem
        case itemNotFound
        case unexpectedStatus(OSStatus)
        case invalidData

        public var errorDescription: String? {
            switch self {
            case .duplicateItem: return "Keychain item already exists"
            case .itemNotFound: return "Keychain item not found"
            case .unexpectedStatus(let status): return "Keychain error: \(status)"
            case .invalidData: return "Invalid keychain data"
            }
        }
    }

    // MARK: - Generic Save/Retrieve/Delete

    public static func save(_ value: String, for key: String) throws {
        guard let data = value.data(using: .utf8) else {
            throw KeychainError.invalidData
        }

        // Try update first, then add
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: JambubrowserKit.keychainService,
            kSecAttrAccount as String: key,
            kSecAttrAccessGroup as String: JambubrowserKit.appGroupIdentifier
        ]

        let updateAttributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock
        ]

        var status = SecItemUpdate(query as CFDictionary, updateAttributes as CFDictionary)

        if status == errSecItemNotFound {
            var addQuery = query
            addQuery[kSecValueData as String] = data
            addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            status = SecItemAdd(addQuery as CFDictionary, nil)
        }

        guard status == errSecSuccess else {
            if status == errSecDuplicateItem {
                throw KeychainError.duplicateItem
            }
            throw KeychainError.unexpectedStatus(status)
        }
    }

    public static func retrieve(key: String) throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: JambubrowserKit.keychainService,
            kSecAttrAccount as String: key,
            kSecAttrAccessGroup as String: JambubrowserKit.appGroupIdentifier,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        if status == errSecItemNotFound {
            return nil
        }

        guard status == errSecSuccess, let data = result as? Data else {
            throw KeychainError.unexpectedStatus(status)
        }

        return String(data: data, encoding: .utf8)
    }

    public static func delete(key: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: JambubrowserKit.keychainService,
            kSecAttrAccount as String: key,
            kSecAttrAccessGroup as String: JambubrowserKit.appGroupIdentifier
        ]

        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.unexpectedStatus(status)
        }
    }

    // MARK: - API Key Convenience

    /// Save an API key for a specific service (e.g., "openai", "anthropic").
    public static func saveAPIKey(_ key: String, identifier: String) throws {
        try save(key, for: "apikey-\(identifier)")
    }

    /// Retrieve an API key for a specific service.
    public static func retrieveAPIKey(identifier: String) throws -> String? {
        try retrieve(key: "apikey-\(identifier)")
    }

    /// Delete an API key for a specific service.
    public static func deleteAPIKey(identifier: String) throws {
        try delete(key: "apikey-\(identifier)")
    }

    /// Check if any API keys are stored.
    public static func hasStoredAPIKeys() -> Bool {
        let keys = ["openai", "anthropic", "jambubrowser"]
        return keys.contains { (try? retrieveAPIKey(identifier: $0)) != nil }
    }
}
