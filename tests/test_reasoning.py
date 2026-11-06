"""Public planning/review workflow tests; fixtures perform zero model API calls."""
from copy import deepcopy
from dataclasses import replace
import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from agent_foundry import reasoning
from agent_foundry.cli import DemoModel, application, execute, parser
from agent_foundry.guards import Hooks
from agent_foundry.memory import MemoryStore
from agent_foundry.registry import ToolRegistry
from agent_foundry.runner import Runner
from agent_foundry.types import ModelReply, RunConfig, Tool, ToolCall, ToolContext, ToolResult


ANSWER = 'The observed value is 12.'
ROOT = Path(__file__).resolve().parents[1]
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def call(identifier, name, args=None):
    return ModelReply(calls=[ToolCall(identifier, name, args or {})])


def plan():
    return call('plan', 'record_plan', {'parts': ['Find the observed value.']})


def coverage(identifier, *, draft=ANSWER):
    return {'draft': draft, 'coverage': [
        {'part_id': 'P1', 'status': 'supported', 'artifact_ids': [identifier],
         'note': 'The original observation supplies the value.'}], 'limitations': []}


def review(messages, tools):
    observed = next(json.loads(m['content']) for m in messages
                    if m['role'] == 'tool' and m['tool_call_id'] == 'read')
    return call('review', 'review_answer', coverage(observed['artifact_id']))


class FixtureModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def respond(self, messages, tools, **kwargs):
        self.requests.append({'messages': deepcopy(messages), 'tools': deepcopy(tools)})
        reply = self.replies.pop(0)
        return reply(messages, tools) if callable(reply) else reply


def build(tmp_path, replies, *, config=None, hooks=None, extra=(), secrets=()):
    registry = ToolRegistry()
    registry.register(Tool('lookup', 'Read an observed value.', EMPTY,
                           lambda ctx, args: ToolResult({'value': 12})))
    registry.register_many(extra)
    store = MemoryStore(tmp_path / 'state.sqlite')
    model = FixtureModel(replies)
    runner = Runner(model, registry, store, tmp_path,
                    config=config or RunConfig(reasoning_mode='plan-review'),
                    hooks=hooks, secrets=secrets)
    return runner, model, store, registry


@pytest.mark.asyncio
async def test_off_is_default_and_has_no_new_schemas_or_extra_exchanges(tmp_path):
    runner, model, _, registry = build(tmp_path, [ModelReply(ANSWER)], config=RunConfig())
    result = await runner.run('Read the value')
    assert result.status == 'completed'
    assert len(model.requests) == 1
    assert runner.registry is registry
    assert [d['function']['name'] for d in model.requests[0]['tools']] == ['lookup']
    assert result.working_memory == {'plan': [], 'notes': {}, 'artifacts': []}
    assert reasoning.INSTRUCTIONS not in model.requests[0]['messages'][0]['content']


@pytest.mark.asyncio
async def test_on_clones_registry_and_completes_an_exact_reviewed_draft(tmp_path):
    runner, model, store, registry = build(tmp_path, [plan(), call('read', 'lookup'), review, ModelReply(ANSWER)])
    assert registry.names() == ['lookup']
    assert set(runner.registry.names()) == {'lookup', 'record_plan', 'review_answer'}
    result = await runner.run('Read the value')
    assert result.status == 'completed' and result.answer == ANSWER
    assert len(model.requests) == 4
    assert reasoning.INSTRUCTIONS in model.requests[0]['messages'][0]['content']
    reviewed = result.working_memory['reasoning']['review']
    assert reviewed['draft_sha256'] == reasoning.answer_hash(ANSWER)
    assert reviewed['assessment'] == 'model_self_assessment_not_semantic_verification'
    assert len(result.working_memory['artifacts']) == 3
    assert len(store.history(runner._session_key('default'))) == 1


