"""Minimal HTTP client for driving the jambu engine against a live app.

Kept in the repo (not a scratch file) because these probes are the evidence for
any change to the agent's capabilities: each one asserts on something the app
actually renders.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class Engine:
    def __init__(self, base="http://127.0.0.1:8002", timeout=180):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _call(self, method, path, body=None, params=None):
        url = self.base + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"raw": raw}
            payload["_http"] = e.code
            return payload

    def get(self, path, **params):
        return self._call("GET", path, params=params or None)

    def post(self, path, body=None):
        return self._call("POST", path, body or {})

    def open_session(self, allow_domains=None, **kw):
        return self.post("/browser/sessions", {
            "allow_domains": allow_domains or ["127.0.0.1"],
            "allow_private": True, **kw,
        })

    def navigate(self, sid, url):
        return self.post(f"/browser/sessions/{sid}/navigate", {"url": url})

    def snapshot(self, sid, **params):
        return self.get(f"/browser/sessions/{sid}/snapshot", **params)

    def run(self, sid, steps, **kw):
        return self.post(f"/browser/sessions/{sid}/run", {"steps": steps, **kw})

    def test(self, url, steps, **kw):
        return self.post("/browser/sessions/run", {"url": url, "steps": steps,
                                                  "local": True, **kw})

    def close(self, sid):
        return self.get(f"/browser/sessions/{sid}") and self.post(
            f"/browser/sessions/{sid}/close")

    def shutdown(self, sid):
        return self.post(f"/browser/sessions/{sid}/close")


def summarize(report, label=""):
    """One line per step: the digest a human reads first."""
    print(f"--- {label} ok={report.get('ok')} "
          f"{report.get('passed')}/{report.get('total')} "
          f"{report.get('duration_ms')}ms")
    for s in report.get("steps") or []:
        mark = "ok  " if s.get("status") == "passed" else "FAIL"
        line = f"  {mark} #{s.get('i')} {s.get('action')}"
        if s.get("detail"):
            line += f" — {s['detail']}"
        if s.get("status") != "passed":
            line += f" — {s.get('reason')}: {s.get('error')}"
            cause = s.get("failure_cause") or {}
            if cause.get("likely_cause"):
                line += f"\n         why: {cause['likely_cause']}"
        print(line)
    for e in (report.get("console_errors") or [])[:4]:
        print("  console:", str(e)[:150])
    for ev in (report.get("evaluated") or []):
        print(f"  evaluated #{ev.get('i')}: {str(ev.get('value'))[:150]}")
    return report
