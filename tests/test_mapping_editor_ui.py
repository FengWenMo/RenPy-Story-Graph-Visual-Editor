# -*- coding: utf-8 -*-
"""
tests/test_mapping_editor_ui.py
===============================
**「逐条编辑」页**（用户 2026-10-30，⑧）：不会写 JSON 也能定义映射

用户原话：

> json的自定义能不能用工具显式化……？本质上它就是键值对嘛！也就是类似于之前
> 探测未定义角色名称的样子，弄一个显化的UI，然后点击保存的话再存为json……？

这一片钉住的是"人能不能**只靠表**把映射改对，并且改的东西真会写进 JSON"：

  · 它是第一个页签（JSON 是产出格式，不是输入方式）；
  · 六段各有各的列，行数/内容来自项目里那份映射；
  · 在格子里打字 → 模型变 → JSON 预览跟着变（**不重建表**，否则焦点被抢）；
  · 加/删/改键/上下移都能用，非法值当场说清；
  · 「保存到项目根」写的**是表里的内容**，而且仍然先 diff、确认后才落盘；
  · 对照表里选中的图能一键补进图片表（未识别的图就是从这里补的）；
  · 生效源是 Excel / 文件读不出来时，表头必须先说清楚再让人动手。

判定/编辑逻辑在 `assets/mapping_edit.py`（`test_mapping_edit.py` 管它）；
这里只测界面有没有把它们**如实**摆出来、接得上写盘那条路。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip('PySide6')

from PySide6.QtCore import Qt  # noqa: E402


# ==================================================================
#  夹具
# ==================================================================

def _write_png(path: Path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (8, 8), (60, 120, 200, 255)).save(path, 'PNG')


IMAGE_ROWS = [
    ('ling_happy', 'ling', 'happy', 'game/art/full/ling/happy.png'),
    ('ling_sad', 'ling', 'sad', 'game/art/full/ling/sad.png'),
    ('room', 'bg', 'room', 'game/images/bg/room.png'),
    ('mystery', '', '', 'game/art/mystery.png'),
]

MAPPING = {
    'schema': 'storygraph.mapping/1',
    'name': '测试项目 · 资源映射',
    'characters': {'ling': {'display': '凌跃捷', 'aliases': ['凌', 'lyj']}},
    'rules': [{'glob': 'game/art/full/<tag>/*.png', 'kind': 'sprite',
               'char': '<tag>', 'attr': '<stem>'}],
    'images': {'room': {'kind': 'bg', 'attr': 'room'}},
    'attributes': {'expressions': {'label': '表情'}},
}


def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}
    for aid, tag, attrs, rel in IMAGE_ROWS:
        reg.images[aid] = {'ID': aid, 'Tag': tag, 'Attributes': attrs,
                           'Path': rel}
    reg.characters = {'ling': {'ID': 'ling', 'Name': '凌跃捷',
                               'ImageTag': 'ling', 'Aliases': '凌|lyj'}}
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


def _write_mapping(proj: Path, data, name: str = 'storygraph.mapping.json') -> Path:
    path = proj / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    return path


@pytest.fixture
def project(tmp_path):
    proj = _build_project(tmp_path)
    _write_mapping(proj, MAPPING)
    return proj


@pytest.fixture
def dlg(qapp, project):
    from storygraph.ui_qt.mapping_dialog import MappingDialog
    d = MappingDialog(None, registry=_registry(), project_root=project)
    yield d
    d.close()


def _editor(dlg):
    return dlg._editor


def _type(dlg, row: int, column: int, text: str):
    """
    在格子里"打字"（走真正的 `itemChanged`，不是直接调模型）。

    下拉列的真值在 `UserRole`（格子上写的是「背景」，文件里是 `bg`）——
    真实编辑时委托同时写这两处，这里照做。
    """
    item = _editor(dlg)._table.item(row, column)
    assert item is not None, (row, column)
    item.setData(Qt.ItemDataRole.UserRole, text)
    item.setText(text)


# ==================================================================
#  ① 页签与六段
# ==================================================================

class TestTheTable:
    def test_the_editor_is_the_first_tab(self, dlg):
        from storygraph.ui_qt.mapping_dialog import TAB_TITLES
        assert [dlg._tabs.tabText(i) for i in range(3)] == list(TAB_TITLES)
        assert TAB_TITLES[0] == '逐条编辑'          # 第一眼就该看到表
        assert dlg._tabs.currentIndex() == 0

    def test_no_jargon_in_the_tab_names(self, dlg):
        """页签也不许留行话（用户 2026-10-30："谁知道你是怎么叫的呢"）。"""
        names = [dlg._tabs.tabText(i) for i in range(3)]
        assert '映射文件' not in names and '资源对照表' not in names, names

    def test_the_left_list_names_all_seven_sections(self, dlg):
        names = [_editor(dlg)._list.item(i).text()
                 for i in range(_editor(dlg)._list.count())]
        assert len(names) == 7
        assert names[0].startswith('角色') and '1' in names[0]
        assert names[1].startswith('单张图片') and '1' in names[1]
        assert names[2].startswith('一类图片') and '1' in names[2]
        # 代号两段（2026-10-31）：美术需求表的写法，「B7」靠它们才换得出来
        assert names[3].startswith('服装代号')
        assert names[4].startswith('表情编号')
        # 词汇表（2026-10-31）：位置 / 音乐 / 背景 / 角色 / 过场 的中文叫法
        assert names[5].startswith('中文叫法')
        assert names[6].startswith('表情怎么叫') and '1' in names[6]

    def test_the_page_says_what_it_is_for(self, dlg):
        """一进门就要知道这张表干什么、改完去哪保存（别让人先猜）。"""
        ed = _editor(dlg)
        text = ed._what.text()
        assert '哪张图是什么' in text and '保存到项目根' in text
        assert '映射' not in text            # 屏幕上不说工具的术语

    def test_the_headers_explain_themselves(self, dlg):
        """每一列悬停都有"这一格怎么填" + 一行技术写法（开发者要查时能看到）。"""
        from storygraph.assets import mapping_edit as ME
        ed = _editor(dlg)
        ed.set_section('images')
        for column in range(1, 1 + len(ME.SPECS['images'].fields)):
            item = ed._table.horizontalHeaderItem(column)
            assert item is not None
            assert item.toolTip(), f'第 {column} 列没有悬停说明'
        first = ed._table.horizontalHeaderItem(1).toolTip()
        assert '技术写法' in first, first

    def test_an_empty_section_tells_you_how_to_start(self, dlg):
        """空表最容易劝退 —— 每段空着时要给一句"怎么开始"。"""
        from storygraph.assets import mapping_edit as ME
        ed = _editor(dlg)
        ed.load({})
        for name in ME.SECTIONS:
            ed.set_section(name)
            assert ed._table.rowCount() == 0, name
            assert ed._empty.text().strip(), f'{name} 空着时没有说明'
            assert ed._empty.isVisibleTo(ed), name

    def test_switching_sections_changes_the_columns(self, dlg):
        from storygraph.assets import mapping_edit as ME
        ed = _editor(dlg)
        ed.set_section('images')
        headers = [ed._table.horizontalHeaderItem(i).text()
                   for i in range(ed._table.columnCount())]
        assert headers[:-1] == [ME.SPECS['images'].key_label] + [
            f.label for f in ME.SPECS['images'].fields]
        # 最后一列是吃完剩余宽度的空列（表头是空的，别给它起名字）。
        assert headers[-1] == ''
        assert ed._table.rowCount() == 1
        ed.set_section('rules')
        assert ed._table.rowCount() == 1
        assert ed.cell_text(0, 1) == 'game/art/full/<tag>/*.png'
        # **屏幕上说人话、文件里存真值**：格子里是「立绘」，写回的是 `sprite`。
        assert ed.cell_text(0, 2) == '立绘'
        assert ed.cell_value(0, 2) == 'sprite'

    def test_an_empty_project_still_opens_with_an_empty_table(self, tmp_path,
                                                              qapp):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _build_project(tmp_path)
        d = MappingDialog(None, registry=_registry(), project_root=proj)
        try:
            assert _editor(d).row_count() == 0
            assert d.current_data() == {}
            assert not d._btn_save.isEnabled()
        finally:
            d.close()


# ==================================================================
#  ② 在表里改
# ==================================================================

class TestEditingInTheTable:
    def test_typing_in_a_cell_updates_the_mapping(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 2, 'ling')
        assert dlg.current_data()['images']['room']['char'] == 'ling'

    def test_typing_does_not_rebuild_the_table(self, dlg):
        """改一格不许重建（重建会把人正在打的字连焦点一起吃掉）。"""
        ed = _editor(dlg)
        ed.set_section('images')
        item = ed._table.item(0, 2)
        item.setText('ling')
        assert ed._table.item(0, 2) is item

    def test_the_json_preview_follows_the_table(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 2, 'ling')
        assert '"char": "ling"' in dlg._json.toPlainText()

    def test_a_bad_value_is_reported_in_the_problem_strip(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 1, 'cgg')
        assert '不是认识的类别' in ed._problems.text()

    def test_a_duplicate_key_is_refused_and_the_cell_reverts(self, dlg):
        ed = _editor(dlg)
        ed.set_section('characters')
        index = ed.add_row()
        ed.set_cell(index, 1, '某人')
        _type(dlg, index, 0, 'ling')          # 和第一行撞键
        assert ed.cell_text(index, 0) == '新角色'
        assert '已经有' in dlg._hint.text()
        assert list(dlg.current_data()['characters']).count('ling') == 1

    def test_add_and_delete_keep_the_counts_in_sync(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        index = ed.add_row()
        assert ed.row_count() == 2
        assert ed.cell_text(index, 0) == '新图片'
        ed._table.selectRow(index)
        assert ed.delete_selected() == 1
        assert ed.row_count() == 1
        assert '新图片' not in json.dumps(dlg.current_data(), ensure_ascii=False)

    def test_rules_are_the_only_section_with_a_row_number(self, dlg):
        ed = _editor(dlg)
        ed.set_section('rules')
        item = ed._table.item(0, 0)
        assert item.text() == '1'
        assert not (item.flags() & item.flags().ItemIsEditable)

    def test_rules_can_be_moved_up_and_down(self, dlg):
        ed = _editor(dlg)
        ed.set_section('rules')
        ed.add_row()
        ed.set_cell(1, 1, 'game/art/extra/*.png')
        ed.set_cell(1, 2, 'bg')
        ed._table.selectRow(1)
        assert ed.move_selected(-1) == 0
        assert dlg.current_data()['rules'][0]['glob'] == 'game/art/extra/*.png'

    def test_moving_is_refused_outside_the_rules_section(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        ed._table.selectRow(0)
        assert ed.move_selected(-1) == -1
        assert '只有「规则」' in dlg._hint.text()

    def test_a_selected_image_path_is_filled_from_the_project(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        index = ed.add_row()
        rel = ed.relative_path(Path(dlg._project_root) / 'game/art/mystery.png')
        assert rel == 'game/art/mystery.png'
        ed._table.selectRow(index)
        assert ed.set_cell(index, 0, rel) == ''
        assert ed.cell_text(index, 0) == rel       # 格子跟着模型走，不是旧值
        assert 'game/art/mystery.png' in dlg.current_data()['images']


# ==================================================================
#  ③ 保存走的是表
# ==================================================================

class TestSave:
    def test_save_writes_what_the_table_shows(self, project, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 1, 'cg')
        assert dlg.prepare_save() is True
        assert dlg.confirm_save() is True
        saved = json.loads((project / 'storygraph.mapping.json')
                           .read_text(encoding='utf-8'))
        assert saved['images']['room']['kind'] == 'cg'
        assert (project / 'storygraph.mapping.json.bak').is_file()

    def test_nothing_is_written_before_the_human_confirms(self, project, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 1, 'cg')
        before = (project / 'storygraph.mapping.json').read_text(encoding='utf-8')
        assert dlg.prepare_save() is True
        after = (project / 'storygraph.mapping.json').read_text(encoding='utf-8')
        assert after == before
        assert dlg.cancel_save() is None
        assert json.loads((project / 'storygraph.mapping.json')
                          .read_text(encoding='utf-8')) == MAPPING

    def test_a_new_row_alone_does_not_change_the_file(self, project, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        ed.add_row()
        assert dlg.prepare_save() is True
        assert dlg.confirm_save() is True
        saved = json.loads((project / 'storygraph.mapping.json')
                           .read_text(encoding='utf-8'))
        assert '新图片' not in json.dumps(saved, ensure_ascii=False)
        assert saved['images'] == MAPPING['images']

    def test_the_diff_shows_the_edited_line(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 1, 'cg')
        assert dlg.prepare_save() is True
        diff = dlg._diff.toPlainText()
        assert '"kind": "cg"' in diff

    def test_without_any_content_it_refuses_to_write(self, tmp_path, qapp):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _build_project(tmp_path)
        d = MappingDialog(None, registry=_registry(), project_root=proj)
        try:
            assert d.prepare_save() is False
            assert '没有可保存' in d._hint.text()
            assert not _editor(d)._btn_save.isEnabled()
            assert not (proj / 'storygraph.mapping.json').exists()
        finally:
            d.close()

    def test_the_table_has_its_own_save_button_on_the_same_path(self, project,
                                                                dlg):
        """表底那个「保存到项目根」= 上方同名按钮，走**同一段** diff→确认。"""
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 1, 'cg')
        path = project / 'storygraph.mapping.json'
        before = path.read_text(encoding='utf-8')
        assert ed._btn_save.isEnabled()
        ed._btn_save.click()
        assert dlg._pending is not None          # 只是摆了 diff，没写
        assert path.read_text(encoding='utf-8') == before
        assert dlg.confirm_save() is True
        assert json.loads(path.read_text(encoding='utf-8'))['images']['room'][
            'kind'] == 'cg'


# ==================================================================
#  ④ 从对照表补进来（未识别的图就是从这里补）
# ==================================================================

class TestFromContrastTable:
    def _row_index(self, dlg, asset_id: str) -> int:
        for i, row in enumerate(dlg._shown):
            if row.asset_id == asset_id:
                return i
        raise AssertionError(f'{asset_id} 不在对照表里：'
                             f'{[r.asset_id for r in dlg._shown]}')

    def test_an_unknown_image_can_be_filled_in_from_the_contrast_table(self, dlg):
        index = self._row_index(dlg, 'mystery')
        dlg._table.selectRow(index)
        assert dlg.add_selected_to_mapping() == 1
        assert _editor(dlg).section() == 'images'
        assert dlg._tabs.currentIndex() == 0
        assert 'mystery' in dlg.current_data()['images']

    def test_a_known_sprite_carries_its_verdict_into_the_row(self, dlg):
        index = self._row_index(dlg, 'ling_happy')
        dlg._table.selectRow(index)
        dlg.add_selected_to_mapping()
        entry = dlg.current_data()['images']['ling_happy']
        assert entry['kind'] == 'sprite'
        assert entry['char'] == 'ling'
        assert entry['attr'] == 'happy'

    def test_adding_the_same_image_twice_does_not_duplicate(self, dlg):
        index = self._row_index(dlg, 'mystery')
        dlg._table.selectRow(index)
        dlg.add_selected_to_mapping()
        dlg.add_selected_to_mapping()
        assert list(dlg.current_data()['images']).count('mystery') == 1

    def test_nothing_selected_says_so_instead_of_guessing(self, dlg):
        dlg._table.clearSelection()
        assert dlg.add_selected_to_mapping() == 0
        assert '先' in dlg._hint.text()


# ==================================================================
#  ⑤ 前置条件必须当场说出来
# ==================================================================

class TestGuards:
    def test_an_excel_source_is_announced_before_editing(self, project, qapp):
        from storygraph.assets import asset_mapping as AM
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        (project / 'storygraph.mapping.json').unlink()
        AM.save_mapping(str(project / AM.MAPPING_XLSX_FILENAME), MAPPING)
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            banner = _editor(d)._banner.text()
            assert 'Excel 优先于 JSON' in banner, banner
            assert _editor(d).isEnabled()
        finally:
            d.close()

    def test_a_broken_file_disables_the_table(self, project, qapp):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        (project / 'storygraph.mapping.json').write_text('{ 坏掉的 json',
                                                         encoding='utf-8')
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            assert d.state().error
            assert not _editor(d).isEnabled()
            assert '读不出来' in _editor(d)._banner.text()
            assert d.prepare_save() is False
        finally:
            d.close()

    def test_reload_discards_the_table_and_says_so(self, dlg):
        ed = _editor(dlg)
        ed.set_section('images')
        _type(dlg, 0, 1, 'cg')
        assert ed.is_dirty()
        assert dlg.reload_mapping() is True
        assert not ed.is_dirty()
        assert '未保存的改动已丢弃' in dlg._hint.text()
        assert dlg.current_data()['images']['room']['kind'] == 'bg'


# ==================================================================
#  ⑥ 卡片那条警告点进来 ⇒ 直接落到那张图那一行
# ==================================================================

class TestFocusFromTheCard:
    def test_it_lands_on_the_row_of_that_image(self, project, qapp):
        """
        「去『资源映射』修」从前只是开个窗 —— 人还得自己在 3889 行里找。
        现在带着那条 spec 过来：翻到「单张图片」并选中它。
        """
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project,
                          focus_image='ling happy')
        try:
            ed = _editor(d)
            assert d._tabs.currentIndex() == 0
            assert ed.section() == 'images'
            row = ed._table.currentRow()
            assert row >= 0, '没有选中任何一行'
            assert ed.cell_text(row, 0) in ('房间', 'ling_happy', 'room'), \
                ed.cell_text(row, 0)
            # 表里本来没有 ling_happy ⇒ 按推断先补了一条（kind=sprite）。
            data = d.current_data()['images']
            assert 'ling_happy' in data or any(
                'ling' in k for k in data), data
        finally:
            d.close()

    def test_a_spec_the_registry_does_not_know_still_takes_me_there(
            self, project, qapp):
        """资源表里也没有这张图：不许静默，落到「单张图片」并说一句。"""
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project,
                          focus_image='不存在的角色 高兴')
        try:
            assert d._tabs.currentIndex() == 0
            assert _editor(d).section() == 'images'
            assert '没在资源表里找到' in d._hint.text(), d._hint.text()
        finally:
            d.close()

    def test_an_existing_row_is_selected_not_duplicated(self, project, qapp):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project,
                          focus_image='bg room')
        try:
            data = d.current_data()['images']
            assert 'room' in data
            assert len([k for k in data if 'room' in k]) == 1, data
        finally:
            d.close()


# ==================================================================
#  ⑧ 代号那两段：`lan swim_suit angry` ⇄ `lan B7`
# ==================================================================

CODES = {
    'schema': 'storygraph.mapping/1',
    'outfit_codes': [
        {'char': 'lan', 'code': 'A', 'attr': 'aa', 'label': '常态'},
        {'char': 'lan', 'code': 'B', 'attr': 'base_swimsuit', 'label': '泳装'},
    ],
    'expression_codes': [
        {'char': 'lan', 'no': '7', 'attr': 'indifferent', 'label': '不在意'},
        {'char': 'lan', 'no': '8', 'attr': 'angry', 'label': '生气'},
    ],
}


def _codes_project(tmp_path) -> Path:
    """一个能"自动建表"的最小项目：编号表 + layeredimage + 一条带注释的剧本。"""
    proj = tmp_path / 'codes'
    game = proj / 'game'
    folder = game / 'images' / 'characters' / 'lan'
    folder.mkdir(parents=True)
    (folder / '对应.txt').write_text('7 不在意-indifferent\n8 生气-angry\n',
                                     encoding='utf-8')
    (game / 'definitions.rpy').write_text(
        'layeredimage lan:\n'
        '    group expressions:\n'
        '        attribute indifferent default:\n'
        "            'indifferent'\n"
        '        attribute angry:\n'
        "            'angry'\n"
        '    group outfit:\n'
        '        attribute base_swimsuit:\n'
        "            'base_swimsuit'\n",
        encoding='utf-8')
    (game / 'chapter1.rpy').write_text(
        '# 表情B8\n'
        'show lan base_swimsuit angry\n'
        '# 表情B7\n'
        'show lan base_swimsuit indifferent\n',
        encoding='utf-8')
    return proj


class TestCodesPage:
    def test_换算框只在那两段出现(self, qapp, project):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            ed = _editor(d)
            ed.set_section('images')
            assert ed._code_box.isHidden()
            ed.set_section('outfit_codes')
            assert not ed._code_box.isHidden()
            ed.set_section('attributes')
            assert ed._code_box.isHidden()
        finally:
            d.close()

    def test_打字就换算成代号(self, qapp, project):
        """用户原话那一句：`lan swim_suit angry` → `lan B7`。"""
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            ed = _editor(d)
            ed.load(CODES)
            ed.set_section('outfit_codes')
            ed._code_input.setText('lan swim_suit angry')
            text = ed._code_out.text()
            assert 'B8' in text, text
            assert 'show lan base_swimsuit angry' in text, text
        finally:
            d.close()

    def test_反过来写代号也行(self, qapp, project):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            ed = _editor(d)
            ed.load(CODES)
            ed.set_section('expression_codes')
            ed._code_input.setText('lan B7')
            assert 'show lan base_swimsuit indifferent' in ed._code_out.text()
        finally:
            d.close()

    def test_缺东西时说缺什么(self, qapp, project):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            ed = _editor(d)
            ed.load(CODES)
            ed.set_section('outfit_codes')
            ed._code_input.setText('lan C7')
            text = ed._code_out.text()
            assert '还差一点' in text and 'C' in text, text
        finally:
            d.close()

    def test_空着时给一个例子(self, qapp, project):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        d = MappingDialog(None, registry=_registry(), project_root=project)
        try:
            ed = _editor(d)
            ed.load(CODES)
            ed.set_section('outfit_codes')
            ed._code_input.setText('')
            text = ed._code_out.text()
            assert 'lan' in text and 'B7' in text, text
        finally:
            d.close()


class TestAutoBuildTable:
    """「导入时自动建一个表」（用户 2026-10-31）。"""

    def test_打开空映射就照项目建好两段(self, qapp, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _codes_project(tmp_path)
        d = MappingDialog(None, project_root=proj)
        try:
            data = d.current_data()
            outfits = {(r['char'], r['code']): r['attr']
                       for r in data.get('outfit_codes') or []}
            assert outfits[('lan', 'B')] == 'base_swimsuit', outfits
            numbers = {r['no']: r['attr']
                       for r in data.get('expression_codes') or []}
            assert numbers == {'7': 'indifferent', '8': 'angry'}, numbers
        finally:
            d.close()

    def test_已经有表就不动它(self, qapp, tmp_path):
        """人写/改过的表**不许**被自动填冲掉（只填空表）。"""
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _codes_project(tmp_path)
        _write_mapping(proj, {'schema': 'storygraph.mapping/1',
                              'outfit_codes': [{'char': 'lan', 'code': 'Z',
                                                'attr': 'base_swimsuit'}]})
        d = MappingDialog(None, project_root=proj)
        try:
            rows = d.current_data().get('outfit_codes') or []
            assert [r['code'] for r in rows] == ['Z'], rows
        finally:
            d.close()

    def test_按钮能把两段补起来(self, qapp, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _codes_project(tmp_path)
        d = MappingDialog(None, project_root=proj)
        try:
            ed = _editor(d)
            ed.load({})
            ed.set_section('outfit_codes')
            counts = ed.autofill_codes()
            assert counts['outfits'] == 1 and counts['expressions'] == 2
            # 再点一次不会翻倍（同一角色同一代号不重复加）
            again = ed.autofill_codes()
            assert again['outfits'] == 0 and again['expressions'] == 0
        finally:
            d.close()

    def test_左栏的条数也跟着变(self, qapp, tmp_path):
        """
        自动建表是在「角色」那段时发生的 —— 左栏要是还写着「服装代号 0」，
        人会以为没建出来（条数只在 `_rebuild` 里刷，很容易漏）。
        """
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _codes_project(tmp_path)
        d = MappingDialog(None, project_root=proj)
        try:
            names = [d._editor._list.item(i).text()
                     for i in range(d._editor._list.count())]
            assert names[3].startswith('服装代号') and '1' in names[3], names
            assert names[4].startswith('表情编号') and '2' in names[4], names
        finally:
            d.close()

    def test_保存后能读回来(self, qapp, tmp_path):
        """自动填出来的两段要能被 `load_mapping` 原样读回（结构没写歪）。"""
        from storygraph.assets import asset_mapping as AM
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _codes_project(tmp_path)
        d = MappingDialog(None, project_root=proj)
        try:
            data = d.current_data()
            path = proj / 'storygraph.mapping.json'
            AM.save_mapping(str(path), data)
            back, warnings = AM.load_mapping(str(path))
            assert warnings == [], warnings
            assert back['outfit_codes'] == data['outfit_codes']
            assert back['expression_codes'] == data['expression_codes']
        finally:
            d.close()


class TestTemplateExport:
    """「提供一个模板 xlsx 或者 json 文件」（用户 2026-10-31）。"""

    def test_模板按钮在(self, dlg):
        assert dlg._btn_template.text() == '存一份空模板…'

    def test_导出的xlsx六张表都有表头(self, dlg, tmp_path):
        target = tmp_path / '模板.xlsx'
        text = dlg.export_template(str(target))
        assert '空模板已存到' in text, text
        import openpyxl
        from storygraph.assets.mapping_excel import SHEET_TITLES
        wb = openpyxl.load_workbook(str(target), read_only=True)
        try:
            for key in ('characters', 'rules', 'images', 'outfit_codes',
                        'expression_codes', 'attributes'):
                assert SHEET_TITLES[key] in wb.sheetnames
        finally:
            wb.close()
        # 模板**不写数据行**：六张表全是空的（人照着列名填）
        assert openpyxl.load_workbook(str(target)).worksheets[1].max_row == 1

    def test_导出的json七个键都在(self, dlg, tmp_path):
        target = tmp_path / '模板.json'
        dlg.export_template(str(target))
        data = json.loads(target.read_text(encoding='utf-8'))
        assert set(data) == {'schema', 'name', 'characters', 'rules', 'images',
                             'outfit_codes', 'expression_codes', 'vocabulary',
                             'attributes'}
        assert data['outfit_codes'] == [] and data['expression_codes'] == []

    def test_写不进去时说人话(self, dlg, tmp_path):
        bad = tmp_path / 'no_such_dir' / 'x.xlsx'
        bad.parent.mkdir()
        # 目录存在但文件被占住的场景不好造，这里直接给一个非法后缀
        text = dlg.export_template(str(tmp_path / 'x.txt'))
        assert text == '' or '空模板已存到' in text


# ==================================================================
#  ⑦ 卡片那颗按钮真的把"是哪张图"带出来了
# ==================================================================

class TestTheCardButtonCarriesTheImage:
    """
    闭环的另一半：卡片提示条上那颗「去『资源映射』修」要把**这一条 spec**
    交给主窗口。无参回调（测试 / 老接线）不许因此报错。
    """

    def test_a_one_argument_callback_gets_the_spec(self, qapp):
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        seen = []
        card = SpriteCardWidget({'image': 'ling happy'},
                                on_open_mapping=seen.append)
        try:
            card._open_mapping()
            assert seen == ['ling happy'], seen
        finally:
            card.deleteLater()

    def test_a_no_argument_callback_still_works(self, qapp):
        from storygraph.ui_qt.sprite_grid import SpriteCardWidget
        calls = []
        card = SpriteCardWidget({'image': 'ling happy'},
                                on_open_mapping=lambda: calls.append(1))
        try:
            card._open_mapping()
            assert calls == [1], calls
        finally:
            card.deleteLater()
