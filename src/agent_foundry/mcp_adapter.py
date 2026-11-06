"""Explicitly configured MCP connections adapt into the ordinary tool registry.

A stdio peer is approved host code, not a sandbox. Server descriptions and
observations are data, and cannot grant permissions to the harness.
"""
from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import asyncio
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from .extensions import _register
from .types import Tool, ToolResult


@dataclass(frozen=True)
class MCPConfig:
    name: str
    allowed_tools: tuple[str, ...]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict, repr=False)
    launch_approved: bool = False
    timeout: float = 20.0

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,19}", self.name):
            raise ValueError("MCP server name must be a portable identifier")
        if bool(self.url) == bool(self.command):
            raise ValueError("Configure exactly one MCP URL or stdio command")
        if not self.allowed_tools or len(set(self.allowed_tools)) != len(self.allowed_tools) or any(not isinstance(t, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", t) for t in self.allowed_tools):
            raise ValueError("MCP needs an explicit, unique tool allowlist")
        if not 0 < self.timeout <= 120:
            raise ValueError("Invalid MCP timeout")
        if self.url:
            parsed = urlparse(self.url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MCP URL must not contain credentials")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote MCP endpoints require HTTPS")
        if self.command:
            if not self.launch_approved:
                raise ValueError("Starting a stdio MCP peer requires explicit host approval")
            if not Path(self.command).is_absolute() or not self.cwd or not Path(self.cwd).is_absolute():
                raise ValueError("stdio command and working directory must be absolute")
            if not Path(self.cwd).is_dir():
                raise ValueError("stdio working directory does not exist")
            if any(not isinstance(k, str) or not isinstance(v, str) or '\x00' in k + v for k, v in self.env.items()):
                raise ValueError("Invalid explicit MCP environment")


class MCPBridge:
    def __init__(self, config: MCPConfig):
        self.config = config
        self.client = None
        self._stack = None

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            from mcp import Client, StdioServerParameters
            target = self.config.url or StdioServerParameters(command=self.config.command, args=list(self.config.args), cwd=self.config.cwd, env=self._environment())
            self.client = await self._stack.enter_async_context(Client(target, read_timeout_seconds=self.config.timeout))
            return self
        except BaseException:
            await self._stack.aclose()
            self.client = None
            raise

    async def __aexit__(self, *exc):
        try:
            return await self._stack.__aexit__(*exc)
        finally:
            self.client = None

    def _environment(self):
        # Pass explicit credentials only. The SDK adds its documented tiny OS allowlist.
        return {"PATH": os.defpath, **self.config.env}

    async def tools(self) -> list[Tool]:
        if self.client is None:
            raise RuntimeError("MCP bridge is not connected")
        discovered = []
        cursor = None
        for _ in range(20):
            result = await asyncio.wait_for(self.client.list_tools(**({"cursor": cursor} if cursor else {})), self.config.timeout)
            discovered.extend(result.tools)
            cursor = getattr(result, 'next_cursor', None) or getattr(result, 'nextCursor', None)
            if not cursor:
                break
        else:
            raise ValueError("MCP discovery exceeded its page limit")
        by_name = {tool.name: tool for tool in discovered}
        if len(by_name) != len(discovered):
            raise ValueError("MCP peer advertised duplicate tool names")
        if set(self.config.allowed_tools) - set(by_name):
            raise ValueError("MCP peer is missing an explicitly allowed tool")
        prepared = []
        for name in self.config.allowed_tools:
            remote = by_name[name]
            schema = getattr(remote, 'input_schema', None) or getattr(remote, 'inputSchema', None)
            if not isinstance(schema, dict):
                raise ValueError("MCP tool is missing its argument schema")
            _check_local_schema(schema)
            local_name = f"mcp__{self.config.name}__{name}"
            if len(local_name) > 64:
                raise ValueError("Namespaced MCP tool name is too long")
            async def call(context, arguments, remote_name=name):
                if self.client is None:
                    raise RuntimeError("MCP connection was closed")
                result = await asyncio.wait_for(self.client.call_tool(remote_name, arguments), self.config.timeout)
                blocks = []
                for block in result.content:
                    if getattr(block, 'type', None) == 'text':
                        blocks.append({"type": "text", "text": block.text[:32000]})
                    else:
                        blocks.append({"type": getattr(block, 'type', 'unknown'), "omitted": True})
                structured = getattr(result, 'structured_content', None)
                if structured is None:
                    structured = getattr(result, 'structuredContent', None)
                if len(json.dumps(structured, default=str)) > 32000:
                    structured = {"truncated": True, "note": "Structured MCP output exceeded the adapter limit"}
                failed = bool(getattr(result, 'is_error', False) or getattr(result, 'isError', False))
                return ToolResult({"server": self.config.name, "tool": remote_name, "content": blocks,
                                   "structured_content": structured, "is_error": failed},
                                  {"external": True, "untrusted_observation": True})
            prepared.append(Tool(local_name, (remote.description or "External MCP tool")[:2000], schema, call,
                                 capability="mcp.call", side_effects=True, timeout=self.config.timeout,
                                 provenance=f"mcp:{self.config.name}", cacheable=False))
        return prepared

    async def register(self, registry) -> list[str]:
        prepared = await self.tools()
        _register(registry, prepared)
        return [tool.name for tool in prepared]


def _check_local_schema(schema):
    """Disallow network reference resolution from third-party schemas."""
    if isinstance(schema, dict):
        if '$ref' in schema and (not isinstance(schema['$ref'], str) or not schema['$ref'].startswith('#')):
            raise ValueError("MCP schemas may only use local JSON references")
        for value in schema.values():
            _check_local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _check_local_schema(value)
