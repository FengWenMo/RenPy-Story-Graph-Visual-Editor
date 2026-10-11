"""
Agent 后端判据：LLM 客户端 + 循环 + **写盘闸门**（2026-10-11 用户目标 1）
=============================================================================

全套**不联网**：客户端支持注入 transport，判据用假的。
三条必须钉住（都是"会让用户吃亏"的）：
  ① key **只进请求头**、绝不进请求体；事件流里也没有 key；
  ② **写盘必须有人点头** —— 没批准时 `declare` 只能给 diff，**绝不真写**
     （而且这是**代码**行为，不依赖提示词）；
  ③ 循环**有界**（不会绕圈烧 token）；模型给的坏 JSON 参数不许炸。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.ai import agent as A                                    # noqa: E402
from storygraph.ai import client as C                                   # noqa: E402

KEY = 'sk-test-key-do-not-leak-123456'


def _cfg(provider='deepseek'):
    return {'provider': provider, 'api_key': KEY, 'model': 'm',
            'base_url': 'https://example.invalid'}


# ==================== ① 客户端：纯函数与 key 的去向 ====================

def test_key_goes_in_headers_never_in_the_body():
    """⭐ key **只进请求头**（请求体常被打印/记日志/贴 issue）。"""
    req = C.build_request(_cfg(), [{'role': 'user', 'content': 'hi'}])
    assert KEY in json.dumps(req['headers'])
    assert KEY not in json.dumps(req['body']), 'key 漏进了请求体'
    assert req['url'].endswith('/chat/completions')
    assert req['body']['model'] == 'm'


def test_anthropic_uses_its_own_headers_and_tool_shape():
    """Anthropic 走 `x-api-key` + `/v1/messages` + `input_schema`。"""
    tools = [{'type': 'function', 'function': {
        'name': 'storygraph_scan_unknown', 'description': 'd',
        'parameters': {'type': 'object', 'properties': {}}}}]
    req = C.build_request(_cfg('anthropic'), [{'role': 'user', 'content': 'hi'}],
                          tools)
    assert req['url'].endswith('/v1/messages')
    assert 'x-api-key' in req['headers']
    assert req['body']['tools'][0]['input_schema']['type'] == 'object'
    assert KEY not in json.dumps(req['body'])


def test_build_request_refuses_when_not_configured():
    """没 key / 没服务方 → **明确报错**（不是发一个空请求出去）。"""
    for bad in ({}, {'provider': 'deepseek'}, {'api_key': 'k'}):
        try:
            C.build_request(bad, [])
        except C.LlmError as e:
            assert str(e)
        else:
            raise AssertionError(f'没拦住坏配置：{bad}')


def test_parse_reply_normalizes_openai_shape():
    """OpenAI 形态：`tool_calls[].function.arguments` 是 **JSON 字符串** → 解成 dict。"""
    got = C.parse_reply('openai', {'choices': [{'message': {
        'content': '我先看看',
        'tool_calls': [{'id': 'c1', 'function': {
            'name': 'storygraph_scan_unknown',
            'arguments': '{"heads": ["a"]}'}}]}}]})
    assert got['text'] == '我先看看'
    assert got['tool_calls'][0]['arguments'] == {'heads': ['a']}


def test_parse_reply_survives_broken_arguments_json():
    """模型偶尔给空串/坏 JSON —— **不许炸**，给 `{}` 并记一条 note。"""
    for raw in ('', '  ', '不是 JSON'):
        got = C.parse_reply('openai', {'choices': [{'message': {
            'tool_calls': [{'id': 'c', 'function': {'name': 'x',
                                                    'arguments': raw}}]}}]})
        assert got['tool_calls'][0]['arguments'] == {}, raw
    assert C.parse_reply('openai', {'choices': [{'message': {
        'tool_calls': [{'id': 'c', 'function': {'name': 'x',
                                                'arguments': 'oops'}}]}}]}
    )['notes'], '坏 JSON 要留一条 note'


def test_chat_uses_the_injected_transport():
    """⭐ 判据**不联网**：注入 transport，就能验"请求发了什么、回复怎么用"。"""
    seen = {}

    def fake(request, timeout):
        seen['url'] = request['url']
        seen['body'] = request['body']
        return {'choices': [{'message': {'content': '好'}}]}

    got = C.chat([{'role': 'user', 'content': 'hi'}], [], _cfg(),
                 transport=fake)
    assert got['text'] == '好' and got['provider'] == 'deepseek'
    assert seen['url'].endswith('/chat/completions')


def test_http_error_becomes_a_readable_llm_error():
    """HTTP 出错 → `LlmError` 且**带上能给人看的原因**（不吞、不抛裸异常）。"""
    import urllib.error

    def boom(request, timeout):
        raise urllib.error.HTTPError(request['url'], 401, 'Unauthorized',
                                     {}, None)

    try:
        C.chat([{'role': 'user', 'content': 'x'}], [], _cfg(), transport=boom)
    except C.LlmError as e:
        assert '401' in str(e)
    else:
        raise AssertionError('HTTP 错没变成 LlmError')


# ==================== ② agent：工具描述与闸门 ====================

def test_tool_specs_are_the_same_tools_as_mcp():
    """⭐ 工具描述**与 MCP 同源**（一处定义，MCP/agent/面板都用它，不会漂移）。"""
    from storygraph.mcp import server as S
    specs = A.tool_specs()
    mcp_names = {t['name'] for t in S.tools_list()}
    assert {s['function']['name'] for s in specs} == mcp_names
    # 描述里带上边界（模型得先知道）
    ask = next(s for s in specs
               if s['function']['name'] == 'storygraph_ask_engine')
    assert '已知边界' in ask['function']['description']


def test_write_tool_is_forced_to_dry_run_without_approval(tmp_path):
    """
    ⭐⭐ **本文件最重要的一条**：没人点头时，`declare` **绝不真写** ——
    而且这是**代码**行为（就算模型被诱导着要求 `dry_run=False`，也写不下去）。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = A.execute('storygraph_declare', {'name': 'weather', 'dry_run': False},
                    project_root=root, approve=None)
    assert got['ok'] is True and got['dry_run'] is True, got
    assert got['written'] == [], '没批准却写了盘'
    assert '未获批准' in got['reason'], got
    assert not (root / 'storygraph.rules.json').exists(), '清单竟然被写了'
    assert got['diff'], '试算也要给 diff（否则人没法判断）'


