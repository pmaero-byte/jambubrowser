// JambubrowserKit — Shared framework for Jambubrowser iOS app, widgets, and intents.
// Contains public models, API client, Keychain service, and ActivityKit attributes.

import Foundation

public enum JambubrowserKit {
    public static let version = "1.0.0"
    public static let appGroupIdentifier = "group.com.jambubrowser.ios"
    public static let keychainService = "com.jambubrowser.ios"
    public static let keychainGatewayService = "jambubrowser-gateway"

    /// Default gateway URL used when no custom URL is configured.
    public static let defaultGatewayURL = "http://localhost:8001"

    /// Read the gateway URL from App Group shared UserDefaults.
    public static var sharedGatewayURL: String {
        UserDefaults(suiteName: appGroupIdentifier)?
            .string(forKey: "gatewayURL")
            ?? defaultGatewayURL
    }

    /// Write the gateway URL to App Group shared UserDefaults.
    public static func setSharedGatewayURL(_ url: String) {
        UserDefaults(suiteName: appGroupIdentifier)?.set(url, forKey: "gatewayURL")
    }
}
