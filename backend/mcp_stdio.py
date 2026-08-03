"""MCP JSON-RPC over stdio — uses subprocess.Popen (works on Windows + uvicorn)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from typing import Any

from backend.config import env_int
from backend.platform_fix import mcp_spawn_command


class McpStdioError(RuntimeError):
    pass


class McpStdioClient:
    """
    Talks to @tableau/mcp-server via stdin/stdout.
    Uses blocking Popen + thread for stdout (avoids Windows asyncio subprocess bugs).
    """

    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self._command = command
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_lock = asyncio.Lock()
        self._stdin_lock = threading.Lock()

    async def start(self) -> None:
        if self._proc is not None:
            return

        self._loop = asyncio.get_running_loop()
        merged = {**os.environ, **self._env}
        cmd = mcp_spawn_command(self._command)

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged,
                bufsize=0,
            )
        except FileNotFoundError as e:
            hint = (
                "Install Node.js (https://nodejs.org) and ensure npx is on PATH. "
                f"Tried command: {cmd!r}"
            )
            raise McpStdioError(hint) from e
        except OSError as e:
            raise McpStdioError(f"Failed to start MCP process {cmd!r}: {e}") from e

        if not self._proc.stdin or not self._proc.stdout:
            raise McpStdioError("MCP process missing stdin/stdout pipes")

        self._stop_reader.clear()
        self._reader_thread = threading.Thread(
            target=self._stdout_reader_thread,
            name="mcp-stdio-reader",
            daemon=True,
        )
        self._reader_thread.start()

        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tableau-mcp-chat-python", "version": "0.2.0"},
            },
            timeout=env_int("MCP_INIT_TIMEOUT_MS", 180_000) / 1000,
        )
        await self._notify("notifications/initialized", {})

    def _stdout_reader_thread(self) -> None:
        assert self._proc and self._proc.stdout
        loop = self._loop
        while not self._stop_reader.is_set():
            try:
                line = self._proc.stdout.readline()
            except Exception:
                break
            if not line:
                break
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(self._dispatch_line, line)

    def _dispatch_line(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return
        msg_id = msg.get("id")
        if msg_id is not None and isinstance(msg_id, int) and msg_id in self._pending:
            fut = self._pending.pop(msg_id)
            if "error" in msg:
                fut.set_exception(McpStdioError(json.dumps(msg["error"])))
            else:
                fut.set_result(msg.get("result") or {})

    async def close(self) -> None:
        self._stop_reader.set()
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                await asyncio.to_thread(self._proc.wait, 5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        self._reader_thread = None

    def _write_sync(self, payload: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._stdin_lock:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()

    async def _write(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.poll() is not None:
            stderr = ""
            if self._proc and self._proc.stderr:
                try:
                    stderr = self._proc.stderr.read(2000).decode("utf-8", errors="replace")
                except Exception:
                    pass
            raise McpStdioError(
                f"MCP process exited (code={self._proc.poll() if self._proc else '?'})"
                + (f". stderr: {stderr[:500]}" if stderr else "")
            )
        await asyncio.to_thread(self._write_sync, payload)

    async def _request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 900.0
    ) -> dict[str, Any]:
        async with self._request_lock:
            self._next_id += 1
            req_id = self._next_id
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending[req_id] = fut
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params or {},
                }
            )
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as e:
            self._pending.pop(req_id, None)
            raise McpStdioError(f"MCP request timed out: {method}") from e

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def list_tools(self) -> list[dict[str, Any]]:
        timeout = env_int("MCP_LIST_TOOLS_TIMEOUT_MS", 120_000) / 1000
        result = await self._request("tools/list", {}, timeout=timeout)
        return list(result.get("tools") or [])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        timeout = env_int("MCP_REQUEST_TIMEOUT_MS", 900_000) / 1000
        return await self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