def test_approval_actually_writes(tmp_path):
    """批准之后**才**真写，并且回执换成真结果（written 非空）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    seen = {}

    def approve(tool, args, preview):
        seen['preview_diff'] = preview.get('diff')
        return True

    got = A.execute('storygraph_declare', {'name': 'weather'},
                    project_root=root, approve=approve)
    assert got['dry_run'] is False and got['written'], got
    assert (root / 'storygraph.rules.json').is_file()
    assert seen['preview_diff'], '批准前没给人看 diff'


def test_a_refusing_approver_stops_the_write(tmp_path):
    """人点了"不" → 也不写（回执里说清"未获批准"）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    got = A.execute('storygraph_declare', {'name': 'weather'},
                    project_root=root, approve=lambda *a: False)
    assert got['written'] == []
    assert not (root / 'storygraph.rules.json').exists()


def test_approver_that_raises_is_treated_as_a_no(tmp_path):
    """批准钩子自己出错 → **当成不批准**（宁可少写，不可乱写）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)

    def boom(*a):
        raise RuntimeError('界面炸了')

    got = A.execute('storygraph_declare', {'name': 'weather'},
                    project_root=root, approve=boom)
    assert got['written'] == []
    assert not (root / 'storygraph.rules.json').exists()


def test_unknown_tool_is_an_honest_result_not_a_crash():
    got = A.execute('storygraph_nope', {}, project_root='.')
    assert got['ok'] is False and '没有这个工具' in got['reason']


def test_gate_event_is_emitted_so_the_panel_can_ask(tmp_path):
    """闸门要发事件 —— 面板靠它弹"要不要真写"。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    events = []
    A.execute('storygraph_declare', {'name': 'weather'}, project_root=root,
              approve=None, events=events)
    assert [e['kind'] for e in events] == ['gate'], events
    assert events[0]['preview']['diff']


