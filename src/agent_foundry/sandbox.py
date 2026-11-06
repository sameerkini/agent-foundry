"""Optional constrained Docker execution; never falls back to host execution.

Containers share a host kernel and trusted daemon. This is a useful execution
boundary, not a claim of VM-grade isolation or protection from daemon compromise.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from .types import Tool, ToolResult


class SandboxUnavailable(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, image="python:3.13-slim", *, docker="docker", max_seconds=5.0,
                 memory_mb=128, cpus=.5, max_output_bytes=16384):
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_@-]{0,255}", image):
            raise ValueError("Invalid trusted container image")
        if not 0 < max_seconds <= 30 or not 32 <= memory_mb <= 1024 or not .1 <= cpus <= 2 or not 1024 <= max_output_bytes <= 65536:
            raise ValueError("Sandbox limits are outside their supported range")
        self.image, self.docker = image, docker
        self.max_seconds, self.memory_mb, self.cpus, self.max_output_bytes = max_seconds, memory_mb, cpus, max_output_bytes

    @property
    def available(self):
        return shutil.which(self.docker) is not None

    def build_command(self, snapshot: Path, name: str) -> list[str]:
        executable = shutil.which(self.docker)
        if not executable:
            raise SandboxUnavailable("Docker is unavailable; code execution remains disabled")
        snapshot = Path(snapshot).resolve()
        if any(c in str(snapshot) for c in ',\n\r'):
            raise ValueError("Snapshot path is not portable to Docker")
        if not re.fullmatch(r"foundry-[a-f0-9]{32}", name):
            raise ValueError("Invalid owned container identifier")
        return [executable, 'run', '--rm', '--name', name, '--pull', 'never', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '32', '--memory', f'{self.memory_mb}m', '--cpus', str(self.cpus),
                '--user', '65534:65534', '--workdir', '/work',
                '--tmpfs', '/work:rw,nosuid,nodev,noexec,size=16m,mode=1777',
                '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777',
                '--mount', f'type=bind,src={snapshot},dst=/input,readonly',
                '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'PYTHONNOUSERSITE=1',
                '-i', self.image, 'python', '-I', '-']

    def run_python(self, code: str, input_files: dict[str, str | bytes] | None = None) -> ToolResult:
        if not self.available:
            raise SandboxUnavailable("Docker is unavailable; no host execution fallback exists")
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 65536:
            raise ValueError("Python code must be nonempty and at most 64 KiB")
        files = input_files or {}
        if not isinstance(files, dict) or len(files) > 20:
            raise ValueError("Use at most 20 explicitly supplied input files")
        total = 0
        with tempfile.TemporaryDirectory(prefix='foundry-input-') as directory:
            snapshot = Path(directory)
            os.chmod(snapshot, 0o755)
            for name, content in files.items():
                if not isinstance(name, str) or not isinstance(content, (str, bytes)):
                    raise ValueError("Input files need text names and text/bytes content")
                relative = Path(name)
                if relative.is_absolute() or '..' in relative.parts or not relative.parts or any(p.startswith('.') for p in relative.parts):
                    raise ValueError("Input names must be relative non-hidden paths")
                data = content.encode() if isinstance(content, str) else content
                total += len(data)
                if total > 1024 * 1024:
                    raise ValueError("Explicit input snapshot exceeds 1 MiB")
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                os.chmod(target, 0o444)
            name = 'foundry-' + uuid.uuid4().hex
            command = self.build_command(snapshot, name)
            # No model/provider credentials are exported to the container.
            env = {k: os.environ[k] for k in ['PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT'] if k in os.environ}
            output = bytearray()
            exceeded = threading.Event()
            started = time.monotonic()
            timed_out = False
            with tempfile.TemporaryFile() as stdin:
                stdin.write(code.encode());stdin.seek(0)
                process = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
                def drain():
                    while True:
                        block = process.stdout.read(4096)
                        if not block:
                            break
                        remaining = self.max_output_bytes - len(output)
                        output.extend(block[:max(remaining, 0)])
                        if len(block) > remaining:
                            exceeded.set()
                reader = threading.Thread(target=drain, daemon=True)
                reader.start()
                try:
                    while process.poll() is None:
                        if exceeded.is_set() or time.monotonic() - started >= self.max_seconds:
                            timed_out = not exceeded.is_set()
                            break
                        time.sleep(.02)
                finally:
                    # Always remove only the unique container created by this invocation.
                    try:
                        subprocess.run([command[0], 'rm', '-f', name], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=3, env=env, check=False)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    reader.join(timeout=2)
            return ToolResult({'exit_code': process.returncode, 'stdout': output.decode(errors='replace'),
                               'truncated': exceeded.is_set(), 'timed_out': timed_out},
                              {'runtime': 'docker', 'network': 'none', 'image': self.image,
                               'input_files': len(files), 'elapsed_seconds': round(time.monotonic() - started, 3)})

    def tool(self):
        async def run(context, args):
            return await asyncio.to_thread(self.run_python, args['code'], args.get('input_files'))
        return Tool('sandbox_python', 'Run Python in an optional constrained Docker container; only explicit input files are visible.',
                    {'type': 'object', 'properties': {'code': {'type': 'string', 'minLength': 1, 'maxLength': 65536},
                     'input_files': {'type': 'object', 'additionalProperties': {'type': 'string'}}},
                     'required': ['code'], 'additionalProperties': False}, run,
                    capability='code.execute', side_effects=True, timeout=self.max_seconds + 8,
                    provenance='docker-sandbox', cacheable=False)
