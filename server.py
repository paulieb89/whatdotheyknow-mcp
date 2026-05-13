from __future__ import annotations

import functools
import json
import os
import time
from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

import httpx
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
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
    updated: str | None = None
    summary: str | None = None


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


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

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
    root = ET.fromstring(xml_text)
    entries: list[AtomEntry] = []
    for entry in root.findall("atom:entry", ns):
        link_el = entry.find("atom:link", ns)
        summary_el = entry.find("atom:summary", ns)
        entries.append(
            AtomEntry(
                id=(entry.findtext("atom:id", default=None, namespaces=ns)),
                title=(entry.findtext("atom:title", default=None, namespaces=ns)),
                link=(link_el.get("href") if link_el is not None else None),
                updated=(entry.findtext("atom:updated", default=None, namespaces=ns)),
                summary=("".join(summary_el.itertext()).strip() if summary_el is not None else None),
            )
        )
    return entries


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
    contains the request URL; use the slug from that URL with request_json or
    authority_json for full detail."""
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
    step — extract the request slug and call the wdtk://requests/{slug} resource or
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


# ----------------------------------------------------------------
# NEW TOOLS: expose Resources as callable tools for MCP clients
# that cannot access Resources directly (e.g. Claude.ai connectors)
# ----------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "read"},
)
@_timed_tool
async def get_request_detail(
    request_slug: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Fetch full detail for a specific FOI request, including the original request
    text, all responses, current status, dates, and correspondence history.

    The request_slug is the URL-friendly identifier found in WhatDoTheyKnow URLs,
    e.g. 'payroll_system_synchronization_f_2' from
    https://www.whatdotheyknow.com/request/payroll_system_synchronization_f_2

    Returns the complete JSON object from WhatDoTheyKnow's API, which typically
    includes: title, status, created_at, updated_at, described_state, user info,
    public_body info, and the full info_request_events array containing every
    outgoing message and incoming response with their body text."""
    await ctx.info(f"Fetching request detail: {request_slug}")
    return await wdtk.get_json(f"/request/{request_slug}.json")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "read"},
)
@_timed_tool
async def get_user_requests(
    user_slug: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Fetch a WhatDoTheyKnow user profile and their FOI requests.

    The user_slug is the URL-friendly identifier from the user's profile page,
    e.g. 'g_spinks' from https://www.whatdotheyknow.com/user/g_spinks

    Returns the JSON user object which typically includes: the user's display name,
    about_me text, and an array of their info_requests with titles, slugs, statuses,
    authorities, and URLs. Use the request slugs from the results with
    get_request_detail to fetch full correspondence for any individual request."""
    await ctx.info(f"Fetching user profile and requests: {user_slug}")
    return await wdtk.get_json(f"/user/{user_slug}.json")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "read"},
)
@_timed_tool
async def get_authority_detail(
    authority_slug: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Fetch full detail for a public authority, including contact info and stats.

    The authority_slug is the URL-friendly identifier found in WhatDoTheyKnow URLs,
    e.g. 'essex_police' from https://www.whatdotheyknow.com/body/essex_police

    Returns the complete JSON object from WhatDoTheyKnow's API, which typically
    includes: name, short_name, notes (description), created_at, updated_at,
    home_page, request_email, tag_string, info_requests (recent requests to this
    authority), and disclosure_log URL if available."""
    await ctx.info(f"Fetching authority detail: {authority_slug}")
    return await wdtk.get_json(f"/body/{authority_slug}.json")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "read"},
)
@_timed_tool
async def get_request_v2(
    request_id: int,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Return full detail for a specific FOI request using the Alaveteli v2 API.

    The v2 API may return more complete data than v1, including full message body
    text rather than truncated summaries. The request_id is the numeric ID found
    in v1 API responses (e.g. the 'id' field from get_request_detail)."""
    await ctx.info(f"Fetching v2 request detail: {request_id}")
    return await wdtk.get_json(f"/api/v2/request/{request_id}.json")


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "read"},
)
@_timed_tool
async def get_request_messages(
    request_id: int,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Return the message list for a specific FOI request via the Alaveteli v2 API.

    This dedicated messages endpoint may return a structured list of individual
    messages in the correspondence, including full body text for each. The
    request_id is the numeric ID found in v1 API responses."""
    await ctx.info(f"Fetching v2 request messages: {request_id}")
    return await wdtk.get_json(f"/api/v2/request/{request_id}/messages.json")


# -------------------------
# NEW TOOL: Scrape HTML correspondence (curl-cffi + BeautifulSoup)
# -------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
    tags={"public", "read"},
)
@_timed_tool
async def get_request_correspondence(
    request_slug: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Fetch and parse the actual message bodies from a WhatDoTheyKnow request page.

    Uses browser impersonation (curl-cffi with Safari 17 user agent) to fetch the HTML
    request page and parses out the full correspondence text using BeautifulSoup. This
    complements get_request_detail by providing the actual message body content that
    the JSON API truncates or omits.

    The request_slug is the URL-friendly identifier from WhatDoTheyKnow URLs,
    e.g. 'payroll_system_synchronization_f_2'.

    Returns a dict containing:
    - title: request title
    - status: current status
    - messages: list of message dicts with 'type', 'date', 'from', and 'body'
    - url: link to the original request
    """
    await ctx.info(f"Scraping request correspondence: {request_slug}")

    url = f"{BASE_URL}/request/{request_slug}"

    try:
        async with AsyncSession(impersonate="safari17_0") as client:
            response = await client.get(url, timeout=20.0)
            response.raise_for_status()
            html_text = response.text
    except Exception as e:
        return {"error": f"Failed to fetch request page: {str(e)}", "url": url}

    try:
        soup = BeautifulSoup(html_text, "html.parser")
    except Exception as e:
        return {"error": f"Failed to parse HTML: {str(e)}", "url": url}

    title_elem = soup.find("h1")
    title = title_elem.get_text(strip=True) if title_elem else "Unknown"

    status = "Unknown"
    status_elem = soup.find("span", class_="request-status")
    if status_elem:
        status = status_elem.get_text(strip=True)

    messages = []

    message_containers = soup.find_all("div", class_=lambda x: x and "event" in x.lower())

    if not message_containers:
        message_containers = soup.find_all("div", class_=lambda x: x and any(
            cls in (x or "") for cls in ["correspondence", "message", "letter", "response"]
        ))

    if not message_containers:
        message_containers = soup.find_all("div", {"data-event-id": True})

    for container in message_containers:
        try:
            msg_type = "unknown"
            msg_date = ""
            msg_from = ""
            msg_body = ""

            if "request" in container.get("class", []):
                msg_type = "request"
            elif "response" in container.get("class", []):
                msg_type = "response"

            date_elem = container.find(["span", "div"], class_=lambda x: x and any(
                d in (x or "") for d in ["date", "time", "timestamp"]
            ))
            if date_elem:
                msg_date = date_elem.get_text(strip=True)

            from_elem = container.find(["span", "div"], class_=lambda x: x and any(
                f in (x or "") for f in ["from", "sender", "authority", "user"]
            ))
            if from_elem:
                msg_from = from_elem.get_text(strip=True)

            body_elem = container.find(["div", "p"], class_=lambda x: x and any(
                b in (x or "") for b in ["body", "content", "message", "text"]
            ))
            if body_elem:
                msg_body = body_elem.get_text(strip=True)
            else:
                for script in container(["script", "style"]):
                    script.decompose()
                msg_body = container.get_text(separator="\n", strip=True)

            if msg_body:
                messages.append({
                    "type": msg_type,
                    "date": msg_date,
                    "from": msg_from,
                    "body": msg_body,
                })
        except Exception as e:
            await ctx.info(f"Error parsing message container: {str(e)}")
            continue

    return {
        "title": title,
        "status": status,
        "url": url,
        "message_count": len(messages),
        "messages": messages,
        "note": "This data is extracted by parsing the HTML page. Some formatting may differ from the original.",
    }


# -------------------------
# Write tools (API key)
# -------------------------

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
    return JSONResponse({"serverInfo": {"name": "whatdotheyknow-mcp", "version": "0.2.0"}})


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
        _AcceptNormalizer(app),
        host="0.0.0.0",
        port=port,
        forwarded_allow_ips="*",
        proxy_headers=True,
        lifespan="on",
        log_level="info",
    )


def main_stdio() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
