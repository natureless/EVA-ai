"""Complex market conversations retain provenance and fail without guesses."""
import json
from datetime import datetime, timezone

import pytest

from agent_os.orchestrator import AgentOrchestrator
from agent_os.registry import AgentRegistry
from agents.base_agent import AgentTask
from agents.chat_agent import ChatAgent
from core.market_snapshot import SOURCE_URL, parse_snapshot
from core.tool_registry import ToolDef, ToolRegistry

CSV = ('observation_date,SP500,NASDAQCOM,DJIA\n'
       '2026-09-28,100,200,300\n2026-09-30,102,198,303\n')
TOOL_CALL = '```tool\n{"tool":"us_market_snapshot","args":{}}\n```'


def envelope(message='标普收涨，纳指收跌。', **overrides):
    claim = {'text': message, 'status': 'tool_observation', 'confidence': 0.9,
             'evidence_ids': ['tool:1'], 'time_sensitive': True, **overrides}
    return '```eva_response\n' + json.dumps({
        'message': message, 'risk_level': 'low', 'claims': [claim],
    }, ensure_ascii=False) + '\n```'


class StubLLM:
    provider = 'test'

    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def chat(self, messages):
        self.messages.append([dict(item) for item in messages])
        return next(self.replies)

    def chat_stream(self, messages):
        yield self.chat(messages)


def execute(monkeypatch, streaming, data, replies, text='比较美股三大指数最新表现，并说明数据局限'):
    calls = []
    tools = ToolRegistry()
    def snapshot():
        calls.append(True)
        return data
    tools.register(ToolDef('us_market_snapshot', 'daily close only', {}, snapshot))
    llm = StubLLM(replies)
    monkeypatch.setattr('agents.chat_agent.get_llm', lambda: llm)
    registry = AgentRegistry()
    registry.register(ChatAgent(tools))
    orchestrator = AgentOrchestrator(registry)
    task = AgentTask('chat', {'text': text, 'context': {'memories': [{'content': 'old price: 99999'}]}})
    if streaming:
        tokens = []
        result, _ = orchestrator.execute_stream('chat_agent', task, tokens.append)
        assert ''.join(tokens) == result.content
    else:
        result, _ = orchestrator.execute('chat_agent', task)
    return result, llm, calls


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('plain', [False, True])
def test_success_retains_trading_dates_and_source_even_if_model_omits_them(monkeypatch, streaming, plain):
    data = parse_snapshot(CSV, datetime(2026, 10, 1, tzinfo=timezone.utc))
    answer = '标普收涨，纳指收跌。' if plain else envelope()
    result, llm, calls = execute(monkeypatch, streaming, data, [TOOL_CALL, answer])
    assert result.ok and len(calls) == 1 and len(llm.messages) == 2
    assert result.content.startswith('标普收涨')
    assert '每日收盘快照，不是盘中实时行情' in result.content
    assert 'SP500: 2026-09-30' in result.content and SOURCE_URL in result.content
    assert data['retrieved_at'] in result.content
    receipt = result.meta['evidence'][0]
    assert receipt['observed_at'] == data['retrieved_at'] and receipt['source'] == SOURCE_URL
    assert receipt['trading_dates'] == {series: '2026-09-30' for series in ('SP500', 'NASDAQCOM', 'DJIA')}
    assert receipt['data_kind'] == 'daily_close' and receipt['is_realtime'] is False
    injected = llm.messages[1][-1]['content']
    assert 'trading_dates' in injected and 'daily_close' in injected
    assert 'first user message is the original human request' in llm.messages[1][0]['content']
    assert result.meta['review']['fact_verified'] is False
    assert result.meta['review']['status'] == ('not_assessed' if plain else 'contract_checked')


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize(('text', 'message'), [
    ('比较美股三大指数最新表现', '本次行情数据获取失败'),
    ('请用英语回答：比较美股三大指数最新表现', 'The market data source could not be read'),
])
def test_failed_complex_lookup_stops_before_model_can_use_old_memory(monkeypatch, streaming, text, message):
    result, llm, calls = execute(monkeypatch, streaming,
                               {'ok': False, 'error': 'market_source_unavailable'}, [TOOL_CALL], text)
    assert not result.ok and len(calls) == len(llm.messages) == result.meta['tool_rounds'] == 1
    assert result.meta['error'] == 'market_data_unavailable' and result.content.startswith(message)
    assert '99999' not in result.content
    receipt = result.meta['evidence'][0]
    assert receipt['ok'] is False and receipt['trading_dates'] == {}
    assert receipt['error'] == 'market_source_unavailable'
    assert result.meta['review']['status'] == 'execution_failed'


