# -*- coding: utf-8 -*-
"""
导出前的身份门禁：`show` 写的名字项目里到底有没有（2026-10-30）
=================================================================

钉四件事：

  1. **判据只认字面量**（`TestScan*`）：合法角色 tag / 项目 .rpy 里声明过的
     图像名 / 真图层名（bg·cg·ui）/ `show screen` 这类目标关键字 —— 一律不报；
     其余的（`show refusing` 这种预览图/文件名）必须报出来。
  2. **没依据就不判**（`TestDegraded`）：拿不到角色表/资源表时白名单是残缺的，
     报出来的多半是假警报 —— `degraded=True`、零 suspect，绝不乱报。
  3. **只读**（`TestReadOnly`）：`scan` 前后 `Beat.to_row()` 逐列一致。
  4. **接进同一个弹窗**（`TestExportFlowIntegration`）：`_review_stage_suspects`
     真的把身份结果交给 `StageSuspectDialog`（不是另造一套框），人点取消时
     一个字节都不写。

判据为什么要这么定、`show refusing` 是怎么来的，见
`storygraph/app/stage_identity_guard.py` 的模块 docstring。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from storygraph.app import stage_identity_guard as G
from storygraph.story.beat_model import BEAT_FIELDS, Beat


# ------------------------------------------------------------------
#  测试数据：一个最小但真实的项目（角色表 + 图片行）
# ------------------------------------------------------------------

#: 角色表：`ImageTag` 是 show 要写的 tag，`Name` 是给人看的显示名。
CHARS = {
    'ling': {'ImageTag': 'ling', 'Name': '凌跃捷', 'Aliases': '凌'},
    'lan': {'ImageTag': 'lan', 'Name': '蓝', 'Aliases': ''},
}

#: 图片行：真立绘 + 预览目录里的副本（预览图**不能**直接 show）。
ROWS = {
    'ling_happy': {'ID': 'ling_happy',
                   'Path': 'game/images/characters/ling/happy.png',
                   'Tag': 'ling', 'Attributes': 'happy'},
    'lan_sad': {'ID': 'lan_sad',
                'Path': 'game/images/characters/lan/sad.png',
                'Tag': 'lan', 'Attributes': 'sad'},
    'happy_preview': {'ID': 'happy_preview',
                      'Path': 'game/cc_preview/ling/happy.png', 'Tag': ''},
    'bg_rooftop': {'ID': 'bg_rooftop',
                   'Path': 'game/images/bg/rooftop.png', 'Tag': 'bg'},
}


def _registry():
    """一个"假 engine/registry"：只提供本判据用到的两张表。"""
    return SimpleNamespace(characters=dict(CHARS), images=dict(ROWS))


def _scan(beats, **kw):
    """用同一个项目上下文扫一遍（与被测调用方 `window_export` 一致）。"""
    kw.setdefault('known_tags', {'ling', 'lan'})
    kw.setdefault('known_chars', CHARS)
    kw.setdefault('all_rows', ROWS)
    return G.scan_stage_identity(beats, **kw)


def _beat(seq, show, **kw):
    return Beat(seq=seq, label='ch01', text=f'第{seq}句', show=show, **kw)


# ------------------------------------------------------------------
#  一、文本拆分：`;` 分隔的 show 项 / spec 首词
# ------------------------------------------------------------------

class TestSpecParsing:
    def test_splits_multi_value_show(self):
        assert G.split_show_specs('ling happy; cg cg01') == \
            ['ling happy', 'cg cg01']

    def test_empty_show_has_no_items(self):
        assert G.split_show_specs('') == []
        assert G.split_show_specs('   ;  ; ') == []
        assert G.split_show_specs(None) == []

    def test_spec_tag_is_the_first_word(self):
        assert G.spec_tag('ling happy') == 'ling'
        assert G.spec_tag('cg cg01 at right') == 'cg'

    def test_at_transform_is_not_part_of_the_name(self):
        """单图带位置时写成 `ling@i11`（紧凑编码）—— 名字是 `ling`。"""
        assert G.spec_tag('ling@i11') == 'ling'
        assert G.spec_tag('ling happy@i11') == 'ling'

    def test_expression_show_is_not_a_name(self):
        """`show expression F(x)` 的首词是关键字，不是图像名。"""
        assert G.spec_tag('expression holo as q at i') == 'expression'


# ------------------------------------------------------------------
#  二、白名单：哪些名字"能上舞台"
# ------------------------------------------------------------------

class TestShowableNames:
    def test_known_character_tag_is_showable(self):
        assert G.is_showable_name('ling', known_tags={'ling', 'lan'})

    def test_known_tag_match_is_case_insensitive(self):
        assert G.is_showable_name('Ling', known_tags={'ling'})

    def test_declared_image_name_is_showable(self):
        """`.rpy` 里 `image mycam idle:` 声明过的名字 → 不许封。"""
        assert G.is_showable_name('mycam', declared={'mycam'})

    def test_non_actor_layer_is_showable(self):
        """`show bg rooftop` / `show cg cg01` 是真图层，不是身份问题。"""
        for word in ('bg', 'cg', 'ui', 'logo', 'black'):
            assert G.is_showable_name(word), word

    def test_show_target_keyword_is_not_reported(self):
        """`show screen …` / `show expression …` 不是图像名，判不了 → 放行。"""
        for word in ('screen', 'expression', 'layer', 'movie', 'text'):
            assert G.is_showable_name(word), word

    def test_show_image_keyword_is_not_reported(self):
        """`show image puzzle_path at …` 的首词是关键字（Ren'Py 的 show image）。"""
        assert G.is_showable_name('image')

    def test_unknown_word_is_not_showable(self):
        assert not G.is_showable_name('refusing', known_tags={'ling'})
        assert not G.is_showable_name('', known_tags={'ling'})

    def test_declared_names_come_from_script_rows(self):
        rows = [
            {'ID': 'mycam', 'Tag': 'mycam', '_from_script': True},
            {'ID': 'logo_blink', 'Tag': 'logo blink', '_atl': True},
            {'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy'},
        ]
        assert G.declared_names(rows) == frozenset({'mycam', 'logo'})

    def test_is_declared_row_accepts_both_markers(self):
        assert G.is_declared_row({'ID': 'x', '_from_script': True})
        assert G.is_declared_row({'ID': 'x', '_layered': True})
        assert not G.is_declared_row({'ID': 'x', 'Path': 'a.png'})


