# -*- coding: utf-8 -*-
"""
舞台列的"看着不像语句"判据 + 导出前的那句问话（2026-10-29）
========================================================================

用户拍板的两句话是这一整个文件的前提：

> 我的补#号只能发生在备注上……高级区 stage 不应当补#
> 可以！但也要尽量检测一下！实在看着不像是代码的才加弹窗确认，询问要不要
> 补为备注！！！如果很明显是代码，那就不需要，原样放进去就行……这个判断器
> 要写好，宁错报，不要漏报

所以这里钉三件事：

  1. **判据宽进**（`TestWhySuspect`）：明显是代码的一律放行，看不出是代码的
     一律报出来。**漏报是不可接受的**（作者写完导出、启动游戏才发现那句没
     生效），错报只是多问一次。
  2. **指认是只读的**（`TestScan`）：`scan()` 不许改 `beat.stage`。
  3. **要改只能人点头**（`TestMoveToComment` / `TestDialog`）：挪到备注是
     弹窗里点出来的动作，且**只挪可疑那几条**，同格其它语句原地不动。
"""

from __future__ import annotations

import pytest

from storygraph.story.beat_model import Beat
from storygraph.story.stage_suspect import (ScanResult, Suspect, known_heads_for,
                                            move_to_comment, scan, why_suspect)

# ------------------------------------------------------------------
#  一、判据：宽进
# ------------------------------------------------------------------

#: 明显是代码 —— **不许报**（报了就是无端打扰）
CODE_LIKE = [
    'show ling happy',
    'hide ling',
    'scene bg room',
    'pause 1.0',
    'with dissolve',
    'window hide',
    'play music bgm03',
    'stop music fadeout 1.0',
    '$ x = 1',
    '# bg1.2',
    '[显示] 凌跃捷 开心',
    'show ling at right with dissolve',
    'renpy.jump("x")',
    'foo[0]',
    'target = ling',
    'mycue "x"',
    'weather6 rain',          # 动词 + 参数形状 → 像语句
    'mycam shake 0.5 loop',   # 带 loop 参数
    'ling happy',             # 两个词都是标识符
]

#: 看着不像语句 —— **必须报**（这是"宁错报不漏报"的落点）
SUSPECT_LIKE = [
    'bg1.2 bgm13',
    '白色转场',
    '（淡出）',
    'bg1.2（淡出）',
    'bg1.2: 淡出',
    'bg1.2, bgm13',
    '后巷 夜',
    'bg9_2.mp3',              # 单看是"文件名"，更像备忘
    'night.ogg',
]


class TestWhySuspect:
    @pytest.mark.parametrize('text', CODE_LIKE)
    def test_code_like_is_not_reported(self, text):
        assert why_suspect(text) == '', f'明明像代码却被报：{text!r}'

    @pytest.mark.parametrize('text', SUSPECT_LIKE)
    def test_suspect_like_is_reported(self, text):
        why = why_suspect(text)
        assert why, f'漏报了：{text!r}'
        assert isinstance(why, str) and len(why) > 4, why

    def test_empty_and_hash_are_skipped(self):
        assert why_suspect('') == ''
        assert why_suspect('   ') == ''
        assert why_suspect('# 随便写点什么') == ''

    def test_registered_custom_statement_is_not_reported(self):
        """项目登记过（rules.json / CDS）的自定义语句头 → 不再问。"""
        assert why_suspect('weather6 rain', known_heads={'weather6'}) == ''

    def test_unregistered_head_is_reported(self):
        """没登记的未知头 + 不像语句的参数 → 问一次（宁可多问）。"""
        assert why_suspect('weather6 雨', known_heads=frozenset())

    def test_reason_is_human_readable(self):
        """原因要能让人一眼看懂是哪一类（弹窗里就显示这行）。"""
        assert '资源' in why_suspect('bg1.2 bgm13')
        assert '不像语句' in why_suspect('白色转场')


class TestKnownHeads:
    def test_builtin_heads_are_always_known(self):
        heads = known_heads_for(None)
        for h in ('show', 'scene', 'pause', 'with', 'play', 'window'):
            assert h in heads, h

    def test_no_project_is_not_an_error(self):
        """没打开工程也要能跑（只认内置那批）。"""
        assert isinstance(known_heads_for(''), frozenset)


# ------------------------------------------------------------------
#  二、scan：只读
# ------------------------------------------------------------------

