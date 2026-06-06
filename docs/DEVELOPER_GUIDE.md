# Developer Guide: Extending the Agent

JambuAI Browser is a self-improving platform. Developers can extend the agent's capabilities by adding new skills, search engines, or AI providers.

## 🧰 The Autonomous Toolbox
The agent can write its own Python tools. You can also manually add tools to the `tools/` directory:
- Create a file `tools/my_tool.py`.
- Define a `def run(**kwargs):` function.
- The agent will automatically discover this tool and can call it using `[USE_TOOL: "my_tool", {"arg": "val"}]`.

## 🌐 Adding Search Engines
The system is powered by SearXNG. To add a new search backend:
1. Update `searxng/searx/settings.yml` to enable the desired engine.
2. Update the `engines` string in the `research` function within `engine.py`.

## ☁️ Custom AI Providers
JambuAI Browser supports any OpenAI-compatible API:
1. Navigate to the **Stealth** tab.
2. Enter a custom **Base URL** (e.g., `https://api.deepseek.com/v1`).
3. Provide your **API Key** and **Model ID**.
4. The Rust orchestrator will dynamically route all reasoning tasks through this endpoint.

## 🍎 macOS Build Process
To compile the project into a native macOS application bundle:
1. Ensure Rust and Node.js are installed.
2. Navigate to the `browser-app` directory.
3. Run the build command:
   ```bash
   npm run tauri build
   ```
4. The standalone `.app` bundle will be generated in `src-tauri/target/release/bundle/macos/`.

## 🛠️ Security & Sandboxing
- **Exec Isolation**: Python tools are executed within the same process but use a redirected `stdout` to capture output securely.
- **Path Sanitization**: The file-access tools are restricted to the project root to prevent unauthorized filesystem traversal.
