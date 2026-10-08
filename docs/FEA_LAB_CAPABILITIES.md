# Browser-agent capabilities for numeric/graphical apps

**Status:** implemented. Origin: a gap analysis produced while testing FEA Lab
(Vite :5180 + FastAPI :8000) against the engine on :8001.

This document records what changed and why, in the order that unblocked the
most. Every claim below is covered by `tests/test_browser_capabilities.py`
unless it says "not covered".

---

## 0. What was already there

Three of the reported gaps were mostly present but undiscoverable, and are
worth stating so nobody re-implements them:

| Reported gap | Actual state when this work started |
|---|---|
| PII scrubber destroys numbers | `scrub_pii` already existed on `/run` and `/browser/sessions` (`browser_sessions.py`). The real defect was in the *regexes*, not the plumbing — see §1. |
| No wait-for-condition | `wait` + `js` predicate already routed to `wait_for_function`, and `url_contains` was there. Missing: visibility semantics and a public network-idle wait — §4. |
| No upload/download | `upload` / `attach_file` / `set_input_files` / `download` all existed, and `TestFlowRequest` already accepted `storage_state` and `context_options`. Missing: content verification — §5. |

---

## 1. The scrubber no longer eats numbers

**Root cause.** `PIIDetector.PATTERNS["phone_intl"]` was
`\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}` — the `+` was
optional, so it matched *any* decimal and *any* 4+ digit run. Reproduced before
the fix:

```
'max displacement 0.1234'  ->  'max displacement [REDACTED_PHONE_INTL]'
'stress 250.5 MPa'         ->  'stress [REDACTED_PHONE_INTL] MPa'
'nodes 999'                ->  'nodes 999'          # 3 digits survived
'elements 1000'            ->  'elements [REDACTED_PHONE_INTL]'
```

A solver app is all numbers, so every result was unassertable and the caller had
to superscript-encode digits in the page and decode them afterwards.

**Fix** (`backend/core/privacy.py`): every digit-shaped pattern is now anchored
to a shape the PII actually has.

- `phone_intl` requires the `+` country prefix that distinguishes an
  international number from a decimal.
- `ssn` requires its separators (`123-45-6789`), so a bare 9-digit number is not
  an SSN.
- `ip_address` bounds each octet to 0–255, so `9999.1.1.1` is a number.

**Default policy.** `default_scrub_pii(host=…, allow_private=…, allow_domains=…)`
resolves `None` to *off* for loopback/RFC1918/`.local` targets and *on* for
public hosts. `/run` and `/browser/sessions` now take `scrub_pii: Optional[bool]`
— unset means policy, `true`/`false` force it. An explicit flag always wins.

**Per-step override.** `{"action": "evaluate", "scrub": false}` unmasks one step
on a scrubbed session; `scrub: true` re-masks an unscrubbed local one. An
unrecognised value keeps the session policy rather than guessing.

`/run` reports `scrub_pii` so the output states which policy it ran under.

## 2. Per-step timeouts are honoured

`timeout` was read in `_run_step` and then consumed only by the `wait`, `api` and
`download` handlers. Every other dispatch went to an adapter method with a
hardcoded `timeout=10000`, so `{"action":"click","timeout":1500}` waited the full
10 s.

Every dispatch path now takes it: `act`, `act_selector`, `upload_files`, and all
eight `*_selector` adapter methods take `timeout_ms`. Clamped by
`_step_timeout_ms` to `1…120000` — `0` means "as soon as possible", not "never",
and a typo cannot wedge a flow.

`_call_optional` degrades to the old call when an adapter predates the keyword,
so an older adapter gets the old (looser) behaviour instead of a hard failure.

## 3. Viewport and device, everywhere, and deterministic

Two separate defects:

1. **The default viewport was random.** Context options came from
   `fingerprint_rotator`, which picks a per-session fingerprint — so the same
   flow rendered at 1680×1050 and 1280×800 on different runs. Rotation is right
   for *identity* and wrong for *geometry*.
2. **Viewport only worked inside a matrix.** `/browser/sessions` and `/run`
   accepted none of `viewport` / `width` / `height`; only `/matrix` did.

New `backend/modules/browser_context_options.py` owns it:

- `DEFAULT_VIEWPORT = {1440, 900}`, always set explicitly — `open()` guarantees
  it even when the caller passes no options.
