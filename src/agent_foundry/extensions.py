"""Trusted instruction skills and declarative, non-executable plugin bundles.

These loaders do not sandbox Python. A host registers approved factories; a
plugin can select a factory, but cannot import code or grant itself permissions.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from .types import Tool

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,120}\Z")
_RESERVED = {"capability", "capabilities", "side_effects", "provenance", "permissions", "handler", "module", "import", "command", "env"}


def _inside(root: Path, value: str | Path) -> Path:
    root = root.resolve()
    raw = Path(value)
    if ".." in raw.parts:
        raise ValueError("Paths cannot traverse parent directories")
    path = raw if raw.is_absolute() else root / raw
    if not path.resolve().is_relative_to(root):
        raise ValueError("Extension path escapes its approved root")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Extension paths cannot contain symbolic links")
    return path.resolve()


def _read(path: Path, maximum: int = 65536) -> str:
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Extension is missing or exceeds its size limit")
    return path.read_text(encoding="utf-8")


def _register(registry, tools: list[Tool]) -> None:
    """Validate all collisions before publishing a bundle."""
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError("Extension bundle contains duplicate tool names")
    existing = set(registry.names())
    if existing.intersection(names):
        raise ValueError("Extension cannot replace an existing tool")
    if hasattr(registry, "register_many"):
        registry.register_many(tools)
    else:
        for tool in tools:
            registry.register(tool)


class Skills:
    """Discover metadata first; load approved instructions only when requested."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("Skill root must be an existing directory")

    def list(self) -> list[dict]:
        descriptors = []
        names = set()
        candidates = sorted(self.root.rglob("*.md"))
        if len(candidates) > 100:
            raise ValueError("Skill catalog exceeds 100 files")
        for raw in candidates:
            path = _inside(self.root, raw)
            relative = path.relative_to(self.root)
            name = relative.parent.as_posix() if path.name == "SKILL.md" else relative.with_suffix("").as_posix()
            if name == ".":
                name = self.root.name
            if not _NAME.fullmatch(name) or name in names:
                raise ValueError("Skill names must be unique and portable")
            text = _read(path)
            # A short, non-executable front matter subset avoids YAML object loading.
            description = ""
            if text.startswith("---\n"):
                header, separator, _ = text[4:].partition("\n---\n")
                if separator:
                    for line in header.splitlines():
                        if line.startswith("description:"):
                            description = line.partition(":")[2].strip().strip("\"'")[:300]
            if not description:
                description = next((line.lstrip("# ") for line in text.splitlines() if line.strip() and line.strip() != "---"), "Instruction skill")[:300]
            names.add(name)
            descriptors.append({"name": name, "description": description,
                                "path": relative.as_posix(), "sha256": hashlib.sha256(text.encode()).hexdigest()})
        return descriptors

    def load(self, name: str) -> str:
        if not isinstance(name, str) or not _NAME.fullmatch(name) or ".." in Path(name).parts:
            raise ValueError("Invalid skill name")
        matches = [item for item in self.list() if item["name"] == name]
        if not matches:
            raise ValueError("Skill is not in the approved catalog")
        return _read(_inside(self.root, matches[0]["path"]))


class PluginLoader:
    """Load a manifest from a host-approved root without executing plugin code.

    Manifest version 1: {"name": str, "version": 1, "skills": [relative.md],
    "tools": [{"factory": approved_id, "options": {factory-specific values}}]}.
    Factory code remains responsible for constraining its own data paths.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def load(self, path: str | Path, registry, tool_factories: dict[str, Callable]) -> dict:
        manifest_path = _inside(self.root, path)
        value = json.loads(_read(manifest_path))
        if not isinstance(value, dict) or set(value) - {"name", "version", "skills", "tools", "description"}:
            raise ValueError("Unknown plugin manifest fields")
        if not isinstance(value.get("name"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,49}", value["name"]):
            raise ValueError("Plugin name must be a portable identifier")
        if type(value.get("version")) is not int or value["version"] != 1:
            raise ValueError("Only plugin manifest version 1 is supported")
        skills = value.get("skills", [])
        entries = value.get("tools", [])
        if not isinstance(skills, list) or len(skills) > 30 or not isinstance(entries, list) or len(entries) > 30:
            raise ValueError("Invalid plugin skills or tools list")
        skill_paths = []
        for relative in skills:
            if not isinstance(relative, str):
                raise ValueError("Skill paths must be text")
            skill = _inside(manifest_path.parent, relative)
            if skill.suffix != ".md":
                raise ValueError("Plugin skills must be Markdown instructions")
            _read(skill)
            skill_paths.append(skill.relative_to(self.root).as_posix())
        prepared = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - {"factory", "options"}:
                raise ValueError("Plugins cannot override tool permissions or import code")
            factory = entry.get("factory")
            options = entry.get("options", {})
            if not isinstance(factory, str) or factory not in tool_factories or not isinstance(options, dict):
                raise ValueError("Plugin requests an unknown approved factory")
            if _has_reserved_options(options):
                raise ValueError("Plugin options cannot grant capabilities or execute code")
            tool = tool_factories[factory](options)
            if not isinstance(tool, Tool):
                raise ValueError("Approved factory did not produce a Tool")
            prepared.append(replace(tool, provenance=f"plugin:{value['name']}:{factory}"))
        _register(registry, prepared)
        return {"name": value["name"], "version": 1, "skills": skill_paths,
                "tools": [tool.name for tool in prepared]}


def _has_reserved_options(value):
    if isinstance(value, dict):
        return bool(_RESERVED.intersection(value)) or any(_has_reserved_options(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_reserved_options(v) for v in value)
    return False