@pytest.mark.asyncio
async def test_ordinary_work_is_denied_before_plan_and_can_recover(tmp_path):
    runner, model, store, _ = build(tmp_path, [call('premature', 'lookup'), plan(),
        call('read', 'lookup'), review, ModelReply(ANSWER)])
    result = await runner.run('Read the value')
    assert result.status == 'completed'
    failures = [e for e in result.trace if e['kind'] == 'tool_failed']
    assert len(failures) == 1 and 'record_plan' in failures[0]['error']
    assert store.load_run(result.run_id)['tool_calls'] == 4
    assert len(model.requests) == 5


@pytest.mark.asyncio
async def test_premature_final_repair_uses_existing_step_budget(tmp_path):
    runner, model, store, _ = build(tmp_path, [ModelReply(ANSWER), ModelReply(ANSWER)],
                                   config=RunConfig(reasoning_mode='plan-review', max_steps=2))
    result = await runner.run('Read the value')
    assert result.status == 'budget_exhausted'
    assert len(model.requests) == 2
    assert any('Runtime plan-review check' in m.get('content', '')
               for m in model.requests[1]['messages'])
    assert store.history(runner._session_key('default')) == []


@pytest.mark.asyncio
async def test_plan_alone_is_not_an_answer_review(tmp_path):
    runner, _, _, _ = build(tmp_path, [plan(), ModelReply(ANSWER)],
                            config=RunConfig(reasoning_mode='plan-review', max_steps=2))
    result = await runner.run('Read the value')
    assert result.status == 'budget_exhausted'
    assert any('review_answer' in e.get('reason', '') for e in result.trace)


@pytest.mark.asyncio
async def test_changed_final_text_requires_reviewed_text_or_a_new_review(tmp_path):
    runner, model, _, _ = build(tmp_path, [plan(), call('read', 'lookup'), review,
        ModelReply(ANSWER + ' Changed.'), ModelReply(ANSWER)])
    result = await runner.run('Read the value')
    assert result.status == 'completed' and result.answer == ANSWER
    assert len(model.requests) == 5
    assert any(e['kind'] == 'review_required' and 'differs' in e['reason'] for e in result.trace)


@pytest.mark.asyncio
@pytest.mark.parametrize('next_tool', ['lookup', 'working_note', 'broken'])
async def test_ordinary_reads_notes_and_failed_work_invalidate_review(tmp_path, next_tool):
    def note(ctx, args):
        ctx.state['notes']['updated'] = 'A new public note.'
        return {'saved': True}
    def broken(ctx, args):
        raise ValueError('Source unavailable')
    extra = [Tool('working_note', 'Write notes', EMPTY, note, 'working.write', cacheable=False),
             Tool('broken', 'A failed read', EMPTY, broken)]
    runner, _, _, _ = build(tmp_path, [plan(), call('read', 'lookup'), review,
        call('later', next_tool), ModelReply(ANSWER)], extra=extra,
        config=RunConfig(reasoning_mode='plan-review', max_steps=5))
    result = await runner.run('Read the value')
    assert result.status == 'budget_exhausted'
    assert 'review' not in result.working_memory['reasoning']
    if next_tool == 'lookup':
        assert any(e['kind'] == 'tool_reused' for e in result.trace)


@pytest.mark.asyncio
async def test_tool_budget_counts_planning_and_review(tmp_path):
    runner, model, store, _ = build(tmp_path, [plan(), call('read', 'lookup')],
        config=RunConfig(reasoning_mode='plan-review', max_tool_calls=2))
    result = await runner.run('Read the value')
    assert result.status == 'budget_exhausted' and len(model.requests) == 2
    assert store.load_run(result.run_id)['tool_calls'] == 2


