"""
tests/test_collab_conflict_dialog.py
====================================
C2（用户 2026-10-19）：冲突界面 —— **两栏 + 三个按钮**，用编剧的话。

用户原话（逐条钉）：

> 三栏 diff 是工程视角，编剧看到 base 那一栏会问"这是什么"。
> 我建议改成两栏 + 一句话：「这一句，你和 TA 都改了」
>   [ 用我的 ]  [ 用 TA 的 ]  [ 两句都留 ▾ ]
> 不提"冲突"这个词…

> 默认选中较新的一份，让人可以一路回车过。

> 「自己拼一版」永远给预填内容，不要给空框。
"""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402


def _conflict(kind='fields', line_id='ch01.0002', mine='我写的', theirs='TA 写的',
              base='原来的', who='bob', **kw):
    c = {
        'kind': kind, 'reason': kind, 'line_id': line_id, 'label': 'ch01',
        'db_revision': 3, 'updated_by': who,
        'fields': [{'field': 'Text', 'base': base, 'mine': mine,
                    'theirs': theirs}],
        'mine': {'LineId': line_id, 'Text': mine},
        'theirs': {'LineId': line_id, 'Text': theirs},
        'base': {'LineId': line_id, 'Text': base},
        'hint': '你和 TA 改了同一句的同一格',
    }
    c.update(kw)
    return c


@pytest.fixture
def dlg(qapp):
    from storygraph.ui_qt.dialogs_collab import CollabConflictDialog
    d = CollabConflictDialog(None, [_conflict()])
    yield d
    d.close()


class TestWording:
    def test_it_says_you_and_they_changed_the_same_line(self, dlg):
        titles = [w.title() for w in dlg.findChildren(type(dlg._rows[0]['group']))]
        assert any('你和 bob 改了同一句' in t for t in titles), titles

    def test_it_never_says_the_word_conflict(self, dlg):
        texts = ' '.join(w.text() for w in dlg.findChildren(
            __import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel))
        assert '冲突' not in texts, texts

    def test_both_sides_are_shown_side_by_side(self, dlg):
        texts = ' '.join(w.text() for w in dlg.findChildren(
            __import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel))
        assert '我写的' in texts and 'TA 写的' in texts
        assert '你写的' in texts

    def test_base_is_not_shown(self, dlg):
        """base 只用于算法，不进界面。"""
        texts = ' '.join(w.text() for w in dlg.findChildren(
            __import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel))
        assert '原来的' not in texts


class TestDefaults:
    def test_theirs_is_selected_by_default(self, dlg):
        row = dlg._rows[0]['fields']['Text']
        assert row.value() == 'theirs'

    def test_picking_mine_is_one_click(self, dlg):
        row = dlg._rows[0]['fields']['Text']
        row.choose('mine')
        assert row.value() == 'mine'

    def test_accept_collects_the_choices(self, dlg):
        dlg._rows[0]['fields']['Text'].choose('mine')
        dlg.accept()
        assert dlg.resolutions == [
            {'line_id': 'ch01.0002', 'kind': 'fields',
             'values': {'Text': 'mine'}}]

    def test_skip_produces_no_resolution(self, dlg):
        dlg._rows[0]['skip'].setChecked(True)
        dlg.accept()
        assert dlg.resolutions == []


class TestKeepBoth:
    def test_the_button_records_the_order(self, dlg):
        dlg._keep_both(0, _Order('mine_first'))
        dlg.accept()
        assert dlg.resolutions[0]['kind'] == 'keep_both'
        assert dlg.resolutions[0]['order'] == 'mine_first'
        assert dlg.resolutions[0]['line_id'] == 'ch01.0002'

    def test_theirs_first_is_the_other_option(self, dlg):
        dlg._keep_both(0, _Order('theirs_first'))
        dlg.accept()
        assert dlg.resolutions[0]['order'] == 'theirs_first'


class _Order:
    """假的 QComboBox（只给 `currentData()`）。"""

    def __init__(self, value):
        self._value = value

    def currentData(self):
        return self._value


class TestCustomMerge:
    def test_the_box_is_prefilled_not_empty(self, dlg, monkeypatch):
        seen = {}

        def fake_exec(self):
            seen['text'] = self.findChildren(
                __import__('PySide6.QtWidgets', fromlist=['QPlainTextEdit'])
                .QPlainTextEdit)[0].toPlainText()
            return 0                        # 取消：只看预填内容

        monkeypatch.setattr(
            __import__('PySide6.QtWidgets', fromlist=['QDialog']).QDialog,
            'exec', fake_exec)
        dlg._custom_merge(0)
        assert seen['text'].strip(), '「自己拼一版」不许给空框'
        assert seen['text'] == 'TA 写的', seen['text']