- `ViewportOptions` mixin on `OpenRequest` / `TestFlowRequest`: `viewport`,
  `device`, `viewport_width/height`, `device_scale_factor`, `is_mobile`,
  `has_touch`, `color_scheme`, `reduced_motion`, `screen`. Accepts
  `"1280x800"`, `[w, h]`, `{width, height}` and JSON strings for booleans.
- Device presets: `desktop`, `laptop`, `tablet`, `mobile`, `iphone_13`, `pixel_5`.
- Precedence: fingerprint defaults → preset → `viewport` → individual scalars.
  An explicit scalar beats a preset.
- `/matrix` gained `reduced_motion` and `screen`, and keeps per-variant geometry.

CLI: `jambu qa test --viewport 390x844 --device mobile --color-scheme dark
--reduced-motion reduce --scrub/--no-scrub --network-idle`.

## 4. Waits: rendered by default, network-idle on request

- `wait` with a `selector` or `text` now waits for something **rendered**.
  Previously `wait_for_selector` matched `state="attached"` (laid out but not
  shown) and `wait_for_text` matched hidden text — including the document title,
  so waiting for `"workbench"` returned while the page still said
  "Loading module…". `visible: false` opts back into the loose check.
- `{"action": "wait", "network_idle": true}` is now a public wait; a bare `wait`
  with no condition also settles on network quiet rather than racing a lazy chunk.
- `run_flow(..., network_idle=True)` waits for quiet after every passing step.
- An adapter without the visibility-aware wait falls back to the loose one rather
  than refusing the step.

## 5. Download content is verified

`download` reported a filename and a byte count but checked neither. An empty or
truncated export was indistinguishable from a good one.

`download` now accepts `min_bytes`, `sha256` and `contains`, and refuses when the
file is absent (`download_empty`) or fails a bound (`download_failed`) with the
actual size/digest in the message. `assert_download` / `expect_download` verify
the previous download. The `path` is returned and kept on the session so a
follow-up step can parse the export.

## 6. Pointer actions

Everything in the existing vocabulary addresses an element; a 3D viewport
("drag to rotate · scroll to zoom · right-drag to pan · click node to probe") and
a slider are defined by *movement*. New steps in `browser_step_actions.py`:

| Step | Shape |
|---|---|
| `drag` | `{"from":{x,y},"to":{x,y},"steps":20,"button":"left"}` or `{"selector":"#viewport","to":{dx,dy}}` |
| `wheel` | `{"x":700,"y":400,"dx":0,"dy":-240}` — position required, zoom-to-cursor reads it |
| `mouse` | `{"event":"down"\|"move"\|"up",…}` — a gesture that straddles steps |
| `click_at` | `{"x":…,"y":…,"button":"right"}` |
| `dblclick` | by selector, ref or coordinates |
| `set_range` | `{"selector":"#deformation","value":42}` |

`steps` defaults to 20 for a drag: a single jump is not a drag, and orbit
controls read the movement stream. `set_range` sets the value *and* dispatches
`input`+`change` (assigning `.value` alone does nothing to React or to most
slider widgets) then performs a real pointer drag across the track so the app's
own handler runs. Adapter methods release the pointer in a `finally` so a failed
gesture cannot leave a button stuck.

Pointer steps require `approve=true` (a gesture lands wherever the pointer is and
the risk classifier cannot see through it) and are in `MUTATING_ACTIONS`, so
`human_takeover` refuses them too.

## 7. Canvas and visual assertions

A WebGL canvas has no readable backing store once the frame is presented —
`toDataURL` and a 2D read both return zeros. The rendered frame only exists in
the compositor's output, which a screenshot captures. So visual assertions go
through a clipped screenshot, and the PNG is decoded in stdlib Python (no Pillow
dependency).

`assert_canvas {selector, min_non_background_pct, min_colors, max_colors,
min_brightness, max_brightness, min_width, min_height}` — needs at least one
bound, so a typo'd assertion cannot silently pass. Reports the measured numbers
either way.

The geometry bounds matter more than they look: a responsive bug that collapses
the viewport to a 3 px sliver still has colour, so `min_colors` and
`min_non_background_pct` both pass on it. On FEA Lab at `device=mobile` the
canvas measures `3x1899` and passes every colour bound until you add
`min_width` — which then fails with `rendered width 3px (want >= 320px) -- the
viewport is collapsed`.

