"""Deterministic content checks for API responses and rendered documents.

Pure functions, no browser and no LLM: JSON-path operators, a typed JSON
schema subset, SVG structure checks, text/script fidelity, prompt coverage
and stable hashing. The flow engine's ``assert_*`` steps and the test packs
call these, and they are unit-testable on their own.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any, Iterable, Optional

_MISSING = object()

# --------------------------------------------------------------------------
# JSON path


def _path_parts(path: str) -> list:
    parts: list = []
    token = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if token:
                parts.append(token)
                token = ""
        elif ch == "[":
            if token:
                parts.append(token)
                token = ""
            end = path.find("]", i)
            if end < 0:
                raise ValueError(f"unclosed '[' in path {path!r}")
            index = path[i + 1:end].strip().strip("'\"")
            parts.append(int(index) if index.lstrip("-").isdigit() else index)
            i = end
        else:
            token += ch
        i += 1
    if token:
        parts.append(token)
    return parts


def resolve_path(payload: Any, path: str) -> Any:
    """Resolve ``a.b[0].c``; returns ``_MISSING`` (not ``None``) when absent.

    ``None`` is a legal JSON value (``null``), so "missing" needs its own
    sentinel or ``exists`` could not tell ``{"a": null}`` from ``{}``.
    Negative indexes count from the end.
    """
    node = payload
    for part in _path_parts(path or ""):
        if isinstance(part, int):
            if not isinstance(node, list) or not -len(node) <= part < len(node):
                return _MISSING
            node = node[part]
        else:
            if not isinstance(node, dict) or part not in node:
                return _MISSING
            node = node[part]
    return node


JSON_PATH_OPS = ("exists", "not_exists", "equals", "not_equals", "regex", "contains",
                 "gte", "lte", "length", "type")

_JSON_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    for name, kind in _JSON_TYPES.items():
        if isinstance(value, kind):
            return name
    return type(value).__name__


def _same(got: Any, expected: Any) -> bool:
    if isinstance(expected, (dict, list, bool)) or expected is None:
        return got == expected
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)) \
            and not isinstance(got, bool):
        return float(got) == float(expected)
    return str(got) == str(expected)


def json_path_check(payload: Any, path: str, op: str = "", expected: Any = None) -> tuple:
    """Evaluate one JSON-path assertion; returns ``(ok, detail)``.

    ``op`` defaults to ``equals`` when ``expected`` is given, else ``exists``.
    Unknown operators raise ``ValueError`` so a typo is never a silent pass.
    """
    op = (op or ("equals" if expected not in (None, "") else "exists")).lower()
    if op not in JSON_PATH_OPS:
        raise ValueError(f"unknown json_path op {op!r}; known: {', '.join(JSON_PATH_OPS)}")
    label = path or "<root>"
    got = resolve_path(payload, path)
    if op == "exists":
        return got is not _MISSING, f"{label} {'exists' if got is not _MISSING else 'missing'}"
    if op == "not_exists":
        return got is _MISSING, (f"{label} absent" if got is _MISSING
                                 else f"{label} present: {_short(got)}")
    if got is _MISSING:
        return False, f"{label} missing (wanted {op} {_short(expected)})"
    if op == "equals":
        ok = _same(got, expected)
        return ok, f"{label} = {_short(got)}" + ("" if ok else f" != {_short(expected)}")
    if op == "not_equals":
        ok = not _same(got, expected)
        return ok, f"{label} = {_short(got)}" + ("" if ok else " (must differ)")
    if op == "regex":
        text = got if isinstance(got, str) else json.dumps(got, ensure_ascii=False)
        ok = re.search(str(expected), text) is not None
        return ok, f"{label} {'matches' if ok else 'does not match'} /{expected}/: {_short(got)}"
    if op == "contains":
        if isinstance(got, (list, tuple)):
            ok = any(_same(item, expected) for item in got)
        elif isinstance(got, dict):
            ok = str(expected) in got
        else:
            ok = str(expected) in str(got)
        return ok, f"{label} {'contains' if ok else 'lacks'} {_short(expected)}"
    if op in ("gte", "lte"):
        try:
            a, b = float(got), float(expected)
        except (TypeError, ValueError):
            return False, f"{label} = {_short(got)} is not numeric"
        ok = a >= b if op == "gte" else a <= b
        return ok, f"{label} = {a:g} ({'>=' if op == 'gte' else '<='} {b:g})"
    if op == "length":
        try:
            n = len(got)
        except TypeError:
            return False, f"{label} has no length"
        ok = n == int(expected)
        return ok, f"len({label}) = {n}" + ("" if ok else f" != {expected}")
    # type
    actual = _json_type(got)
    wanted = str(expected)
    ok = actual == wanted or (wanted == "number" and actual == "integer")
    return ok, f"{label} is {actual}" + ("" if ok else f", wanted {wanted}")


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False,
                                                           default=str)
    text = repr(text) if isinstance(value, str) else text
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------
# Typed JSON schema (a deliberate subset of JSON Schema draft 2020-12)

SCHEMA_KEYWORDS = ("type", "required", "properties", "additionalProperties", "items",
                   "enum", "const", "pattern", "minimum", "maximum", "minLength",
                   "maxLength", "minItems", "maxItems", "anyOf", "$comment",
                   "description", "title")


def validate_schema(instance: Any, schema: dict, where: str = "$") -> list:
    """Validate ``instance`` against a JSON-Schema subset; returns error strings.

    Supported: type (string or list), required, properties,
    additionalProperties (bool or schema), items, enum, const, pattern,
    minimum/maximum, minLength/maxLength, minItems/maxItems, anyOf. An
    unsupported keyword is an error rather than being ignored, so a schema
    never looks stricter than it is.
    """
    errors: list = []
    if not isinstance(schema, dict):
        return [f"{where}: schema must be an object"]
    unknown = [k for k in schema if k not in SCHEMA_KEYWORDS]
    if unknown:
        return [f"{where}: unsupported schema keyword(s): {', '.join(sorted(unknown))}"]
    types = schema.get("type")
    if types is not None:
        wanted = [types] if isinstance(types, str) else list(types)
        actual = _json_type(instance)
        if not (actual in wanted or (actual == "integer" and "number" in wanted)):
            return [f"{where}: expected {'|'.join(wanted)}, got {actual}"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{where}: expected const {_short(schema['const'])}, "
                      f"got {_short(instance)}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{where}: {_short(instance)} not in enum {_short(schema['enum'])}")
    if "anyOf" in schema:
        if not any(not validate_schema(instance, sub, where) for sub in schema["anyOf"]):
            errors.append(f"{where}: matches none of anyOf")
    if isinstance(instance, str):
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{where}: {_short(instance)} does not match /{schema['pattern']}/")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{where}: length {len(instance)} < {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{where}: length {len(instance)} > {schema['maxLength']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{where}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{where}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{where}: {len(instance)} items < {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{where}: {len(instance)} items > {schema['maxItems']}")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(instance):
                errors.extend(validate_schema(item, schema["items"], f"{where}[{i}]"))
    if isinstance(instance, dict):
        for key in schema.get("required") or []:
            if key not in instance:
                errors.append(f"{where}: missing required key {key!r}")
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate_schema(instance[key], sub, f"{where}.{key}"))
        extra = schema.get("additionalProperties", True)
        if extra is not True:
            for key in instance:
                if key in props:
                    continue
                if extra is False:
                    errors.append(f"{where}: unexpected key {key!r}")
                elif isinstance(extra, dict):
                    errors.extend(validate_schema(instance[key], extra, f"{where}.{key}"))
    return errors


# --------------------------------------------------------------------------
# SVG checks

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_NAN = re.compile(r"(?<![A-Za-z_])(?:NaN|-?inf(?:inity)?)(?![A-Za-z_])", re.I)
_GEOMETRY_ATTRS = ("x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
                   "width", "height", "d", "points", "transform", "viewBox")
_TEXT_TAGS = ("text", "tspan", "title", "desc", "textPath")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_svg(svg: str):
    """Parse SVG text; returns ``(root, error)``."""
    try:
        return ET.fromstring(svg.encode("utf-8") if isinstance(svg, str) else svg), None
    except ET.ParseError as exc:
        return None, f"not well-formed XML: {exc}"


def svg_text_runs(root) -> list:
    """Text content of ``<text>``/``<tspan>``/``<title>``/``<desc>`` elements."""
    runs = []
    for el in root.iter():
        if _local(el.tag) in _TEXT_TAGS:
            text = "".join(el.itertext()).strip()
            if text:
                runs.append(text)
    return runs


def _viewbox(root) -> Optional[tuple]:
    vb = root.get("viewBox")
    if vb:
        nums = [float(n) for n in _NUM.findall(vb)]
        if len(nums) == 4:
            return nums[0], nums[1], nums[0] + nums[2], nums[1] + nums[3]
    try:
        w = float(_NUM.findall(root.get("width", ""))[0])
        h = float(_NUM.findall(root.get("height", ""))[0])
        return 0.0, 0.0, w, h
    except (IndexError, ValueError):
        return None


def _points(el) -> list:
    tag = _local(el.tag)
    pts: list = []
    if tag == "path":
        nums = [float(n) for n in _NUM.findall(el.get("d", ""))]
        # Absolute pairs only; relative commands would need a full path walk,
        # so a path using them is measured by its absolute anchor points.
        if re.search(r"[a-z]", re.sub(r"[eE]", "", el.get("d", ""))):
            return []
        pts = list(zip(nums[0::2], nums[1::2]))
    elif tag in ("polygon", "polyline"):
        nums = [float(n) for n in _NUM.findall(el.get("points", ""))]
        pts = list(zip(nums[0::2], nums[1::2]))
    elif tag == "line":
        pts = [(float(el.get("x1", 0)), float(el.get("y1", 0))),
               (float(el.get("x2", 0)), float(el.get("y2", 0)))]
    elif tag in ("circle", "ellipse"):
        cx, cy = float(el.get("cx", 0)), float(el.get("cy", 0))
        rx = float(el.get("rx", el.get("r", 0)) or 0)
        ry = float(el.get("ry", el.get("r", 0)) or 0)
        pts = [(cx - rx, cy - ry), (cx + rx, cy + ry)]
    elif tag in ("rect", "image", "use", "text"):
        try:
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
        except ValueError:
            return []
        w = el.get("width")
        h = el.get("height")
        if tag != "text" and w and h and "%" not in w + h:
            pts = [(x, y), (x + float(w), y + float(h))]
        else:
            pts = [(x, y)]
    return pts


def _has_transform(el, parents: dict) -> bool:
    node = el
    while node is not None:
        if node.get("transform"):
            return True
        node = parents.get(node)
    return False


SVG_CHECKS = ("well_formed", "no_nan", "unique_ids", "element_counts", "text_contains",
              "within_viewbox", "no_script", "script", "max_bytes")


def svg_checks(svg: str, checks: dict) -> list:
    """Run the requested SVG checks; returns ``[{check, ok, detail}]``.

    ``checks`` keys (all optional): ``no_nan`` / ``unique_ids`` /
    ``no_script`` (bool), ``element_counts`` ({tag: n | {min,max}}),
    ``text_contains`` ([str]), ``within_viewbox`` (bool or margin px),
    ``script`` ({"name": "Tamil", "min_ratio": 0.9} or "Tamil"),
    ``max_bytes`` (int). The document is always checked for well-formedness.
    """
    unknown = [k for k in checks if k not in SVG_CHECKS]
    if unknown:
        raise ValueError(f"unknown svg check(s): {', '.join(unknown)}")
    results: list = []
    root, error = parse_svg(svg)
    results.append({"check": "well_formed", "ok": error is None,
                    "detail": error or "parsed"})
    if root is None:
        return results
    elements = list(root.iter())

    if checks.get("max_bytes"):
        size = len(svg.encode("utf-8"))
        results.append({"check": "max_bytes", "ok": size <= int(checks["max_bytes"]),
                        "detail": f"{size} bytes (max {checks['max_bytes']})"})

    if checks.get("no_nan"):
        bad = []
        for el in elements:
            for attr in _GEOMETRY_ATTRS:
                value = el.get(attr)
                if value and _NAN.search(value):
                    bad.append(f"<{_local(el.tag)} {attr}={value[:40]!r}>")
        results.append({"check": "no_nan", "ok": not bad,
                        "detail": (f"{len(bad)} non-finite value(s): {', '.join(bad[:5])}"
                                   if bad else "all geometry finite")})

    if checks.get("unique_ids"):
        seen: dict = {}
        for el in elements:
            if el.get("id"):
                seen[el.get("id")] = seen.get(el.get("id"), 0) + 1
        dups = sorted(k for k, n in seen.items() if n > 1)
        results.append({"check": "unique_ids", "ok": not dups,
                        "detail": (f"duplicate id(s): {', '.join(dups[:10])}" if dups
                                   else f"{len(seen)} unique ids")})

    for tag, rule in (checks.get("element_counts") or {}).items():
        n = sum(1 for el in elements if _local(el.tag) == tag)
        if isinstance(rule, dict):
            lo, hi = rule.get("min"), rule.get("max")
        else:
            lo = hi = int(rule)
        ok = (lo is None or n >= lo) and (hi is None or n <= hi)
        want = (f"={lo}" if lo == hi else
                f"{'>=' + str(lo) if lo is not None else ''}"
                f"{' ' if lo is not None and hi is not None else ''}"
                f"{'<=' + str(hi) if hi is not None else ''}")
        results.append({"check": f"element_counts.{tag}", "ok": ok,
                        "detail": f"{n} <{tag}> (want {want})"})

    if checks.get("text_contains"):
        runs = svg_text_runs(root)
        joined = "\n".join(unicodedata.normalize("NFC", r) for r in runs)
        for needle in checks["text_contains"]:
            norm = unicodedata.normalize("NFC", needle)
            ok = norm in joined
            results.append({"check": "text_contains", "ok": ok,
                            "detail": (f"found {needle!r}" if ok else
                                       f"{needle!r} not in {len(runs)} text run(s)"
                                       + (f": {_short(runs[:4])}" if runs else ""))})

    if checks.get("within_viewbox"):
        margin = (0.0 if checks["within_viewbox"] is True
                  else float(checks["within_viewbox"]))
        vb = _viewbox(root)
        if vb is None:
            results.append({"check": "within_viewbox", "ok": False,
                            "detail": "no viewBox or width/height to measure against"})
        else:
            parents = {child: parent for parent in elements for child in parent}
            outside = []
            measured = 0
            for el in elements:
                if el is root or _has_transform(el, parents):
                    continue
                pts = _points(el)
                if not pts:
                    continue
                measured += 1
                for x, y in pts:
                    if not (math.isfinite(x) and math.isfinite(y)) or \
                            x < vb[0] - margin or x > vb[2] + margin or \
                            y < vb[1] - margin or y > vb[3] + margin:
                        label = el.get("id") or el.get("data-rule") or _local(el.tag)
                        outside.append(f"{label}@({x:g},{y:g})")
                        break
            results.append({"check": "within_viewbox", "ok": not outside,
                            "detail": (f"{len(outside)}/{measured} element(s) outside "
                                       f"{vb} ±{margin:g}: {', '.join(outside[:5])}"
                                       if outside else
                                       f"{measured} element(s) inside {vb} ±{margin:g}")})

    if checks.get("no_script"):
        found = []
        for el in elements:
            if _local(el.tag) in ("script", "foreignObject", "iframe", "embed", "object"):
                found.append(f"<{_local(el.tag)}>")
            for attr, value in el.attrib.items():
                name = _local(attr).lower()
                if name.startswith("on"):
                    found.append(f"{name}=")
                if name == "href" and value.strip().lower().startswith(("javascript:",
                                                                        "data:text/html")):
                    found.append(f"href={value[:30]!r}")
        results.append({"check": "no_script", "ok": not found,
                        "detail": (f"active content: {', '.join(found[:5])}" if found
                                   else "no active content")})

    if checks.get("script"):
        rule = checks["script"]
        name = rule if isinstance(rule, str) else rule.get("name", "")
        min_ratio = 0.9 if isinstance(rule, str) else float(rule.get("min_ratio", 0.9))
        runs = svg_text_runs(root)
        report = script_ratio("".join(runs), name)
        ok = report["letters"] > 0 and report["ratio"] >= min_ratio
        results.append({"check": "script", "ok": ok,
                        "detail": (f"{report['ratio']:.0%} of {report['letters']} letters are "
                                   f"{name} (min {min_ratio:.0%})"
                                   + (f"; others: {report['others']}" if report["others"]
                                      else ""))})
    return results


# --------------------------------------------------------------------------
# Text and script fidelity

# Unicode block ranges for the scripts a pack is likely to assert on. A
# script name not in this table is refused rather than guessed.
SCRIPT_RANGES = {
    "latin": [(0x0041, 0x024F), (0x1E00, 0x1EFF)],
    "tamil": [(0x0B80, 0x0BFF), (0x11FC0, 0x11FFF)],
    "devanagari": [(0x0900, 0x097F), (0xA8E0, 0xA8FF)],
    "bengali": [(0x0980, 0x09FF)],
    "telugu": [(0x0C00, 0x0C7F)],
    "kannada": [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "greek": [(0x0370, 0x03FF), (0x1F00, 0x1FFF)],
    "cyrillic": [(0x0400, 0x04FF)],
    "arabic": [(0x0600, 0x06FF)],
    "han": [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)],
}


def script_of(ch: str) -> str:
    cp = ord(ch)
    for name, ranges in SCRIPT_RANGES.items():
        if any(lo <= cp <= hi for lo, hi in ranges):
            return name
    return "other"


def script_ratio(text: str, script: str) -> dict:
    """Share of letters/marks in ``text`` that belong to ``script``."""
    name = script.lower()
    if name not in SCRIPT_RANGES:
        raise ValueError(f"unknown script {script!r}; known: {', '.join(SCRIPT_RANGES)}")
    letters = [c for c in text if unicodedata.category(c)[0] in ("L", "M")]
    inside = sum(1 for c in letters if script_of(c) == name)
    others: dict = {}
    for c in letters:
        s = script_of(c)
        if s != name:
            others[s] = others.get(s, 0) + 1
    return {"letters": len(letters), "in_script": inside,
            "ratio": (inside / len(letters)) if letters else 0.0, "others": others}


def _marks(text: str) -> list:
    return [c for c in text if unicodedata.category(c) in ("Mn", "Mc", "Me")]


def _base(text: str) -> str:
    return "".join(c for c in text if unicodedata.category(c) not in ("Mn", "Mc", "Me")
                   and not c.isspace())


def text_fidelity(expected: str, actual: str) -> dict:
    """Compare expected text with what a renderer reports it drew.

    Both are NFC-normalised. On a mismatch the report says *how* it differs:
    combining marks dropped (vowel signs such as U+0BC1 TAMIL VOWEL SIGN U),
    word spaces dropped, or letters replaced by another script, which are
    the three ways complex-script text usually breaks.
    """
    exp = unicodedata.normalize("NFC", expected or "")
    act = unicodedata.normalize("NFC", actual or "")
    report: dict = {"expected": exp, "actual": act, "ok": exp == act or exp in act,
                    "dropped_marks": [], "dropped_spaces": 0, "foreign_letters": {}}
    if report["ok"]:
        return report
    exp_marks, act_marks = _marks(exp), _marks(act)
    remaining = list(act_marks)
    for m in exp_marks:
        if m in remaining:
            remaining.remove(m)
        else:
            report["dropped_marks"].append(f"U+{ord(m):04X} {unicodedata.name(m, '?')}")
    report["dropped_spaces"] = max(0, exp.count(" ") - act.count(" "))
    exp_scripts = {script_of(c) for c in exp if unicodedata.category(c)[0] == "L"}
    for c in act:
        if unicodedata.category(c)[0] == "L" and script_of(c) not in exp_scripts:
            s = script_of(c)
            report["foreign_letters"][s] = report["foreign_letters"].get(s, 0) + 1
    report["same_base_letters"] = _base(exp) == _base(act)
    return report


def describe_fidelity(report: dict) -> str:
    if report["ok"]:
        return f"text intact: {report['expected']!r}"
    parts = [f"expected {report['expected']!r}, got {report['actual']!r}"]
    if report["dropped_marks"]:
        parts.append(f"dropped {len(report['dropped_marks'])} mark(s): "
                     + ", ".join(report["dropped_marks"][:6]))
    if report["dropped_spaces"]:
        parts.append(f"dropped {report['dropped_spaces']} word space(s)")
    if report["foreign_letters"]:
        parts.append("letters from other scripts: "
                     + ", ".join(f"{k}={v}" for k, v in report["foreign_letters"].items()))
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Prompt coverage and hashing


def tokenize(text: str) -> list:
    """Whitespace/punctuation tokens, keeping combining marks inside words."""
    return [t for t in re.split(r"[\s,.;:!?\"'()\[\]{}|/]+", text or "") if t]


def prompt_coverage(query: str, unmatched: Iterable[str],
                    total_tokens: Optional[int] = None) -> dict:
    """Share of prompt tokens a compiler understood.

    ``unmatched`` is what the compiler reported it could not map. The
    denominator is the tokenised query unless the caller supplies its own.
    """
    tokens = tokenize(query)
    unmatched = [u for u in (unmatched or []) if u]
    total = total_tokens if total_tokens is not None else len(tokens)
    total = max(total, len(unmatched))
    coverage = 1.0 - (len(unmatched) / total) if total else 0.0
    return {"tokens": total, "unmatched": unmatched, "coverage": coverage}


def stable_hash(payload: Any, *, path: str = "", ignore: Iterable[str] = ()) -> str:
    """SHA-256 of a payload, optionally narrowed to a path and minus volatile keys.

    ``ignore`` names keys dropped at any depth (timings such as
    ``render_ms``), so a determinism check compares content, not latency.
    """
    if path:
        payload = resolve_path(payload, path)
        if payload is _MISSING:
            payload = None
    drop = set(ignore or ())

    def scrub(node):
        if isinstance(node, dict):
            return {k: scrub(v) for k, v in node.items() if k not in drop}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return node

    if isinstance(payload, (bytes, bytearray)):
        data = bytes(payload)
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(scrub(payload), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()
