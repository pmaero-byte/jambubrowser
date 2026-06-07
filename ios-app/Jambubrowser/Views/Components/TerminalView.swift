// Codex-style Terminal View — monospace console with streaming, auto-scroll, and theme.
// Reusable component for displaying real-time AI output with a terminal aesthetic.

import SwiftUI

struct TerminalView: View {
    @Binding var output: String
    @Binding var isRunning: Bool

    var placeholder: String = "Waiting for output..."
    var showPrompt: Bool = true
    var promptText: String = ""
    @Binding var promptInput: String
    var onSend: (() -> Void)? = nil

    @State private var scrollProxy: ScrollViewProxy?
    @State private var showClearConfirm = false

    // Codex-inspired color theme
    private let bgColor = Color(hex: "#1a1b26")
    private let surfaceColor = Color(hex: "#24283b")
    private let textColor = Color(hex: "#c0caf5")
    private let dimTextColor = Color(hex: "#565f89")
    private let accentColor = Color(hex: "#7aa2f7")
    private let greenColor = Color(hex: "#9ece6a")
    private let redColor = Color(hex: "#f7768e")
    private let yellowColor = Color(hex: "#e0af68")
    private let promptColor = Color(hex: "#bb9af7")
    private let cursorColor = Color(hex: "#c0caf5")

