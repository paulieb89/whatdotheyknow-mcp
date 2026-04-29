# whatdotheyknow-mcp

<!-- mcp-name: io.github.paulieb89/whatdotheyknow-mcp -->

A Model Context Protocol server for UK Freedom of Information research. Connects AI assistants to [WhatDoTheyKnow](https://www.whatdotheyknow.com/) — the UK's largest FOI request platform — to search requests, read responses, look up public authorities, and draft new requests.

## Tools

| Tool | Description |
|------|-------------|
| `search_request_events` | Full-text search of FOI requests and responses via WhatDoTheyKnow's Atom feed. Supports structured expressions (`status:successful`, `body:"Liverpool City Council"`). |
| `search_authorities` | Search UK public authorities by name. Returns slug for use with other tools. |
| `get_request_feed_items` | Fetch the event timeline (sent, response, clarification) for a specific FOI request. |
| `build_request_url` | Build a prefilled WhatDoTheyKnow request URL for a given authority and topic. |
| `create_request_record` | Create a request via the write API (requires `WDTK_API_KEY`). |
| `update_request_state` | Update user-assessed state of a request (requires `WDTK_API_KEY`). |

## Resources

| URI template | Returns |
|---|---|
| `wdtk://authorities/{authority_slug}` | Authority profile JSON |
| `wdtk://requests/{request_slug}` | FOI request detail JSON |
| `wdtk://users/{user_slug}` | User profile JSON |
| `wdtk://requests/{request_slug}/feed` | Request event Atom feed |
| `wdtk://users/{user_slug}/feed` | User activity Atom feed |
| `wdtk://authorities/all.csv` | Full CSV of all UK public authorities |

## Prompts

| Prompt | Description |
|--------|-------------|
| `draft_foi_request` | Draft a narrow, specific FOI request for a given authority and topic. |

## Quickstart

### Run locally

```bash
pip install fastmcp httpx pydantic
fastmcp run server.py
```

Inspect with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WDTK_API_KEY` | Optional | Enables `create_request_record` and `update_request_state` write tools |

## Upstream API and Licence

| Source | API | Licence | Auth |
|--------|-----|---------|------|
| WhatDoTheyKnow | `www.whatdotheyknow.com` | [OGL v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | None (read) / API key (write) |

Data is sourced directly from the WhatDoTheyKnow public API. The platform is operated by mySociety.
