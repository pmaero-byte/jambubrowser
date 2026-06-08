# User Guide

## Getting Started

### Starting the Application

1. Start the backend:
```bash
python3 -m uvicorn backend.engine:app --host 127.0.0.1 --port 8001
```

2. Start the frontend:
```bash
cd frontend/jambubrowser-ui && npm run dev
```

3. Open `http://localhost:5173`

### First Research Query

1. Click the input field (or press Cmd+K)
2. Type your question: "What is quantum computing?"
3. Press Enter or click the arrow button
4. The agent will search, scrape, and synthesize an answer
5. Click source chips to navigate the browser to that URL

## Interface Layout

```
┌─────────────────────────────────────────────────────────────┐
│ 🌳 Jambubrowser  [Research] [Intelligence] [Workspace]     │
│                   [Privacy] [Audit] [Vault]   [History] 🔥 │
├─────────────────────────────────────────────────────────────┤
│ ● Ready                                          WS        │
├──────────────────┬──────────────────────────────────────────┤
│ 0 nodes          │                                          │
│ 0 tokens         │     Browser Workspace                    │
│ 0.0GB RAM        │  ┌──────────────────────────────────────┐│
│                  │  │ [Tab1] [Tab2] [+]                    ││
│ Welcome Screen   │  │ ◀ ▶ ↻ │ 🔒 about:blank              ││
│                  │  │───────────────────────────────────── ││
│ [General][Acad]  │  │                                      ││
│ [Coding]         │  │     Enter a URL to start browsing     ││
│                  │  │                                      ││
│ ┌──────────────┐ │  │                                      ││
│ │ 🎤 │ 📎 │ ⌨ │ │  │                                      ││
│ └──────────────┘ │  └──────────────────────────────────────┘│
└──────────────────┴──────────────────────────────────────────┘
```

## Research Modes

### Brain-Only Mode (Default)
- Searches only your local knowledge vault
- Fast response (<1 second)
- Uses vector search + LLM synthesis
- No web access required

### Full Power Mode
Toggle the "FULL POWER" switch in the header to enable:
- Multi-engine web search
- Page scraping and content extraction
- Knowledge graph building
- LLM synthesis from web sources
- Longer response time (5-15 seconds)

## Privacy Controls

Click **Privacy** in the header to access privacy settings.

### Privacy Modes
- **Standard**: Basic sanitization of stored content
- **Enhanced** (default): Aggressive PII removal, tracking blocked
- **Maximum**: Zero external calls, full content sanitization
- **Local-Only**: No network access at all

### What Gets Protected
- Email addresses, phone numbers, SSNs, credit cards
- IP addresses, MAC addresses, passport numbers
- Google Analytics, Facebook Pixel, Mixpanel trackers
- UTM parameters, referral tracking URLs

## Credential Vault

Click **Vault** in the header to access the credential vault.

1. Enter your master password
2. Click "Unlock Vault"
3. Store credentials for websites via the `/login` endpoint
4. Credentials are encrypted with AES-256-GCM

### Security Features
- PBKDF2 key derivation (480,000 iterations)
- Machine-specific salt (tied to your hardware)
- Auto-lock after 5 minutes of inactivity
- 5 failed attempts → 5 minute lockout

## Audit Log

Click **Audit** in the header to view the audit log.

- **Live Status**: WebSocket-connected indicator
- **Category Filter**: Filter by research, browser, credential, network, privacy, system
- **Chain Verification**: Each entry is cryptographically chained
- **PII Protection**: All logged data is automatically redacted

## Browser Tabs

### Managing Tabs
- Click **+** to add a new tab
- Click **X** on a tab to close it
- Click a tab to switch to it
- Enter a URL in the address bar to navigate

### Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| Cmd+K | Focus input field |
| Cmd+P | Open Privacy tab |
| Cmd+L | Open Audit tab |
| Cmd+1 | Return to Research tab |
| Cmd+T | New browser tab |
| Escape | Close overlay, return to chat |

## Research Domains

The CommandBar has three domain buttons:
- **General**: Standard web search
- **Academic**: ArXiv papers + Google Scholar
- **Coding**: GitHub repositories + documentation

## Source Citations

When the agent returns research results:
- Source chips appear below the answer
- Click a chip to navigate the browser to that source
- The browser pane shows the actual webpage
- This is the "Deep Trust Bridge" - verifying AI claims with original sources

## WebSocket Real-Time Updates

The Agent Status Bar at the top shows:
- **State**: Ready, thinking, searching, reading, writing
- **Zone**: Current work area (pile, cabinet, desk)
- **Task Query**: Current research question
- **Metrics**: Tokens generated, tokens per second
- **WS Badge**: Green when connected, red when disconnected

## Troubleshooting

### "WebSocket connection failed"
- Ensure the backend is running on port 8001
- The frontend auto-reconnects every 3 seconds

### "Research timed out"
- The LLM (Ollama) may not be running
- Start Ollama: `ollama serve`
- The app shows a timeout message after 30 seconds

### "No results found"
- Try Full Power mode for web search
- Check if SearXNG is running on port 8888
- The app falls back to DuckDuckGo API automatically

### Vault won't unlock
- Ensure no other process is using the vault
- The vault auto-locks after 5 minutes of inactivity
- 5 failed attempts trigger a 5-minute lockout
