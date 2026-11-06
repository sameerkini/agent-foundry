"""Real local MCP protocol; scripted model, no API key or network server needed."""
import asyncio,sys,tempfile
from pathlib import Path
from mcp.server import MCPServer
from agent_foundry.mcp_adapter import MCPBridge,MCPConfig
from agent_foundry.registry import ToolRegistry
from agent_foundry.memory import MemoryStore
from agent_foundry.models import ScriptedModel
from agent_foundry.runner import Runner
from agent_foundry.types import ToolCall,ModelReply,RunConfig

if __name__=='__main__' and '--server' in sys.argv:
    server=MCPServer('local-demo')
    @server.tool()
    def multiply(a:int,b:int)->dict:return {'result':a*b}
    server.run(transport='stdio')
else:
    async def main():
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            config=MCPConfig(name='localdemo',allowed_tools=('multiply',),command=sys.executable,args=(str(Path(__file__).resolve()),'--server'),cwd=str(Path(__file__).resolve().parent),launch_approved=True)
            registry=ToolRegistry()
            async with MCPBridge(config) as bridge:
                await bridge.register(registry)
                model=ScriptedModel([ModelReply(calls=[ToolCall('multiply-1','mcp__localdemo__multiply',{'a':6,'b':7})]),ModelReply('The real local MCP tool returned its result. This is a scripted protocol demo, not model inference.')])
                runner=Runner(model,registry,MemoryStore(root/'state.sqlite'),root,config=RunConfig(allowed_capabilities=frozenset({'mcp.call'})))
                async def approve(request):
                    print('Explicit demo approval for:',request['tool'],request['arguments']);return True
                result=await runner.run('Multiply six by seven using the configured local MCP server.',approval=approve)
                print('Status:',result.status)
                for event in result.trace:
                    if event['kind']=='tool_finished':print('Tool evidence:',runner.store.get_artifact(event['artifact_id'])['content'])
    if __name__=='__main__':asyncio.run(main())
