# whatdotheyknow-mcp

[![PyPI](https://img.shields.io/pypi/v/whatdotheyknow-mcp)](https://pypi.org/project/whatdotheyknow-mcp/)

A Model Context Protocol server for UK Freedom of Information research. Connects AI assistants to [WhatDoTheyKnow](https://www.whatdotheyknow.com/) — the UK's largest FOI request platform — to search requests, read responses, look up public authorities, and draft new requests.

> **Fork note:** This fork adds six new tools that expose Resources and scrape HTML correspondence:
> - `get_request_detail`, `get_user_requests`, `get_authority_detail` expose existing MCP Resources as callable tools (essential for Claude.ai connectors)
> - `get_request_v2`, `get_request_messages` attempt v2 API endpoints (may not be available on all Alaveteli instances)
> - `get_request_correspondence` scrapes and parses actual message bodies from HTML pages using browser impersonation
> 
> This is essential for MCP clients that can only invoke tools, not access Resources directly. Upstream: [paulieb89/whatdotheyknow-mcp](https://github.com/paulieb89/whatdotheyknow-mcp)

## Tools

| Tool | Description |
| --- | --- |
| `search_request_events` | Full-text search of FOI requests and responses via WhatDoTheyKnow's Atom feed. Supports structured expressions (`status:successful`, `body:"Liverpool City Council"`). |
| `search_authorities` | Search UK public authorities by name. Returns slug for use with other tools. |
| `get_request_feed_items` | Fetch the event timeline (sent, response, clarification) for a specific FOI request. |
| `get_request_detail` | **NEW** — Fetch the full JSON detail for an FOI request, including all correspondence text, status, dates, and attachments. |
| `get_request_v2` | **NEW** — Attempt to fetch full request via Alaveteli v2 API (may not be available on all instances). |
| `get_request_messages` | **NEW** — Attempt to fetch dedicated message list via v2 API (may not be available on all instances). |
| `get_request_correspondence` | **NEW** — Scrape and parse actual message bodies from HTML request pages using browser impersonation + BeautifulSoup. This provides the actual text that the JSON API omits. |
| `get_user_requests` | **NEW** — Fetch a user's profile and list of all their FOI requests with titles, slugs, statuses, and authorities. |
| `get_authority_detail` | **NEW** — Fetch full detail for a public authority, including contact info, description, and recent requests. |
| `build_request_url` | Build a prefilled WhatDoTheyKnow request URL for a given authority and topic. |
| `create_request_record` | Create a request via the write API (requires `WDTK_API_KEY`). |
| `update_request_state` | Update user-assessed state of a request (requires `WDTK_API_KEY`). |

## Resources

| URI template | Returns |
| --- | --- |
| `wdtk://authorities/{authority_slug}` | Authority profile JSON |
| `wdtk://requests/{request_slug}` | FOI request detail JSON |
| `wdtk://users/{user_slug}` | User profile JSON |
| `wdtk://requests/{request_slug}/feed` | Request event Atom feed |
| `wdtk://users/{user_slug}/feed` | User activity Atom feed |
| `wdtk://authorities/all.csv` | Full CSV of all UK public authorities |

## Prompts

| Prompt | Description |
| --- | --- |
| `draft_foi_request` | Draft a narrow, specific FOI request for a given authority and topic. |

## What's new in this fork

The upstream MCP server defines Resources for reading request detail, user profiles, and authority info — but many MCP clients (including Claude.ai's connector system) can only call **tools**, not access Resources. This fork adds three tools that wrap those Resources:

### `get_request_detail(request_slug)`

Returns the complete JSON for an FOI request, including:
- Original request text and all response correspondence
- Current status and described state
- Dates (created, updated)
- Public body info
- Full `info_request_events` array with every message

**Example:** `get_request_detail("payroll_system_synchronization_f_2")`

### `get_user_requests(user_slug)`

Returns a user's profile and all their FOI requests, including:
- Display name and about text
- Array of `info_requests` with titles, slugs, statuses, and authorities

**Example:** `get_user_requests("g_spinks")`

### `get_authority_detail(authority_slug)`

Returns full authority profile, including:
- Name, description, home page
- Request email and tag string
- Recent requests made to this authority

**Example:** `get_authority_detail("essex_police")`

### `get_request_v2(request_id)` & `get_request_messages(request_id)`

Attempt to fetch request data via the Alaveteli v2 API. These endpoints may not be available on all Alaveteli instances (including WhatDoTheyKnow). The `request_id` is the numeric ID found in v1 API responses.

**Caveat:** WhatDoTheyKnow currently returns errors on these endpoints (they may be admin-only or not implemented on this instance).

### `get_request_correspondence(request_slug)` ⭐

Scrapes and parses the actual message bodies from the HTML request page. This **solves the original problem** — the JSON API truncates or omits message text, but this tool fetches the HTML page using browser impersonation (curl-cffi with Safari 17 user agent) and parses it with BeautifulSoup to extract the complete correspondence.

Returns:
- `title`: request title
- `status`: current status
- `messages`: list of message objects with `type`, `date`, `from`, and `body`
- `url`: link to the original request
- `note`: clarification that this is HTML-scraped data

**Example:** `get_request_correspondence("payroll_system_synchronization_f_2")`

**Note:** This tool performs real HTTP requests and HTML parsing, so it's slightly slower than JSON API calls. It respects rate limits and uses browser impersonation to avoid blocks.

## Connect

### Hosted (no install)

```json
{
  "mcpServers": {
    "whatdotheyknow": {
      "type": "http",
      "url": "https://whatdotheyknow-mcp.fly.dev/mcp"
    }
  }
}
```

### Local (uvx)

```json
{
  "mcpServers": {
    "whatdotheyknow": {
      "type": "stdio",
      "command": "uvx",
      "args": ["whatdotheyknow-mcp"]
    }
  }
}
```

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| `WDTK_API_KEY` | Optional | Enables `create_request_record` and `update_request_state` write tools |

## Upstream API and Licence

| Source | API | Licence | Auth |
| --- | --- | --- | --- |
| WhatDoTheyKnow | `www.whatdotheyknow.com` | [OGL v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | None (read) / API key (write) |

Data is sourced directly from the WhatDoTheyKnow public API. The platform is operated by mySociety.
