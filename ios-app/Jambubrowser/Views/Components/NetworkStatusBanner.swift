// Offline indicator banner that appears when network connectivity is lost.

import SwiftUI

struct NetworkStatusBanner: View {
    @Environment(NetworkMonitor.self) private var networkMonitor

    var body: some View {
        if !networkMonitor.isConnected {
            HStack(spacing: 8) {
                Image(systemName: "wifi.slash")
                    .font(.caption)
                Text("You're offline. Tasks will be queued.")
                    .font(.caption)
                Spacer()
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(.orange)
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }
}
