# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An MCP server exposing [WhatDoTheyKnow](https://www.whatdotheyknow.com) FOI request data to Claude and other MCP clients. Built with FastMCP on Python 3.13+.

## Commands

This project uses `uv` for dependency management.

```bash
# Install dependencies
uv sync

# Run the server (listens on http://127.0.0.1:9000)
uv run server.py

# Type checking
uv run mypy server.py

# Add a dependency
uv add <package>
```

## Architecture

All logic lives in [server.py](server.py). `main.py` is an unused stub.

**`WDTKClient`** — thin `httpx.AsyncClient` wrapper around `https://www.whatdotheyknow.com`. Write operations require `WDTK_API_KEY` env var and POST via `multipart/form-data`.

**Resources** (`wdtk://...`) — read-only data mapped to WhatDoTheyKnow REST endpoints (authorities, requests, users, feeds, CSV). Exposed as tools via `ResourcesAsTools` transform.

**Tools** — `build_request_url` and `search_request_events` are public. `create_request_record` and `update_request_state` are tagged `"write"` and can be disabled at startup.

**Prompt** — `draft_foi_request` generates a system prompt guiding narrow, specific FOI request drafting.

**Write API toggle** — uncomment `mcp.disable(tags={"write"})` in `server.py` to run in read-only mode.
