# MCP Tools Reference

Auto-generated from `backend/mcp_server.py` by `tools/mcp/generate_docs.py`. Do not edit by hand — re-run the generator after adding or renaming a tool.

**Total tools:** 37

## Table of contents

- [`agent_eval_certify`](#agent_eval_certify)
- [`agent_eval_verify`](#agent_eval_verify)
- [`analyze_screenshot`](#analyze_screenshot)
- [`browser_session_act`](#browser_session_act)
- [`browser_session_close`](#browser_session_close)
- [`browser_session_open`](#browser_session_open)
- [`browser_session_receipts`](#browser_session_receipts)
- [`browser_session_run`](#browser_session_run)
- [`browser_session_snapshot`](#browser_session_snapshot)
- [`browser_test_flow`](#browser_test_flow)
- [`check_engine_health`](#check_engine_health)
- [`click_element`](#click_element)
- [`dcm_earnings`](#dcm_earnings)
- [`dcm_infer`](#dcm_infer)
- [`dcm_models`](#dcm_models)
- [`dcm_settlement_log`](#dcm_settlement_log)
- [`dcm_status`](#dcm_status)
- [`deep_research`](#deep_research)
- [`execute_tool`](#execute_tool)
- [`get_brain_stats`](#get_brain_stats)
- [`get_system_stats`](#get_system_stats)
- [`list_custom_tools`](#list_custom_tools)
- [`meshpay_anchor`](#meshpay_anchor)
- [`meshpay_audit`](#meshpay_audit)
- [`navigate_browser`](#navigate_browser)
- [`query_brain`](#query_brain)
- [`recall_memory`](#recall_memory)
- [`research_web`](#research_web)
- [`scrape_page`](#scrape_page)
- [`search_academic`](#search_academic)
- [`search_code`](#search_code)
- [`search_multi_engine`](#search_multi_engine)
- [`start_mission`](#start_mission)
- [`stop_mission`](#stop_mission)
- [`take_screenshot`](#take_screenshot)
- [`type_text`](#type_text)
- [`visual_grounding`](#visual_grounding)

## Tools

### `agent_eval_certify`

**Signature**

```python
agent_eval_certify(suite: str, provider: str = '', pass_threshold: float = 0.8)
```

**Description**

Run an eval suite under a frozen spec and issue a signed certificate.
The spec (task list + scoring + provider) is hashed before the run, so
dropping failed tasks afterwards is detectable; verdicts are PASS, FAIL,
INCONCLUSIVE (harness errors) or INVALID (coverage mismatch).

Args:
    suite: Suite name, e.g. "smoke" (see the GET /eval/suites list)
    provider: LLM provider under test (empty = engine default)
    pass_threshold: Pass rate required for PASS (0-1)

### `agent_eval_verify`

**Signature**

```python
agent_eval_verify(certificate_id: int)
```

**Description**

Verify a certificate's signature and recompute its verdict from the
embedded results (a signed certificate whose verdict doesn't follow
from its data is rejected).

Args:
    certificate_id: Bundle id from agent_eval_certify

### `analyze_screenshot`

**Signature**

```python
analyze_screenshot(image_data: str)
```

**Description**

Analyze a screenshot or image using the vision model.
Describe what the agent sees in the image.

Args:
    image_data: Base64-encoded image data

### `browser_session_act`

**Signature**

```python
browser_session_act(session_id: str, action: str, ref: str, text: str = '', approve: bool = False)
```

**Description**

Deterministic dispatch by catalog ref. Refusals are explicit: blocked
domains, unknown refs, and actions needing approval (risky elements such
as delete/pay/send always require approve=true).

Args:
    session_id: Session id
    action: "click" or "type"
    ref: Element ref from the last snapshot (e.g. @e3)
    text: Text to type (for action="type")
    approve: Explicit approval for input/risky actions

### `browser_session_close`

**Signature**

```python
browser_session_close(session_id: str)
```

**Description**

Close a browser session (ephemeral context is torn down).

Args:
    session_id: Session id

### `browser_session_open`

**Signature**

```python
browser_session_open(allow_domains: str, require_approval: bool = True)
```

**Description**

Open an isolated browser session for agent-driven work, restricted to a
domain allowlist. Navigations outside it are refused; irreversible-looking
actions need ``approve=true``; PII is scrubbed from snapshots.

Args:
    allow_domains: Comma-separated domains the session may visit (subdomains allowed)
    require_approval: Require approve=true for input actions inside the allowlist

### `browser_session_receipts`

**Signature**

```python
browser_session_receipts(session_id: str)
```

**Description**

Hash-chained receipt log for a session (every action, blocked or not),
with the Merkle root that can be signed into an evidence bundle.

Args:
    session_id: Session id

### `browser_session_run`

**Signature**

```python
browser_session_run(session_id: str, steps: str, approve: bool = False, stop_on_failure: bool = False, network: str = '', resolve_sources: bool = False)
```

**Description**

Run a declarative step flow against an existing browser session and return
a compact pass/fail report (one call instead of many snapshot/act calls).

Args:
    session_id: Session from browser_session_open
    steps: JSON array of step objects (see browser_test_flow for actions)
    approve: Approve risky/input actions for every step
    stop_on_failure: Stop at the first failed step
    network: Optional JSON request-interception policy (see browser_test_flow)
    resolve_sources: Map console errors through source maps

### `browser_session_snapshot`

**Signature**

```python
browser_session_snapshot(session_id: str)
```

**Description**

Perception step: accessibility-style snapshot with a typed element
catalog (refs @e1…). Act on refs, never on selector guesses.

Args:
    session_id: Session from browser_session_open

### `browser_test_flow`

**Signature**

```python
browser_test_flow(url: str, steps: str = '[]', allow_domains: str = '', local: bool = False, approve: bool = False, stop_on_failure: bool = False, network: str = '', trace: bool = False, har: bool = False, video: bool = False, resolve_sources: bool = False, storage_state: str = '')
```

**Description**

Test a web app end-to-end in ONE call: opens a browser session, runs a
declarative step list (navigate / click / type / press / wait / assert_*),
and returns a compact pass/fail report with console errors and failed
requests already attached. Prefer this over open→snapshot→act loops to
save tool calls.

Set local=true for localhost / private dev servers (e.g. http://localhost:3000).

Args:
    url: Starting URL (also the default allowlist host), e.g. http://localhost:3000
    steps: JSON array of step objects. Actions: navigate{url}, click{target|ref},
        type{target|ref,value}, press{key,target?}, hover, select{value},
        check/uncheck, reload, back, forward, wait{selector|text|url_contains},
        screenshot, assert_visible/assert_not_visible/assert_text{value}/
        assert_text_equals/assert_value/assert_url/assert_title/assert_count/
        assert_checked/assert_unchecked/assert_enabled/assert_disabled/
        assert_console_clean/assert_no_failed_requests/assert_no_a11y_violations/
        assert_lcp/assert_fcp/assert_load/assert_dom_nodes/assert_transfer_kb/
        assert_made_request{value}/assert_no_request{value}.
        'target' matches element text by exact name, unique substring, or "role name".
    allow_domains: Optional comma-separated allowlist (defaults to url host)
    local: Allow loopback/private hosts (local dev testing)
    approve: Approve risky/input actions for every step (delete/pay/send…)
    stop_on_failure: Stop at the first failed step
    network: Optional JSON request-interception policy:
        {"mocks":[{"url":"**/api/user","json":{...},"status":200}],
         "fail":["**/analytics/**"],
         "delay":[{"url":"**/slow","ms":3000}],"offline":false}
    trace: Capture a Playwright trace artifact (screenshots+snapshots)
    har: Capture a HAR network archive
    video: Capture a video recording
    resolve_sources: Map console errors through source maps to original files
    storage_state: Optional JSON storage state ({cookies,origins}) to seed auth

### `check_engine_health`

**Description**

Check if the Jambubrowser engine is running and healthy.
Returns engine status and system metrics.

### `click_element`

**Signature**

```python
click_element(url: str, selector: str, session_id: str = None)
```

**Description**

Click an element on a webpage using a CSS selector.
Returns the page state after clicking.

Args:
    url: The page URL
    selector: CSS selector for the element to click
    session_id: Optional browser session ID

### `dcm_earnings`

**Signature**

```python
dcm_earnings(did: str)
```

**Description**

Show accrued DCT earnings for a provider DID on the local DCM node.

Args:
    did: Provider DID (e.g. 'did:dcm:...' or the node's registered DID)

### `dcm_infer`

**Signature**

```python
dcm_infer(prompt: str, model: str = '', max_tokens: int = 64)
```

**Description**

Run a prompt on the local DecentraCode Mesh (distributed inference).

Args:
    prompt: The prompt to run
    model: Optional DCM model id (e.g. 'qwen1.5-moe-a2.7b'); empty uses the node default
    max_tokens: Maximum tokens to generate (1-4096)

### `dcm_models`

**Description**

List the local DecentraCode Mesh model catalog with availability and
runtime per model.

### `dcm_settlement_log`

**Signature**

```python
dcm_settlement_log(limit: int = 20)
```

**Description**

Fetch the DCM node's hash-chained settlement receipts (billing audit
trail: usage, inference-charge, simulation-charge, settlement).

Args:
    limit: Number of receipts to fetch (1-500)

### `dcm_status`

**Description**

Check the local DecentraCode Mesh (DCM) node: reachability, inference
runtimes, available models, and connected peers.

### `deep_research`

**Signature**

```python
deep_research(query: str, rounds: int = 3)
```

**Description**

Perform multi-round recursive research that builds on previous findings.
More thorough than single-pass research.

Args:
    query: The research topic
    rounds: Number of recursive research rounds (default: 3, max: 5)

### `execute_tool`

**Signature**

```python
execute_tool(name: str, kwargs: str = '{}')
```

**Description**

Execute a previously saved custom tool/script.

Args:
    name: Name of the tool to execute
    kwargs: JSON string of keyword arguments to pass to the tool

### `get_brain_stats`

**Description**

Get statistics about the local knowledge vault:
document count, active missions, stored tools, credentials.

### `get_system_stats`

**Description**

Get detailed system statistics: CPU usage, RAM, document count,
active missions, and database size.

### `list_custom_tools`

**Description**

List all saved agent-generated tools and skills stored
in the toolbox.

### `meshpay_anchor`

**Signature**

```python
meshpay_anchor(epoch_index: int = -1, epoch_size: int = 50)
```

**Description**

Anchor an epoch's Merkle receipt root (Solana memo program on the
configured cluster, or the explicit mock transport). Returns the
signature and explorer link when a real cluster is configured.

Args:
    epoch_index: Epoch to anchor (-1 = latest)
    epoch_size: Receipts per epoch

### `meshpay_audit`

**Signature**

```python
meshpay_audit(limit: int = 200)
```

**Description**

Independently audit the DCM settlement receipt chain and preview the
USDC payout plan. Replays the hash chain with MeshPay's own verifier
and compares it to DCM's verdict.

Args:
    limit: Receipts to audit (1-200)

### `navigate_browser`

**Signature**

```python
navigate_browser(url: str, session_id: str = None)
```

**Description**

Navigate the browser to a URL. Use before other browser actions
to establish the page context.

Args:
    url: The URL to navigate to
    session_id: Optional browser session ID

### `query_brain`

**Signature**

```python
query_brain(query: str)
```

**Description**

Search the local knowledge vault (vector search) for relevant
previously-researched information.

Args:
    query: What to search for in the knowledge vault

### `recall_memory`

**Signature**

```python
recall_memory(query: str)
```

**Description**

Cross-session semantic recall. Finds information from past
research sessions that relates to the current query.

Args:
    query: Context to find related past research for

### `research_web`

**Signature**

```python
research_web(query: str, tor: bool = False)
```

**Description**

Perform an autonomous research mission using the Jambubrowser swarm.
Decomposes query into parallel sub-tasks and synthesizes findings.

Args:
    query: The research question or topic
    tor: Route through Tor for anonymity (default: False)

### `scrape_page`

**Signature**

```python
scrape_page(url: str, session_id: str = None)
```

**Description**

Scrape a webpage and return its text content as clean text.
Includes page title, main content, and a screenshot.

Args:
    url: The webpage URL to scrape
    session_id: Optional browser session ID for stateful navigation

### `search_academic`

**Signature**

```python
search_academic(query: str)
```

**Description**

Search ArXiv for academic papers on a topic.
Returns paper titles, abstracts, and links.

Args:
    query: Research topic to search for

### `search_code`

**Signature**

```python
search_code(query: str)
```

**Description**

Search GitHub for code repositories matching a query.
Returns repo names, descriptions, and links.

Args:
    query: Code or project topic to search for

### `search_multi_engine`

**Signature**

```python
search_multi_engine(query: str, engines: str = 'google,bing,duckduckgo')
```

**Description**

Search across multiple engines without scraping pages.
Returns raw search results with URLs and snippets.

Args:
    query: Search query
    engines: Comma-separated engine list (default: google,bing,duckduckgo)

### `start_mission`

**Signature**

```python
start_mission(query: str, schedule: str = None)
```

**Description**

Register a long-running background research mission.
The engine will periodically research this topic and report findings.

Args:
    query: The research topic to monitor
    schedule: Cron-style schedule (e.g., '0 */6 * * *' for every 6 hours)

### `stop_mission`

**Signature**

```python
stop_mission(mission_id: str)
```

**Description**

Stop a running background research mission.

Args:
    mission_id: The mission ID to stop (from start_mission)

### `take_screenshot`

**Signature**

```python
take_screenshot(url: str, full_page: bool = False, session_id: str = None)
```

**Description**

Take a screenshot of a webpage. Returns base64-encoded PNG.

Args:
    url: The page URL to screenshot
    full_page: Capture the full scrollable page (default: viewport only)
    session_id: Optional browser session ID

### `type_text`

**Signature**

```python
type_text(url: str, selector: str, text: str, session_id: str = None)
```

**Description**

Type text into an input field on a webpage.

Args:
    url: The page URL
    selector: CSS selector for the input field
    text: Text to type
    session_id: Optional browser session ID

### `visual_grounding`

**Signature**

```python
visual_grounding(url: str)
```

**Description**

Analyze a webpage visually and identify interactive elements
(buttons, forms, links). Returns suggested actions the agent can take.

Args:
    url: The page URL to analyze visually
