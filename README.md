# whatdotheyknow-mcp

<!-- mcp-name: io.github.paulieb89/whatdotheyknow-mcp -->

[![Glama](https://img.shields.io/badge/Glama-listed-orange?style=flat-square)](https://glama.ai/mcp/servers/paulieb89/whatdotheyknow-mcp)
[![smithery badge](https://smithery.ai/badge/bouch/whatdotheyknow)](https://smithery.ai/servers/bouch/whatdotheyknow)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Server-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=whatdotheyknow&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fwhatdotheyknow-mcp.fly.dev%2Fmcp%22%7D)
[![Install in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install_Server-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=whatdotheyknow&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fwhatdotheyknow-mcp.fly.dev%2Fmcp%22%7D&quality=insiders)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_Server-000000?style=flat-square&logoColor=white)](https://cursor.com/en/install-mcp?name=whatdotheyknow&config=eyJ0eXBlIjoiaHR0cCIsInVybCI6Imh0dHBzOi8vd2hhdGRvdGhleWtub3ctbWNwLmZseS5kZXYvbWNwIn0=)
[![Install in VS Code (local)](https://img.shields.io/badge/VS_Code-Install_Local-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=whatdotheyknow&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22whatdotheyknow-mcp%22%5D%7D)

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
|----------|----------|-------------|
| `WDTK_API_KEY` | Optional | Enables `create_request_record` and `update_request_state` write tools |

## Upstream API and Licence

| Source | API | Licence | Auth |
|--------|-----|---------|------|
| WhatDoTheyKnow | `www.whatdotheyknow.com` | [OGL v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | None (read) / API key (write) |

Data is sourced directly from the WhatDoTheyKnow public API. The platform is operated by mySociety.
