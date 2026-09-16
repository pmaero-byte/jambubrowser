"""
JS-faithful JSON serialization.

DCM hashes receipts with ``crypto.createHash('sha256').update(JSON.stringify(entry))``.
To verify a DCM chain independently we must reproduce ``JSON.stringify``
byte-for-byte, and that differs from Python's ``json.dumps`` in two ways
that actually bite here:

1. **Number formatting.** JS prints ``0.00001`` where Python prints
   ``1e-05`` — and DCM's inference rates are exactly ``1e-5``/``5e-5`` DCT
   per token. This module implements ECMAScript's Number→String rules
   (shortest round-trip digits, decimal notation for 1e-6 ≤ |x| < 1e21,
   exponential with a signed, unpadded exponent otherwise, ``0`` for -0,
   ``null`` for NaN/Infinity).
2. **Key order.** ``JSON.stringify`` preserves insertion order; Python
   dicts do too, and ``json.loads`` preserves the order of the wire JSON,
   so callers just pass the parsed entry through unchanged.

String escaping matches: both output raw non-ASCII, escape ``"`` / ``\\``
and control characters the same way.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any


def js_number(value: float | int) -> str:
    """Format a number the way ECMAScript's Number::toString does."""
    if isinstance(value, bool):  # bools are not numbers here
        raise TypeError("js_number() does not accept bool")
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return "null"  # JSON.stringify(NaN) === "null"
    if value == 0:
        return "0"  # also covers -0.0

    d = Decimal(repr(value))  # repr() = shortest round-trip digits
    sign = "-" if d < 0 else ""
    d = abs(d)
    exponent = d.adjusted()  # position of the first significant digit
    digits = "".join(str(digit) for digit in d.as_tuple().digits).rstrip("0")
    if not digits:
        return "0"

    if -7 < exponent < 21:
        if exponent >= len(digits) - 1:
            # Integer with trailing zeros: 1e20 -> "100000000000000000000"
            return sign + digits + "0" * (exponent - len(digits) + 1)
        if exponent >= 0:
            return sign + digits[: exponent + 1] + "." + digits[exponent + 1:]
        return sign + "0." + "0" * (-exponent - 1) + digits

    mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    exp_sign = "+" if exponent >= 0 else "-"
    return f"{sign}{mantissa}e{exp_sign}{abs(exponent)}"


def _js_string(value: str) -> str:
    # Python's json string escaping matches JSON.stringify for the
    # characters that matter (no ASCII-escaping of non-ASCII).
    return json.dumps(value, ensure_ascii=False)


def js_dumps(value: Any) -> str:
    """Serialize a JSON-compatible value exactly like ``JSON.stringify``."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return js_number(value)
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(js_dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{_js_string(str(key))}:{js_dumps(item)}"
            for key, item in value.items()
        ) + "}"
    raise TypeError(f"js_dumps: unsupported type {type(value).__name__}")
