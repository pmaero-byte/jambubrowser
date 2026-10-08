# Agent prompt — test FEA Lab with JambuBrowser, and fix what you find

Hand the block in **"The prompt"** section below to the agent. The rest of this
file is reference material it can read if you want it to, but the prompt is
self-contained.

---

## The prompt

> You are testing **FEA Lab** — a React/Vite frontend on `127.0.0.1:5180` with a
> FastAPI backend on `127.0.0.1:8000` — using **JambuBrowser**, a browser-testing
> engine at `127.0.0.1:8001`. Source: `~/Aerospace_projects/FEA/fea-lab`.
> JambuBrowser source: `~/My_projects/browser_project`.
>
> **Your job: find real defects in FEA Lab, fix them, and prove each fix with a
> check that fails before and passes after.** Work autonomously. Do not stop at
> the first red test — sweep, triage, fix, repeat.
>
> ### 0. Verify your tooling before trusting anything
>
> JambuBrowser must be a version that has the capabilities below. Check first:
>
> ```bash
> curl -s http://127.0.0.1:8001/health | head -c 120     # expect v3.4.0
> curl -s http://127.0.0.1:8001/openapi.json | python3 -c \
>   "import sys,json; p=json.load(sys.stdin)['components']['schemas']['TestFlowRequest']['properties']; \
>    print([k for k in ('viewport','device','scrub_pii','network_idle') if k in p])"
> ```
>
> If that prints `[]`, the engine is stale. Get the current code first —
> `~/Aerospace_projects/jambubrowser` may be on an old branch. Either merge
> `origin/main` into it, or start a second engine from the clone that has it:
>
> ```bash
> cd ~/My_projects/browser_project
> .venv/bin/python -m uvicorn backend.engine:app --host 127.0.0.1 --port 8002
> export JAMBU_ENGINE_URL=http://127.0.0.1:8002
> ```
>
> Do not diagnose a capability as "missing" until you have ruled this out. A
> missing capability and a stale engine look identical from the outside.
>
> ### 1. The loop
>
> For every defect:
>
> 1. **Reproduce** — write the smallest flow that shows it, with a real
>    assertion, not a screenshot you eyeballed.
> 2. **Diagnose** — read `failure_cause` and `console`. These are the evidence;
>    do not re-derive them by hand.
> 3. **Fix** — edit FEA Lab. Vite hot-reloads, so just re-run.
> 4. **Verify** — re-run the *same* flow. It must now pass.
> 5. **Keep the check** — save the flow under
>    `~/Aerospace_projects/FEA/fea-lab/tests/jambu/` so it is a regression test.
> 6. Report: symptom → cause → fix → before/after output.
>
> Never move on from a step 3 that did not produce a green step 4.
>
> ### 2. How to run a check
>
> ```bash
> cat > /tmp/flow.json <<'JSON'
> {"steps": [
>   {"action": "wait", "network_idle": true, "timeout": 25000},
>   {"action": "wait", "js": "document.body.innerText.length > 300", "approve": true, "timeout": 25000},
>   {"action": "assert_canvas", "selector": "canvas", "min_non_background_pct": 5, "min_colors": 3, "min_width": 320, "min_height": 240},
>   {"action": "evaluate", "script": "document.querySelectorAll('canvas').length", "approve": true}
> ]}
> JSON
> jambu test /tmp/flow.json --url http://127.0.0.1:5180/results --local --approve
> ```
>
> Add `--json` for the full report, `--device mobile` for phone width,
> `--no-scrub` is already the default for local targets. `tools/fealab_probe.py`
> in the JambuBrowser repo wraps the HTTP API if you want structured output.
>
> ### 3. What to look for
>
> Sweep the routes in `frontend/src/routes.jsx` (~70 of them, including
> `/results`, `/workbench`, `/mesh`, `/solver`, `/guided-answers`, `/report`,
> `/validation`, `/agent-dock`, `/optimize`). For each, at minimum:
>
> - does it render, or is it stuck on a loading state?
> - any **console error**? (`console_errors` in the report, with `url` + `line`)
> - any **failed request** or 4xx/5xx?
> - at `--device mobile`, does the layout survive?
> - can you **complete the core action** and assert on the *numbers*?
>
> ### 4. Non-negotiable rules
>
> - **Never weaken an assertion to get green.** Deleting `min_width`, or
>   replacing a value check with a substring check, is not a fix. If an
>   assertion is wrong, say so and change it deliberately.
> - **Never report a fix you did not re-run.** An unverified fix is a guess.
> - **Attribute honestly.** Say "FEA Lab bug" or "JambuBrowser bug". If the
>   engine is at fault, that is a finding to report, not to work around.
> - **Report numbers as measured.** Quote the actual output. Never invent a
>   result, and never describe a run you did not perform.
> - If you cannot verify something, say so plainly instead of implying success.
>
> ### 5. Already known — do not redo these
>
> **Fixed in JambuBrowser** (so if you hit them, the engine is stale):
> WebSocket routing crashed all steps; `assert_canvas` could not decode a
> screenshot; `/run` was capped at 30 s so any waiting flow 504'd;
> `device_scale_factor` drifted between runs; `describe_selector` could not
> read `text=` selectors; a failed `download` produced no diagnosis.
>
> **Open FEA Lab bugs — start here:**
>
> 1. **The 3D viewport collapses at phone width.** At `--device mobile`,
>    `/results` measures `canvas: 3x1899` — a 3px sliver. Reproduce with the
>    `assert_canvas` block above and `--device mobile`. The canvas in
>    `frontend/src/components/Viewport3D.jsx` is not responsive.
> 2. **SVG rendering errors at phone width**, repeated per frame:
>    `Error: <text> attribute x: Expected length, "NaN".` and
>    `Error: <line> attribute x1: Expected length, "NaN".` Something is
>    computing a coordinate from a zero-width box — likely the same root cause
>    as (1). Fix (1) and check whether these go with it.
> 3. **`ERR_CONNECTION_REFUSED` ×3 on every page load.** Something the frontend
>    requests is not being served. Find out what.
>
> **Convenience:** the onboarding modal ("Welcome to FEA Lab") covers the
> export button. Suppress it rather than clicking through:
>
> ```json
> {"storage_state": {"cookies": [], "origins": [{"origin": "http://127.0.0.1:5180",
>   "localStorage": [{"name": "fealab_onboarded", "value": "1"}]}]}}
> ```
>
> Pass that as `storage_state` on the request.
>
> ### 6. Available vocabulary — do not invent actions
>
> **Interactions:** `navigate` `click` `dblclick` `click_at` `type` `press`
> `hover` `select` `check` `uncheck` `reload` `back` `forward` `drag` `wheel`
> `mouse` `set_range` `upload` `download` `screenshot` `evaluate`
>
> **Gestures:** `drag {selector, to:{dx,dy}, steps, button}` ·
> `wheel {x,y,dx,dy}` · `mouse {event:down|move|up, x, y}` ·
> `set_range {selector, value}` (fires real events *and* drags the handle)
>
> **Waits:** `wait` with `selector` / `text` (waits for *rendered* content) /
> `js` predicate (`approve:true`) / `url_contains` / `network_idle`.
> Use `wait js` before asserting — pages here lazy-load.
>
> **Assertions:** `assert_visible` `assert_not_visible` `assert_text`
> `assert_text_equals` `assert_value` `assert_count` `assert_url` `assert_title`
> `assert_checked` `assert_enabled` `assert_console_clean` `assert_no_warnings`
> `assert_no_failed_requests` `assert_dialog` `assert_lcp`/`fcp`/`load`
> `assert_no_a11y_violations` `assert_request_body` `assert_request_status`
> `assert_request_fast` **`assert_canvas`** **`assert_screenshot`** ·
> `assert_download` · `nth` / `within` / `testid` / `role`+`name` / `xpath`
>
> **Every step takes `timeout` (ms)** and it is honoured — use a small one on
> negative assertions so they fail fast.
>
> **Numbers come back unmasked** for local targets. `evaluate` returns raw
> values, so assert on real solver output: node counts, von Mises, displacement.
> `results` in the report is the place to read them.
>
> ### 7. Report format
>
> For each defect: **what** (symptom + the exact failing output) → **why** (the
> `failure_cause` / console evidence) → **fix** (file + what changed) →
> **proof** (same flow, before vs after). Then a summary table and an explicit
> list of what you did *not* manage to fix or verify.

---

## Reference

- `docs/BROWSER_TESTING.md` — full step schema and API (in the JambuBrowser repo)
- `docs/FEA_LAB_CAPABILITIES.md` — what was added and why, plus the live-verified
  results and the six engine bugs already fixed
- `tools/fealab_probe.py` — HTTP client + report summariser