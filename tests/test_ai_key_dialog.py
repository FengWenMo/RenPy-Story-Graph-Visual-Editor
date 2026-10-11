"""
「AI 助手 · 填 API key」对话框判据（用户 2026-10-11 的要求）
================================================================

> "最好打开之后能有一个地方点开，然后让用户输入 api key"

三条必须钉住（都是"会给用户惹麻烦"的那种）：
  ① key **存在用户级目录**（判据里用注入路径，绝不碰真机配置）；
  ② 界面**不回显明文 key**（存完立刻清掉输入框；状态行只给脱敏形式）；
  ③ **能点就必须有反应**：没填 key 时保存按钮灰化，且状态行说清缺什么。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_the_dialog_saves_to_the_user_level_file(qapp, tmp_path):
    """① 保存走 `ai_settings.save`（用户级文件），**不写项目、不写 cwd**。"""
    from storygraph.core import ai_settings as AI
    from storygraph.ui_qt.dialog_ai import AiKeyDialog
    fp = tmp_path / 'ai.json'
    dlg = AiKeyDialog(None, path=fp)
    dlg.provider.setCurrentIndex(dlg.provider.findData('deepseek'))
    dlg.key.setText('sk-test-abcdefghijkl')
    dlg.model.setText('deepseek-chat')
    dlg._save()
    assert fp.is_file(), '没写到注入的那个路径'
    cfg = AI.load(fp)
    assert cfg['api_key'] == 'sk-test-abcdefghijkl'
    assert cfg['provider'] == 'deepseek'


def test_the_dialog_never_shows_the_raw_key(qapp, tmp_path):
    """
    ② ⭐ 界面里不许出现明文 key（截图、录屏、issue 都会带上）——
    存完**立刻清空输入框**，状态行只说脱敏形式。
    """
    from storygraph.core import ai_settings as AI
    from storygraph.ui_qt.dialog_ai import AiKeyDialog
    fp = tmp_path / 'ai.json'
    dlg = AiKeyDialog(None, path=fp)
    dlg.key.setText('sk-super-secret-value')
    dlg._save()
    assert dlg.key.text() == '', '存完还留着明文'
    text = dlg.status.text()
    assert 'sk-super-secret-value' not in text, text
    assert AI.masked('sk-super-secret-value') in text, text
    # 再打开一次（模拟"回来改"）：仍然只有脱敏
    dlg2 = AiKeyDialog(None, path=fp)
    assert dlg2.key.text() == ''
    assert 'sk-super-secret-value' not in dlg2.status.text()


def test_save_button_is_disabled_until_a_key_is_typed(qapp, tmp_path):
    """③ 能点就必须有反应：没填 → 灰化 + 状态行说清楚。"""
    from storygraph.ui_qt.dialog_ai import AiKeyDialog
    dlg = AiKeyDialog(None, path=tmp_path / 'ai.json')
    assert not dlg.save_btn.isEnabled(), '没填 key 却能点保存'
    assert '先粘一个 key' in dlg.status.text(), dlg.status.text()
    dlg.key.setText('sk-x')
    assert dlg.save_btn.isEnabled()
    dlg.key.clear()
    assert not dlg.save_btn.isEnabled()


def test_clear_removes_the_key_and_the_button_state_follows(qapp, tmp_path):
    """清 key 也要有反应：状态行改口，且清完就没得再清。"""
    from storygraph.core import ai_settings as AI
    from storygraph.ui_qt.dialog_ai import AiKeyDialog
    fp = tmp_path / 'ai.json'
    AI.save('deepseek', 'sk-to-be-cleared', path=fp)
    dlg = AiKeyDialog(None, path=fp)
    assert dlg.clear_btn.isEnabled()
    dlg._clear()
    assert AI.load(fp)['api_key'] == ''
    assert not dlg.clear_btn.isEnabled()
    assert '已清掉' in dlg.status.text(), dlg.status.text()


def test_the_dialog_text_has_no_markdown_markers(qapp, tmp_path):
    """界面红线：Qt 不渲染 Markdown —— 文案里不许出现 `**`。"""
    import re
    from PySide6.QtWidgets import QLabel, QPushButton, QComboBox
    from storygraph.ui_qt.dialog_ai import AiKeyDialog
    dlg = AiKeyDialog(None, path=tmp_path / 'ai.json')
    widgets = (dlg.findChildren(QLabel) + dlg.findChildren(QPushButton)
               + dlg.findChildren(QComboBox))
    bad = [w.text() for w in widgets if hasattr(w, 'text') and '**' in w.text()]
    assert not bad, bad
    src = (ROOT / 'storygraph' / 'ui_qt' / 'dialog_ai.py').read_text(
        encoding='utf-8')
    import ast
    tree = ast.parse(src)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = getattr(node, 'body', None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docs and '**' in node.value:
            raise AssertionError(f'第 {node.lineno} 行有 Markdown 星号：'
                                 f'{node.value[:60]}')


def test_the_window_exposes_the_entry(qapp):
    """
    ⭐ 入口要在（用户："最好打开之后能有一个地方点开"）——
    工具栏里能找到一个「填 API key」的动作，而且它连着真方法。
    """
    from storygraph.ui_qt.main_window import MainWindowQt
    assert callable(getattr(MainWindowQt, 'open_ai_key_dialog', None)), \
        '窗口上没有 open_ai_key_dialog —— 入口没接上'
    src = (ROOT / 'storygraph' / 'ui_qt' / 'main_window.py').read_text(
        encoding='utf-8')
    assert 'AI 助手 · 填 API key' in src, '工具栏里没有那个按钮'
    assert 'self.open_ai_key_dialog' in src, '按钮没连到方法'
