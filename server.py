from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

import httpx
from pydantic import BaseModel, Field, HttpUrl

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.server.transforms import PromptsAsTools, ResourcesAsTools
from mcp.types import ToolAnnotations


BASE_URL = "https://www.whatdotheyknow.com"


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


class WDTKClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_json(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.get(
                path,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    async def get_text(self, path: str, accept: str | None = None) -> str:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            headers = {"Accept": accept} if accept else {}
            response = await client.get(path, headers=headers)
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
    """Download the full authorities CSV."""
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
async def get_request_feed_items(
    request_slug: str,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[AtomEntry]:
    """Return a normalized list of entries from a request Atom feed."""
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
async def search_request_events(
    search_expression: str,
    limit: int = 20,
    ctx: Context = CurrentContext(),
) -> list[AtomEntry]:
    """
    Query WhatDoTheyKnow's feed-based search surface.

    Example expression:
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


@mcp.tool(tags={"write", "admin"})
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
    api_key = os.environ["WDTK_API_KEY"]
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


@mcp.tool(tags={"write", "admin"})
async def update_request_state(
    request_id: int,
    state: str,
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """
    Update the user-assessed state of a request through the experimental write API.

    Requires WDTK_API_KEY in the server environment.
    """
    api_key = os.environ["WDTK_API_KEY"]
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


# Expose resources/prompts to tool-only clients.
mcp.add_transform(ResourcesAsTools(mcp))
mcp.add_transform(PromptsAsTools(mcp))

# Optional: keep admin/write tools hidden on the public server.
# mcp.disable(tags={"write"})


if __name__ == "__main__":
    mcp.run()