# ==================== ③ 循环：有界、可读、key 不外泄 ====================

def _scripted(replies):
    """假 transport：按脚本一条条回（验循环行为用）。"""
    box = {'i': 0, 'seen': []}

    def send(request, timeout):
        box['seen'].append(request)
        reply = replies[min(box['i'], len(replies) - 1)]
        box['i'] += 1
        return reply

    return send, box


def test_loop_runs_a_tool_then_finishes(tmp_path):
    """走一遍：模型调工具 → 本地执行 → 回执喂回 → 模型收尾。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    weather rain 强度=3\n    return\n', encoding='utf-8')
    send, _box = _scripted([
        {'choices': [{'message': {'content': '', 'tool_calls': [
            {'id': 'c1', 'function': {
                'name': 'storygraph_scan_unknown',
                'arguments': json.dumps({'project_root': str(root)})}}]}}]},
        {'choices': [{'message': {'content': '我看到了 weather。'}}]},
    ])
    got = A.run_agent(root, [{'role': 'user', 'content': '项目里有什么不认识的写法'}],
                      _cfg(), transport=send)
    kinds = [e['kind'] for e in got['events']]
    assert kinds == ['tool_call', 'tool_result', 'assistant', 'done'], kinds
    assert got['final'] == '我看到了 weather。'
    assert got['ok'] is True
    # 工具回执真的喂回去了（模型那条 tool 消息里能看到 head）
    tool_msgs = [m for m in got['messages'] if m.get('role') == 'tool']
    assert tool_msgs and 'weather' in tool_msgs[0]['content']


def test_loop_never_leaks_the_key_into_events(tmp_path):
    """⭐ 事件流会被面板显示、被日志收、被贴 issue —— **里面不许有 key**。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    send, box = _scripted([{'choices': [{'message': {'content': '完了'}}]}])
    got = A.run_agent(root, [{'role': 'user', 'content': 'hi'}], _cfg(),
                      transport=send)
    blob = json.dumps(got['events'], ensure_ascii=False)
    assert KEY not in blob
    # 但请求头里必须有（否则根本调不通）
    assert KEY in json.dumps(box['seen'][0]['headers'])


def test_loop_is_bounded(tmp_path):
    """模型一直调工具 → **到上限就停**（不许绕圈烧 token）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    send, box = _scripted([{'choices': [{'message': {'content': '', 'tool_calls': [
        {'id': 'c', 'function': {'name': 'storygraph_engine_facts',
                                 'arguments': '{}'}}]}}]}])
    got = A.run_agent(root, [{'role': 'user', 'content': 'x'}], _cfg(),
                      transport=send, max_steps=3)
    assert got['ok'] is False and '上限' in got['reason'], got
    assert box['i'] == 3, box['i']


def test_loop_reports_a_client_error_as_an_event(tmp_path):
    """连不上模型 → **一条 error 事件 + 一句话**（不抛给界面炸）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)

    def boom(request, timeout):
        raise C.LlmError('连不上接口：网络不通')

    got = A.run_agent(root, [{'role': 'user', 'content': 'x'}], _cfg(),
                      transport=boom)
    assert got['ok'] is False and '网络不通' in got['reason']
    assert got['events'][-1]['kind'] == 'error'


def test_truncation_says_so():
    """回执截断要**明说**（否则模型以为看全了）。"""
    long_text = A._truncate('x' * (A.RESULT_LIMIT + 100))
    assert '已截断' in long_text
    assert A._truncate('短') == '短'