    var body: some View {
        VStack(spacing: 0) {
            // Header bar
            headerBar

            // Terminal output area
            terminalOutput

            // Input prompt (optional)
            if showPrompt {
                inputPrompt
            }
        }
        .background(bgColor)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(surfaceColor, lineWidth: 1)
        )
    }

    // MARK: - Header Bar

    private var headerBar: some View {
        HStack(spacing: 8) {
            // Traffic light dots
            Circle().fill(Color(hex: "#f7768e")).frame(width: 10, height: 10)
            Circle().fill(Color(hex: "#e0af68")).frame(width: 10, height: 10)
            Circle().fill(Color(hex: "#9ece6a")).frame(width: 10, height: 10)

            Spacer()

            Text("jambubrowser ~ terminal")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(dimTextColor)

            Spacer()

            // Action buttons
            if !output.isEmpty {
                Button {
                    showClearConfirm = true
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 11))
                        .foregroundStyle(dimTextColor)
                }
                .buttonStyle(.plain)
                .confirmationDialog("Clear terminal?", isPresented: $showClearConfirm) {
                    Button("Clear", role: .destructive) {
                        withAnimation(.easeOut(duration: 0.2)) {
                            output = ""
                        }
                    }
                }

                Button {
                    UIPasteboard.general.string = output
                } label: {
                    Image(systemName: "doc.on.doc")
                        .font(.system(size: 11))
                        .foregroundStyle(dimTextColor)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(surfaceColor)
    }

    // MARK: - Terminal Output

    private var terminalOutput: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if output.isEmpty && !isRunning {
                        Text(placeholder)
                            .font(.system(size: 13, design: .monospaced))
                            .foregroundStyle(dimTextColor)
                            .padding(16)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        // Render output with syntax highlighting
                        renderOutput(output)
                            .id("output-bottom")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .onAppear {
                scrollProxy = proxy
                scrollToBottom(proxy: proxy)
            }
            .onChange(of: output) { _, _ in
                scrollToBottom(proxy: proxy)
            }
            .onChange(of: isRunning) { _, running in
                if running {
                    scrollToBottom(proxy: proxy)
                }
            }
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: - Input Prompt

    private var inputPrompt: some View {
        HStack(spacing: 6) {
            // Prompt symbol
            Text("❯")
                .font(.system(size: 13, design: .monospaced))
                .foregroundStyle(greenColor)

            TextField("", text: $promptInput)
                .font(.system(size: 13, design: .monospaced))
                .foregroundStyle(textColor)
                .tint(accentColor)
                .textFieldStyle(.plain)
                .onSubmit {
                    onSend?()
                }

            if isRunning {
                ProgressView()
                    .scaleEffect(0.6)
                    .tint(accentColor)
            } else if !promptInput.isEmpty {
                Button {
                    onSend?()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 16))
                        .foregroundStyle(accentColor)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(surfaceColor.opacity(0.5))
        .overlay(
            Rectangle()
                .frame(height: 1)
                .foregroundStyle(surfaceColor),
            alignment: .top
        )
    }

    // MARK: - Output Rendering

    @ViewBuilder
    private func renderOutput(_ text: String) -> some View {
        let lines = text.components(separatedBy: "\n")
        ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
            HStack(alignment: .top, spacing: 8) {
                // Line number
                Text(String(format: "%4d", index + 1))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(dimTextColor.opacity(0.5))
                    .frame(width: 36, alignment: .trailing)

                // Line content with basic syntax coloring
                Text(highlightSyntax(line))
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(textColor)
                    .textSelection(.enabled)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
        }

        // Blinking cursor when running
        if isRunning {
            HStack(spacing: 8) {
                Text(String(format: "%4d", lines.count + 1))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(dimTextColor.opacity(0.5))
                    .frame(width: 36, alignment: .trailing)

                Rectangle()
                    .fill(cursorColor)
                    .frame(width: 8, height: 16)
                    .opacity(blinkingOpacity)
                    .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true), value: blinkingOpacity)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
        }
    }

    @State private var blinkingOpacity: Double = 1.0

    // MARK: - Helpers

    private func scrollToBottom(proxy: ScrollViewProxy) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            withAnimation(.easeOut(duration: 0.1)) {
                proxy.scrollTo("output-bottom", anchor: .bottom)
            }
        }
    }

    private func highlightSyntax(_ line: String) -> AttributedString {
        var result = AttributedString(line)
        result.font = .system(size: 13, design: .monospaced)
        result.foregroundColor = UIColor(Color(hex: "#c0caf5"))

        // Highlight common patterns
        let patterns: [(String, Color)] = [
            // Errors (red)
            ("error", Color(hex: "#f7768e")),
            ("Error", Color(hex: "#f7768e")),
            ("ERROR", Color(hex: "#f7768e")),
            ("failed", Color(hex: "#f7768e")),
            ("FAILED", Color(hex: "#f7768e")),
            ("exception", Color(hex: "#f7768e")),
            ("traceback", Color(hex: "#f7768e")),
            // Success (green)
            ("success", Color(hex: "#9ece6a")),
            ("SUCCESS", Color(hex: "#9ece6a")),
            ("completed", Color(hex: "#9ece6a")),
            ("done", Color(hex: "#9ece6a")),
            // Warnings (yellow)
            ("warning", Color(hex: "#e0af68")),
            ("WARNING", Color(hex: "#e0af68")),
            ("deprecated", Color(hex: "#e0af68")),
            // Keywords (purple)
            ("import ", Color(hex: "#bb9af7")),
            ("func ", Color(hex: "#bb9af7")),
            ("class ", Color(hex: "#bb9af7")),
            ("struct ", Color(hex: "#bb9af7")),
            ("def ", Color(hex: "#bb9af7")),
            // Strings (green)
            ("\"", Color(hex: "#9ece6a")),
            ("'", Color(hex: "#9ece6a")),
        ]

        for (keyword, color) in patterns {
            if let range = line.range(of: keyword, options: .caseInsensitive) {
                let nsRange = NSRange(range, in: line)
                if let attrRange = Range(nsRange, in: result) {
                    result[attrRange].foregroundColor = UIColor(color)
                }
            }
        }

        return result
    }
}

// MARK: - Color Extension

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r, g, b: UInt64
        switch hex.count {
        case 6:
            (r, g, b) = ((int >> 16) & 0xFF, (int >> 8) & 0xFF, int & 0xFF)
        default:
            (r, g, b) = (0, 0, 0)
        }
        self.init(
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue: Double(b) / 255,
            opacity: 1.0
        )
    }
}

#Preview {
    @Previewable @State var output = """
╭─ Jambubrowser Agent v1.1.0 ─────────────────────────────────────────────╮
│  Connected connectors: hermes ✅ | claude ✅ | opencode ✅          │
│  MCP servers: filesystem (14 tools)                                 │
╰─────────────────────────────────────────────────────────────────────╯

❯ Running task: Build periodic table webapp...
  → Analyzing prompt...
  → Routing to connector: hermes
  → Executing...

✅ Task completed successfully (1.6s)
   Output: 5000 chars | Session: 528257db9620
   File: /tmp/periodic_table_final.html

──────────────────────────────────────────────────────────────────────
"""
    @Previewable @State var isRunning = false
    @Previewable @State var input = ""

    TerminalView(
        output: $output,
        isRunning: $isRunning,
        promptInput: $input
    )
    .frame(height: 400)
}
