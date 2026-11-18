# Changelog

All notable changes to `whatdotheyknow-mcp` are documented here, starting with 0.1.5. Earlier releases are described in their GitHub release notes.

## [0.1.5] — 2026-11-18

### Fixed

- `GET /mcp` returns 405 instead of a held-open empty event stream. Clients kept those streams open, which held production at 35–57 open connections (#6).

### Also first published to PyPI in this release

0.1.4 was deployed to production without a PyPI release or tag, so PyPI goes from 0.1.3 to 0.1.5. These changes were already running in production: the WhatDoTheyKnow content tools, HTTP status codes surfaced on 404 from `get_request_detail` and `get_authority_detail`, and clientInfo logging on initialize.
