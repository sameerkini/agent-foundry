"""Explicit control points. Regex and trusted Python hooks are not a sandbox."""
from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
import inspect, json, re

@dataclass(frozen=True)
class Decision:
    allowed: bool = True
    reason: str = ''

class GuardBlocked(RuntimeError): pass

class RegexGuard:
    def __init__(self, pattern, *, phases=('input','output'), label='pattern rule'):
        self.regex=re.compile(pattern,re.I);self.phases=frozenset(phases);self.label=label
    def __call__(self, phase, event):
        if phase not in self.phases:return Decision()
        return Decision(not bool(self.regex.search(json.dumps(event,ensure_ascii=False))),self.label)

class Hooks:
    """Hook snapshots are copied; any failure blocks the operation."""
    PHASES={'input','before_model','after_model','before_tool','after_tool','output','after_run'}
    def __init__(self, callbacks=()):self.callbacks=list(callbacks)
    def add(self, callback):self.callbacks.append(callback)
    async def check(self, phase, event):
        if phase not in self.PHASES:raise ValueError('Unknown hook phase')
        for callback in self.callbacks:
            try:
                verdict=callback(phase,deepcopy(event))
                if inspect.isawaitable(verdict):verdict=await verdict
            except Exception as exc:raise GuardBlocked(f'{phase} hook failed: {type(exc).__name__}') from None
            if verdict is False or isinstance(verdict,Decision) and not verdict.allowed:
                raise GuardBlocked(f'{phase} blocked: {getattr(verdict,"reason","policy hook")}')

class Redactor:
    def __init__(self, secrets=()):self.secrets=tuple(s for s in secrets if s)
    def text(self, value):
        for secret in self.secrets:value=value.replace(secret,'[REDACTED]')
        return value
    def value(self,value):
        if isinstance(value,str):return self.text(value)
        if isinstance(value,dict):return {self.text(str(k)):self.value(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [self.value(v) for v in value]
        return value
