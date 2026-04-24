# WhatDoTheyKnow MCP — Audit TODO

Tracked from canonical-fitness audit 2026-04-23.

## Fixes

- [x] Add `destructiveHint=True` to `create_request_record` and `update_request_state`
- [x] Improve `all_authorities_csv` description (warn LLM of size, suggest alternatives)
- [x] Improve `get_request_feed_items` docstring (explain why it exists alongside raw feed resource)
- [x] Add next-step hint to `search_request_events` docstring
- [x] Add `search_authorities(query, limit)` tool — bounded authority lookup

## Deferred

- [ ] Add `ResponseCachingMiddleware` for read-only tools and resources

## Will Not Fix

- `CurrentContext()` injection — docs confirm both `ctx: Context` and
  `ctx: Context = CurrentContext()` are valid; explicit form is intentional here.
- `-> str` + `json.dumps()` in resources — correct pattern per fastmcp docs;
  anti-pattern only applies to tools (all tools already return Pydantic/dict).
