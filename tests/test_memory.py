from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from agent_foundry.memory import MemoryStore
import agent_foundry.memory as memory_module


def test_completed_turns_are_session_scoped_ordered_and_durable(tmp_path):
    path = tmp_path / 'state.sqlite'
    store = MemoryStore(path)
    for i in range(6):
        store.add_turn('one', f'q{i}', f'a{i}')
    store.add_turn('two', 'private question', 'private answer')
    assert MemoryStore(path).history('one', 2) == [
        {'question': 'q4', 'answer': 'a4'}, {'question': 'q5', 'answer': 'a5'}]
    assert store.history('one', 0) == []
    assert store.history('unknown') == []


def test_long_term_memory_requires_explicit_write_and_never_crosses_scope(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    store.add_turn('a', 'I prefer careful answers', 'Understood')
    assert store.search('project:a', 'careful') == []
    first = store.remember('project:a', 'answer-style', 'Prefer careful source-backed answers', 'user:explicit')
    store.remember('project:b', 'answer-style', 'Prefer brief answers', 'user:other-project')
    found = store.search('project:a', 'answers')
    assert [r['id'] for r in found] == [first['id']]
    assert found[0]['source'] == 'user:explicit'
    assert 'T' in found[0]['created_at']
    assert store.search('project:a', ' ') == []


def test_expiry_filters_without_silently_mutating_and_forget_deletes(tmp_path, monkeypatch):
    now = [1700000000.0]
    monkeypatch.setattr(memory_module.time, 'time', lambda: now[0])
    path = tmp_path / 'state.sqlite'
    store = MemoryStore(path)
    store.remember('project', 'temporary', 'calibration preference', 'user', ttl_seconds=10)
    store.remember('other', 'temporary', 'calibration elsewhere', 'user')
    assert len(store.search('project', 'calibration')) == 1
    now[0] += 10
    assert store.search('project', 'calibration') == []
    with sqlite3.connect(path) as con:
        assert con.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 2
    assert store.forget('project', 'temporary') is True
    assert store.forget('project', 'temporary') is False
    assert len(store.search('other', 'calibration')) == 1


def test_memory_update_retains_identity_and_replaces_source_and_expiry(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    before = store.remember('s', 'k', 'outdated preference', 'old', ttl_seconds=2)
    after = store.remember('s', 'k', 'updated preference', 'new')
    assert after['id'] == before['id']
    assert after['expires_at'] is None
    assert store.search('s', 'outdated') == []
    assert store.search('s', 'updated')[0]['source'] == 'new'


@pytest.mark.parametrize('ttl', [0, -1, True, float('inf'), float('nan'), '10'])
def test_memory_rejects_invalid_ttl(tmp_path, ttl):
    store = MemoryStore(tmp_path / 'state.sqlite')
    with pytest.raises(ValueError):
        store.remember('s', 'k', 'some text', 'user', ttl_seconds=ttl)


def test_artifacts_are_content_addressed_and_runs_round_trip_without_aliasing(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    content = {'rows': [{'sensor': 'A', 'value': None}], 'quote': 'Read exactly'}
    id = store.put_artifact('run-a', content, {'tool': 'query'})
    assert id == store.put_artifact('run-a', content, {'tool': 'query'})
    assert id != store.put_artifact('run-b', content, {'tool': 'query'})
    content['rows'][0]['value'] = 100
    assert store.get_artifact(id)['content']['rows'][0]['value'] is None
    store.save_run('run-a', {'status': 'running', 'artifacts': [id]})
    store.save_run('run-a', {'status': 'complete', 'artifacts': [id]})
    assert MemoryStore(store.path).load_run('run-a')['status'] == 'complete'
    with pytest.raises(KeyError):
        store.get_artifact('not-present')
    with pytest.raises(KeyError):
        store.load_run('not-present')


def test_event_sequence_is_atomic_across_writers(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    def append(i):
        return store.append_event('run', {'tool': 'read', 'index': i})
    with ThreadPoolExecutor(max_workers=6) as pool:
        sequences = list(pool.map(append, range(18)))
    assert sorted(sequences) == list(range(1, 19))
    events = store.events('run')
    assert [e['sequence'] for e in events] == list(range(1, 19))
    assert sorted(e['event']['index'] for e in events) == list(range(18))
    assert store.events('other') == []


def test_json_state_rejects_nonfinite_numbers(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    with pytest.raises(ValueError):
        store.put_artifact('run', {'invalid': float('nan')})
    with pytest.raises(ValueError):
        store.save_run('run', {'invalid': float('inf')})
    with pytest.raises(ValueError):
        store.append_event('run', {'invalid': float('nan')})
    assert store.events('run') == []


def test_same_memory_key_updated_concurrently_keeps_one_identity(tmp_path):
    store = MemoryStore(tmp_path / 'state.sqlite')
    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(lambda i: store.remember('same-scope', 'same-key',
                                                        f'value {i}', 'user'), range(12)))
    assert len({row['id'] for row in records}) == 1
    assert len(store.search('same-scope', 'value')) == 1
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('ttl', [10**500, 1e100])
def test_huge_ttl_is_cleanly_rejected_before_any_write(tmp_path, ttl):
    store = MemoryStore(tmp_path / 'state.sqlite')
    with pytest.raises(ValueError):
        store.remember('scope', 'key', 'text value', 'user', ttl_seconds=ttl)
    assert store.search('scope', 'value') == []