@pytest.mark.asyncio
async def test_scaffold_uses_normal_hooks_and_capabilities(tmp_path):
    phases = []
    def hook(phase, event):
        if phase == 'before_tool':
            phases.append(event['tool'])
            return False
    runner, _, _, _ = build(tmp_path, [plan()], hooks=Hooks([hook]))
    result = await runner.run('Read the value')
    assert result.status == 'blocked' and phases == ['record_plan']
    assert result.working_memory['plan'] == []

    runner, model, _, _ = build(tmp_path, [plan(), ModelReply(ANSWER)],
        config=RunConfig(reasoning_mode='plan-review', max_steps=2,
                         allowed_capabilities=frozenset({'data.read'})))
    result = await runner.run('Read the value')
    assert result.status == 'budget_exhausted'
    assert 'record_plan' not in {t['function']['name'] for t in model.requests[0]['tools']}
    assert any('Capability denied' in e.get('error', '') for e in result.trace)


@pytest.mark.asyncio
async def test_secret_in_scaffold_arguments_is_rejected_and_redacted(tmp_path):
    secret = 'fixture-private-key'
    runner, model, store, _ = build(tmp_path, [call('plan', 'record_plan', {'parts': [secret]}), ModelReply(ANSWER)],
        secrets=(secret,), config=RunConfig(reasoning_mode='plan-review', max_steps=2))
    result = await runner.run('Read the value')
    assert result.status == 'budget_exhausted'
    assert result.working_memory['plan'] == []
    assert secret not in json.dumps(store.load_run(result.run_id))
    assert any('Configured secrets' in e.get('error', '') for e in result.trace)


@pytest.mark.asyncio
async def test_review_boundaries_survive_approval_pause_and_mode_change_is_rejected(tmp_path):
    writes = []
    write = Tool('write', 'Record observed value', EMPTY,
                 lambda ctx, args: writes.append(12) or {'value': 12},
                 'data.write', side_effects=True)
    config = RunConfig(reasoning_mode='plan-review',
                       allowed_capabilities=RunConfig().allowed_capabilities | {'data.write'})
    runner, _, store, registry = build(tmp_path, [plan(), call('read', 'write'), review, ModelReply(ANSWER)],
                                       config=config, extra=[write])
    paused = await runner.run('Record the value')
    assert paused.status == 'awaiting_approval' and writes == []
    changed = Runner(FixtureModel([]), registry, store, tmp_path,
                     config=replace(config, reasoning_mode='off'))
    with pytest.raises(ValueError, match='Runtime/configuration changed'):
        await changed.resume(paused.run_id, approved_action_ids=[paused.pending_approvals[0]['action_id']])
    result = await runner.resume(paused.run_id, approved_action_ids=[paused.pending_approvals[0]['action_id']])
    assert result.status == 'completed' and writes == [12]
    assert store.load_run(result.run_id)['tool_calls'] == 3


def review_context(tmp_path, source_tool='lookup'):
    store = MemoryStore(tmp_path / 'state.sqlite')
    ctx = ToolContext(tmp_path, 'current-run', 'chat',
                      {'plan': [], 'notes': {}, 'artifacts': []}, {'memory': store})
    registry = ToolRegistry()
    registry.register_many(reasoning.scaffold_tools())
    registry.get('record_plan').handler(ctx, {'parts': ['Find the value.']})
    identifier = store.put_artifact(ctx.run_id, {'value': 12}, {'tool': source_tool})
    ctx.state['artifacts'].append(identifier)
    return ctx, registry, store, identifier


@pytest.mark.parametrize('mutate', [
    lambda a: a.update(chain_of_thought='private reasoning'),
    lambda a: a.update(reasoning='private reasoning'),
    lambda a: a.update(parts=['x'] * 9),
    lambda a: a.update(parts=['x' * 301]),
    lambda a: a.update(parts=[]),
])
def test_plan_schema_rejects_private_fields_and_unbounded_parts(mutate):
    registry = ToolRegistry(); registry.register_many(reasoning.scaffold_tools())
    args = {'parts': ['Read the supplied data.']}; mutate(args)
    with pytest.raises(ValidationError):
        registry.validate(registry.get('record_plan'), args)


