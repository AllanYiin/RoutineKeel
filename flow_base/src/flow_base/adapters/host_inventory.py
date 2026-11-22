"""Embedding-host adapter. Observe an already connected MCP client without invoking a tool."""

from datetime import datetime, timedelta, timezone

from ..graph.capabilities import unavailable
from ..runtime import digest


async def observe_tools(client, grants, *, validity_seconds=300):
    """grants belong to the embedding host; no blueprint input can add an entry."""
    if not 1 <= validity_seconds <= 3600:
        raise ValueError("invalid observation lifetime")
    server = client.server_info
    if not server or not server.version:
        unavailable("宿主未提供可核對的服務版本。")
    tools = {}
    cursor = None
    seen = set()
    while True:
        page = await client.list_tools(cursor=cursor, cache_mode="bypass")
        for tool in page.tools:
            if tool.name not in grants:
                continue
            facts = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            observed = {
                "server": server.name,
                "version": server.version,
                "tool": tool.name,
                "input_schema": facts["inputSchema"],
                "output_schema": facts.get("outputSchema"),
            }
            tools[tool.name] = {
                **grants[tool.name],
                **observed,
                "receipt_id": digest(observed),
                "observed": "tools/list",
                "available": True,
            }
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in seen or len(seen) >= 128:
            unavailable("工具清冊分頁無法收束。")
        seen.add(cursor)
    expires = datetime.now(timezone.utc) + timedelta(seconds=validity_seconds)
    return {"version": 1, "expires_at": expires.isoformat(), "tools": tools}
