"""
MCP server + 用户自己的 API key 配置（用户 2026-10-11："先 2 吧"）
======================================================================

两件事一起钉：

  ① **MCP server**（`storygraph/mcp/server.py`）—— 让别人家的 AI 客户端连上本工具。
     它是**本地 stdio server，不需要任何 key**（key 是客户端自己的事）。
     两条命门：**工具清单从 `capabilities()` 来**（不另写一份，防漂移）；
     **server 绝不许崩**（任何异常都变成 JSON-RPC error，且继续读）。
  ② **key 存哪儿**（`core/ai_settings.py`）—— 用户自己填，存在**用户级**目录
     （`%LOCALAPPDATA%/StoryGraph/ai.json`，`STORYGRAPH_AI_CONFIG` 可覆盖），
     **绝不进项目仓库、不进日志、不进回执**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import ai_settings as AI                            # noqa: E402
from storygraph.mcp import server as S                                   # noqa: E402


# ==================== ① MCP server ====================

def test_initialize_handshake():
    got = S.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {}})
    assert got['id'] == 1
    assert got['result']['protocolVersion'] == S.PROTOCOL_VERSION
    assert got['result']['capabilities']['tools']
    assert got['result']['serverInfo']['name'] == 'storygraph'


def test_tools_list_comes_from_capabilities_not_a_copy():
    """
    ⭐ **防漂移**：清单里每个工具的描述与边界都来自 `mcp_surface.capabilities()`；
    没实现的（如 table_export）**也列**（客户端要知道它存在但还不能用），
    而**能力清单里没有的**一律不列。
    """
    from storygraph import mcp_surface as M
    got = S.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    names = {t['name'] for t in got['result']['tools']}
    assert 'storygraph_project_context' in names
    assert 'storygraph_declare' in names
    assert 'storygraph_table_export' in names, '未实现的也要让客户端知道'
    # 每个 schema 都要能对上能力清单里的边界文字
    by_name = {t['name']: t for t in got['result']['tools']}
    assert '已知边界' in by_name['storygraph_ask_engine']['description'], \
        by_name['storygraph_ask_engine']['description']
    assert '还没实现' in by_name['storygraph_table_export']['description']
    # 能力清单里没实现的工具不许出现在 server 清单里
    assert not (names - {f'storygraph_{k}' for k in S._SCHEMAS}), names


def test_tools_have_project_root_unless_they_do_not_need_it():
    """需要项目根的工具，schema 里必须给 `project_root`（否则模型不知道要传）。"""
    got = S.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'})
    for t in got['result']['tools']:
        props = t['inputSchema']['properties']
        short = t['name'].replace('storygraph_', '')
        if short in S._NO_PROJECT:
            assert 'project_root' not in props, t['name']
        else:
            assert 'project_root' in props, t['name']


def test_a_missing_project_root_is_an_honest_result_not_a_crash(monkeypatch):
    """
    ⚠ **不猜用户的目录**：没给项目根 → 如实说"没给"（不是拿当前目录去乱写）。
    """
    monkeypatch.delenv('STORYGRAPH_PROJECT', raising=False)
    got = S.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                    'params': {'name': 'storygraph_scan_unknown',
                               'arguments': {}}})
    payload = json.loads(got['result']['content'][0]['text'])
    assert payload['ok'] is False
    assert '没给项目根' in payload['reason'], payload
    assert got['result']['isError'] is True


def test_env_var_provides_the_default_project(tmp_path, monkeypatch):
    """`STORYGRAPH_PROJECT` 给了就默认用它（客户端配置里写一次）。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    weather rain 强度=3\n    return\n', encoding='utf-8')
    monkeypatch.setenv('STORYGRAPH_PROJECT', str(root))
    got = S.handle({'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                    'params': {'name': 'storygraph_scan_unknown',
                               'arguments': {}}})
    payload = json.loads(got['result']['content'][0]['text'])
    assert payload['ok'] is True
    assert [h['head'] for h in payload['heads']] == ['weather']


def test_unknown_method_and_unknown_tool_are_errors_not_crashes():
    """协议错误走 error；**工具自己的 ok=false 不算协议错误**（模型要读到 reason）。"""
    bad_method = S.handle({'jsonrpc': '2.0', 'id': 6, 'method': 'no/such'})
    assert bad_method['error']['code'] == -32601
    bad_tool = S.handle({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                         'params': {'name': 'storygraph_nope', 'arguments': {}}})
    assert bad_tool['error']['code'] == -32602


def test_notifications_get_no_reply():
    """协议要求：通知不回。"""
    assert S.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) == {}


