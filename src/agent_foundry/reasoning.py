"""Optional public task planning and evidence review, never private reasoning.

These checks verify workflow and artifact references. They do not establish that
the cited evidence entails an answer, or that a model's coverage judgment is true.
"""
from __future__ import annotations

import hashlib
import json

from .types import Tool, ToolResult


MODES = ('off', 'plan-review')
SCAFFOLD_TOOLS = frozenset({'record_plan', 'review_answer'})
# Instructions, memory and wrappers are not original source observations.
NON_EVIDENCE_TOOLS = SCAFFOLD_TOOLS | frozenset({
    'working_note', 'load_skill', 'search_memory', 'remember', 'forget_memory',
    'read_artifact',
})
INSTRUCTIONS = """
Public plan-review workflow is enabled. Before other tools, call record_plan
with a short checklist of the user's requested deliverables. Do not provide
private chain-of-thought or hidden reasoning. Use ordinary tools for evidence.
Before a final answer, call review_answer with the exact final-answer draft and
one coverage entry for every planned part. Cite only original source artifacts
from this run; plans, reviews, notes, skills and saved memory are not source
evidence. Mark gaps partial/missing rather than inventing support. Review is your
self-assessment, not an automatic truth check. Any further ordinary tool use or
changed working notes requires a new review. Then return the reviewed draft
verbatim. Use the existing call/time budget; no separate reviewer model runs.
""".strip()


def answer_hash(answer: str) -> str:
    """Bind a review to the exact UTF-8 answer text, including whitespace."""
    return hashlib.sha256(answer.encode('utf-8')).hexdigest()


def _state_hash(ctx) -> str:
    """Fingerprint public notes and source artifacts, excluding scaffold logs."""
    public = {key: value for key, value in ctx.state.items()
              if key not in {'reasoning', 'artifacts'}}
    source_ids = []
    for identifier in ctx.state.get('artifacts', []):
        artifact = ctx.services['memory'].get_artifact(identifier)
        if artifact['metadata'].get('tool') not in SCAFFOLD_TOOLS:
            source_ids.append(identifier)
    public['artifacts'] = source_ids
    encoded = json.dumps(public, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def require_plan(state: dict, tool_name: str) -> None:
    """Reject ordinary tool work until a public plan has been recorded."""
    if tool_name != 'record_plan' and not state.get('plan'):
        raise ValueError('Call record_plan before other tools in plan-review mode')


def invalidate_review(state: dict, tool_name: str) -> None:
    """Clear a review before ordinary work, including cached reads and notes."""
    if tool_name not in SCAFFOLD_TOOLS:
        state.get('reasoning', {}).pop('review', None)


def completion_issue(ctx, answer: str) -> str | None:
    """Return repair feedback unless plan, current evidence and exact draft agree."""
    if not ctx.state.get('plan'):
        return 'Record a public task checklist with record_plan before completing.'
    review = ctx.state.get('reasoning', {}).get('review')
    if not review:
        return 'Call review_answer with the exact final draft and coverage for every planned part.'
    if review['draft_sha256'] != answer_hash(answer):
        return 'The final text differs from the reviewed draft. Review the exact draft again.'
    if review['working_sha256'] != _state_hash(ctx):
        return 'Working notes or source artifacts changed. Review the current answer again.'
    return None


def _record_plan(ctx, args):
    """Store a bounded, immutable checklist of public deliverables for this run."""
    if ctx.state.get('plan'):
        raise ValueError('The public task plan is already recorded for this run')
    plan = [{'id': f'P{index}', 'task': text}
            for index, text in enumerate(args['parts'], 1)]
    ctx.state['plan'] = plan
    ctx.state['reasoning'] = {'mode': 'plan-review'}
    return ToolResult({'plan': plan, 'notice': 'Public task checklist; not source evidence.'})


def _review_answer(ctx, args):
    """Validate coverage references and save a self-assessed review of one draft."""
    require_plan(ctx.state, 'review_answer')
    expected = {part['id'] for part in ctx.state['plan']}
    actual = [entry['part_id'] for entry in args['coverage']]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('Coverage must include every planned part exactly once')
    available = set(ctx.state.get('artifacts', []))
    for entry in args['coverage']:
        references = entry['artifact_ids']
        if len(references) != len(set(references)):
            raise ValueError('Evidence references must not repeat within a coverage entry')
        if entry['status'] in {'supported', 'partial'} and not references:
            raise ValueError('Supported/partial entries require original source artifacts')
        for identifier in references:
            if identifier not in available:
                raise ValueError('Evidence must reference an artifact observed in the current run')
            artifact = ctx.services['memory'].get_artifact(identifier)
            if artifact['run_id'] != ctx.run_id:
                raise ValueError('Evidence must belong to the current run')
            source_tool = artifact['metadata'].get('tool')
            if not source_tool or source_tool in NON_EVIDENCE_TOOLS:
                raise ValueError('Plans, reviews, notes, skills and memory wrappers are not original source evidence')
    review = {'draft_sha256': answer_hash(args['draft']),
              'working_sha256': _state_hash(ctx),
              'coverage': args['coverage'], 'limitations': args['limitations'],
              'assessment': 'model_self_assessment_not_semantic_verification'}
    ctx.state.setdefault('reasoning', {})['review'] = review
    return ToolResult({**review, 'notice': 'References and workflow checked; factual correctness is not established.'})


def scaffold_tools() -> list[Tool]:
    """Build strict declarative tools using the normal working-memory capability."""
    short_text = {'type': 'string', 'minLength': 1, 'maxLength': 300}
    plan_schema = {'type': 'object', 'properties': {
        'parts': {'type': 'array', 'minItems': 1, 'maxItems': 8,
                  'uniqueItems': True, 'items': short_text}},
        'required': ['parts'], 'additionalProperties': False}
    coverage = {'type': 'object', 'properties': {
        'part_id': {'type': 'string', 'pattern': '^P[1-8]$'},
        'status': {'enum': ['supported', 'partial', 'missing', 'not_applicable']},
        'artifact_ids': {'type': 'array', 'maxItems': 8, 'uniqueItems': True,
                         'items': {'type': 'string', 'minLength': 1, 'maxLength': 100}},
        'note': short_text},
        'required': ['part_id', 'status', 'artifact_ids', 'note'],
        'additionalProperties': False}
    review_schema = {'type': 'object', 'properties': {
        'draft': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
        'coverage': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': coverage},
        'limitations': {'type': 'array', 'maxItems': 8, 'items': short_text}},
        'required': ['draft', 'coverage', 'limitations'], 'additionalProperties': False}
    return [
        Tool('record_plan', 'Record a public checklist of requested deliverables, not private reasoning.',
             plan_schema, _record_plan, 'working.write', cacheable=False),
        Tool('review_answer', 'Self-assess coverage of the exact final draft against current-run source artifacts. Does not prove factual correctness.',
             review_schema, _review_answer, 'working.write', cacheable=False),
    ]