@pytest.mark.parametrize('streaming', [False, True])
def test_partial_stale_data_is_disclosed_per_series(monkeypatch, streaming):
    data = parse_snapshot('observation_date,SP500,NASDAQCOM,DJIA\n2026-09-10,100,.,.\n',
                          datetime(2026, 10, 1, tzinfo=timezone.utc))
    result, _, _ = execute(monkeypatch, streaming, data, [TOOL_CALL, envelope('标普收盘为100。')])
    assert result.ok and 'SP500: 2026-09-10' in result.content
    assert '超过 7 天未更新：SP500' in result.content and '缺少数据：NASDAQCOM, DJIA' in result.content
    assert result.meta['evidence'][0]['stale_series'] == ['SP500']


@pytest.mark.parametrize('streaming', [False, True])
def test_provenance_does_not_bypass_evidence_rejection(monkeypatch, streaming):
    data = parse_snapshot(CSV, datetime(2026, 10, 1, tzinfo=timezone.utc))
    result, llm, _ = execute(monkeypatch, streaming, data, [TOOL_CALL, envelope(evidence_ids=['invented'])])
    assert not result.ok and result.meta['error'] == 'response_review_failed'
    assert len(llm.messages) == 2 and '系统附注' not in result.content


@pytest.mark.parametrize('streaming', [False, True])
def test_provenance_is_added_after_valid_format_repair(monkeypatch, streaming):
    data = parse_snapshot(CSV, datetime(2026, 10, 1, tzinfo=timezone.utc))
    result, llm, _ = execute(monkeypatch, streaming, data,
                             [TOOL_CALL, 'outside\n' + envelope(), envelope()])
    assert result.ok and len(llm.messages) == 3 and '系统附注' in result.content
    assert result.meta['response_repair'] == {'attempted': True, 'succeeded': True}


@pytest.mark.parametrize('text', ['解释指数函数', '分析我提供的历史收盘数据：2020年标普上涨',
                                 'Resume los datos que he proporcionado'])
def test_ordinary_requests_do_not_fetch_or_force_english(monkeypatch, text):
    result, llm, calls = execute(monkeypatch, False, None, ['普通解释'], text)
    assert result.ok and not calls and result.content == '普通解释'
    assert 'Original human request output language: English' not in llm.messages[0][0]['content']


def test_explicit_english_provenance_and_language_binding(monkeypatch):
    data = parse_snapshot(CSV, datetime(2026, 10, 1, tzinfo=timezone.utc))
    result, llm, _ = execute(monkeypatch, False, data, [TOOL_CALL, envelope('The indices diverged.')],
                             '请用英语回答：比较美股三大指数最新表现')
    assert result.ok and 'Data note (runtime)' in result.content and '系统附注' not in result.content
    assert 'Original human request output language: English.' in llm.messages[1][0]['content']


@pytest.mark.parametrize('claim_count', [0, 64])
def test_runtime_note_preserves_model_claim_count_and_review_status(monkeypatch, claim_count):
    data = parse_snapshot(CSV, datetime(2026, 10, 1, tzinfo=timezone.utc))
    draft = json.loads(envelope().split('\n')[1])
    draft['claims'] *= claim_count
    reply = '```eva_response\n' + json.dumps(draft, ensure_ascii=False) + '\n```'
    result, _, _ = execute(monkeypatch, False, data, [TOOL_CALL, reply])
    assert result.ok and '系统附注' in result.content
    assert len(result.meta['claims']) == claim_count
    assert result.meta['review']['status'] == ('not_assessed' if claim_count == 0 else 'contract_checked')
