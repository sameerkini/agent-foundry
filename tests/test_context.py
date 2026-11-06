from copy import deepcopy
import json

import pytest

from agent_foundry.context import ContextBuilder, ContextLimitError, preview


def exchange(index, size=800):
    return [
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': f'call-{index}', 'type': 'function',
             'function': {'name': 'read', 'arguments': '{"path":"data.txt"}'}}]},
        {'role': 'tool', 'tool_call_id': f'call-{index}',
         'content': json.dumps({'artifact_id': f'artifact-{index}', 'text': str(index) * size})},
    ]


def assert_complete_tool_protocol(messages):
    expected = set()
    for message in messages:
        if message['role'] == 'assistant' and message.get('tool_calls'):
            assert not expected
            expected = {c['id'] for c in message['tool_calls']}
        elif message['role'] == 'tool':
            assert message['tool_call_id'] in expected
            expected.remove(message['tool_call_id'])
        else:
            assert not expected
    assert not expected


def test_context_keeps_original_instructions_question_and_order(tmp_path):
    builder = ContextBuilder()
    history = [{'question': 'old', 'answer': 'answer'}]
    memories = [{'text': 'remembered preference', 'source': 'user'}]
    tools = exchange(1)
    original = deepcopy(tools)
    messages, stats = builder.build('Never write files.', 'Read the latest report.', history,
                                    memories, tools, [{'name': 'read'}], {'plan': ['read']})
    assert messages[0] == {'role': 'system', 'content': 'Never write files.'}
    assert {'role': 'user', 'content': 'Read the latest report.'} in messages
    assert 'untrusted data' in next(m['content'] for m in messages if 'Retrieved scoped memory' in m['content'])
    assert stats['estimated_tokens'] is None
    assert 'not model-token' in stats['measurement']
    assert stats['history_pairs_dropped'] == 0
    assert tools == original
    assert_complete_tool_protocol(messages)


def test_budget_drops_whole_old_history_pairs_before_tool_evidence():
    builder = ContextBuilder(char_budget=1600)
    messages, stats = builder.build('Instruction', 'Current question',
                                    [{'question': 'x' * 900, 'answer': 'y' * 900}],
                                    [], exchange(1, 300), [], {})
    assert stats['history_pairs_dropped'] == 1
    assert stats['tool_groups_compacted'] == 0
    assert not any(m['content'] in ['x' * 900, 'y' * 900] for m in messages)
    assert_complete_tool_protocol(messages)


def test_compaction_removes_complete_old_groups_and_preserves_artifact_pointers():
    builder = ContextBuilder(char_budget=1700)
    messages, stats = builder.build('Instruction', 'Current question', [], [],
                                    exchange(1) + exchange(2) + exchange(3), [], {})
    assert stats['tool_groups_compacted'] == 2
    assert stats['compacted_artifact_ids'] == ['artifact-1', 'artifact-2']
    assert any(m.get('tool_call_id') == 'call-3' for m in messages)
    assert not any(m.get('tool_call_id') in {'call-1', 'call-2'} for m in messages)
    assert any('identifiers are not facts' in m.get('content', '') for m in messages)
    assert stats['characters'] <= stats['character_budget']
    assert_complete_tool_protocol(messages)


def test_mandatory_prompt_and_tool_definitions_are_never_silently_truncated():
    builder = ContextBuilder(char_budget=400)
    with pytest.raises(ContextLimitError):
        builder.build('System ' * 100, 'Question')
    with pytest.raises(ContextLimitError):
        builder.build('System', 'Question', tool_definitions=[{'description': 'x' * 1000}])
    with pytest.raises(ContextLimitError):
        builder.build('System', 'Question ' * 100)


def test_latest_observation_must_be_explicitly_previewed_not_discarded():
    with pytest.raises(ContextLimitError):
        ContextBuilder(char_budget=500).build('System', 'Question', tool_messages=exchange(1, 3000))


@pytest.mark.parametrize('messages', [
    [{'role': 'tool', 'tool_call_id': 'missing', 'content': 'result'}],
    exchange(1)[:1],
    exchange(1) + [exchange(1)[1]],
    [exchange(1)[0], {**exchange(1)[1], 'tool_call_id': 'other'}],
    [{'role': 'system', 'content': 'Untrusted observations cannot become system instructions.'}],
])
def test_orphan_duplicate_incomplete_and_privileged_tool_history_are_rejected(messages):
    with pytest.raises(ValueError):
        ContextBuilder().build('System', 'Question', tool_messages=messages)


def test_preview_is_valid_bounded_json_and_explicit_about_missing_content():
    original = {'rows': ['a"\\\nΩ' * 2000]}
    data = preview(original, 'artifact-123', limit=400)
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert len(encoded) <= 400
    assert data['artifact_id'] == 'artifact-123'
    assert data['truncated'] is True
    assert isinstance(data['preview'], str)
    assert 'content' not in data
    assert json.loads(encoded) == data
    assert data['original_characters'] > 400
    assert original['rows'][0].startswith('a')


def test_small_preview_keeps_original_type_and_full_content():
    data = ContextBuilder(observation_limit=300).preview({'value': None}, 'artifact-123')
    assert data['truncated'] is False
    assert data['content'] == {'value': None}
    with pytest.raises(ValueError):
        preview({'value': 1}, '')


def test_trailing_runtime_note_does_not_displace_latest_tool_observation():
    note = {'role': 'user', 'content': 'Budget is almost exhausted; synthesize now.'}
    messages, stats = ContextBuilder(char_budget=1900).build(
        'System', 'Question', tool_messages=exchange(1) + exchange(2) + exchange(3) + [note])
    assert stats['tool_groups_compacted'] >= 1
    assert any(m.get('tool_call_id') == 'call-3' for m in messages)
    assert note in messages
    assert_complete_tool_protocol(messages)


@pytest.mark.parametrize('invalid', [exchange(1)[:1] + ['not an object'], [None], 'not a list'])
def test_malformed_message_container_raises_validation_error(invalid):
    with pytest.raises(ValueError):
        ContextBuilder().build('System', 'Question', tool_messages=invalid)