# ------------------------------------------------------------------
#  二·五、项目 .rpy 文本里的名字（真机假警报的根因，2026-10-30）
# ------------------------------------------------------------------

def _write_rpy(root, name, text):
    game = root / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / name
    path.write_text(text, encoding='utf-8')
    return path


class TestProjectScriptNames:
    """
    资源表（xlsx）里**没有**"这行是脚本声明的"标记（那个标记只在内存里），
    所以只看行会认不出 `image bgocean = …` / `layeredimage qing:` —— 真机
    实测 26 条假警报全出在这。名字必须再从磁盘 `.rpy` 文本取一次。
    """

    def test_collects_declared_and_shown_names(self, tmp_path):
        _write_rpy(tmp_path, 'a.rpy', '\n'.join([
            'image bgocean = Transform("x.jpg")',
            'image logo blink:',
            '    "logo.png"',
            'layeredimage qing:',
            '    always:',
            '        "qing.png"',
            'show xqlz at left',
            'show image puzzle_path at Transform(xysize=(1, 1))',
            '# image commented_out = "nope.png"',
            '    $ renpy.notify("show fake_line")',
        ]))
        names = G.project_script_names(str(tmp_path))
        for word in ('bgocean', 'logo', 'qing', 'xqlz', 'image'):
            assert word in names, f'{word} 没被认出来：{sorted(names)}'
        assert 'commented_out' not in names, '注释里的名字不算'
        assert 'fake_line' not in names, '字符串里的名字不算'

    def test_missing_game_dir_is_not_an_error(self, tmp_path):
        assert G.project_script_names(str(tmp_path)) == frozenset()
        assert G.project_script_names('') == frozenset()

    def test_cache_refreshes_when_script_changes(self, tmp_path):
        path = _write_rpy(tmp_path, 'a.rpy', 'image first = "a.png"\n')
        assert 'first' in G.project_script_names(str(tmp_path))
        # 同一份内容 → 走缓存（结果一致就够了）
        assert 'first' in G.project_script_names(str(tmp_path))
        # 改了脚本（大小也变了）→ 缓存必须失效，新名字立刻可用
        path.write_text('image first = "a.png"\nimage second = "b.png"\n',
                        encoding='utf-8')
        names = G.project_script_names(str(tmp_path))
        assert 'first' in names and 'second' in names, names

    def test_scan_uses_the_script_whitelist(self, tmp_path):
        """脚本里真有的名字不报；工具新写出来（脚本里没有）的照样报。"""
        _write_rpy(tmp_path, 'a.rpy', '\n'.join([
            'image bgocean = Transform("x.jpg")',
            'show xqlz at left',
        ]))
        res = G.scan_stage_identity(
            [_beat(1, 'bgocean'), _beat(2, 'xqlz'), _beat(3, 'refusing')],
            project_root=str(tmp_path), known_tags={'ling'},
            all_rows=ROWS)
        assert [s.spec for s in res.suspects] == ['refusing']
        assert res.degraded is False

    def test_script_names_alone_are_enough_evidence(self, tmp_path):
        """没有资源表/角色表，但脚本里有名有姓 → 有依据，可以判。"""
        _write_rpy(tmp_path, 'a.rpy', 'image bgocean = "x.jpg"\n')
        res = G.scan_stage_identity([_beat(1, 'refusing')],
                                    project_root=str(tmp_path))
        assert res.degraded is False
        assert len(res) == 1


