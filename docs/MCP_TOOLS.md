# MCP Tools Reference

Auto-generated from `backend/mcp_server.py` by `tools/mcp/generate_docs.py`. Do not edit by hand — re-run the generator after adding or renaming a tool.

**Total tools:** 21

## Table of contents

- [`analyze_screenshot`](#analyze_screenshot)
- [`check_engine_health`](#check_engine_health)
- [`click_element`](#click_element)
- [`deep_research`](#deep_research)
- [`execute_tool`](#execute_tool)
- [`get_brain_stats`](#get_brain_stats)
- [`get_system_stats`](#get_system_stats)
- [`list_custom_tools`](#list_custom_tools)
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
