# AGENTS.md — whatdotheyknow-mcp

AI agent instructions for working in this repo. See `/home/bch/dev/ops/OPS.md` for credentials, fleet overview, and release tooling.

## Repo shape

Single `server.py`. Tools for searching FOI requests, authorities, and drafting WhatDoTheyKnow submissions.
Not on PyPI — deployed to Fly only.

## Deploy

```bash
fly deploy --ha=false
```

Single instance, lhr region. App name: `whatdotheyknow-mcp`. Fly.io account: articat1066@gmail.com.

## Version bump

1. Update `version` in `pyproject.toml`
2. Update version string in the `smithery_server_card` route in `server.py`
3. Commit and push (no PyPI release needed)
4. `fly deploy --ha=false`
5. Cut a new Glama release

## Standard routes (must always be present)

- `/.well-known/mcp/server-card.json` — Smithery metadata
- `/.well-known/glama.json` — Glama maintainer claim
- `/health` — Fly health check

Verify after deploy:
```bash
curl https://whatdotheyknow-mcp.fly.dev/.well-known/mcp/server-card.json
curl https://whatdotheyknow-mcp.fly.dev/.well-known/glama.json
curl https://whatdotheyknow-mcp.fly.dev/health
```

## README badge order

```
SafeSkill → Glama card → Smithery
```
(No PyPI badge — not published to PyPI)

## Do not

- Do not use `FASTMCP_PORT` — the server reads `PORT` env var only
- Do not set `internal_port` in fly.toml to anything other than 8080
- Do not commit API keys — all secrets are in Fly secrets (`fly secrets list`)