@pytest.mark.parametrize('mutate', [
    lambda a: a.update(thoughts='private reasoning'),
    lambda a: a.update(draft='x' * 16001),
    lambda a: a['coverage'][0].update(private_reasoning='private'),
    lambda a: a['coverage'][0].update(status='verified_truth'),
    lambda a: a['coverage'][0].update(artifact_ids=['x'] * 9),
])
def test_review_schema_has_only_bounded_public_fields(tmp_path, mutate):
    _, registry, _, identifier = review_context(tmp_path)
    args = coverage(identifier); mutate(args)
    with pytest.raises(ValidationError):
        registry.validate(registry.get('review_answer'), args)


@pytest.mark.parametrize('source_tool', sorted(reasoning.NON_EVIDENCE_TOOLS))
def test_scaffold_notes_skills_and_memory_are_not_source_evidence(tmp_path, source_tool):
    ctx, registry, _, identifier = review_context(tmp_path, source_tool)
    with pytest.raises(ValueError, match='not original source evidence'):
        registry.get('review_answer').handler(ctx, coverage(identifier))


def test_review_rejects_unknown_foreign_and_missing_part_references(tmp_path):
    ctx, registry, store, identifier = review_context(tmp_path)
    handler = registry.get('review_answer').handler
    args = coverage(identifier); args['coverage'][0]['part_id'] = 'P2'
    with pytest.raises(ValueError, match='every planned part'):
        handler(ctx, args)
    with pytest.raises(ValueError, match='current run'):
        handler(ctx, coverage('unknown-artifact'))
    foreign = store.put_artifact('other-run', {'value': 12}, {'tool': 'lookup'})
    ctx.state['artifacts'].append(foreign)
    with pytest.raises(ValueError, match='current run'):
        handler(ctx, coverage(foreign))
    args = coverage(identifier); args['coverage'][0]['artifact_ids'] = []
    with pytest.raises(ValueError, match='require original source artifacts'):
        handler(ctx, args)


def test_review_is_stale_after_public_state_change_and_does_not_claim_truth(tmp_path):
    ctx, registry, _, identifier = review_context(tmp_path)
    result = registry.get('review_answer').handler(ctx, coverage(identifier))
    assert reasoning.completion_issue(ctx, ANSWER) is None
    assert 'factual correctness is not established' in result.content['notice']
    ctx.state['notes']['new'] = 'Changed after review'
    assert 'changed' in reasoning.completion_issue(ctx, ANSWER)


def test_a_gap_can_be_honestly_reviewed_without_fabricated_evidence(tmp_path):
    ctx, registry, _, _ = review_context(tmp_path)
    args = {'draft': 'The data is unavailable.', 'coverage': [
        {'part_id': 'P1', 'status': 'missing', 'artifact_ids': [],
         'note': 'No applicable evidence was retrieved.'}], 'limitations': ['Missing data.']}
    registry.get('review_answer').handler(ctx, args)
    assert reasoning.completion_issue(ctx, args['draft']) is None


@pytest.mark.asyncio
async def test_source_tool_cannot_spoof_the_recorded_artifact_tool_name(tmp_path):
    spoof = Tool('spoof', 'Read data', EMPTY,
                 lambda ctx, args: ToolResult({'value': 12}, {'tool': 'review_answer'}))
    runner, _, store, _ = build(tmp_path, [call('read', 'spoof'), ModelReply(ANSWER)],
                                 config=RunConfig(), extra=[spoof])
    result = await runner.run('Read data')
    artifact = store.get_artifact(result.working_memory['artifacts'][0])
    assert artifact['metadata']['tool'] == 'spoof'


@pytest.mark.parametrize('command,args', [('demo', []), ('chat', ['A question']), ('resume', ['run', '--approve', 'exact-id'])])
def test_cli_all_run_commands_expose_reasoning_mode(command, args):
    parsed = parser().parse_args([command, *args, '--reasoning', 'plan-review'])
    assert parsed.reasoning == 'plan-review'
    assert parser().parse_args([command, *args]).reasoning is None