def _beats():
    return [
        Beat(seq=1, label='ch01', text='一', stage='show ling happy'),
        Beat(seq=2, label='ch01', text='二', stage='bg1.2 bgm13'),
        Beat(seq=3, label='ch01', text='三',
             stage='show ling happy\n白色转场'),
        Beat(seq=4, label='ch01', text='四', stage='weather6 rain'),
        Beat(seq=5, label='ch01', text='五', stage=''),
    ]


def _attr_res():
    """一条属性门禁结果（只用来测弹窗第三块，不跑真扫描）。"""
    from storygraph.app.sprite_attr_guard import AttrSuspect
    from storygraph.app.sprite_attr_guard import ScanResult as AttrScan
    return AttrScan(
        suspects=[AttrSuspect(
            index=0, seq=1, label='ch01', spec='ye base_hand_down happy',
            tag='ye', attr='base_hand_down', fix='base_handdown',
            reason='「ye」没有「base_hand_down」这个属性 —— 项目里写的是'
                   '「base_handdown」。',
            consequence='引擎会忽略它：游戏通常还能跑，但画面不是你要的。')],
        checked=1, judged=1)


class TestScan:
    def test_finds_the_right_ones(self):
        res = scan(_beats())
        assert [s.seq for s in res.suspects] == [2, 3], res.suspects
        assert res.suspects[0].text == 'bg1.2 bgm13'
        assert res.suspects[1].text == '白色转场'

    def test_marks_multi_when_cell_has_more(self):
        res = scan(_beats())
        by_seq = {s.seq: s for s in res.suspects}
        assert by_seq[2].multi is False
        assert by_seq[3].multi is True

    def test_is_read_only(self):
        beats = _beats()
        before = [(b.stage, b.comment) for b in beats]
        scan(beats)
        assert [(b.stage, b.comment) for b in beats] == before

    def test_clean_project_is_silent(self):
        beats = [Beat(seq=1, label='x', text='t', stage='show ling at right')]
        res = scan(beats)
        assert not res and len(res) == 0
        assert res.report() == ''

    def test_report_lists_every_row(self):
        res = scan(_beats())
        text = res.report()
        assert '第 2 拍' in text and '第 3 拍' in text
        assert '备注' in text

    def test_empty_input_is_fine(self):
        assert scan([]) == [] or not scan([])


# ------------------------------------------------------------------
#  三、move_to_comment：只挪可疑的
# ------------------------------------------------------------------

class TestMoveToComment:
    def test_moves_only_the_suspect_chunk(self):
        beats = _beats()
        res = scan(beats)
        n = move_to_comment(beats, res.suspects)
        assert n == 2
        # 第 3 拍：可疑的挪走，同格的 show 原地留下
        assert beats[2].stage == 'show ling happy'
        assert beats[2].comment == '白色转场'
        # 第 2 拍：整格挪走
        assert beats[1].stage == ''
        assert beats[1].comment == 'bg1.2 bgm13'
        # 干净的拍一个字节都没动
        assert beats[0].stage == 'show ling happy' and beats[0].comment == ''
        assert beats[3].stage == 'weather6 rain' and beats[3].comment == ''

    def test_appends_to_existing_comment(self):
        beats = [Beat(seq=1, label='x', text='t',
                      stage='bg1.2', comment='原有备注')]
        res = scan(beats)
        move_to_comment(beats, res.suspects)
        assert beats[0].comment == '原有备注\nbg1.2'

    def test_moved_text_renders_as_comment(self):
        from storygraph.story.script_table import render_beats_to_rpy

        beats = [Beat(seq=1, label='x', text='台词', stage='bg1.2 bgm13')]
        move_to_comment(beats, scan(beats).suspects)
        out = render_beats_to_rpy(beats, label_name='x')
        assert '\n    # bg1.2 bgm13' in out, out
        assert '\n    bg1.2 bgm13' not in out, out

    def test_no_suspects_is_a_noop(self):
        beats = [Beat(seq=1, label='x', text='t', stage='show a')]
        assert move_to_comment(beats, []) == 0
        assert beats[0].stage == 'show a'


# ------------------------------------------------------------------
#  四、弹窗：真建一个框（不 exec，避免模态挂死）
# ------------------------------------------------------------------