`assert_screenshot {name, threshold, masks}` / `assert_not_screenshot` — diffs
against `<artifacts>/baselines/<name>.png`; the first run of a missing baseline
creates it and passes with `baseline_created`; a failing frame is saved to
`<artifacts>/diffs/`. Channel tolerance absorbs anti-aliasing; a size change is
reported as a full difference rather than raising.

## 8. Failures explain themselves

A failed step reported a reason and a Playwright call log ("waiting for
locator"). Now, for interaction and wait steps, `failure_cause` carries:

`found` · `in_viewport` · `covered_by` (selector + text) · `enabled` ·
`hidden_by_css` · `rect` vs `viewport` · up to 3 alternative selector spellings
(`data-testid` first) · `likely_cause` (a one-line verdict) · a screenshot.

This is attached to *inconclusive* steps too, since a Playwright timeout arrives
as a bare exception rather than a refusal. Diagnosis is best-effort: every probe
is individually guarded, and an adapter that cannot answer reports
`probes_available: false` rather than claiming the element was absent.

## 9. Evidence is no longer dropped

- `run_flow` reports `evaluated` (every evaluate step's value) and `screenshots`.
- `/matrix` variants carry `context_options`, `evaluated`, `screenshots` and the
  full `report` alongside the digest, and `cause` per failed step.
- `jambu qa test --json` prints the API response as-is; the CLI also prints
  evaluated values and a one-line DOM verdict per failed step.
- Console messages carry `{step, url, line}`, so "unsupported MIME type
  ('text/html')" is traceable to the module that failed.
- `worker_errors` surfaces Web Worker failures. An in-browser solve that throws
  leaves the page looking correct and the result simply never arrives.
- New assertions: `request_body` (`body_path`/`body_contains`/`body_equals`,
  recursive subset matching), `request_status`, `request_fast`, `no_warnings`.
  Request bodies are captured only when an assertion asks, and never retained
  above 64 KB.

## 10. Target ergonomics

`nth` and `within` narrow an ambiguous match ("third Skip", "Save inside the
toolbar") and are normalised for XPath, which was previously refused by
`assert_not_visible`. `testid`, `role` + `name` (via Playwright's
`internal:role=` engine selector) and `xpath` are accepted as targets. A `note`
recording the narrowing goes into the step detail, so a pass still says which
element it looked at.

---

## Verified live against FEA Lab

Run against Vite :5180 + FastAPI :8000 with the engine on :8002. Harness:
`tools/fealab_probe.py`.

| Capability | Result |
|---|---|
| Numeric results unmasked | `{"max_disp_mm":0.1234,"von_mises_mpa":355,"nodes":12903,"elements":1000,"iterations":50000,"ssn_like":123456789}` — every value intact, and still intact with `scrub: true` (numbers and PII are now independent) |
| Visible waits | before: `text="Loading module…"`, `count=0`; after `wait js` + `network_idle`: `count=32` |
| Per-step timeout | `timeout: 2000` step finished in 2529 ms, not 10 s |
| 3D viewport | `assert_canvas` → `2088x1378, 48.8% non-background, 248 colours`; drag-to-rotate changed it to **262 colours**, i.e. the gesture provably moved the scene |
| Scroll-to-zoom / right-drag pan | wheel + right-drag both dispatch and the canvas stays rendered |
| Sliders | `set_range` → `85`, reads back `85`, handle really dragged |
| Viewport determinism | three runs → identical `1440x900`, dpr `2`, canvas `2088x1378` |
| Device presets | `mobile` → `390x844` dpr 3 touch, `tablet` → `834x1112` |
| Visual regression | baseline created → `0.00% differing` on rerun → `4.11% differing` after a drag |
| Export | `Export Results (CSV)` → `3376 bytes`, `min_bytes`/`contains` verified; file holds 106 rows of nodal displacement + von Mises |
| Failure diagnosis | `covered_by: '✕ FEA Welcome to FEA Lab…'`, `likely_cause: another element is on top at its centre point` — the onboarding modal blocking the export |
| Console attribution | `{"step":"4:click","url":".../src/store.jsx","line":894}` |

### Bugs found by running it (all fixed)

1. **WebSocket routing crashed every later step.** `connect_to_server()` is
   *synchronous* in Playwright 1.63; `await`ing it raised
   `'ServerWebSocketRoute' object can't be awaited` inside the page's own
   `evaluate`, so *any* app opening a socket — which FEA Lab does for its agent
   bridge — had every snapshot and step fail. Fixed to call it synchronously
   **and** forward both directions, because once you connect, Playwright stops
   proxying for you and a one-way proxy silently drops every server frame.
   `tests/test_browser_websocket.py`.
2. **`/browser/sessions/run` was capped at 30 s** by the request-timeout
   middleware, so any flow containing a real wait returned `504` with no step
   results at all. Excluded, which is safe now that per-step timeouts bound a
   flow.
3. **`assert_canvas` could never have worked.** `screenshot_clip` returned raw
   PNG bytes where the decoder expected base64 (a `zlib` "incorrect padding"
   error), and passed an un-awaited coroutine as the clip rectangle. Also clamps
   the clip to the viewport, since Playwright rejects one that reaches outside.
4. **`device_scale_factor` still drifted** (1.25 vs 2 between runs), which
   resized the canvas from 2088 px to 1566 px and would have invalidated every
   pixel-count assertion. Pinned alongside the viewport.
5. **`describe_selector` could not read engine selectors.** `text=Export Results
   (CSV)` was reported as "bad selector", hiding the diagnosis for exactly the
   selectors people write by hand. Resolution now goes through a Playwright
   locator first.
6. **A download that never started produced no diagnosis.** `download` was not
   in the diagnosable set — and it was the action that most needed it.

## FEA Lab coverage now

| Area | Before | After |
|---|---|---|
| Guided workflow, templates, Run safe | yes | yes |
| Command palette, shortcuts | yes | yes |
| Route health / console / network | yes | yes, with step + source attribution |
| Numeric results (max disp/stress, counts) | encoding hack | direct |
| 3D viewport (rotate/zoom/probe) | screenshot only | drag/wheel/click_at + `assert_canvas` |
| Sliders (deformation, mesh size) | no | `set_range` (value + events + real drag) |
| Import/export (INP/STL/VTK/report) | no | `upload`/`download` + `min_bytes`/`sha256`/`contains` |
| Phone/tablet layouts | matrix only, no evidence | `--device mobile`, matrix carries screenshots + evaluated values |
| Agent Bridge (`window.feaAgent`) | via evaluate | direct, numbers unmasked |
| Heavy solves / progress | partly | `wait` + `js` + `network_idle`, worker errors |
| Accessibility | counts only | unchanged (still counts; see below) |

## Still open

- **A11y findings** carry rule id and count only. Selector, HTML snippet and
  impact are still needed to turn a violation into a fix or to ratchet a budget.
- **Variables / loops / `include`** — flows are still flat JSON. A 46-route
  sweep needs `repeat`, shared preludes and `save {as:…}` → `assert_eval`.
- **State seeding**: `storage_state` and `context_options` reach the context, but
  there is no `init_script` pre-navigation, so suppressing FEA Lab's onboarding
  modal still costs a step. `storage_state` with `origins[].localStorage` is the
  workaround — it was enough to make the modal disappear once seeded.
- **The 3D canvas has zero width at 390 px** (`canvas_px: "0x1899"` on
  `device="mobile"`): the viewport is not responsive, so phone-width visual
  assertions will fail until the app fixes it. Engine-side, `device="mobile"`
  reports the geometry correctly.
- **WebGL perf**: `lcp`/`fcp`/`load` exist; long-task count, frame-time/FPS
  sampling and JS heap do not.
- **Multi-tab visibility**: workers surface errors; tab-level events do not.
- **Record → export → import round-trip** and semantic diff between runs are
  still blocked on the items above.

## Test coverage map

`tests/test_browser_capabilities.py` (168 tests) covers §1–§10. The PNG codec,
diff and canvas arithmetic run against real encoded images rather than mocks, so
the pixel maths is genuine; the pointer/gesture paths assert on what the session
asked the adapter to do.

Two more files cover what live testing exposed, and both are skipped rather than
failed when no browser is installed so the unit suite stays hermetic:

- `tests/test_browser_websocket.py` (10) — the socket proxy, against Playwright's
  real signatures: connect is synchronous and one-shot, and both directions must
  be forwarded by hand.
- `tests/test_browser_diagnosis.py` (15) — `describe_selector` against a real
  layout, because its answers depend on real geometry and real hit-testing:
  covered, off-screen, disabled, `display:none`, `visibility:hidden`, a parent
  containing its own child (must not accuse the child), and a bad selector.