@pytest.mark.asyncio
async def test_demo_plan_review_runs_actual_tools_without_model_api(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    runner = application(ROOT, model=DemoModel('plan-review'), store=store,
                         config=RunConfig(reasoning_mode='plan-review'))
    result = await runner.run('Count flags and retrieve the protocol.')
    assert result.status == 'completed'
    assert 'no model inference' in result.answer
    assert len(result.working_memory['plan']) == 2
    assert len(result.working_memory['reasoning']['review']['coverage']) == 2
    assert result.usage == {}
    assert len([e for e in result.trace if e['kind'] == 'model_step']) == 5


@pytest.mark.asyncio
async def test_cli_environment_default_and_flag_override(monkeypatch, tmp_path):
    import agent_foundry.cli as cli
    def configured(root, *, model, store, config):
        return application(ROOT, model=model, store=store, config=config)
    monkeypatch.setattr(cli, 'application', configured)
    monkeypatch.setenv('REASONING_SCAFFOLD', 'plan-review')
    result = await execute(parser().parse_args(['--root', str(tmp_path), 'demo']))
    assert result['working_memory']['reasoning']['mode'] == 'plan-review'
    result = await execute(parser().parse_args(['--root', str(tmp_path), 'demo', '--reasoning', 'off']))
    assert 'reasoning' not in result['working_memory']
    monkeypatch.setenv('REASONING_SCAFFOLD', 'unknown')
    with pytest.raises(ValueError, match='REASONING_SCAFFOLD'):
        await execute(parser().parse_args(['--root', str(tmp_path), 'demo']))


def test_unknown_runtime_mode_and_scaffold_name_collision_fail_closed(tmp_path):
    with pytest.raises(ValueError, match='reasoning_mode'):
        build(tmp_path, [], config=RunConfig(reasoning_mode='hidden-cot'))
    collision = Tool('record_plan', 'An unrelated tool', EMPTY, lambda ctx, args: {})
    with pytest.raises(ValueError):
        build(tmp_path, [], extra=[collision])


@pytest.mark.asyncio
async def test_fingerprint_is_canonical_after_a_thread_retains_code_constants(tmp_path):
    def retain(ctx, args):
        ctx.state['note'] = 'Unique retained handler literal for fingerprint regression.'
    ctx = ToolContext(tmp_path, 'run', 'session', {})
    registry = ToolRegistry()
    registry.register(Tool('retain', 'Keep a public note.', EMPTY, retain))
    # marshal.dumps(code) can change reference flags when the string in
    # co_consts is retained by a second object after the first execution.
    before = registry.fingerprint()
    await asyncio.to_thread(retain, ctx, {})
    assert registry.fingerprint() == before
    assert ctx.state['note'].startswith('Unique retained')


def test_fingerprint_still_detects_changed_literals_and_nested_code():
    def first(ctx, args):
        return [value + 1 for value in [12]]
    def second(ctx, args):
        return [value + 2 for value in [12]]
    # Normalize the descriptive handler name so code contents make the change.
    second.__qualname__ = first.__qualname__
    a = ToolRegistry(); b = ToolRegistry()
    a.register(Tool('lookup', 'Read', EMPTY, first))
    b.register(Tool('lookup', 'Read', EMPTY, second))
    assert a.fingerprint() != b.fingerprint()


@pytest.mark.asyncio
async def test_long_valid_question_is_not_rejected_by_memory_search(tmp_path):
    runner, model, _, _ = build(tmp_path, [ModelReply(ANSWER)], config=RunConfig())
    question = 'Observation quality? ' + 'x' * 6000
    result = await runner.run(question)
    assert result.status == 'completed'
    assert any(m.get('content') == question for m in model.requests[0]['messages'])
