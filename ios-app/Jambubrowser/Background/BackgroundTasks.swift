// Background task registration and handlers for connector health refresh and session sync.

@preconcurrency import BackgroundTasks
import UIKit
import UserNotifications
import JambubrowserKit

enum BackgroundTaskIdentifiers {
    static let connectorHealthRefresh = "com.jambubrowser.ios.connector-health-refresh"
    static let sessionSync = "com.jambubrowser.ios.session-sync"
}

enum BackgroundTaskManager {

    // MARK: - Registration

    static func register() {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: BackgroundTaskIdentifiers.connectorHealthRefresh,
            using: nil
        ) { task in
            handleConnectorHealthRefresh(task: task as! BGAppRefreshTask)
        }

        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: BackgroundTaskIdentifiers.sessionSync,
            using: nil
        ) { task in
            handleSessionSync(task: task as! BGProcessingTask)
        }
    }

    // MARK: - Scheduling

    static func scheduleConnectorHealthRefresh() {
        let request = BGAppRefreshTaskRequest(
            identifier: BackgroundTaskIdentifiers.connectorHealthRefresh
        )
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60) // 15 min
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            print("Failed to schedule connector health refresh: \(error)")
        }
    }

    static func scheduleSessionSync() {
        let request = BGProcessingTaskRequest(
            identifier: BackgroundTaskIdentifiers.sessionSync
        )
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        request.earliestBeginDate = Date(timeIntervalSinceNow: 30 * 60) // 30 min
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            print("Failed to schedule session sync: \(error)")
        }
    }

    // MARK: - Handlers

    private static func handleConnectorHealthRefresh(task: BGAppRefreshTask) {
        // Reschedule for next time
        scheduleConnectorHealthRefresh()

        let taskOperation = ConnectorHealthOperation()
        task.expirationHandler = {
            taskOperation.cancel()
        }

        nonisolated(unsafe) let bgTask = task
        taskOperation.completionBlock = {
            bgTask.setTaskCompleted(success: !taskOperation.isCancelled)
        }

        OperationQueue.main.addOperation(taskOperation)
    }

    private static func handleSessionSync(task: BGProcessingTask) {
        scheduleSessionSync()

        let taskOperation = SessionSyncOperation()
        task.expirationHandler = {
            taskOperation.cancel()
        }

        nonisolated(unsafe) let bgTask = task
        taskOperation.completionBlock = {
            bgTask.setTaskCompleted(success: !taskOperation.isCancelled)
        }

        OperationQueue.main.addOperation(taskOperation)
    }
}

// MARK: - Background Operations

/// Checks connector health and sends a notification if any connector went down.
class ConnectorHealthOperation: AsynchronousOperation, @unchecked Sendable {
    override func main() {
        guard !isCancelled else { return }

        let urlString = JambubrowserKit.sharedGatewayURL
        guard let url = URL(string: urlString) else {
            finish()
            return
        }

        let service = GatewayService(baseURL: url)

        Task {
            do {
                let health = try await service.health()
                let connectors = try await service.listConnectors()

                let downConnectors = connectors.filter { !$0.available }
                if !downConnectors.isEmpty {
                    for connector in downConnectors {
                        let content = UNMutableNotificationContent()
                        content.title = "Connector Down"
                        content.body = "\(connector.name) is unavailable"
                        content.sound = .default

                        let request = UNNotificationRequest(
                            identifier: "bg-connector-\(connector.name)",
                            content: content,
                            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
                        )
                        try? await UNUserNotificationCenter.current().add(request)
                    }
                }

                // Store health in App Group for widget
                let encoder = JSONEncoder()
                if let data = try? encoder.encode(health) {
                    UserDefaults(suiteName: JambubrowserKit.appGroupIdentifier)?
                        .set(data, forKey: "cachedHealth")
                }
                if let data = try? encoder.encode(connectors) {
                    UserDefaults(suiteName: JambubrowserKit.appGroupIdentifier)?
                        .set(data, forKey: "cachedConnectors")
                }

            } catch {
                // Gateway unreachable — that's okay in background
                print("Background health check failed: \(error)")
            }

            finish()
        }
    }
}

/// Syncs recent sessions to shared storage for widget access.
class SessionSyncOperation: AsynchronousOperation, @unchecked Sendable {
    override func main() {
        guard !isCancelled else { return }

        let urlString = JambubrowserKit.sharedGatewayURL
        guard let url = URL(string: urlString) else {
            finish()
            return
        }

        let service = GatewayService(baseURL: url)

        Task {
            do {
                let sessions = try await service.listSessions(limit: 20)
                let encoder = JSONEncoder()
                if let data = try? encoder.encode(sessions) {
                    UserDefaults(suiteName: JambubrowserKit.appGroupIdentifier)?
                        .set(data, forKey: "cachedSessions")
                }

                UserDefaults(suiteName: JambubrowserKit.appGroupIdentifier)?
                    .set(Date().timeIntervalSince1970, forKey: "lastSessionSync")
            } catch {
                print("Background session sync failed: \(error)")
            }

            finish()
        }
    }
}

// MARK: - Asynchronous Operation Base

class AsynchronousOperation: Operation, @unchecked Sendable {
    private var _isExecuting = false
    private var _isFinished = false

    override var isAsynchronous: Bool { true }
    override var isExecuting: Bool { _isExecuting }
    override var isFinished: Bool { _isFinished }

    override func start() {
        guard !isCancelled else {
            finish()
            return
        }
        willChangeValue(forKey: "isExecuting")
        _isExecuting = true
        didChangeValue(forKey: "isExecuting")
        main()
    }

    func finish() {
        willChangeValue(forKey: "isExecuting")
        willChangeValue(forKey: "isFinished")
        _isExecuting = false
        _isFinished = true
        didChangeValue(forKey: "isExecuting")
        didChangeValue(forKey: "isFinished")
    }
}
