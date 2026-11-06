from pathlib import Path
import json,sys
import pytest
from agent_foundry.mcp_adapter import MCPBridge,MCPConfig,_check_local_schema
from agent_foundry.registry import ToolRegistry
from agent_foundry.types import ToolContext


def make_server(tmp_path):
    path=tmp_path/'demo_server.py'
    path.write_text('''import os
from mcp.server import MCPServer
server=MCPServer("Local integration test")
@server.tool()
def add(a:int,b:int)->dict:
    return {"sum":a+b}
@server.tool()
def environment()->dict:
    return {"inherited_secret": "FOUNDRY_TEST_SECRET" in os.environ,"explicit":os.environ.get("FOUNDRY_EXPLICIT","")}
@server.tool()
def hidden_delete()->str:
    return "This tool must not be exposed"
if __name__=="__main__":server.run(transport="stdio")
''')
    return path


@pytest.mark.asyncio
async def test_real_local_mcp_tool_discovery_call_allowlist_and_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('FOUNDRY_TEST_SECRET','synthetic-do-not-inherit')
    path=make_server(tmp_path)
    config=MCPConfig(name='demo',allowed_tools=('add','environment'),command=sys.executable,args=(str(path),),cwd=str(tmp_path),env={'FOUNDRY_EXPLICIT':'allowed'},launch_approved=True)
    registry=ToolRegistry();context=ToolContext(tmp_path,'run','session')
    async with MCPBridge(config) as bridge:
        names=await bridge.register(registry)
        assert names==['mcp__demo__add','mcp__demo__environment']
        assert 'mcp__demo__hidden_delete' not in registry.names()
        tool=registry.get('mcp__demo__add')
        assert tool.schema['properties']['a']['type']=='integer'
        assert tool.side_effects and tool.capability=='mcp.call' and not tool.cacheable
        result=await tool.handler(context,{'a':7,'b':5})
        assert json.loads(result.content['content'][0]['text'])=={'sum':12}
        assert result.content['is_error'] is False
        environment=await registry.get('mcp__demo__environment').handler(context,{})
        assert json.loads(environment.content['content'][0]['text'])=={'inherited_secret':False,'explicit':'allowed'}
    with pytest.raises(RuntimeError):await tool.handler(context,{'a':1,'b':2})


@pytest.mark.asyncio
async def test_missing_allowlisted_mcp_tool_fails_before_registry_changes(tmp_path):
    config=MCPConfig(name='demo',allowed_tools=('not_present',),command=sys.executable,args=(str(make_server(tmp_path)),),cwd=str(tmp_path),launch_approved=True)
    registry=ToolRegistry()
    async with MCPBridge(config) as bridge:
        with pytest.raises(ValueError,match='missing'):await bridge.register(registry)
    assert registry.names()==[]


@pytest.mark.parametrize('kwargs',[
    {'name':'demo','allowed_tools':(), 'url':'https://example.test/mcp'},
    {'name':'demo','allowed_tools':('*',), 'url':'https://example.test/mcp'},
    {'name':'demo','allowed_tools':('add',), 'url':'http://remote.test/mcp'},
    {'name':'demo','allowed_tools':('add',), 'url':'https://user:password@example.test/mcp'},
    {'name':'demo','allowed_tools':('add',), 'command':'python'},
])
def test_mcp_connections_require_explicit_trusted_configuration(kwargs):
    with pytest.raises(ValueError):MCPConfig(**kwargs)


def test_untrusted_schema_cannot_trigger_network_reference_resolution():
    with pytest.raises(ValueError):_check_local_schema({'type':'object','properties':{'value':{'$ref':'https://example.test/schema'}}})
    _check_local_schema({'type':'object','$defs':{'value':{'type':'string'}},'properties':{'value':{'$ref':'#/$defs/value'}}})
