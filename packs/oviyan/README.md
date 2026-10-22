# Oviyan test pack

Black-box tests for a running **Oviyan Studio** (`oviyan serve`), the Rust comic
engine. The pack checks the JSON API, the SVG it draws, the Tamil lettering
pixel by pixel, and the Studio UI. Every check is deterministic and none of
them calls a model.

```bash
oviyan serve 127.0.0.1:7878 &          # in the Oviyan checkout
python cli/jambu.py pack run oviyan --base http://127.0.0.1:7878 \
    --var oviyan_root=/path/to/oviyan --json oviyan-report.json
```

| Exit code | Meaning |
|---|---|
| `0` | Every step passed. |
| `1` | A step failed. This includes known findings that still reproduce. |
| `2` | The pack could not run: the target is unreachable, a variable is missing, or a flow crashed. |

Other options:
- `--flow a,b` runs a subset.
- `--artifacts DIR` keeps the glyph rasters and screenshot diffs.
- `--update-baselines` rewrites `baselines/`. Review the new PNG before you commit it.

## Variables

| Name | Default | Purpose |
|---|---|---|
| `oviyan_root` | `$OVIYAN_ROOT` | The Oviyan checkout. It is used to find the bundled font. |
| `font` | `$OVIYAN_TAMIL_FONT`, else `{oviyan_root}/assets/fonts/noto_sans_tamil/NotoSansTamil.ttf` | The reference font for glyph fidelity. Required. |

## Flows

| Flow | What it proves |
|---|---|
| `capabilities-contract` | `/api/capabilities` follows the v0.4 schema: formats, all nine patch ops and the cast. Three calls return the same body. |
| `prompt-coverage-en` / `-ta` | `/api/prompt` understands the words of a prompt (`unmatched_tokens`). A word split into fragments counts once. |
| `render-structure` | `/api/render` SVG is well-formed, has no NaN, unique ids and no script, and is byte-identical across three runs. |
| `caption-glyphs-ta` / `-control` | The drawn caption looks like Noto Sans Tamil shaping of its text. The control caption has no U+0BC1 sign. |
| `balloon-glyphs-ta` | A Tamil speech balloon is drawn in Tamil letters. |
| `scene-svg`, `scene-svg-errors` | `/api/scene/svg` draws a pilot page. A scope/id mismatch reports an accurate error with an error status. |
| `http-head` | `HEAD /` answers like `GET /`. |
| `validate-and-inspect` | A pilot page validates clean and inspects to resolved actors. |
| `studio-ui` | The Studio loads a clean plate that matches `baselines/studio-default-plate.png` (compared at 130 px wide, because the canvas follows the window). It then compiles an English prompt with a clean console. |
| `studio-ui-tamil-sample` | The "தமிழ் அரசி" sample button draws its balloon in Tamil letters. |

## Known findings

A step tagged with `"finding": "OVY-…"` checks a known Oviyan defect. That step
fails while the defect exists, and the report lists the finding as
`reproduced`. Once Oviyan is fixed it reads `not_reproduced`. Findings never
make the pack pass: it stays red until Oviyan is fixed.

| Id | Defect |
|---|---|
| `OVY-TA-CAPTION` | `ஒரு சிறுமி` is drawn as `ஒரசிறமி`: the ு signs and the word space are lost, while the `<title>` claims the right text. |
| `OVY-TA-BALLOON` | Tamil balloon text is drawn with Latin-looking stroke glyphs. |
| `OVY-TA-PROMPT` | The prompt tokenizer splits Tamil words at the virama and misses common words. |
| `OVY-SCENE-SVG` | `/api/scene/svg` without a debug view returns "No such file or directory". |
| `OVY-ERRORS-AS-200` | Errors come back as HTTP 200 SVG bodies. |
| `OVY-HEAD` | `HEAD /` returns 404 with a body after `Connection: close`. |
| `OVY-PANEL-SCOPE` | `scope=panel` with a page id answers the misleading `UNSUPPORTED_ASSET`. |

## How the glyph check works

`assert_glyph_fidelity` rasterises the SVG region the product drew. It also
shapes the expected string with the reference font in Chromium, which uses
HarfBuzz. It then compares ink coverage, the worst sliding window, the word gaps
and the aspect ratio. The thresholds were calibrated on this product: the
faithful control caption passes and each known-bad string fails. They are
heuristics, so read the saved `glyphs/*.actual.png` / `*.reference.png` when a
result surprises you.
