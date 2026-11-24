"""Embedding-host adapter. Observe an already connected MCP client without invoking a tool."""

from datetime import UTC, datetime, timedelta

from ..graph.capabilities import unavailable
from ..runtime import digest


async def observe_tools(client, grants, *, validity_seconds=300, source_id=None, routes=None):
    """grants belong to the embedding host; no blueprint input can add an entry."""
    if not 1 <= validity_seconds <= 3600:
        raise ValueError("invalid observation lifetime")
    server = client.server_info
    if not server or not server.version:
        unavailable("宿主未提供可核對的服務版本。")
    if source_id is not None and (not isinstance(source_id, str) or not 1 <= len(source_id) <= 200):
        raise ValueError("invalid inventory source identifier")
    routes = {} if routes is None else routes
    if not isinstance(routes, dict) or (
        routes
        and (
            source_id is None
            or not routes.keys() <= grants.keys()
            or not all(
                isinstance(route, str) and 1 <= len(route) <= 200 for route in routes.values()
            )
        )
    ):
        raise ValueError("invalid host tool routes")
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
            route = routes.get(tool.name, tool.name)
            if route in tools:
                unavailable("宿主能力清冊回傳重複工具：" + route)
            tools[route] = {
                **grants[tool.name],
                **observed,
                "tool": route,
                "receipt_id": digest(
                    observed if source_id is None else [source_id, route, observed]
                ),
                "observed": "tools/list",
                "available": True,
            }
            if source_id is not None:
                tools[route].update(source_id=source_id, provider_tool=tool.name)
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in seen or len(seen) >= 128:
            unavailable("工具清冊分頁無法收束。")
        seen.add(cursor)
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=validity_seconds)
    return {
        "version": 1,
        "observed_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "tools": tools,
    }


def merge_tool_observations(*catalogs):
    """Combine observations after the host has assigned distinct dispatch routes."""
    now = datetime.now(UTC)
    tools, expiry = {}, None
    for catalog in catalogs:
        expires = datetime.fromisoformat(catalog["expires_at"])
        if catalog["version"] != 1 or expires.tzinfo is None or expires <= now:
            unavailable("請以目前有效的連線觀測合併工具清冊。")
        overlapping = tools.keys() & catalog["tools"].keys()
        if overlapping:
            unavailable("宿主須為同名工具指定不同派送名稱：" + ", ".join(sorted(overlapping)))
        tools.update(catalog["tools"])
        expiry = expires if expiry is None else min(expiry, expires)
    if expiry is None:
        raise ValueError("at least one host inventory is required")
    return {
        "version": 1,
        "observed_at": now.isoformat(),
        "expires_at": expiry.isoformat(),
        "tools": tools,
    }