def test_the_server_never_dies_on_garbage():
    """
    ⭐ server 不许崩：坏 JSON、空行、内部异常都要**继续服务**
    （崩了客户端只会看到断线，用户完全不知道发生了什么）。
    """
    import io
    lines = ['不是 JSON\n', '\n',
             json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}) + '\n',
             json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}) + '\n']
    out = io.StringIO()
    rc = S.serve(stdin=io.StringIO(''.join(lines)), stdout=out)
    assert rc == 0
    replies = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    assert len(replies) == 3, replies          # 坏 JSON 也回一条 error
    assert replies[0]['error']['code'] == -32700
    assert replies[1]['result']['protocolVersion']
    assert replies[2]['result']['tools']


# ==================== ② 用户自己的 API key ====================

def test_config_lives_outside_the_project(tmp_path, monkeypatch):
    """
    ⭐⭐ **key 绝不进项目仓库**：默认路径必须在用户级目录（不是 cwd、不是项目里）。
    """
    monkeypatch.delenv('STORYGRAPH_AI_CONFIG', raising=False)
    path = AI.config_path()
    assert 'StoryGraph' in str(path) or '.local' in str(path), path
    assert str(ROOT) not in str(path), f'配置文件竟然落在项目里：{path}'


def test_config_path_can_be_overridden(tmp_path, monkeypatch):
    """`STORYGRAPH_AI_CONFIG` 可覆盖（打包/多用户/测试都要用得上）。"""
    target = tmp_path / 'mine.json'
    monkeypatch.setenv('STORYGRAPH_AI_CONFIG', str(target))
    assert AI.config_path() == target


def test_save_and_load_roundtrip_never_leaks_the_key(tmp_path):
    """
    ⭐ 存进去能读出来；**回执里绝不含 key**（回执会被打印、被模型读、被贴进 issue）。
    """
    fp = tmp_path / 'ai.json'
    got = AI.save('deepseek', 'sk-secret-1234567890', model='deepseek-chat',
                  path=fp)
    assert got['ok'] is True
    assert 'sk-secret' not in json.dumps(got), '回执里漏了 key'
    assert 'sk-secret' not in got['reason']
    cfg = AI.load(fp)
    assert cfg['api_key'] == 'sk-secret-1234567890'
    assert cfg['provider'] == 'deepseek' and cfg['model'] == 'deepseek-chat'


def test_empty_key_is_refused(tmp_path):
    """没填 key 就不保存（免得存了个空壳，用户以为填好了）。"""
    got = AI.save('deepseek', '   ', path=tmp_path / 'ai.json')
    assert got['ok'] is False and '空' in got['reason']
    got2 = AI.save('', 'sk-x', path=tmp_path / 'ai.json')
    assert got2['ok'] is False and '服务方' in got2['reason']


def test_masked_is_the_only_way_to_show_a_key():
    """任何显示 key 的地方都要过 `masked()`（否则 key 会进截图/日志）。"""
    assert AI.masked('sk-1234567890abcdef') == 'sk-1…cdef（共 19 位）'
    assert AI.masked('short') == '•••••'
    assert AI.masked('') == ''
    assert '1234' not in AI.masked('sk-1234')


def test_is_ready_tells_the_user_what_is_missing():
    """没配好时要说清**缺什么**（界面照它提示，不替用户猜）。"""
    ok, why = AI.is_ready({})
    assert ok is False and '服务方' in why
    ok, why = AI.is_ready({'provider': 'deepseek'})
    assert ok is False and 'API key' in why and '本机' in why
    ok, why = AI.is_ready({'provider': 'deepseek', 'api_key': 'sk-x'})
    assert ok is True and why == ''


def test_resolved_fills_defaults_but_never_invents_a_key():
    """补默认 model/base_url；**key 没有就是没有**（不编、不去别处找）。"""
    got = AI.resolved({'provider': 'deepseek', 'api_key': 'sk-x'})
    assert got['model'] == 'deepseek-chat'
    assert got['base_url'] == 'https://api.deepseek.com'
    empty = AI.resolved({'provider': 'openai'})
    assert empty['api_key'] == ''


def test_a_broken_config_file_does_not_break_anything(tmp_path):
    """手写坏文件 → 当空配置（不抛异常），用户重新填一次就好。"""
    fp = tmp_path / 'ai.json'
    fp.write_text('{ 这不是 json', encoding='utf-8')
    cfg = AI.load(fp)
    assert cfg['api_key'] == '' and cfg['provider'] == ''
