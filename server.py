from __future__ import annotations

import functools
import html
import json
import os
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

import httpx
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from prometheus_client import CONTENT_TYPE_LATEST, Counter as PromCounter, Histogram, generate_latest
from pydantic import BaseModel, Field, HttpUrl
from starlette.responses import JSONResponse, Response

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.transforms import PromptsAsTools
from mcp.types import ToolAnnotations


BASE_URL = "https://www.whatdotheyknow.com"

TRANSPORT = os.getenv("FASTMCP_TRANSPORT", "http")
REGION = os.getenv("FLY_REGION", "local")
INVALID_XML_CHARS_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]"
)

tool_calls_total = PromCounter(
    "whatdotheyknow_tool_calls_total",
    "Count of MCP tool invocations.",
    labelnames=["tool", "transport", "region", "status"],
)
tool_duration_seconds = Histogram(
    "whatdotheyknow_tool_duration_seconds",
    "Tool invocation latency in seconds.",
    labelnames=["tool", "transport", "region"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def _timed_tool(fn):
    tool_name = fn.__name__

    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
            tool_calls_total.labels(tool_name, TRANSPORT, REGION, "ok").inc()
            return result
        except BaseException:
            tool_calls_total.labels(tool_name, TRANSPORT, REGION, "error").inc()
            raise
        finally:
            tool_duration_seconds.labels(tool_name, TRANSPORT, REGION).observe(
                time.perf_counter() - t0
            )

    return wrapped


class NewRequestLink(BaseModel):
    authority_slug: str
    url: HttpUrl


class AtomEntry(BaseModel):
    id: str | None = None
    title: str | None = None
    link: str | None = None
    published: str | None = None
    updated: str | None = None
    summary: str | None = None
    content: str | None = None
    content_html: str | None = None


class UserRequestResult(BaseModel):
    title: str | None = None
    request_slug: str | None = None
    url: str | None = None
    status: str | None = None
    authority_name: str | None = None
    authority_slug: str | None = None
    updated: str | None = None
    event: str | None = None
    snippet: str | None = None


class CreateRequestPayload(BaseModel):
    title: str
    body: str
    external_user_name: str
    external_url: HttpUrl


class AddCorrespondencePayload(BaseModel):
    direction: str = Field(pattern="^(request|response)$")
    body: str
    sent_at: str
    state: str | None = Field(
        default=None,
        pattern="^(waiting_response|rejected|successful|partially_successful)$",
    )


class UpdateRequestStatePayload(BaseModel):
    state: str = Field(pattern="^(waiting_response|rejected|successful|partially_successful)$")


class AuthorityResult(BaseModel):
    name: str
    short_name: str
    slug: str
    tags: str | None = None


class WDTKClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_json(self, path: str) -> dict[str, Any]:
        async with AsyncSession(impersonate="safari17_0") as client:
            response = await client.get(
                f"{self.base_url}{path}",
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()

    async def get_text(self, path: str, accept: str | None = None) -> str:
        async with AsyncSession(impersonate="safari17_0") as client:
            headers = {"Accept": accept} if accept else {}
            response = await client.get(
                f"{self.base_url}{path}",
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text

    async def post_form_json(
        self,
        path: str,
        *,
        api_key: str,
        json_payload: dict[str, Any],
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> dict[str, Any]:
        data = {"json": json.dumps(json_payload)}
        params = {"k": api_key}

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post(path, params=params, data=data, files=files)
            response.raise_for_status()
            return response.json()


def parse_atom(xml_text: str) -> list[AtomEntry]:
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    xml_text = INVALID_XML_CHARS_RE.sub("", xml_text)
    root = ET.fromstring(xml_text)
    entries: list[AtomEntry] = []

    for entry in root.findall("atom:entry", ns):
        link_el = entry.find("atom:link", ns)
        summary_el = entry.find("atom:summary", ns)
        content_el = entry.find("atom:content", ns)
        summary = _xml_element_text(summary_el)
        content_html = _xml_element_text(content_el)
        content = html_to_text(content_html) if content_html else None
        entries.append(
            AtomEntry(
                id=(entry.findtext("atom:id", default=None, namespaces=ns)),
                title=(entry.findtext("atom:title", default=None, namespaces=ns)),
                link=(link_el.get("href") if link_el is not None else None),
                published=(entry.findtext("atom:published", default=None, namespaces=ns)),
                updated=(entry.findtext("atom:updated", default=None, namespaces=ns)),
                summary=summary or content,
                content=content,
                content_html=content_html,
            )
        )

    return entries


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html.unescape(value))
    parser.close()
    return parser.text()


def _xml_element_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    return text or None


def _first_match(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)


def _strip_fragment(path: str) -> str:
    return path.split("#", 1)[0].split("?", 1)[0]


def parse_user_requests_html(html_text: str, limit: int) -> list[UserRequestResult]:
    chunks = re.split(r'(?=<div class="request_listing">)', html_text)
    results: list[UserRequestResult] = []

    for chunk in chunks:
        if 'class="request_listing"' not in chunk:
            continue

        title: str | None = None
        request_slug: str | None = None
        url: str | None = None
        head = _first_match(r'<span class="head">\s*<a href="([^"]+)">(.*?)</a>', chunk)
        if head:
            href = html.unescape(head.group(1))
            title = html_to_text(head.group(2))
            path = _strip_fragment(href)
            slug_match = re.search(r"/request/([^/]+)$", path)
            if slug_match:
                request_slug = slug_match.group(1)
                url = f"{BASE_URL}/request/{request_slug}"

        authority_name: str | None = None
        authority_slug: str | None = None
        authority = _first_match(r'href="https://www\.whatdotheyknow\.com/body/([^"]+)">(.*?)</a>', chunk)
        if authority:
            authority_slug = html.unescape(authority.group(1))
            authority_name = html_to_text(authority.group(2))

        status_match = _first_match(r"<strong>\s*(.*?)\s*</strong>", chunk)
        time_match = _first_match(r'<time datetime="([^"]+)"', chunk)
        requester_match = _first_match(r'<div class="requester">\s*(.*?)\s*</div>', chunk)
        snippet_match = _first_match(r'<span class="desc">\s*(.*?)\s*</span>', chunk)

        item = UserRequestResult(
            title=title,
            request_slug=request_slug,
            url=url,
            status=html_to_text(status_match.group(1)) if status_match else None,
            authority_name=authority_name,
            authority_slug=authority_slug,
            updated=html.unescape(time_match.group(1)) if time_match else None,
            event=html_to_text(requester_match.group(1)) if requester_match else None,
            snippet=html_to_text(snippet_match.group(1)) if snippet_match else None,
        )

        if item.request_slug or item.title:
            results.append(item)
            if len(results) >= limit:
                break

    return results


wdtk = WDTKClient()

mcp = FastMCP(
    name="WhatDoTheyKnow MCP",
    instructions=(
        "Read WhatDoTheyKnow public JSON, Atom feeds, and CSV exports. "
        "Optionally call the experimental write API if configured."
    ),
    mask_error_details=True,
)


# -------------------------
# Resources: read-only data
# -------------------------

@mcp.resource("wdtk://authorities/{authority_slug}", mime_type="application/json")
async def authority_json(authority_slug: str, ctx: Context = CurrentContext()) -> str:
    """Read a public authority as JSON."""
    await ctx.info(f"Fetching authority JSON: {authority_slug}")
    payload = await wdtk.get_json(f"/body/{authority_slug}.json")
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.resource("wdtk://requests/{request_slug}", mime_type="application/json")
async def request_json(request_slug: str, ctx: Context = CurrentContext()) -> str:
    """Read a request as JSON."""
    await ctx.info(f"Fetching request JSON: {request_slug}")
    payload = await wdtk.get_json(f"/request/{request_slug}.json")
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.resource("wdtk://users/{user_slug}", mime_type="application/json")
async def user_json(user_slug: str, ctx: Context = CurrentContext()) -> str:
    """Read a user as JSON."""
    await ctx.info(f"Fetching user JSON: {user_slug}")
    payload = await wdtk.get_json(f"/user/{user_slug}.json")
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.resource("wdtk://requests/{request_slug}/feed", mime_type="application/atom+xml")
async def request_feed_xml(request_slug: str, ctx: Context = CurrentContext()) -> str:
    """Read a request Atom feed as raw XML."""
    await ctx.info(f"Fetching request feed: {request_slug}")
    return await wdtk.get_text(
        f"/request/{request_slug}/feed",
        accept="application/atom+xml, application/xml;q=0.9, */*;q=0.1",
    )


@mcp.resource("wdtk://users/{user_slug}/feed", mime_type="application/atom+xml")
async def user_feed_xml(user_slug: str, ctx: Context = CurrentContext()) -> str:
    """Read a user Atom feed as raw XML."""
    await ctx.info(f"Fetching user feed: {user_slug}")
    return await wdtk.get_text(
        f"/feed/user/{user_slug}",
        accept="application/atom+xml, application/xml;q=0.9, */*;q=0.1",
    )


@mcp.resource("wdtk://authorities/all.csv", mime_type="text/csv")
async def all_authorities_csv(ctx: Context = CurrentContext()) -> str:
    """Download the complete CSV of every WhatDoTheyKnow public authority.
    WARNING: this is a large payload — use search_authorities(query) for targeted
    lookups, or authority_json for a specific body. Only call this when you need
    the full dataset (e.g. bulk analysis or seeding a list)."""
    await ctx.info("Fetching all-authorities CSV")
    return await wdtk.get_text("/body/all-authorities.csv", accept="text/csv, */*;q=0.1")


# -------------------------
# Tools: operations/helpers
# -------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "compose"},
)
def build_request_url(
    authority_slug: str,
    title: str | None = None,
    default_letter: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
) -> NewRequestLink:
    """Build a prefilled WhatDoTheyKnow request URL."""
    params: dict[str, str] = {}
    if title:
        params["title"] = title
    if default_letter:
        params["default_letter"] = default_letter
    if body:
        params["body"] = body
    if tags:
        params["tags"] = " ".join(tags)

    query = urlencode(params, doseq=False)
    url = f"{BASE_URL}/new/{authority_slug}"
    if query:
        url = f"{url}?{query}"

    return NewRequestLink(authority_slug=authority_slug, url=url)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "feed"},
)
@_timed_tool
async def get_request_feed_items(
    request_slug: str,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[AtomEntry]:
    """Return parsed Atom feed entries for a specific FOI request as structured objects.

    Use this instead of reading the raw wdtk://requests/{slug}/feed resource when you
    want structured AtomEntry objects rather than raw XML. Each entry's `link` field
    contains the request URL; use the slug from that URL with get_request_detail
    for full detail."""
    await ctx.info(f"Parsing request feed for: {request_slug}")
    xml_text = await wdtk.get_text(
        f"/request/{request_slug}/feed",
        accept="application/atom+xml, application/xml;q=0.9, */*;q=0.1",
    )
    items = parse_atom(xml_text)
    return items[:limit]


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "request"},
)
@_timed_tool
async def get_request_detail(
    request_slug: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Return full public JSON for a WhatDoTheyKnow FOI request.

    The response includes request metadata, public body details, requester details,
    state/status fields, and the visible info_request_events array containing
    correspondence text and attachment metadata where WhatDoTheyKnow exposes it.
    """
    await ctx.info(f"Fetching request detail JSON: {request_slug}")
    try:
        return await wdtk.get_json(f"/request/{request_slug}.json")
    except CurlHTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        hint = (
            "Slug not found — check spelling or use search_request_events to locate it."
            if status == 404
            else "Upstream error."
        )
        raise ValueError(f"WDTK returned HTTP {status} for request slug '{request_slug}'. {hint}") from exc


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "authority"},
)
@_timed_tool
async def get_authority_detail(
    authority_slug: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Return full public JSON for a WhatDoTheyKnow public authority.

    The response includes contact/profile fields, tags, publication links, and
    request statistics such as successful, overdue, and classified request counts
    where WhatDoTheyKnow exposes them.
    """
    await ctx.info(f"Fetching authority detail JSON: {authority_slug}")
    try:
        return await wdtk.get_json(f"/body/{authority_slug}.json")
    except CurlHTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        hint = (
            "Slug not found — check spelling or use search_authorities to locate it."
            if status == 404
            else "Upstream error."
        )
        raise ValueError(f"WDTK returned HTTP {status} for authority slug '{authority_slug}'. {hint}") from exc


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "feed", "user"},
)
@_timed_tool
async def get_user_feed_items(
    user_slug: str,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[AtomEntry]:
    """Return parsed Atom feed entries for a user's WhatDoTheyKnow activity.

    Unlike the raw wdtk://users/{slug}/feed resource, this returns structured
    entries with the Atom content converted to readable text in `content` and
    mirrored to `summary` for clients that only display summary fields.
    """
    await ctx.info(f"Parsing user feed for: {user_slug}")
    xml_text = await wdtk.get_text(
        f"/feed/user/{user_slug}",
        accept="application/atom+xml, application/xml;q=0.9, */*;q=0.1",
    )
    items = parse_atom(xml_text)
    return items[:limit]


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "user"},
)
@_timed_tool
async def get_user_requests(
    user_slug: str,
    status_filter: str | None = None,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[UserRequestResult]:
    """List a user's visible WhatDoTheyKnow requests from their public requests page.

    Returns request title, slug, URL, current displayed status, authority, updated
    timestamp, event text, and the page snippet. `status_filter` is a
    case-insensitive substring match against the displayed status.
    """
    await ctx.info(f"Fetching request list for user: {user_slug}")
    html_text = await wdtk.get_text(
        f"/user/{user_slug}/requests",
        accept="text/html, application/xhtml+xml;q=0.9, */*;q=0.1",
    )
    parse_limit = limit if status_filter is None else max(limit * 4, 50)
    items = parse_user_requests_html(html_text, limit=parse_limit)
    if status_filter:
        needle = status_filter.lower()
        items = [item for item in items if item.status and needle in item.status.lower()]
    return items[:limit]


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "search"},
)
@_timed_tool
async def search_request_events(
    search_expression: str,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[AtomEntry]:
    """Search WhatDoTheyKnow's feed-based event index and return structured results.

    Call this to find FOI requests matching a query expression. Returns up to `limit`
    AtomEntry objects. Use the `link` field of each result as the next navigation
    step — extract the request slug and call get_request_detail or
    get_request_feed_items for full detail.

    Example expressions:
      status:successful
      body:"Liverpool City Council"
      (variety:sent OR variety:response) status:successful
    """
    await ctx.info(f"Searching WDTK feed with expression: {search_expression}")
    encoded = quote(search_expression, safe="")
    xml_text = await wdtk.get_text(
        f"/feed/search/{encoded}",
        accept="application/atom+xml, application/xml;q=0.9, */*;q=0.1",
    )
    items = parse_atom(xml_text)
    return items[:limit]


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "search"},
)
@_timed_tool
async def search_authorities(
    query: str,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[AuthorityResult]:
    """Search WhatDoTheyKnow public authorities by name.

    Returns up to `limit` authorities whose name or short_name contains `query`
    (case-insensitive). Use the `slug` field with authority_json or
    build_request_url as the next step.

    Example: search_authorities("Liverpool") → slug "liverpool_city_council"
    Then: authority_json with that slug, or build_request_url with it."""
    import csv
    import io

    await ctx.info(f"Searching authorities for: {query}")
    csv_text = await wdtk.get_text("/body/all-authorities.csv", accept="text/csv, */*;q=0.1")
    reader = csv.DictReader(io.StringIO(csv_text))
    q = query.lower()
    results: list[AuthorityResult] = []
    for row in reader:
        if q in row.get("Name", "").lower() or q in row.get("Short name", "").lower():
            results.append(
                AuthorityResult(
                    name=row.get("Name", ""),
                    short_name=row.get("Short name", ""),
                    slug=row.get("URL name", ""),
                    tags=row.get("Tags") or None,
                )
            )
            if len(results) >= limit:
                break
    return results


@mcp.tool(
    annotations=ToolAnnotations(destructiveHint=True, openWorldHint=True),
    tags={"write", "admin"},
)
@_timed_tool
async def create_request_record(
    title: str,
    body: str,
    external_user_name: str,
    external_url: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """
    Create a request through the experimental write API.

    Requires WDTK_API_KEY in the server environment.
    """
    api_key = os.getenv("WDTK_API_KEY")
    if not api_key:
        return {"error": "Write API unavailable: WDTK_API_KEY not configured. Requires an authority-level key from the WhatDoTheyKnow admin interface."}
    payload = CreateRequestPayload(
        title=title,
        body=body,
        external_user_name=external_user_name,
        external_url=external_url,
    )
    await ctx.info(f"Creating request record for {external_user_name}")
    return await wdtk.post_form_json(
        "/api/v2/request",
        api_key=api_key,
        json_payload=payload.model_dump(mode="json"),
    )


@mcp.tool(
    annotations=ToolAnnotations(destructiveHint=True, openWorldHint=True),
    tags={"write", "admin"},
)
@_timed_tool
async def update_request_state(
    request_id: int,
    state: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """
    Update the user-assessed state of a request through the experimental write API.

    Requires WDTK_API_KEY in the server environment.
    """
    api_key = os.getenv("WDTK_API_KEY")
    if not api_key:
        return {"error": "Write API unavailable: WDTK_API_KEY not configured. Requires an authority-level key from the WhatDoTheyKnow admin interface."}
    payload = UpdateRequestStatePayload(state=state)
    await ctx.info(f"Updating request {request_id} to state={state}")
    return await wdtk.post_form_json(
        f"/api/v2/request/{request_id}/update.json",
        api_key=api_key,
        json_payload=payload.model_dump(mode="json"),
    )


# -------------------------
# Prompt: optional workflow
# -------------------------

@mcp.prompt
def draft_foi_request(
    authority_slug: str,
    topic: str,
    facts: str | None = None,
) -> str:
    """Draft a narrow, specific FOI request suitable for WhatDoTheyKnow."""
    extra = f"\nRelevant facts:\n{facts}\n" if facts else ""
    return (
        f"Draft a concise UK FOI request for authority '{authority_slug}' about '{topic}'.\n"
        "Requirements:\n"
        "- narrow scope\n"
        "- precise date range if useful\n"
        "- request existing recorded information only\n"
        "- avoid arguments/opinions\n"
        "- suitable for publication on WhatDoTheyKnow\n"
        f"{extra}"
    )


mcp.add_transform(PromptsAsTools(mcp))

# Optional: keep admin/write tools hidden on the public server.
# mcp.disable(tags={"write"})


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def smithery_server_card(request):
    return JSONResponse({"serverInfo": {"name": "whatdotheyknow-mcp", "version": "0.1.4"}})


@mcp.custom_route("/.well-known/glama.json", methods=["GET"])
async def glama_claim(request):
    return JSONResponse({
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "maintainers": [{"email": "paul@bouch.dev"}],
    })


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "healthy"})


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics_endpoint(request):
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class _HttpGuard:
    """Return a held-open SSE stream for GET /mcp; 405 for DELETE /mcp.

    claude.ai probes GET /mcp to establish an SSE stream before sending MCP
    protocol messages via POST. With stateless_http=True FastMCP only registers
    POST routes, so GET returns 405 — claude.ai treats this as a connection
    failure even though POST works fine.

    Fix: intercept GET /mcp and return 200 text/event-stream held open until
    the client disconnects. FastMCP never sees the GET; stateless semantics
    are preserved. DELETE is rejected (405) — stateless servers have no sessions.
    """

    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "").rstrip("/").encode()
            method = scope.get("method", "").upper().encode()
            if path == self._mcp_path:
                if method == b"GET":
                    await send({"type": "http.response.start", "status": 200, "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"connection", b"keep-alive"),
                    ]})
                    await send({"type": "http.response.body", "body": b"", "more_body": True})
                    while True:
                        event = await receive()
                        if event["type"] == "http.disconnect":
                            break
                    return
                if method == b"DELETE":
                    from starlette.responses import Response as StarletteResponse
                    await StarletteResponse("Method Not Allowed", status_code=405, headers={"Allow": "POST"})(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class _AcceptNormalizer:
    """Stamp Accept to the MCP-spec value on /mcp only, so json_response=True never 406s.

    Anthropic sends mixed Accept headers per request type (application/json for
    initialize, text/event-stream for tools/list). Only stamp the MCP endpoint —
    leave /metrics, /health, /.well-known/* with their original Accept headers.
    """
    def __init__(self, app, mcp_path: bytes = b"/mcp"):
        self.app = app
        self._mcp_path = mcp_path.rstrip(b"/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").rstrip("/").encode() == self._mcp_path:
            headers = [
                (b"accept", b"application/json, text/event-stream")
                if name.lower() == b"accept"
                else (name, value)
                for name, value in scope.get("headers", [])
            ]
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def main() -> None:
    import uvicorn
    from fastmcp.server.http import create_streamable_http_app

    port = int(os.environ.get("PORT", "8080"))
    app = create_streamable_http_app(
        mcp,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
    uvicorn.run(
        _HttpGuard(_AcceptNormalizer(app)),
        host="0.0.0.0",
        port=port,
        forwarded_allow_ips="*",
        proxy_headers=True,
        lifespan="on",
        log_level="info",
    )


if __name__ == "__main__":
    main()
