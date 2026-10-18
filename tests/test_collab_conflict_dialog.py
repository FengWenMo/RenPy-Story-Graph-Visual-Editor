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


class TestAThirtyThousandCharacterLineFitsOnScreen:
    """
    用户 2026-10-18："一条 3w 多字，整个屏幕估计都装不下。"

    32767 是 Excel 的上限，不是**屏幕**的上限：一句 3 万字的独白合法，
    但两栏各放一块 3 万字的 QLabel，窗口会被撑到屏幕外，而人只是要选一边。
    """

    def _dlg(self, mine, theirs):
        from storygraph.ui_qt.dialogs_collab import CollabConflictDialog
        return CollabConflictDialog(None, [_conflict(mine=mine, theirs=theirs)])

    def test_each_side_shows_only_the_beginning(self, qapp):
        long = '甲' * 30000
        d = self._dlg(long, long)
        try:
            from PySide6.QtWidgets import QLabel, QPushButton
            labels = [w.text() for w in d.findChildren(QLabel)]
            buttons = [w.text() for w in d.findChildren(QPushButton)]
            longest = max((len(t) for t in labels), default=0)
            assert longest <= 400, f'有一格显示了 {longest} 字，屏幕放不下'
            assert any('共 30,000 字' in t for t in buttons), (
                f'截断了却没说总字数 / 全文在哪：{buttons}')
        finally:
            d.close()

    def test_the_choice_still_carries_the_full_text(self, qapp):
        mine, theirs = '甲' * 30000, '乙' * 30000
        d = self._dlg(mine, theirs)
        try:
            d.accept()
            assert d.resolutions[0]['values'] == {'Text': 'theirs'}
            assert d._rows[0]['fields']['Text']._theirs == theirs
            assert d._rows[0]['fields']['Text']._mine == mine
        finally:
            d.close()

    def test_short_text_is_shown_whole(self, qapp):
        from storygraph.ui_qt.dialogs_collab import DISPLAY_LIMIT, _split_display
        assert _split_display('短句') == ('短句', 2)
        shown, total = _split_display('长' * (DISPLAY_LIMIT + 1))
        assert total == DISPLAY_LIMIT + 1
        assert len(shown) <= DISPLAY_LIMIT + 2 and shown.endswith('…')
