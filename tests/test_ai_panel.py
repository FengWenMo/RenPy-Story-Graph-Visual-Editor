"""
「AI 助手」面板判据（用户目标 1 的第 3 步：包一层聊天面板）
================================================================

⚠ **全部脱网**：面板支持注入假 transport（`transport=`），
所以判据不会打真接口、不花 token、也不会把 key 发出去。

三条要钉的（都是"会让用户吃亏"的地方）：
  ① 没填 key → 发送按钮灰化 + 状态行说清"去哪填"（能点就必须有反应）；
  ② **闸门必须等人**：子线程问"要不要写盘"时**不许默认放行**
     （默认放行 = AI 自己决定写盘，那与本项目立场相反）；
  ③ 面板文案里不许出现 Markdown 星号。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import ai_settings as AI                            # noqa: E402


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def no_key(monkeypatch, tmp_path):
    """把配置指到一个空的临时文件 —— **绝不碰用户真机上的 key**。"""
    monkeypatch.setenv('STORYGRAPH_AI_CONFIG', str(tmp_path / 'ai.json'))
    return tmp_path / 'ai.json'


@pytest.fixture
def with_key(no_key):
    AI.save('deepseek', 'sk-fake-key-123456', path=no_key)
    return no_key


def test_without_a_key_it_says_where_to_fill_it(qapp, no_key):
    """① 没配好 → 发送灰化 + 状态行**说清缺什么、去哪填**。"""
    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root='.')
    assert not panel.send_btn.isEnabled(), '没 key 却能点发送'
    text = panel.status.text()
    assert '还不能用' in text, text
    # 缺什么就说什么（先缺服务方，再缺 key）——**不替用户猜**
    assert ('服务方' in text) or ('API key' in text), text
    assert panel.key_btn.text().startswith('🔑'), '要有个地方点开去填'
    # 点发送要**有反应**（提示先说话），而不是默默不动
    panel.send()
    assert '先说一句' in panel.log.toPlainText(), panel.log.toPlainText()


def test_when_only_the_key_is_missing_it_points_at_the_button(qapp, no_key):
    """只差 key 时，状态行要指明"点那个按钮填一次"。"""
    AI.save('deepseek', '', path=no_key)          # 选了服务方但没 key
    # save 会拒绝空 key，所以直接手写一份"只有服务方"的配置
    no_key.write_text(json.dumps({'provider': 'deepseek'}), encoding='utf-8')
    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root='.')
    text = panel.status.text()
    assert 'API key' in text and '本机' in text, text


def test_with_a_key_the_send_button_works(qapp, with_key):
    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root='.')
    assert panel.send_btn.isEnabled()
    assert '已配好' in panel.status.text()
    assert 'sk-fake-key' not in panel.status.text(), '状态行漏了明文 key'


class _GateStub:
    """替掉 `threading.Event`（它的属性是只读的，猴子补丁打不上）。"""

    def __init__(self, answered: bool, value: bool = False):
        self.answered, self.value = answered, value
        self.cleared = False

    def clear(self):
        self.cleared = True

    def wait(self, timeout=None):
        return self.answered

    def set(self):
        self.answered = True


def test_the_gate_must_wait_for_the_human(qapp, no_key):
    """
    ② ⭐⭐ **闸门不许默认放行**：把询问丢出去之后要**等**答案；
    没人答（超时）→ 当成"不许写"。

    ⚠ 这里要**断开** `ask` 与真弹窗的连接 —— 否则 `_on_ask` 会弹模态框
    并 `exec()` **一直等**（判据当场挂住，我踩过一次）。
    """
    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root='.')
    panel.ask.disconnect()
    panel._gate_wait = _GateStub(answered=False)          # 等于"没人回答"
    got = panel._approve_bridge('declare', {}, {'diff': '- a\n+ b'})
    assert got is False, '没人回答却放行了 —— 那就等于 AI 自己决定写盘'


def test_the_gate_returns_what_the_human_said(qapp, no_key):
    """
    人答"允许" → True；答"先别写" → False。

    ⚠ 答案要在**收到询问时**给（`_approve_bridge` 每次会先复位答案 ——
    那是真实流程里正确的行为：上一次的答案不能算这一次的）。
    """
    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root='.')
    panel.ask.disconnect()
    panel._gate_wait = _GateStub(answered=True)
    answer = {'value': True}
    panel.ask.connect(lambda _p: panel._gate_answer.update(answer))
    assert panel._approve_bridge('declare', {}, {}) is True
    answer['value'] = False
    assert panel._approve_bridge('declare', {}, {}) is False


def test_the_gate_resets_the_previous_answer(qapp, no_key):
    """⚠ 上一次的"允许"**不能**算到这一次头上（否则一次点头会授权后面所有写盘）。"""
    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root='.')
    panel.ask.disconnect()
    panel._gate_wait = _GateStub(answered=False)          # 这次没人答
    panel._gate_answer['value'] = True                    # 上次的残留
    assert panel._approve_bridge('declare', {}, {}) is False


def test_panel_text_has_no_markdown_markers(qapp, no_key):
    """③ 界面红线：Qt 不渲染 Markdown —— 面板文案里不许有 `**`。"""
    import ast
    src = (ROOT / 'storygraph' / 'ui_qt' / 'panel_ai.py').read_text(
        encoding='utf-8')
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = getattr(node, 'body', None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    bad = [f'第 {n.lineno} 行: {n.value[:60]}' for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and id(n) not in docs and '**' in n.value]
    assert not bad, bad


def test_a_full_round_runs_offscreen_with_a_fake_transport(qapp, with_key,
                                                           tmp_path):
    """
    ④ 端到端（脱网）：给假 transport，面板真的跑完一轮并渲染出工具轨迹。
    ⚠ 不要在这里弹闸门（`run_agent` 的 approve 走 `_approve_bridge`，会等 300 秒），
    所以脚本里**只调只读工具**。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'ch.rpy').write_text(
        'label a:\n    weather rain 强度=3\n    return\n', encoding='utf-8')
    replies = [
        {'choices': [{'message': {'content': '我看看', 'tool_calls': [
            {'id': 'c1', 'function': {
                'name': 'storygraph_scan_unknown',
                'arguments': json.dumps({'project_root': str(root)})}}]}}]},
        {'choices': [{'message': {'content': '我看到了 weather。'}}]},
    ]
    box = {'i': 0}

    def send(request, timeout):
        reply = replies[min(box['i'], len(replies) - 1)]
        box['i'] += 1
        return reply

    from storygraph.ui_qt.panel_ai import AiPanel
    panel = AiPanel(None, project_root=root, transport=send)
    panel.input.setText('看看有哪些不认识的写法')
    panel.send()
    # ⚠ 跨线程的 Qt 信号要**跑事件循环**才送得到主线程（判据里没有事件循环，
    #   所以手动 processEvents；否则 assert 时事件还排在队列里）。
    for _ in range(200):
        qapp.processEvents()
        if panel._worker is None or not panel._worker.isRunning():
            break
        panel._worker.wait(50)
    qapp.processEvents()
    text = panel.log.toPlainText()
    assert 'scan_unknown' in text, text          # 工具轨迹渲染出来了
    assert 'weather' in text, text               # 回执内容也在
    assert '我看到了 weather' in text, text
    panel.refresh()
    assert panel.send_btn.isEnabled(), '跑完要恢复可点'
