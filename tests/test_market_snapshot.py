"""Daily observations never become fabricated live quotes."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agents.base_agent import AgentTask
from agents.chat_agent import ChatAgent
from core.market_snapshot import SOURCE_URL, fetch_snapshot, parse_snapshot
from core.response_review import review_result
from core.tool_registry import ToolRegistry, create_builtin_tools, execute_tool, format_tool_result

NOW = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
CSV = ("observation_date,SP500,NASDAQCOM,DJIA\n"
       "2026-09-28,100,200,300\n2026-09-29,.,.,.\n2026-09-30,102,198,303\n")


def test_parse_closes_previous_valid_day_and_source_timestamps():
    result = parse_snapshot(CSV, NOW)
    assert result['data_kind'] == 'daily_close' and result['is_realtime'] is False
    assert result['retrieved_at'] == NOW.isoformat()
    assert [q['change_percent'] for q in result['quotes']] == [2.0, -1.0, 1.0]
    assert all(q['as_of_date'] == '2026-09-30' and q['previous_date'] == '2026-09-28'
               for q in result['quotes'])
    assert all(q['source_url'].startswith('https://fred.stlouisfed.org/series/')
               for q in result['quotes'])


@pytest.mark.parametrize('body', [
    '<html>Login required</html>',
    'date,SP500\n2026-09-30,123\n',
    'observation_date,SP500,NASDAQCOM,DJIA\n2026-09-30,.,.,.\n',
    CSV + '2026-09-30,103,199,304\n',
    CSV.replace('2026-09-30', '2026-10-02'),
    CSV.replace('102,198,303', 'NaN,198,303'),
    CSV.replace('102,198,303', 'Infinity,198,303'),
    CSV.replace('102,198,303', '1e9999,198,303'),
    CSV.replace('102,198,303', '-1,198,303'),
    CSV.replace('102,198,303', '0,198,303'),
    CSV.replace('102,198,303', '102,198'),
    CSV.replace('102,198,303', '102,198,303,99'),
    'x' * 1_000_001,
], ids=lambda _: 'invalid-source')
def test_bad_source_cannot_produce_numbers(body, monkeypatch):
    # The future-date fixture must stay future as the real calendar advances.
    monkeypatch.setattr('core.market_snapshot.datetime', SimpleNamespace(now=lambda _: NOW))
    result = fetch_snapshot(lambda url: {'ok': True, 'body': body})
    assert result == {'ok': False, 'error': 'invalid_market_source_data'}


def test_partial_series_and_single_observation_are_explicit():
    result = parse_snapshot('observation_date,SP500,NASDAQCOM,DJIA\n2026-09-30,102,.,.\n', NOW)
    assert result['missing_series'] == ['NASDAQCOM', 'DJIA']
    quote = result['quotes'][0]
    assert quote['previous_date'] is quote['change'] is quote['change_percent'] is None


def test_stale_and_timezone_validation():
    result = parse_snapshot(CSV, datetime(2026, 10, 10, tzinfo=timezone.utc))
    assert all(quote['stale'] for quote in result['quotes'])
    with pytest.raises(ValueError):
        parse_snapshot(CSV, NOW.replace(tzinfo=None))


@pytest.mark.parametrize('response', [{'ok': False, 'status_code': 403}, {'ok': False, 'status_code': 429}])
def test_failed_fetch_has_no_fallback_values(response):
    assert fetch_snapshot(lambda _: response) == {'ok': False, 'error': 'market_source_unavailable'}


def test_timeout_is_a_failure():
    def fail(_):
        raise TimeoutError()
    assert fetch_snapshot(fail) == {'ok': False, 'error': 'market_source_unavailable'}


def registry_with_result(result):
    calls = []
    def fetch(action, params, **kwargs):
        calls.append((action, params, kwargs))
        return result
    registry = ToolRegistry()
    for tool in create_builtin_tools(enable_us_market_snapshot=True,
                                     executors={'browser': SimpleNamespace(execute=fetch)}):
        registry.register(tool)
    return registry, calls


def test_separate_opt_in_and_fixed_url_ignore_no_user_args():
    assert 'us_market_snapshot' not in {tool.name for tool in create_builtin_tools()}
    registry, calls = registry_with_result({'ok': True, 'body': CSV})
    assert 'web_fetch' not in registry.list_names() and 'browse_web' not in registry.list_names()
    bad = execute_tool('us_market_snapshot', {'url': 'http://127.0.0.1/'}, registry)
    assert bad['ok'] is False and not calls
    good = execute_tool('us_market_snapshot', {}, registry)
    assert good['ok'] is True
    assert calls == [('fetch', {'url': SOURCE_URL}, {'task_id': 'tool_us_market_snapshot'})]
    formatted = format_tool_result('us_market_snapshot', good)
    assert 'daily_close' in formatted and 'as_of_date' in formatted and 'retrieved_at' in formatted


def test_missing_executor_does_not_bypass_boundaries():
    tool = next(t for t in create_builtin_tools(enable_us_market_snapshot=True)
                if t.name == 'us_market_snapshot')
    assert tool.handler() == {'ok': False, 'error': 'market_network_executor_unavailable'}


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize(('question', 'intro'), [
    ('现在美股行情', '以下是最新可取得的'),
    ('请用英语回答：现在美股行情', 'These are the latest available'),
    ('latest US market', 'These are the latest available'),
])
def test_short_lookup_uses_observed_data_without_model(monkeypatch, streaming, question, intro):
    monkeypatch.setattr('agents.chat_agent.get_llm', lambda: pytest.fail('must not call model'))
    registry, calls = registry_with_result({'ok': True, 'body': CSV})
    agent = ChatAgent(registry)
    tokens = []
    task = AgentTask('chat', {'text': question})
    result = agent.run_stream(task, tokens.append) if streaming else agent.run(task)
    if streaming:
        assert ''.join(tokens) == result.content
    result = review_result(result)
    assert result.ok and result.content.startswith(intro)
    assert '2026-09-30' in result.content and '2026-09-28' in result.content
    assert len(calls) == result.meta['tool_rounds'] == 1
    assert result.meta['review']['fact_verified'] is False
    assert result.meta['review']['status'] == 'contract_checked'


@pytest.mark.parametrize('streaming', [False, True])
def test_failed_market_request_is_failed_receipt_not_model_guess(monkeypatch, streaming):
    monkeypatch.setattr('agents.chat_agent.get_llm', lambda: pytest.fail('must not call model'))
    registry, calls = registry_with_result({'ok': False})
    task = AgentTask('chat', {'text': '现在美股行情'})
    agent = ChatAgent(registry)
    result = agent.run_stream(task, lambda _: None) if streaming else agent.run(task)
    assert not result.ok and result.meta['error'] == 'market_data_unavailable'
    assert result.meta['evidence'][0]['ok'] is False and len(calls) == 1


@pytest.mark.parametrize('question', ['今天天气怎么样', '最新新闻', 'AAPL股价', '今日港股行情'])
def test_index_tool_does_not_grant_unrelated_live_capabilities(monkeypatch, question):
    monkeypatch.setattr('agents.chat_agent.get_llm', lambda: pytest.fail('must not call model'))
    registry, calls = registry_with_result({'ok': True, 'body': CSV})
    result = ChatAgent(registry).run(AgentTask('chat', {'text': question}))
    assert result.meta['current_data_guard'] and not calls
    assert '只提供美股三大指数的每日收盘快照' in result.content


def test_tools_disallowed_does_not_fetch(monkeypatch):
    monkeypatch.setattr('agents.chat_agent.get_llm', lambda: pytest.fail('must not call model'))
    registry, calls = registry_with_result({'ok': True, 'body': CSV})
    result = ChatAgent(registry).run(AgentTask('chat', {'text': '现在美股行情', 'tools_allowed': False}))
    assert result.meta['current_data_guard'] and not calls
    assert result.meta['tools_available'] == 0


def test_opt_in_reaches_runtime_and_only_adds_fixed_source(monkeypatch, request):
    monkeypatch.setenv('EVA_ENABLE_US_MARKET_SNAPSHOT', 'true')
    monkeypatch.setenv('EVA_ENABLE_NETWORK_TOOLS', 'false')
    client = request.getfixturevalue('client')
    registry = client.app.state.container.tool_registry
    assert 'us_market_snapshot' in registry.list_names() and 'web_fetch' not in registry.list_names()
    executor = client.app.state.container.executors['browser']
    assert 'fred.stlouisfed.org' in executor.allowed_domains


def test_actual_executor_accepts_application_csv_and_audits_fixed_source(monkeypatch):
    from core.executor import BrowserExecutor

    audit = []
    monkeypatch.setattr('core.executor._is_private_or_loopback', lambda _: False)
    def get(url, **kwargs):
        assert url == SOURCE_URL and kwargs['follow_redirects'] is False
        return SimpleNamespace(status_code=200, is_success=True, text=CSV,
                               content=CSV.encode(), headers={'content-type': 'application/csv'})
    monkeypatch.setattr('httpx.get', get)
    executor = BrowserExecutor(SimpleNamespace(record=lambda **kwargs: audit.append(kwargs)), {
        'executors': {'browser': {'allowed_domains': ['fred.stlouisfed.org']}},
    })
    registry = ToolRegistry()
    for tool in create_builtin_tools(enable_us_market_snapshot=True, executors={'browser': executor}):
        registry.register(tool)
    result = execute_tool('us_market_snapshot', {}, registry)
    assert result['ok'] and len(result['quotes']) == 3
    assert len(audit) == 1 and audit[0]['status'] == 'success'
    assert audit[0]['parameters'] == {'url': SOURCE_URL}