# ------------------------------------------------------------------
#  三、主判据：合法不报 / 未知必报
# ------------------------------------------------------------------

class TestScan:
    def test_legal_tag_is_not_reported(self):
        """真立绘的名字（已知角色 tag）→ 一条都不报。"""
        res = _scan([_beat(1, 'ling happy'), _beat(2, 'lan')])
        assert res.suspects == []
        assert not res
        assert res.checked == 2

    def test_preview_tag_is_not_reported(self):
        """
        预览图对应的**角色 tag** 是合法首词（换真立绘之后写的就是它）。

        `_beat(1, 'ling happy')` 里的 `ling` 来自角色表；预览图本身在
        `ROWS['happy_preview']`（不能直接 show），但这不影响合法 tag。
        """
        res = _scan([_beat(1, 'ling happy')])
        assert res.suspects == []

    def test_unknown_spec_is_reported(self):
        """用户原话：**不许静默写出 `show refusing`** → 必须报。"""
        res = _scan([_beat(1, 'ling happy'),
                     _beat(2, 'refusing happy')])
        assert len(res) == 1
        it = res.suspects[0]
        assert (it.index, it.seq, it.spec, it.tag) == \
            (1, 2, 'refusing happy', 'refusing')
        assert 'refusing' in it.reason
        assert '立绘 tag' in it.reason or '图像名' in it.reason

    def test_empty_beats_are_not_reported(self):
        """空拍 / 没有 show 的拍 → 不报，也不配上下文白跑解析。"""
        res = _scan([_beat(1, ''), _beat(2, ''), Beat(seq=3)])
        assert res.suspects == []
        assert res.checked == 0

    def test_multi_show_reports_only_the_bad_item(self):
        """一格多图（`;` 分隔）只报坏的那一条，其余不动。"""
        res = _scan([_beat(1, 'ling happy; refusing sad; cg cg01')])
        assert [s.spec for s in res.suspects] == ['refusing sad']
        assert res.checked == 3

    def test_expression_and_screen_shows_are_not_reported(self):
        """不是图像名的 show 写法（expression / screen）→ 不报。"""
        res = _scan([_beat(1, 'expression holo as q'),
                     _beat(2, 'screen msg_dialogue')])
        assert res.suspects == []

    def test_declared_image_in_project_is_not_reported(self):
        """`.rpy` 里声明过的图像名（不在角色表里）→ 不报。"""
        rows = dict(ROWS)
        rows['mycam'] = {'ID': 'mycam', 'Tag': 'mycam', '_from_script': True}
        res = _scan([_beat(1, 'mycam idle')], all_rows=rows)
        assert res.suspects == []

    def test_display_name_written_instead_of_tag_gets_a_hint(self):
        """把**显示名**写进 show → 报，并且告诉他该写哪个 tag。"""
        res = _scan([_beat(1, '凌跃捷 开心')])
        assert len(res) == 1
        it = res.suspects[0]
        assert it.tag == '凌跃捷'
        assert '显示名' in it.reason
        assert 'ling' in it.reason

    def test_preview_file_name_gets_the_real_sprite_hint(self):
        """
        用户把**预览图的名字**写进 show → 报，并指出同角色的真立绘。

        `happy_preview` 的文件名（`happy`）不是能上舞台的名字；同一张脸在
        资源表里有真立绘（`ling happy`），所以提示要能照着改。
        """
        res = _scan([_beat(1, 'happy')])
        assert len(res) == 1
        it = res.suspects[0]
        assert '预览图' in it.reason
        assert 'ling happy' in it.reason, it.reason

    def test_reason_is_human_readable_for_completely_unknown(self):
        res = _scan([_beat(1, 'zzz')])
        reason = res.suspects[0].reason
        assert 'zzz' in reason
        assert '找不到' in reason or '不存在' in reason
        assert '导出后' in reason, '要说清后果，不能只是"不认识"'

    def test_result_report_lists_every_row(self):
        res = _scan([_beat(1, 'refusing'), _beat(2, 'zzz')])
        text = res.report()
        assert '2 条' in text
        assert 'refusing' in text and 'zzz' in text
        assert res.report() != ''

    def test_clean_result_report_is_empty(self):
        assert _scan([_beat(1, 'ling happy')]).report() == ''


