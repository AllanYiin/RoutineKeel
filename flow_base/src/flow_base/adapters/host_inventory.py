"""Observe host-authorized MCP tools without invoking them."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock, Timeout

from ..graph.capabilities import unavailable
from ..outputs import reject_links
from ..runtime import FlowError, atomic_json, digest


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


async def _observe_sources(sources, directory):
    from mcp import Client, StdioServerParameters

    async def observe(identifier, source):
        try:
            parameters = StdioServerParameters(command=source["command"], args=source.get("args", []),
                                               cwd=directory)
            async with asyncio.timeout(15), Client(parameters, read_timeout_seconds=8) as client:
                return await observe_tools(client, source["grants"], source_id=identifier,
                                           routes=source.get("routes"))
        except Exception:
            # A provider can be down independently. User-facing results do not expose its launch data.
            return None

    peers = await asyncio.gather(*(observe(key, value) for key, value in sources.items()))
    good = [peer for peer in peers if peer is not None]
    if not good:
        return {"version": 1, "tools": {}, "status": "unavailable",
                "expires_at": (datetime.now(UTC) + timedelta(seconds=15)).isoformat()}
    observation = merge_tool_observations(*good)
    if len(good) != len(peers):
        observation["expires_at"] = (datetime.now(UTC) + timedelta(seconds=15)).isoformat()
    return observation


def refresh_configured_inventory(configuration, cache):
    """Resolve a trusted launcher's stdio sources and share the resulting short-lived snapshot."""
    def read(path):
        with path.open("rb") as file:
            body = file.read(2 * 1024**2 + 1)
        if len(body) > 2 * 1024**2:
            raise ValueError("inventory exceeds limit")
        return json.loads(body)

    def cached(config_hash):
        try:
            prior = read(cache)
            expires_at = datetime.fromisoformat(prior["expires_at"])
            if prior["source_config_hash"] == config_hash and expires_at > datetime.now(UTC):
                return prior
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    try:
        config = read(configuration)
        sources = config["sources"]
        if config["version"] != 1 or not isinstance(sources, dict) or not 1 <= len(sources) <= 12:
            raise ValueError("invalid sources")
        for key, source in sources.items():
            if (not 1 <= len(key) <= 200 or not Path(source["command"]).is_absolute()
                    or not isinstance(source["grants"], dict)):
                raise ValueError("invalid source")
        config_hash = digest(config)
        lock_path = cache.with_suffix(cache.suffix + ".lock")
        reject_links(cache)
        reject_links(lock_path)
        if prior := cached(config_hash):
            return prior
        with FileLock(lock_path, timeout=18):
            if digest(read(configuration)) != config_hash:
                raise ValueError("sources changed while waiting for inventory")
            if prior := cached(config_hash):
                return prior
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                observed = asyncio.run(_observe_sources(sources, configuration.parent))
            else:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    observed = pool.submit(lambda: asyncio.run(
                        _observe_sources(sources, configuration.parent))).result()
            observed["source_config_hash"] = config_hash
            if digest(read(configuration)) != config_hash:
                raise ValueError("sources changed during observation")
            if len(json.dumps(observed, ensure_ascii=False).encode()) > 2 * 1024**2:
                raise ValueError("inventory exceeds limit")
            atomic_json(cache, observed)
            return observed
    except (OSError, ImportError, ValueError, KeyError, TypeError, Timeout, FlowError):
        unavailable("無法從宿主已設定的 MCP 來源取得工具。", observation_status="unavailable")
