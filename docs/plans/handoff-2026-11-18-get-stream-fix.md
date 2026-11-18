---
status: landed
repo: whatdotheyknow-mcp
date: 2026-11-18
supersedes: none
sessions: one Claude Code session, 2026-11-18 (fleet-wide _HttpGuard removal)
---

# Handoff: GET /mcp returns 405 (0.1.5)

## Purpose and scope

Stop held-open `GET /mcp` streams from tying up connection slots, the same fix
govuk-mcp shipped in 0.3.2. Shape A of the fleet handoff template
([uk-due-diligence-mcp/docs/plans/TEMPLATE-handoff.md](https://github.com/paulieb89/uk-due-diligence-mcp/blob/main/docs/plans/TEMPLATE-handoff.md)).
Changes: [CHANGELOG 0.1.5](../../CHANGELOG.md), PRs #7 (fix) and #8 (release).

## What shipped

- **0.1.5, tag `v0.1.5` → `e20c0d2`**, released 2026-11-18 ~19:46 UTC via
  `release.yml`. Rollback image recorded before release: Fly v20,
  `registry.fly.io/whatdotheyknow-mcp:deployment-01KXQTHMFTYZ5AYMX7ZFBVGFM5`
  (the hand-deployed 0.1.4).
- `_HttpGuard` removed; app construction moved into `create_app()`; added
  `tests/test_mcp_get_405.py` and a `dev` dependency group with pytest (the repo
  had none). With the guard in place, the GET test hangs on the held stream.
- Checks:
  - prod `GET /mcp` → 405 in 0.06 s;
  - the server card, PyPI and Fly v21 all show 0.1.5;
  - prod still runs fastmcp 3.4.4 / mcp 1.28.1;
  - 12 tools listed, and `search_request_events` works;
  - `fly_app_concurrency`: 35–57 over 24 h and 41 just before release; no data
    after release, while serving 93 responses in 30 minutes;
  - three held test connections read exactly 3, and the metric drops back to no
    data about 5.5 min after they close.

## Root cause

`_HttpGuard` answered `GET /mcp` with 200 and held an empty event stream open.
Clients (mostly Claude Code, across the fleet) keep that optional stream, so each
one held a connection slot. Same cause as
[govuk-mcp #7](https://github.com/paulieb89/govuk-mcp/issues/7). It was not
uk-business-mcp's proxy.

## Decisions

1. **0.1.5, not 0.1.4.** 0.1.4 had been deployed to prod by hand on 2026-07-17,
   with no tag and no PyPI release. Reusing the number would have meant two
   different builds with the same version. PyPI therefore goes 0.1.3 → 0.1.5 and
   now carries the 0.1.4 changes that were already live (content tools, 404
   status surfacing, clientInfo logging).
2. **The Dockerfile is pinned to prod's versions.** It pip-installs an unpinned
   `fastmcp>=3.2.4`, ignoring `uv.lock`, so a rebuild would have pulled FastMCP
   4.x into this release. It is now pinned to exactly what prod ran (read with
   `pip list` on the Fly machine).

## Gotchas

- `pyproject.toml` requires Python ≥ 3.13, but the image is `python:3.12-slim`
  and prod runs 3.12. The local `.venv` is 3.12, so `uv lock` needs
  `--python 3.13`. Tests were run in an isolated 3.12 env with prod's exact
  versions.
- The Dockerfile ignores `uv.lock`, so the lockfile and the image can disagree.
- Fly's `fly_app_concurrency` omits the series at zero and keeps the last
  non-zero value for ~5 min.

## Open issues

[#1](https://github.com/paulieb89/whatdotheyknow-mcp/issues/1) (expose full
content and user feed). Fleet:
[mcpfleet-obs #11](https://github.com/uk-agents/mcpfleet-obs/issues/11) (shared
transport layer).

## Recommended next step

Build the image from `uv.lock` (as DD and uk-business-mcp do) and settle the
Python version (3.12 vs ≥3.13). Then join the fleet's FastMCP 4.1.8 move
(uk-due-diligence-mcp #49, uk-legal-mcp #116, govuk-mcp #11).

## Re-verify

```bash
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' -H 'Accept: text/event-stream' https://whatdotheyknow-mcp.fly.dev/mcp   # 405
curl -s https://whatdotheyknow-mcp.fly.dev/.well-known/mcp/server-card.json
# Fly Prometheus (Authorization: FlyV1 <fly auth token>): fly_app_concurrency{app="whatdotheyknow-mcp"}
```