class TestDialog:
    def _dlg(self, qapp):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.dialog_stage_suspect import StageSuspectDialog
        res = scan(_beats())
        return StageSuspectDialog(None, res)

    def test_rows_and_defaults(self, qapp):
        dlg = self._dlg(qapp)
        assert dlg.table.rowCount() == 2
        assert len(dlg.selected()) == 2, '默认应全勾'
        assert '2' in dlg.btn_move.text()
        assert dlg.decision == 'keep', '默认决定必须是"原样导出"'
        dlg.close()

    def test_unchecking_disables_move(self, qapp):
        from PySide6.QtCore import Qt

        dlg = self._dlg(qapp)
        for row in range(dlg.table.rowCount()):
            dlg.table.item(row, 0).setCheckState(Qt.Unchecked)
        assert dlg.selected() == []
        assert not dlg.btn_move.isEnabled(), '一条都没勾时"移到备注"应是灰的'
        dlg.close()

    def test_keep_and_cancel_are_distinguishable(self, qapp):
        from PySide6.QtWidgets import QDialog

        dlg = self._dlg(qapp)
        dlg._on_keep()
        assert dlg.decision == 'keep'
        assert dlg.result() == QDialog.Accepted
        dlg.close()

        dlg2 = self._dlg(qapp)
        dlg2._on_cancel()
        assert dlg2.decision == 'cancel'
        assert dlg2.result() == QDialog.Rejected, '取消必须是 Rejected（零写）'
        dlg2.close()

    def test_move_requires_selection(self, qapp):
        from PySide6.QtCore import Qt

        dlg = self._dlg(qapp)
        for row in range(dlg.table.rowCount()):
            dlg.table.item(row, 0).setCheckState(Qt.Unchecked)
        dlg._on_move()                       # 没勾任何一条 → 什么都不该发生
        assert dlg.decision == 'keep'
        dlg.close()

    def test_table_path_offers_no_move(self, qapp):
        """从表导出那条路不给"移到备注"：真源是 Excel，工具不改工作簿。"""
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.dialog_stage_suspect import StageSuspectDialog

        dlg = StageSuspectDialog(None, scan(_beats()), allow_move=False)
        assert dlg.table.isColumnHidden(0), '不能挪却不该让人勾'
        dlg.close()

    def test_attrs_section_rows_and_no_fix_button(self, qapp):
        """第三类只摆出来：没有"修一下"按钮，默认仍是原样导出。"""
        pytest.importorskip('PySide6')
        from storygraph.story import stage_suspect as SS
        from storygraph.ui_qt.dialog_stage_suspect import StageSuspectDialog

        dlg = StageSuspectDialog(None, SS.ScanResult(), attrs=_attr_res())
        assert dlg.attr_table.rowCount() == 1
        assert not hasattr(dlg, 'btn_fix'), '只报，不许给一键改'
        assert dlg.btn_keep.text() == '原样导出'
        assert dlg.decision == 'keep'
        dlg.close()

    def test_attrs_only_hides_stage_table(self, qapp):
        pytest.importorskip('PySide6')
        from storygraph.story import stage_suspect as SS
        from storygraph.ui_qt.dialog_stage_suspect import StageSuspectDialog

        dlg = StageSuspectDialog(None, SS.ScanResult(), attrs=_attr_res())
        assert dlg.table.isHidden(), '没有舞台可疑行，那张表不该出现'
        assert dlg.btn_move.isHidden(), '没有可挪的东西，按钮也不该出现'
        assert '立绘' in dlg.btn_cancel.text()
        dlg.close()


# ------------------------------------------------------------------
#  五、接线：导出入口真的要问一句
# ------------------------------------------------------------------

class TestWiring:
    def test_export_flow_has_the_review_hook(self):
        from storygraph.ui_qt.window_export import WindowExportMixin

        assert hasattr(WindowExportMixin, '_review_stage_suspects'), \
            '导出流程里没有接"导出前检查"'

    def test_review_runs_before_compile(self):
        """`export_script` 必须先问、后 compile（问完取消就一个字节都不写）。"""
        import inspect

        from storygraph.ui_qt.window_export import WindowExportMixin

        src = inspect.getsource(WindowExportMixin.export_script)
        i_review = src.find('_review_stage_suspects')
        i_compile = src.find('self._session.compile()')
        assert 0 <= i_review < i_compile, (i_review, i_compile)

    def test_cli_has_the_warning(self):
        from storygraph import cli

        assert hasattr(cli, '_warn_stage_suspects')

    def test_cli_warns_without_blocking(self, tmp_path):
        """命令行只提示、不改表、不拦（退出码交给后面那套判据）。"""
        from storygraph.story.excel_export import export_beats_to_excel

        xlsx = tmp_path / 't.xlsx'
        export_beats_to_excel(
            [Beat(seq=1, label='t', text='台词', stage='bg1.2 bgm13')], xlsx)
        before = xlsx.read_bytes()
        from storygraph import cli

        n = cli._warn_stage_suspects(xlsx)
        assert n == 1
        assert xlsx.read_bytes() == before, '提示不许改表'


