# Technical Architecture: JambuAI Browser

The JambuAI Browser is built on a distributed, multi-process architecture designed for high-performance autonomous research while maintaining absolute local privacy.

## 1. System Overview

The application consists of three primary layers:
1.  **UI Layer (React/Tauri)**: Handles the 3D visualizations, research history, and user workspace.
2.  **Orchestration Layer (Rust)**: Manages concurrent agentic loops, LLM tool-calling, and state aggregation.
3.  **Intelligence & Action Layer (Python/FastAPI)**: Handles web scraping, vector indexing (RAG), and autonomous browser interactions.

## 2. Intent Orchestration

The Rust layer performs an 'Intent Analysis' pass before spawning the research swarm:
- **Conversational Path**: Greetings and simple questions (< 15 chars) are routed to a 'Fast-Chat' LLM thread. This returns a response in < 0.5s.
- **Research Path**: Complex queries trigger the full 7-stage agentic loop.

## 3. The Agentic Loop

The system uses a multi-stage reasoning process for every query:
1.  **Decomposition**: LLM breaks the query into 3 parallel sub-missions.
2.  **Hypothesis**: Agent generates a theoretical expectation of the findings.
3.  **Swarm Research**: 3 sub-agents execute parallel web scrapes and API calls (ArXiv/GitHub).
4.  **Hybrid RAG**: Findings are indexed into `sqlite-vec` and re-ranked using a BM25-like lexical scoring.
5.  **Multimodal Synthesis**: Primary screenshots are passed to the vision model for spatial layout reasoning.
6.  **Internal Debate**: An "Optimist" and "Skeptic" persona critique the findings to eliminate bias.
7.  **Final Synthesis**: A "Judge" pass consolidates the debate into the final answer.

## 3. Storage & Privacy

- **Local Brain**: Uses SQLite with the `vec0` extension for semantic search. All research data stays on-disk locally.
- **Credential Vault**: Encrypted local storage for domain-specific logins, allowing autonomous traversal of paywalled or gated portals.
- **Tor Routing**: Integrated SOCKS5 tunnel support for all outbound research traffic.
- **Stateless Incognito**: Spawns isolated browser instances that are destroyed after every round, leaving no forensic traces.

## 4. Collective Hive Intelligence (P2P)

- **Discovery**: Uses UDP broadcasts to find other Gemma nodes on the same local network.
- **Skill Sharing**: Agents can "Pull" Python tools built by other nodes, creating a decentralized library of agentic capabilities.
- **Vector Sync**: Peer-to-Peer exchange of anonymized research snippets to augment the local knowledge base.

## 5. Performance Optimization

- **Dynamic Scaling**: Monitors system RAM via `psutil` to adjust the number of concurrent threads.
- **Session Persistence**: Maintains a global `Crawl4AI` instance to eliminate browser cold-start latency.
- **Embedding Cache**: Hashes and caches vector embeddings to skip redundant LLM calls for recurring text fragments.
