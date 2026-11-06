"""One capability registry for native, plugin, MCP and runtime tools."""
from __future__ import annotations
import hashlib, inspect, json, re, marshal
from dataclasses import asdict, is_dataclass
from pathlib import Path
from jsonschema import Draft202012Validator
from .types import Tool

class ToolRegistry:
    def __init__(self): self._tools = {}
    def register(self, tool: Tool):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', tool.name): raise ValueError('Invalid tool name')
        if tool.name in self._tools: raise ValueError(f'Tool name collision: {tool.name}')
        if tool.schema.get('type') != 'object': raise ValueError('Tool arguments must be an object')
        Draft202012Validator.check_schema(tool.schema)
        if not callable(tool.handler) or not 0 < tool.timeout <= 120: raise ValueError('Invalid tool handler/timeout')
        self._tools[tool.name] = tool
        return tool
    def names(self): return list(self._tools)
    def register_many(self, tools):
        candidate=ToolRegistry()
        candidate._tools=dict(self._tools)
        for tool in tools:candidate.register(tool)
        self._tools=candidate._tools
        return tools
    def get(self, name):
        if name not in self._tools: raise ValueError(f'Unknown tool: {name}')
        return self._tools[name]
    def list(self): return list(self._tools.values())
    def definitions(self, capabilities=None):
        return [t.definition() for t in self.list() if capabilities is None or t.capability in capabilities]
    def validate(self, tool, arguments):
        json.dumps(arguments, allow_nan=False)
        Draft202012Validator(tool.schema).validate(arguments)
    def fingerprint(self):
        def stable(value,seen=None):
            seen=set() if seen is None else seen
            if value is None or isinstance(value,(str,int,float,bool)):return value
            if isinstance(value,Path):return str(value.resolve())
            if id(value) in seen:return {'cycle':type(value).__qualname__}
            seen=seen|{id(value)}
            if isinstance(value,dict):return {str(k):stable(v,seen) for k,v in sorted(value.items(),key=lambda pair:str(pair[0]))}
            if isinstance(value,(list,tuple)):return [stable(v,seen) for v in value]
            if isinstance(value,(set,frozenset)):return sorted([stable(v,seen) for v in value],key=str)
            if is_dataclass(value):return stable(asdict(value),seen)
            details={'type':type(value).__module__+'.'+type(value).__qualname__}
            for key in ('root','workspace','path','config'):
                if hasattr(value,key):details[key]=stable(getattr(value,key),seen)
            return details
        rows=[]
        for t in self.list():
            fn=t.handler
            code=getattr(fn,'__code__',None)
            binding={'module':getattr(fn,'__module__',''),'name':getattr(fn,'__qualname__',type(fn).__qualname__),
                     'bytecode':hashlib.sha256(marshal.dumps(code)).hexdigest() if code else None,
                     'defaults':stable(getattr(fn,'__defaults__',None)),
                     'closure':[stable(cell.cell_contents) for cell in (getattr(fn,'__closure__',None) or [])]}
            rows.append({'name':t.name,'schema':t.schema,'capability':t.capability,'side_effects':t.side_effects,
                         'timeout':t.timeout,'cacheable':t.cacheable,'provenance':t.provenance,'handler':binding})
        return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
