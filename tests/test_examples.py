from pathlib import Path
import importlib.util,json
import pytest
from agent_foundry.cli import application,DemoModel
from agent_foundry.memory import MemoryStore
from agent_foundry.types import RunConfig,ModelReply,ToolCall,Tool
from agent_foundry.models import ScriptedModel
from agent_foundry.runner import Runner
from agent_foundry.registry import ToolRegistry

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.asyncio
async def test_scripted_fieldnote_plugin_runs_real_database_and_reports(tmp_path):
    store=MemoryStore(tmp_path/'state.sqlite')
    runner=application(ROOT,model=DemoModel(),store=store,config=RunConfig())
    result=await runner.run('Check observation quality',session_id='test')
    assert result.status=='completed'
    assert 'no model inference' in result.answer
    results=[store.get_artifact(x)['content'] for x in result.working_memory['artifacts']]
    assert results[0]['rows']==[{'scheduled':108,'valid':101,'suspect':4,'missing':3}]
    assert results[1]['documents']

@pytest.mark.asyncio
async def test_fieldnote_loader_has_skills_and_rejects_database_writes(tmp_path):
    model=ScriptedModel([ModelReply(calls=[ToolCall('bad','query_observations',{'sql':'DELETE FROM readings'})]),ModelReply('Cannot modify observations.')])
    runner=application(ROOT,model=model,store=MemoryStore(tmp_path/'state.sqlite'),config=RunConfig())
    assert runner.registry.get('load_skill').capability=='skills.read'
    result=await runner.run('Read only')
    assert any(e['kind']=='tool_failed' for e in result.trace)

@pytest.mark.asyncio
async def test_known_configured_secrets_never_reach_model_or_tool(tmp_path):
    calls=[];secret='dummy-configured-secret-not-a-key'
    def handler(ctx,args):calls.append(args);return {'ok':True}
    registry=ToolRegistry();registry.register(Tool('echo','echo',{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},handler))
    model=ScriptedModel([ModelReply(calls=[ToolCall('secret','echo',{'text':secret})]),ModelReply('Finished')])
    runner=Runner(model,registry,MemoryStore(tmp_path/'state.sqlite'),tmp_path,secrets=[secret])
    result=await runner.run('Here is '+secret)
    assert not calls
    assert secret not in json.dumps(model.requests)
    assert secret not in json.dumps(result.trace)

@pytest.mark.asyncio
async def test_approved_skill_loads_progressively_as_task_instructions(tmp_path):
    model=ScriptedModel([ModelReply(calls=[ToolCall('skill','load_skill',{'name':'research'})]),ModelReply('Ready to inspect evidence.')])
    runner=application(ROOT,model=model,store=MemoryStore(tmp_path/'state.sqlite'),config=RunConfig())
    result=await runner.run('Use the approved research skill.')
    assert result.status=='completed'
    assert 'skills' in result.working_memory
    assert 'Approved host-loaded task skills' in model.requests[1]['messages'][0]['content']
    assert 'A sample mean is not time-weighted' in model.requests[1]['messages'][0]['content']

@pytest.mark.asyncio
async def test_final_lifecycle_guard_cannot_leak_blocked_answer_into_history(tmp_path):
    from agent_foundry.guards import Hooks,Decision
    def block_release(phase,event):return Decision(False,'final policy') if phase=='after_run' else Decision()
    store=MemoryStore(tmp_path/'state.sqlite');registry=ToolRegistry()
    model=ScriptedModel([ModelReply('Blocked draft')])
    runner=Runner(model,registry,store,tmp_path,hooks=Hooks([block_release]))
    result=await runner.run('A question')
    assert result.status=='blocked'
    assert store.history(runner._session_key('default'))==[]

@pytest.mark.asyncio
async def test_empty_final_answer_does_not_create_a_completed_turn(tmp_path):
    store=MemoryStore(tmp_path/'state.sqlite')
    runner=Runner(ScriptedModel([ModelReply()]),ToolRegistry(),store,tmp_path)
    result=await runner.run('A question')
    assert result.status=='error'
    assert store.history(runner._session_key('default'))==[]
