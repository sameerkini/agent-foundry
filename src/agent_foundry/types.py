"""Small provider-neutral contracts shared by runtime extensions."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class ModelReply:
    text: str = ''
    calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

class Model(Protocol):
    async def respond(self, messages: list[dict], tools: list[dict], *, timeout: float, max_output_tokens: int) -> ModelReply: ...

@dataclass
class ToolContext:
    workspace: Path
    run_id: str
    session_id: str
    state: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)

@dataclass
class ToolResult:
    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable
    capability: str = 'data.read'
    side_effects: bool = False
    timeout: float = 10.0
    provenance: str = 'builtin'
    cacheable: bool = True

    def definition(self):
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description, 'parameters': self.schema}}

@dataclass
class RunConfig:
    max_steps: int = 8
    max_tool_calls: int = 16
    max_seconds: float = 120.0
    max_output_tokens: int = 2000
    context_char_budget: int = 32000
    observation_char_limit: int = 6000
    allowed_capabilities: frozenset[str] = frozenset({'data.read', 'files.read', 'skills.read', 'memory.read', 'working.write'})

@dataclass
class RunResult:
    run_id: str
    session_id: str
    status: str
    answer: str
    trace: list[dict]
    usage: dict[str, int]
    pending_approvals: list[dict] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