class TestReviewHookBehaviour:
    """
    行为级：`_review_stage_suspects` 真的会问、问完真会改。

    不用 `inspect.getsource` 读源码 —— 那种断言只能证明"字串出现了"，
    证明不了"点了会动"。这里把弹窗的 `exec/selected/decision` 打桩成
    "人点了移到备注"，然后看内存里的 beat 是不是真的变了。
    """

    def _fake_window(self, beats, qapp):
        """
        一个**真的** QWidget 当 parent（Qt 只认 QWidget，`SimpleNamespace`
        会在 `super().__init__(parent)` 当场 TypeError），属性照旧挂上去。
        """
        from types import SimpleNamespace

        from PySide6.QtWidgets import QWidget

        win = QWidget()
        win._session = SimpleNamespace(beats=beats)
        win._project_root = ''
        win._status = lambda *a, **k: None
        win._log = lambda *a, **k: None
        win._status_bar = SimpleNamespace(set_dirty=lambda *a, **k: None)
        return win

    def test_asks_and_moves_when_user_agrees(self, qapp, monkeypatch):
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialog_stage_suspect as D
        from storygraph.ui_qt.window_export import WindowExportMixin

        beats = [Beat(seq=1, label='x', text='台词', stage='bg1.2 bgm13')]
        win = self._fake_window(beats, qapp)

        def _fake_exec(self):
            # 模拟"人勾了、点了移到备注"：`decision` 是实例属性（`__init__`
            # 里设成 keep），只能在实例上改 —— patch 类属性会被 __init__ 覆盖。
            self.decision = D.MOVE
            return QDialog.Accepted

        monkeypatch.setattr(D.StageSuspectDialog, 'exec', _fake_exec)

        assert WindowExportMixin._review_stage_suspects(win) is True
        # 人点了"移到备注" → 内存真的改了（stage 空、comment 有了）
        assert beats[0].stage == ''
        assert beats[0].comment == 'bg1.2 bgm13'

    def test_cancel_blocks_the_export(self, qapp, monkeypatch):
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialog_stage_suspect as D
        from storygraph.ui_qt.window_export import WindowExportMixin

        beats = [Beat(seq=1, label='x', text='台词', stage='bg1.2 bgm13')]
        win = self._fake_window(beats, qapp)

        monkeypatch.setattr(D.StageSuspectDialog, 'exec',
                            lambda self: QDialog.Rejected)

        assert WindowExportMixin._review_stage_suspects(win) is False, \
            '取消必须是 False（调用方据此 return，零写）'
        assert beats[0].stage == 'bg1.2 bgm13', '取消时一个字都不许改'

    def test_clean_beats_never_open_a_dialog(self, qapp, monkeypatch):
        """没有可疑内容 → 连弹窗都不该构造（否则每次导出都闪一下）。"""
        pytest.importorskip('PySide6')
        from storygraph.ui_qt import dialog_stage_suspect as D
        from storygraph.ui_qt.window_export import WindowExportMixin

        called = []

        def _boom(self, *a, **k):
            called.append(1)
            raise AssertionError('不该建这个框')

        monkeypatch.setattr(D.StageSuspectDialog, '__init__', _boom)
        beats = [Beat(seq=1, label='x', text='台词', stage='show ling happy')]
        assert WindowExportMixin._review_stage_suspects(
            self._fake_window(beats, qapp)) is True
        assert not called

    def test_attr_suspects_open_the_dialog(self, qapp, monkeypatch):
        """属性类也要在导出前问一句：`base_hand_down` 不能被静默放行。"""
        pytest.importorskip('PySide6')
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt import dialog_stage_suspect as D
        from storygraph.ui_qt.window_export import WindowExportMixin

        class _Sym:
            def declared_attrs_of(self, tag):
                return ['ahah', 'gratified', 'base_handdown'] if tag == 'ye' else []

            def attrs_of_tag(self, tag):
                return self.declared_attrs_of(tag)

            def defined_words(self):
                return ['aa']

            def token_image_path(self, tag, token):
                return ''

        beats = [Beat(seq=1, label='x', text='台词',
                      show='ye base_hand_down happy')]
        win = self._fake_window(beats, qapp)
        win._session.symbols = _Sym()

        monkeypatch.setattr(D.StageSuspectDialog, 'exec',
                            lambda self: QDialog.Rejected)

        assert WindowExportMixin._review_stage_suspects(win) is False, \
            '属性问题必须走"取消 = 零写"那条路'
        assert beats[0].show == 'ye base_hand_down happy', '一个字都不许改'