# ------------------------------------------------------------------
#  四、没有依据 → 一条都不判（别乱报）
# ------------------------------------------------------------------

class TestDegraded:
    def test_without_any_evidence_nothing_is_reported(self):
        """
        没有角色表/资源表/声明名 → 白名单是残缺的，**不判**。

        此时报 `show ling` 只会让用户学会无视这个弹窗；真正的 `show refusing`
        也就跟着被无视了。
        """
        res = G.scan_stage_identity([_beat(1, 'refusing')])
        assert res.degraded is True
        assert res.suspects == []
        assert not res

    def test_declared_names_alone_are_enough_evidence(self):
        """没有资源表，但脚本里声明过图像名 → 有依据，可以判。"""
        rows = [{'ID': 'mycam', 'Tag': 'mycam', '_from_script': True}]
        res = G.scan_stage_identity([_beat(1, 'refusing')], all_rows=rows)
        assert res.degraded is False
        assert len(res) == 1

    def test_known_tags_alone_are_enough_evidence(self):
        res = G.scan_stage_identity([_beat(1, 'refusing')],
                                    known_tags={'ling'})
        assert res.degraded is False
        assert len(res) == 1

    def test_broken_rows_do_not_crash_scan(self):
        """资源表行坏了（不是 mapping）→ 提示写不出也不能炸。"""
        res = _scan([_beat(1, 'refusing')], all_rows=['不是一行', None, 42])
        assert len(res) == 1 and res.suspects[0].reason


# ------------------------------------------------------------------
#  五、只读：一个字符都不许改
# ------------------------------------------------------------------

class TestReadOnly:
    def test_to_row_is_identical_before_and_after(self):
        beats = [_beat(1, 'ling happy'),
                 _beat(2, 'refusing happy; cg cg01'),
                 _beat(3, '凌跃捷 开心')]
        before = [dict(zip(BEAT_FIELDS, b.to_row())) for b in beats]
        show_before = [b.show for b in beats]
        stage_before = [b.stage for b in beats]
        comment_before = [b.comment for b in beats]

        _scan(beats)

        after = [dict(zip(BEAT_FIELDS, b.to_row())) for b in beats]
        assert before == after, 'scan 改了拍的内容（导出前的检查必须只读）'
        assert [b.show for b in beats] == show_before
        assert [b.stage for b in beats] == stage_before
        assert [b.comment for b in beats] == comment_before

    def test_suspect_points_at_the_beat_but_does_not_touch_it(self):
        beat = _beat(4, 'refusing')
        res = _scan([beat])
        assert res.suspects[0].target is beat
        assert beat.show == 'refusing'


# ------------------------------------------------------------------
#  六、接进导出流程：同一个弹窗、可仍然导出 / 取消
# ------------------------------------------------------------------

