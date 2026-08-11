"""Tableau MCP client for list-datasources / query-datasource."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from backend.config import env, has_tableau_pat, require_env
from backend.mcp_stdio import McpStdioClient
from backend.platform_fix import mcp_spawn_command

_client: McpStdioClient | None = None
_mcp_env_fp: tuple[str, ...] | None = None
_init_lock = asyncio.Lock()

_MCP_KEYS = ("SERVER", "SITE_NAME", "PAT_NAME", "PAT_VALUE")


def _build_mcp_env() -> dict[str, str]:
    if not has_tableau_pat():
        raise RuntimeError(
            "Tableau MCP (query-datasource / get-view-data) requires PAT. "
            "Set TABLEAU_PAT_NAME and TABLEAU_PAT_VALUE, or use the dashboard "
            "extension session path (no server credentials)."
        )
    mcp_env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    mcp_env["SERVER"] = require_env("TABLEAU_SERVER")
    mcp_env["SITE_NAME"] = env("TABLEAU_SITE_NAME")
    mcp_env["PAT_NAME"] = require_env("TABLEAU_PAT_NAME")
    mcp_env["PAT_VALUE"] = require_env("TABLEAU_PAT_VALUE")
    if env("NODE_TLS_REJECT_UNAUTHORIZED") == "0":
        mcp_env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    # Avoid broken metadata tool; keep list-datasources + query-datasource
    if not env("INCLUDE_TOOLS"):
        meta = "get-datasource-metadata"
        raw = env("EXCLUDE_TOOLS")
        parts = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
        if meta not in parts:
            parts.append(meta)
        mcp_env["EXCLUDE_TOOLS"] = ",".join(parts)
    return mcp_env


def _fingerprint() -> tuple[str, ...]:
    e = _build_mcp_env()
    return tuple(e.get(k, "") for k in _MCP_KEYS)


async def reset_mcp_client() -> None:
    global _client, _mcp_env_fp
    async with _init_lock:
        if _client is not None:
            await _client.close()
        _client = None
        _mcp_env_fp = None


async def get_mcp_client() -> McpStdioClient:
    global _client, _mcp_env_fp
    fp = _fingerprint()
    async with _init_lock:
        if _client is not None and _mcp_env_fp != fp:
            await _client.close()
            _client = None
        if _client is not None:
            return _client
        c = McpStdioClient(
            mcp_spawn_command(["npx", "-y", "@tableau/mcp-server@latest"]),
            _build_mcp_env(),
        )
        await c.start()
        _client = c
        _mcp_env_fp = fp
        return c


def tool_result_to_text(result: dict[str, Any]) -> str:
    if result.get("structuredContent"):
        sc = result["structuredContent"]
        if isinstance(sc, dict) and sc:
            return json.dumps(sc)
    lines: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            lines.append(json.dumps(block))
            continue
        if block.get("type") == "text" and block.get("text") is not None:
            lines.append(str(block["text"]))
        else:
            lines.append(json.dumps(block))
    text = "\n\n".join(lines) if lines else "(empty tool result)"
    if result.get("isError"):
        return json.dumps({"isError": True, "content": text})
    return text


async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    global _client, _mcp_env_fp
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            client = await get_mcp_client()
            result = await client.call_tool(name, arguments)
            text = tool_result_to_text(result).lower()
            if attempt == 0 and result.get("isError") and ("401" in text or "invalid" in text):
                await reset_mcp_client()
                continue
            return result
        except Exception as e:
            last_error = e
            _client = None
            _mcp_env_fp = None
            if attempt == 1:
                raise
    if last_error:
        raise last_error
    raise RuntimeError("call_tool failed")
