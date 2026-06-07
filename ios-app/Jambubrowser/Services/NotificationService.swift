// Push notification service for task completion and connector alerts.

import UserNotifications
import Observation

@Observable
final class NotificationService: @unchecked Sendable {
    private(set) var isAuthorized = false

    func requestPermission() async {
        do {
            isAuthorized = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .badge, .sound])
        } catch {
            isAuthorized = false
        }
    }

    func checkAuthorization() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        isAuthorized = settings.authorizationStatus == .authorized
    }

    func scheduleTaskCompletionNotification(taskId: String, output: String) async {
        let content = UNMutableNotificationContent()
        content.title = "Task Complete"
        content.body = String(output.prefix(100))
        content.sound = .default
        content.categoryIdentifier = "TASK_COMPLETE"

        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        let request = UNNotificationRequest(
            identifier: "task-\(taskId)",
            content: content,
            trigger: trigger
        )
        try? await UNUserNotificationCenter.current().add(request)
    }

    func scheduleConnectorDownAlert(connectorName: String) async {
        let content = UNMutableNotificationContent()
        content.title = "Connector Down"
        content.body = "\(connectorName) is no longer available"
        content.sound = .default
        content.categoryIdentifier = "CONNECTOR_ALERT"

        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        let request = UNNotificationRequest(
            identifier: "connector-\(connectorName)-down",
            content: content,
            trigger: trigger
        )
        try? await UNUserNotificationCenter.current().add(request)
    }

    func scheduleConnectorUpAlert(connectorName: String) async {
        let content = UNMutableNotificationContent()
        content.title = "Connector Restored"
        content.body = "\(connectorName) is back online"
        content.sound = .default

        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        let request = UNNotificationRequest(
            identifier: "connector-\(connectorName)-up",
            content: content,
            trigger: trigger
        )
        try? await UNUserNotificationCenter.current().add(request)
    }

    func clearAllDelivered() {
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
    }
}