class TestExportFlowIntegration:
    """
    这条门禁的价值全在"导出前真的被调用"（`test_stage_suspect.py` 当初
    就是因为只测纯函数、没测接线，才让 `NameError` 活到用户手上）。
    """

    def _fake_window(self, beats):
        from PySide6.QtWidgets import QWidget

        win = QWidget()
        win._session = SimpleNamespace(beats=beats)
        win._project_root = ''
        win._status = lambda *a, **k: None
        win._log = lambda *a, **k: None
        win._status_bar = SimpleNamespace(set_dirty=lambda *a, **k: None)
        win._registry_provider = _registry
        return win

    @pytest.fixture()
    def dialog_module(self):
        pytest.importorskip('PySide6')
        from storygraph.ui_qt import dialog_stage_suspect as D
        return D

    def test_unknown_show_is_reported_through_the_same_dialog(
            self, qapp, monkeypatch, dialog_module):
        """坏 show → 走同一个 `StageSuspectDialog`，身份结果一并交进去。"""
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt.window_export import WindowExportMixin

        beats = [_beat(1, 'ling happy'), _beat(2, 'refusing happy')]
        win = self._fake_window(beats)

        opened = []
        orig_init = dialog_module.StageSuspectDialog.__init__

        def _init(self, parent, result, allow_move=True, identity=None):
            opened.append({'stage': result, 'identity': identity})
            orig_init(self, parent, result, allow_move=allow_move,
                      identity=identity)

        monkeypatch.setattr(dialog_module.StageSuspectDialog, '__init__', _init)
        monkeypatch.setattr(dialog_module.StageSuspectDialog, 'exec',
                            lambda self: QDialog.Accepted)

        assert WindowExportMixin._review_stage_suspects(win) is True
        assert len(opened) == 1, '身份问题必须和舞台问题进同一个框'
        ident = opened[0]['identity']
        assert ident is not None and len(ident) == 1
        assert 'refusing' in ident.suspects[0].tag

    def test_legal_show_never_opens_a_dialog(self, qapp, monkeypatch,
                                             dialog_module):
        """全部合法 → 连弹窗都不该出现（否则每次导出都闪一下）。"""
        from storygraph.ui_qt.window_export import WindowExportMixin

        called = []

        def _boom(self, *a, **k):
            called.append(1)
            raise AssertionError('不该建这个框')

        monkeypatch.setattr(dialog_module.StageSuspectDialog, '__init__', _boom)
        beats = [_beat(1, 'ling happy'), _beat(2, 'lan sad; cg cg01')]
        assert WindowExportMixin._review_stage_suspects(
            self._fake_window(beats)) is True
        assert not called

    def test_cancel_writes_nothing(self, qapp, monkeypatch, dialog_module):
        """人点「取消导出，去改立绘」→ False（调用方据此一个字节都不写）。"""
        from PySide6.QtWidgets import QDialog

        from storygraph.ui_qt.window_export import WindowExportMixin

        beats = [_beat(1, 'refusing happy')]
        win = self._fake_window(beats)
        monkeypatch.setattr(dialog_module.StageSuspectDialog, 'exec',
                            lambda self: QDialog.Rejected)

        assert WindowExportMixin._review_stage_suspects(win) is False
        assert beats[0].show == 'refusing happy', '取消时什么都不许改'

    def test_table_export_path_also_checks_identity(self, qapp, monkeypatch,
                                                    tmp_path, dialog_module):
        """从 .xlsx 导回那条路同样要过身份门禁（换个入口不能少一道）。"""
        from PySide6.QtWidgets import QDialog

        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.ui_qt.window_export import WindowExportMixin

        xlsx = tmp_path / 'table.xlsx'
        export_beats_to_excel([_beat(1, 'refusing happy')], xlsx)
        win = self._fake_window([])

        opened = []
        orig_init = dialog_module.StageSuspectDialog.__init__

        def _init(self, parent, result, allow_move=True, identity=None):
            opened.append({'stage': result, 'identity': identity,
                           'allow_move': allow_move})
            orig_init(self, parent, result, allow_move=allow_move,
                      identity=identity)

        monkeypatch.setattr(dialog_module.StageSuspectDialog, '__init__', _init)
        monkeypatch.setattr(dialog_module.StageSuspectDialog, 'exec',
                            lambda self: QDialog.Accepted)

        assert WindowExportMixin._review_table_stage_suspects(win, xlsx) is True
        assert opened, '表这条路也必须问一句'
        assert opened[0]['allow_move'] is False, '表的真源是 Excel，不许改工作簿'
        assert len(opened[0]['identity']) == 1

    def test_dialog_shows_identity_section_without_a_move_button(self, qapp):
        """弹窗里：身份问题只列出来、**不能**"移到备注"（那等于删演出）。"""
        from storygraph.story import stage_suspect as SS
        from storygraph.ui_qt.dialog_stage_suspect import StageSuspectDialog

        ident = _scan([_beat(2, 'refusing happy')])
        dlg = StageSuspectDialog(None, SS.ScanResult(), identity=ident)
        try:
            assert dlg.identity_table.rowCount() == 1
            assert 'refusing happy' in dlg.identity_table.item(0, 1).text()
            assert dlg.btn_move.isHidden(), '身份问题不该给"移到备注"'
            assert '去改' in dlg.btn_cancel.text()
        finally:
            dlg.close()

    def test_dialog_with_both_kinds_keeps_stage_move(self, qapp):
        """两类同时出现：舞台那张表照旧可勾可挪，身份问题只读。"""
        from storygraph.story import stage_suspect as SS
        from storygraph.ui_qt.dialog_stage_suspect import StageSuspectDialog

        stage_res = SS.scan([Beat(seq=1, label='x', text='台词',
                                  stage='bg1.2 bgm13')])
        ident = _scan([_beat(2, 'refusing')])
        dlg = StageSuspectDialog(None, stage_res, identity=ident)
        try:
            assert dlg.table.rowCount() == 1
            assert dlg.identity_table.rowCount() == 1
            assert not dlg.btn_move.isHidden()
            assert dlg.selected(), '舞台可疑行仍然默认勾选'
        finally:
            dlg.close()
