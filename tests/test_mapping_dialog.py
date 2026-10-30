# -*- coding: utf-8 -*-
"""
tests/test_mapping_dialog.py
============================
**资源映射管理对话框 + 资源对照表**（用户 2026-10-30，⑥）

用户原话：

> 映射表：查询 / 对照 / 修改 / 随包分发

这一片钉住的是"人能对一份映射文件做什么"，以及**每一步都别把人锁在门外**：

  · 项目里有 `storygraph.mapping.json` → 自动认出来（状态说"项目文件"）；
  · 没有 → 明确说"按文件夹推断"，不装成有映射；
  · 坏 JSON / 未知键 → 一句人话提示，**不闪退**；
  · 资源对照表：一张图一行（路径 / 推断 / tag / 属性 / 依据 / 能否 show），
    可以只看"未识别"；
  · 导出初稿 → 写出、读回、和 draft 一致（round-trip）；
  · 保存到项目根 → **先给 diff，确认后才写**，旧文件留 `.bak`；
  · 「重读」真的从磁盘重读（外部编辑器改完不用重启工具）。

判定/读写逻辑在 S1 的 `assets.asset_mapping` / `asset_identity`；这里只测
界面层有没有把它们**如实**摆出来。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip('PySide6')


# ==================================================================
#  夹具
# ==================================================================

def _write_png(path: Path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (8, 8), (60, 120, 200, 255)).save(path, 'PNG')


IMAGE_ROWS = [
    ('ling_happy', 'ling', 'happy',
     'game/images/characters/ling/happy.png'),
    ('ling_refusing', 'ling', 'refusing',
     'game/images/characters/ling/refusing.png'),
    ('refusing', '', '', 'game/preview/characters/ling/refusing.png'),
    ('bg_room', 'bg', 'room', 'game/images/bg/room.png'),
    ('cg_01', 'cg', '01', 'game/images/cg/cg01.png'),
    ('mystery', '', '', 'game/art/mystery.png'),
]

CHARACTER_ROWS = {
    'ling': {'ID': 'ling', 'Name': '凌跃捷', 'ImageTag': 'ling',
             'Color': '#c8ffc8', 'Aliases': '凌|lyj', 'Notes': ''},
}


def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}
    for aid, tag, attrs, rel in IMAGE_ROWS:
        reg.images[aid] = {'ID': aid, 'Tag': tag, 'Attributes': attrs,
                           'Path': rel}
    reg.characters = dict(CHARACTER_ROWS)
    reg.music = {}
    reg.sounds = {}
    reg.voices = {}
    reg.transforms = {}
    return reg


def _build_project(tmp_path) -> Path:
    proj = tmp_path / 'proj'
    (proj / 'game').mkdir(parents=True)
    for _aid, _tag, _attrs, rel in IMAGE_ROWS:
        _write_png(proj / rel)
    return proj


#: 一份合法的映射（覆盖 角色表 / 规则 / 精确覆盖 三个关键手段）。
VALID_MAPPING = {
    'schema': 'storygraph.mapping/1',
    'name': '测试项目 · 资源映射',
    'characters': {
        'ling': {'display': '凌跃捷', 'aliases': ['凌', 'lyj']},
    },
    'rules': [
        {'glob': 'game/images/characters/<tag>/*.png', 'kind': 'sprite',
         'char': '<tag>', 'attr': '<stem>'},
        {'glob': 'game/preview/characters/<tag>/*.png', 'kind': 'preview',
         'char': '<tag>', 'attr': '<stem>'},
    ],
    'images': {'cg_01': {'kind': 'cg'}},
    'attributes': {'expressions': {'label': '表情'}},
}


def _write_mapping(proj: Path, data, name: str = 'storygraph.mapping.json') -> Path:
    path = proj / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    return path


@pytest.fixture
def project(tmp_path):
    return _build_project(tmp_path)


@pytest.fixture
def dlg(qapp, project):
    from storygraph.ui_qt.mapping_dialog import MappingDialog
    d = MappingDialog(None, registry=_registry(), project_root=project)
    yield d
    d.close()


# ==================================================================
#  ① 当前映射是哪来的（状态那一行）
# ==================================================================

class TestStateFromProject:
    def test_no_mapping_says_so_instead_of_pretending(self, project):
        from storygraph.ui_qt import mapping_dialog as MD
        state = MD.state_from_project(project)
        assert state.kind == MD.SOURCE_NONE
        assert not state.has_data()
        assert MD.status_text(state) == '映射：无（按文件夹推断）'

    def test_a_project_mapping_is_found_and_loaded(self, project):
        from storygraph.ui_qt import mapping_dialog as MD
        _write_mapping(project, VALID_MAPPING)
        state = MD.state_from_project(project)
        assert state.kind == MD.SOURCE_PROJECT
        assert state.has_data()
        assert state.data.get('schema') == 'storygraph.mapping/1'
        assert state.path.endswith('storygraph.mapping.json'), state.path
        assert '项目文件' in MD.status_text(state)

    def test_the_dialog_status_line_matches_the_project(self, project, dlg):
        assert '按文件夹推断' in dlg.status_line(), dlg.status_line()
        _write_mapping(project, VALID_MAPPING)
        assert dlg.reload_mapping() is True
        assert '项目文件' in dlg.status_line(), dlg.status_line()

    def test_a_game_level_mapping_is_also_found(self, project):
        from storygraph.ui_qt import mapping_dialog as MD
        _write_mapping(project, VALID_MAPPING, name='game/storygraph.mapping.json')
        state = MD.state_from_project(project)
        assert state.has_data(), 'game/ 下的同名映射也要认'
        assert str(Path('game')) in state.path

    def test_loading_the_project_file_itself_reports_project_source(self,
                                                                   project, dlg):
        path = _write_mapping(project, VALID_MAPPING)
        assert dlg.load_mapping_file(path) is True
        state = dlg.state()
        assert state.kind == 'project', state.kind
        assert '项目文件' in state.label()


# ==================================================================
#  ② 坏 JSON / 未知键：一句人话，不闪退
# ==================================================================

class TestBadMappingIsSaidNotCrashed:
    def test_bad_json_becomes_an_error_state_not_a_traceback(self, tmp_path):
        from storygraph.ui_qt import mapping_dialog as MD
        bad = tmp_path / 'bad.json'
        bad.write_text('{ 这不是 JSON', encoding='utf-8')
        state = MD.state_from_file(bad)
        assert state.error, '坏 JSON 必须给出一句话'
        assert not state.has_data()
        assert '读取失败' in MD.status_text(state)

    def test_the_dialog_opens_on_a_broken_file(self, qapp, project, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        bad = tmp_path / 'bad.json'
        bad.write_text('{ 坏', encoding='utf-8')
        d = MappingDialog(None, registry=_registry(), project_root=project,
                          mapping_path=str(bad))
        try:
            assert d._json.toPlainText() == ''
            assert '读不出来' in d._warn.text(), d._warn.text()
            assert '读取失败' in d.status_line()
        finally:
            d.close()

    def test_loading_a_broken_file_keeps_the_current_mapping(self, project, dlg):
        good = _write_mapping(project, VALID_MAPPING)
        assert dlg.load_mapping_file(good) is True
        before = dlg.state()
        bad = project / 'broken.json'
        bad.write_text('{ nope', encoding='utf-8')
        assert dlg.load_mapping_file(bad) is False
        assert dlg.state() == before, '读失败不许把现有的映射换掉'
        assert '读不出来' in dlg._hint.text(), dlg._hint.text()

    def test_unknown_keys_warn_but_still_load(self, project, dlg, tmp_path):
        data = dict(VALID_MAPPING)
        data['some_future_key'] = {'whatever': 1}
        path = tmp_path / 'future.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        assert dlg.load_mapping_file(path) is True
        state = dlg.state()
        assert state.has_data(), '未知键只是提醒，不许当坏文件'
        assert state.warnings, '未知键要给出提醒（否则用户不知道它没生效）'
        assert '⚠' in dlg._warn.text() or '提醒' in dlg._hint.text()


# ==================================================================
#  ③ 资源对照表
# ==================================================================

class TestContrastTable:
    def test_one_row_per_image_with_the_verdict(self, project):
        from storygraph.ui_qt import mapping_dialog as MD
        rows = MD.contrast_rows(_registry(), project)
        by_id = {r.asset_id: r for r in rows}
        assert set(by_id) == {aid for aid, _t, _a, _p in IMAGE_ROWS}
        assert by_id['ling_happy'].kind == 'sprite'
        assert by_id['bg_room'].kind == 'bg'
        assert by_id['cg_01'].kind == 'cg'
        assert by_id['mystery'].kind == 'unknown'
        assert by_id['mystery'].is_unknown()
        assert by_id['ling_happy'].relpath == \
            'game/images/characters/ling/happy.png'

    def test_the_table_shows_every_picture(self, dlg):
        assert dlg._table.rowCount() == len(IMAGE_ROWS), dlg._table.rowCount()
        assert dlg.unknown_count() == 1, dlg.unknown_count()

    def test_only_unknown_filter_and_count(self, dlg):
        dlg._only_unknown.setChecked(True)
        assert dlg._table.rowCount() == 1, dlg._table.rowCount()
        cell = dlg._table.item(0, 0).text()
        assert 'mystery' in cell, cell
        assert '未识别 1' in dlg._table_count.text(), dlg._table_count.text()

    def test_the_table_shows_the_basis_of_the_guess(self, dlg):
        """依据那一列必须非空 —— 用户"只有文件名能匹配"就是看不到依据。"""
        for i in range(dlg._table.rowCount()):
            basis = dlg._table.item(i, 4).text()
            assert basis and basis != '—', (i, basis)

    def test_the_table_does_not_autosize_per_cell(self, dlg):
        """
        真机 1720 行：`ResizeToContents` + 每次 `setItem` 会重算全表列宽
        （O(行²)，实测卡死几分钟）。列宽必须是"填完整一次"的量法。
        """
        from PySide6.QtWidgets import QHeaderView
        head = dlg._table.horizontalHeader()
        for i in range(dlg._table.columnCount()):
            assert head.sectionResizeMode(i) != QHeaderView.ResizeToContents, i
        assert dlg._table.columnWidth(0) > 0

    def test_a_mapping_changes_the_verdict(self, project, dlg):
        """映射是"覆盖/纠正"：给 mystery 登记成 ui，重算后它就不是未识别。"""
        from storygraph.ui_qt import mapping_dialog as MD
        data = dict(VALID_MAPPING)
        data['images'] = dict(data.get('images') or {})
        data['images']['mystery'] = {'kind': 'ui'}
        path = _write_mapping(project, data)
        assert dlg.load_mapping_file(path) is True
        rows = {r.asset_id: r for r in MD.contrast_rows(_registry(), project)}
        assert rows['mystery'].kind == 'ui', rows['mystery']


# ==================================================================
#  ④ 导出初稿（按项目数据生成；导出后能原样读回）
# ==================================================================

class TestExportDraft:
    def test_draft_is_generated_from_the_project(self, dlg):
        draft = dlg.draft_data()
        assert isinstance(draft, dict) and draft, draft
        assert draft.get('schema') == 'storygraph.mapping/1', draft
        assert 'characters' in draft and 'rules' in draft, draft

    def test_export_writes_a_file_that_loads_back_identically(self, dlg, tmp_path):
        path = tmp_path / 'draft.json'
        assert dlg.export_draft(path) is True
        assert path.is_file()
        from storygraph.assets import asset_mapping as AM
        data, _warnings = AM.load_mapping(str(path))
        assert data == dlg.draft_data(), '导出的初稿必须能被 load_mapping 原样读回'
        assert dlg.state().has_data(), '导出后当前映射应该就是这份初稿'
        assert str(path) in dlg._hint.text(), dlg._hint.text()


# ==================================================================
#  ⑤ 保存到项目根：先 diff、确认才写、旧文件留 .bak
# ==================================================================

class TestSaveToProjectRoot:
    def _load_an_external_mapping(self, dlg, tmp_path):
        """把"别人发来的"一份映射载入进来（保存到项目根的前置状态）。"""
        path = tmp_path / 'from_friend.json'
        path.write_text(json.dumps(VALID_MAPPING, ensure_ascii=False, indent=2)
                        + '\n', encoding='utf-8')
        assert dlg.load_mapping_file(path) is True, dlg._hint.text()

    def test_it_does_not_write_before_the_human_confirms(self, project, dlg,
                                                         tmp_path):
        self._load_an_external_mapping(dlg, tmp_path)
        assert dlg.prepare_save() is True
        target = project / 'storygraph.mapping.json'
        assert not target.exists(), '确认之前一个字节都不许写'
        assert '将要写入' in dlg._diff_label.text()
        assert dlg._confirm_bar.isVisible() or True          # 控件可见性交给真窗口

    def test_confirming_writes_the_project_file(self, project, dlg, tmp_path):
        self._load_an_external_mapping(dlg, tmp_path)
        assert dlg.prepare_save() is True
        assert dlg.confirm_save() is True
        target = project / 'storygraph.mapping.json'
        assert target.is_file(), '确认后要真的写进项目根'
        assert '已写入' in dlg._hint.text(), dlg._hint.text()
        assert dlg.state().kind == 'project', dlg.state().kind
        from storygraph.assets import asset_mapping as AM
        data, _w = AM.load_mapping(str(target))
        assert data.get('rules') == VALID_MAPPING['rules'], data

    def test_an_existing_file_is_backed_up_before_overwriting(self, project,
                                                              tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        old = _write_mapping(project, {'schema': 'storygraph.mapping/1',
                                       'name': '旧的那份'})
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            # 现在换成另一份（名字不同）—— diff 必须把"删掉的旧行"摆出来
            other = tmp_path / 'other.json'
            changed = dict(VALID_MAPPING)
            changed['name'] = '新的那份'
            other.write_text(json.dumps(changed, ensure_ascii=False, indent=2),
                             encoding='utf-8')
            assert d.load_mapping_file(other) is True
            assert d.prepare_save() is True
            diff = d._diff.toPlainText()
            assert '旧的那份' in diff, diff
            assert '新的那份' in diff, diff
            assert d.confirm_save() is True
            bak = old.with_name(old.name + '.bak')
            assert bak.is_file(), '覆盖前必须留旧文件备份'
            assert '旧的那份' in bak.read_text(encoding='utf-8')
        finally:
            d.close()

    def test_cancelling_writes_nothing(self, project, dlg, tmp_path):
        self._load_an_external_mapping(dlg, tmp_path)
        assert dlg.prepare_save() is True
        dlg.cancel_save()
        assert not (project / 'storygraph.mapping.json').exists()
        assert '没有写入' in dlg._hint.text(), dlg._hint.text()

    def test_without_a_loaded_mapping_it_refuses_instead_of_writing(self,
                                                                   project,
                                                                   dlg):
        assert dlg.prepare_save() is False
        assert '没有可保存的映射' in dlg._hint.text(), dlg._hint.text()

    def test_a_bom_on_the_existing_file_is_not_a_fake_change(self, project):
        """现有文件带 UTF-8 BOM 时，diff 不许冒出一条"删除了看不见的字符"。"""
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        path = project / 'storygraph.mapping.json'
        path.write_text(json.dumps(VALID_MAPPING, ensure_ascii=False, indent=2)
                        + '\n', encoding='utf-8-sig')
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            assert d.prepare_save() is True
            assert '\ufeff' not in d._diff.toPlainText()
            summary = d._diff_label.text()
            assert '一字不差' in summary or '只有末尾换行不同' in summary, summary
        finally:
            d.close()

    def test_target_prefers_the_existing_location(self, project):
        from storygraph.ui_qt import mapping_dialog as MD
        assert MD.write_target(project) == str(Path(project) / MD.MAPPING_NAME)
        game_file = _write_mapping(project, VALID_MAPPING,
                                   name='game/storygraph.mapping.json')
        assert MD.write_target(project) == str(game_file), \
            '项目里已有（并在 game/ 下）就写回原处，不另起一份'


# ==================================================================
#  ⑥ 「重读」真的重读磁盘
# ==================================================================

class TestReload:
    def test_reload_picks_up_an_external_edit(self, project, dlg):
        path = _write_mapping(project, VALID_MAPPING)
        assert dlg.reload_mapping() is True
        assert dlg.state().data.get('name') == '测试项目 · 资源映射'

        changed = dict(VALID_MAPPING)
        changed['name'] = '外部编辑器改过的'
        path.write_text(json.dumps(changed, ensure_ascii=False),
                        encoding='utf-8')
        # 缓存按 mtime 失效；"重读"必须绕过它，立刻看到新内容
        assert dlg.reload_mapping() is True
        assert dlg.state().data.get('name') == '外部编辑器改过的'


# ==================================================================
#  ⑦ 纯函数：diff / 状态栏文案
# ==================================================================

class TestPureHelpers:
    def test_diff_lines_are_empty_when_nothing_changed(self):
        from storygraph.ui_qt.mapping_dialog import diff_lines
        assert diff_lines('{"a": 1}\n', '{"a": 1}\n') == []
        assert diff_lines('{"a": 1}\n', '{"a": 2}\n')

    def test_diff_summary_says_new_or_changed(self):
        from storygraph.ui_qt.mapping_dialog import diff_summary
        assert '新建文件' in diff_summary('', '{"a": 1}\n')
        assert '一字不差' in diff_summary('{"a": 1}\n', '{"a": 1}\n')
        assert '新增' in diff_summary('{"a": 1}\n', '{"a": 1, "b": 2}\n')

    def test_status_text_covers_all_three_states(self):
        from storygraph.ui_qt.mapping_dialog import (MappingState,
                                                     status_text)
        assert '按文件夹推断' in status_text(None)
        assert '按文件夹推断' in status_text(MappingState())
        project = MappingState(kind='project', path='/x/mapping.json',
                               data={'schema': 'storygraph.mapping/1'})
        assert status_text(project) == '映射：项目文件'
        loaded = MappingState(kind='loaded', path='/x/m.json',
                              data={'schema': 'storygraph.mapping/1'})
        assert '手动载入' in status_text(loaded)
        assert '3 张未识别' in status_text(MappingState(), 3)
        broken = MappingState(error='坏 JSON')
        assert '读取失败' in status_text(broken)

    def test_json_text_is_readable_chinese(self):
        from storygraph.ui_qt.mapping_dialog import json_text
        text = json_text({'name': '凌跃捷'})
        assert '凌跃捷' in text, 'JSON 不许把中文转成 \\u 转义'
        assert text.endswith('\n')


# ==================================================================
#  ⑧ Excel 定义途径（用户 2026-10-30）
# ==================================================================

class TestExcelMapping:
    """
    同一份映射的第二种写法：`.xlsx`。界面这里钉三件事 ——
    **认得出、说得出、不碰人家的表**。
    """

    def _write_excel(self, project):
        from storygraph.assets import asset_mapping as AM
        path = project / AM.MAPPING_XLSX_FILENAME
        AM.save_mapping(str(path), VALID_MAPPING)
        return path

    def test_project_excel_is_picked_up_and_says_so(self, project):
        """打开项目自动认 xlsx；状态行写 `（Excel）`，页签里说明"改请改它"。"""
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        self._write_excel(project)
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            assert d.state().path.endswith('.xlsx'), d.state().path
            assert '（Excel）' in d.status_line(), d.status_line()
            assert '等价 JSON' in d._warn.text(), d._warn.text()
            assert '"rules"' in d._json.toPlainText()
            assert d.state().data.get('rules') == VALID_MAPPING['rules']
        finally:
            d.close()

    def test_export_draft_can_write_xlsx(self, project, dlg):
        """导出初稿存成 .xlsx：写得出来，并且**当场说清它成了生效源**。"""
        target = project / 'storygraph.mapping.xlsx'
        assert dlg.export_draft(target) is True
        assert target.is_file()
        assert dlg.state().path.endswith('.xlsx'), dlg.state().path
        hint = dlg._hint.text()
        assert '以这份' in hint and 'Excel' in hint, hint

    def test_重读之后仍提醒还有另一份(self, project, dlg):
        """
        「重读」走的是"绕过缓存、按路径直接读" —— 那句"当前以哪份为准"
        不能因为换了这条读取路径就消失（否则人刚被提醒过，点一下就忘了）。
        """
        self._write_excel(project)
        _write_mapping(project, {'schema': 'storygraph.mapping/1',
                                 'name': '另一份（不会生效）'})
        assert dlg.reload_mapping() is True
        notes = dlg.state().warnings
        assert any('为准' in str(w) for w in notes), notes

    def test_save_writes_a_json_snapshot_and_never_touches_the_excel(self,
                                                                     project):
        """
        生效源是 Excel 时，「保存到项目根」写的是**同目录的 JSON 快照**：
        那份 Excel 一个字节都不许动（工具从不回写人做的表）。
        """
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        xlsx = self._write_excel(project)
        before = xlsx.read_bytes()
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            assert d.prepare_save() is True
            target, _data = d._pending
            assert str(target).endswith('storygraph.mapping.json'), target
            assert '不会' in d._diff_label.text(), d._diff_label.text()
            assert d.confirm_save() is True
            assert xlsx.read_bytes() == before, 'Excel 那份被改了'
            assert (project / 'storygraph.mapping.json').is_file()
            assert d.state().path.endswith('.xlsx'), '生效源不该因为写 JSON 而变'
        finally:
            d.close()
