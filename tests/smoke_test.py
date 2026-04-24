"""WhatDoTheyKnow MCP — smoke test with token sizing.

Probes every tool against the local server and measures response size.
Catches context-explosion regressions and verifies the server is wired up
correctly after changes.

Usage:
    # Start the server first:
    uv run server.py &

    # Then run the smoke test:
    uv run --with tiktoken python tests/smoke_test.py
    uv run --with tiktoken python tests/smoke_test.py --json out.json
    uv run --with tiktoken python tests/smoke_test.py --url https://your-server/mcp

Token budgets:
    Soft warn : 5,000  (flag for review)
    Hard fail : 20,000 (context explosion — must fix)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field

from fastmcp import Client

SOFT = 5_000
HARD = 20_000
DEFAULT_URL = "http://127.0.0.1:9000/mcp"


@dataclass
class Probe:
    tool: str
    args: dict
    soft: int = SOFT
    hard: int = HARD
    note: str = ""


PROBES: list[Probe] = [
    # ─── build_request_url ────────────────────────────────────────────────────
    # Pure compute — no network. Should be tiny.
    Probe(
        tool="build_request_url",
        args={"authority_slug": "liverpool_city_council", "title": "CCTV camera locations"},
        soft=200,
        hard=500,
        note="URL builder — pure compute, no upstream call",
    ),
    # ─── search_authorities ───────────────────────────────────────────────────
    # Fetches all-authorities.csv then filters. The key regression to catch:
    # returning the whole CSV instead of the filtered slice.
    Probe(
        tool="search_authorities",
        args={"query": "Liverpool", "limit": 10},
        soft=1_000,
        hard=3_000,
        note="Authority search — 10-result slice of CSV",
    ),
    Probe(
        tool="search_authorities",
        args={"query": "council", "limit": 50},
        soft=5_000,
        hard=15_000,
        note="Broad authority search — 50-result slice; many matches expected",
    ),
    # ─── search_request_events ────────────────────────────────────────────────
    Probe(
        tool="search_request_events",
        args={"search_expression": "status:successful", "limit": 20},
        note="Feed search — successful requests, 20 entries",
    ),
    Probe(
        tool="search_request_events",
        args={"search_expression": 'body:"Liverpool City Council"', "limit": 10},
        note="Feed search — authority-scoped, 10 entries",
    ),
    # ─── get_request_feed_items ───────────────────────────────────────────────
    # Uses a real WDTK slug (high-profile long-running request).
    Probe(
        tool="get_request_feed_items",
        args={"request_slug": "a_request_about_facial_recogni", "limit": 20},
        note="Feed items — known active request; error expected if slug stale",
    ),
    # ─── read_resource (via ResourcesAsTools) ────────────────────────────────
    Probe(
        tool="read_resource",
        args={"uri": "wdtk://authorities/liverpool_city_council"},
        soft=2_000,
        hard=8_000,
        note="Authority JSON — Liverpool City Council",
    ),
    Probe(
        tool="read_resource",
        args={"uri": "wdtk://users/julian_todd/feed"},
        soft=3_000,
        hard=10_000,
        note="User Atom feed — prolific WDTK contributor",
    ),
    # THE regression canary: all.csv should be large but not infinite.
    # If someone accidentally pipes the whole CSV into a tool result this blows up.
    Probe(
        tool="read_resource",
        args={"uri": "wdtk://authorities/all.csv"},
        soft=15_000,
        hard=80_000,
        note="⚠ Full authority CSV — expected large; documents baseline size",
    ),
]


# Token estimation
try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def estimate_tokens(text: str) -> int:
        return len(_enc.encode(text, disallowed_special=()))
except ImportError:
    def estimate_tokens(text: str) -> int:
        return len(text) // 4


@dataclass
class Result:
    probe: Probe
    bytes_: int = 0
    tokens: int = 0
    duration_ms: float = 0.0
    status: str = "?"       # ok / warn / fail / error
    detail: str = ""
    response_repr: str = field(default="", repr=False)


def _flatten(content_list) -> str:
    parts = []
    for c in content_list:
        if hasattr(c, "text") and c.text:
            parts.append(c.text)
        elif hasattr(c, "data"):
            parts.append(json.dumps(c.data, default=str) if not isinstance(c.data, str) else c.data)
        else:
            parts.append(repr(c))
    return "\n".join(parts)


async def run_probe(url: str, probe: Probe) -> Result:
    res = Result(probe=probe)
    t0 = time.perf_counter()
    try:
        async with Client(url) as client:
            call_result = await client.call_tool(probe.tool, probe.args)
            text = _flatten(call_result.content)
            res.bytes_ = len(text.encode("utf-8"))
            res.tokens = estimate_tokens(text)
            res.response_repr = text[:300]

        if res.tokens >= probe.hard:
            res.status = "fail"
            res.detail = f"{res.tokens:,} ≥ hard {probe.hard:,}"
        elif res.tokens >= probe.soft:
            res.status = "warn"
            res.detail = f"{res.tokens:,} ≥ soft {probe.soft:,}"
        else:
            res.status = "ok"
            res.detail = f"{res.tokens:,} tokens"
    except Exception as e:
        res.status = "error"
        res.detail = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        res.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    return res


async def main() -> int:
    parser = argparse.ArgumentParser(description="WhatDoTheyKnow MCP smoke test")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP server URL (default: {DEFAULT_URL})")
    parser.add_argument("--json", help="Write JSON results to this path")
    args = parser.parse_args()

    print(f"Probing {args.url} with {len(PROBES)} probes...\n")
    results = await asyncio.gather(*(run_probe(args.url, p) for p in PROBES))

    width_tool = max(len(r.probe.tool) for r in results)
    print(
        f"{'STATUS':<6}  {'TOOL':<{width_tool}}  {'BYTES':>9}  {'TOKENS':>8}  {'MS':>6}  DETAIL"
    )
    print("─" * (width_tool + 55))
    for r in results:
        icon = {"ok": " ok ", "warn": "WARN", "fail": "FAIL", "error": " err"}[r.status]
        print(
            f"{icon:<6}  {r.probe.tool:<{width_tool}}  "
            f"{r.bytes_:>9,}  {r.tokens:>8,}  {r.duration_ms:>6.0f}  {r.detail}"
        )
        if r.probe.note:
            print(f"{'':6}  {'':>{width_tool}}  {'':>9}  {'':>8}  {'':>6}  ↳ {r.probe.note}")

    fails = [r for r in results if r.status == "fail"]
    errors = [r for r in results if r.status == "error"]
    warns = [r for r in results if r.status == "warn"]
    oks = [r for r in results if r.status == "ok"]

    print()
    print(f"Summary: {len(results)} probes — {len(oks)} ok, {len(warns)} warn, {len(fails)} fail, {len(errors)} error")

    if fails:
        print("\nFAILURES:")
        for r in fails:
            print(f"  {r.probe.tool}: {r.detail}")
            print(f"  Preview: {r.response_repr[:200]}")

    if args.json:
        payload = [
            {
                "tool": r.probe.tool,
                "args": r.probe.args,
                "note": r.probe.note,
                "bytes": r.bytes_,
                "tokens": r.tokens,
                "duration_ms": r.duration_ms,
                "status": r.status,
                "detail": r.detail,
                "response_preview": r.response_repr,
            }
            for r in results
        ]
        with open(args.json, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote {args.json}")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
