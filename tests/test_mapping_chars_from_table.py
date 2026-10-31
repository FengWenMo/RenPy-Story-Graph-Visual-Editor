# -*- coding: utf-8 -*-
"""
tests/test_mapping_chars_from_table.py
=====================================
**「角色 0」到底是什么意思**（用户 2026-11-01 的截图 + 原话）

> 为什么角色是0……？我查看到这个资源映射……除了服装代号和表情编号外
> 什么都没有……？？？它不能自己联动xlsx中的资源管理表格进行标明吗……？
> 我觉得十分奇怪……

诊断结论（`known_characters` 实测）：**名字一直在用**。资源表
（`characters.xlsx`）里 `ling` 的 `ImageTag=ling / Name=「凌跃捷」`，
映射空着时 `identity_of` 照样给出 `display='凌跃捷'`。映射的 `characters`
段是**覆盖层**：空 = 照资源表；写了 = 以映射为准。

所以这一片钉的不是"自动把 45 个角色写进映射"（那会让映射盖住资源表，
以后改 xlsx 名字不生效），而是三件事：

  · 空着时**当场说清楚**：资源表里已经有 N 个角色、空着不影响使用；
  · **想抄有路**：一个「把资源表里的角色写进来」的按钮（只加不改）；
  · 按钮抄的规矩：名字 = 写法的跳过、别名照抄、已有行不动、顺序照表。

判据：抄完 `current_data()` 能原样被 `load_mapping` 读回（结构没写歪）。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip('PySide6')


# ==================================================================
#  夹具：一个带「角色」表的项目（资源表里有 4 个角色 + 1 个旁白）
# ==================================================================

#: 资源表 `characters` 的原始行（`load_asset_registry` 读出来就是这个形状）。
#: 顺序 = 剧本 `define` 的顺序（前几个是主角），**故意不是字母序**。
CHARACTER_ROWS = (
    ('narrator', {'Name': 'narrator', 'ImageTag': None}),
    ('jc', {'Name': '「蒋忱」', 'ImageTag': 'jc'}),
    ('yhj', {'Name': '「叶怀瑾」', 'ImageTag': 'ye'}),
    ('xqlz', {'Name': '「星桥理子」', 'ImageTag': 'xing'}),
    ('l', {'Name': '「蓝」', 'ImageTag': 'lan'}),
    ('lyj', {'Name': '「凌跃捷」', 'ImageTag': 'ling', 'Aliases': 'LTJ|凌'}),
    ('u', {'Name': None, 'ImageTag': 'u'}),          # 表里没名字 → 不抄
)


def _registry():
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = {}
    reg.characters = {cid: dict(row, ID=cid) for cid, row in CHARACTER_ROWS}
    reg.music = {}
    reg.sounds = {}
    reg.voices = {}
    reg.transforms = {}
    return reg


def _project(tmp_path) -> Path:
    proj = tmp_path / 'proj'
    (proj / 'game' / 'images').mkdir(parents=True)
    return proj


@pytest.fixture
def dialog(qapp, tmp_path):
    from storygraph.ui_qt.mapping_dialog import MappingDialog
    d = MappingDialog(None, registry=_registry(), project_root=_project(tmp_path))
    yield d
    d.close()


def _editor(dlg):
    return dlg._editor


# ==================================================================
#  ① 空着的时候，屏幕上要说清楚"为什么是 0 也没关系"
# ==================================================================

class TestTheEmptySectionExplainsItself:

    def test_it_names_the_roles_the_table_already_has(self, dialog):
        """
        用户看到「角色 0」以为工具没读到项目里的角色 —— 那句话必须当场
        说出表里已经有谁（而且按出场顺序挑，别挑出「所有人 / 猫 / 狗」）。
        """
        ed = _editor(dialog)
        ed.set_section('characters')
        text = ed._empty.text()
        assert '7 个角色' in text, text          # 7 = 表里认得出的 tag 数
        assert '蒋忱' in text and '叶怀瑾' in text, text
        assert '所有人' not in text and '猫' not in text, text
        assert '空着就行' in text, text
        # 说清"写了会怎样"：这一段优先。
        assert '以这里为准' in text, text

    def test_without_a_table_it_falls_back_to_the_plain_hint(self, qapp, tmp_path):
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        from storygraph.assets import mapping_edit as ME
        d = MappingDialog(None, project_root=_project(tmp_path))
        try:
            ed = _editor(d)
            ed.set_section('characters')
            assert ed._empty.text() == ME.SPECS['characters'].empty
            assert not ed._btn_import.isVisibleTo(ed)
        finally:
            d.close()

    def test_the_other_sections_explain_why_zero_is_fine(self, dialog):
        """其余四段空着时也得说清"留空也行"，并指到**真实存在**的入口。"""
        from storygraph.assets import mapping_edit as ME
        ed = _editor(dialog)
        images = ME.SPECS['images'].empty
        # 页签名字必须是屏幕上真有的那两个（从前写成「工具认成了什么」，找不到）
        assert '工具怎么认的' in images, images
        assert '加进表里' in images, images
        assert '工具认成了什么' not in images, images
        for name in ('rules', 'vocabulary', 'attributes'):
            assert '留空' in ME.SPECS[name].empty or '空着就行' in ME.SPECS[name].empty, name

    def test_known_chars_are_names_not_stringified_dicts(self, dialog):
        """回归：`_known_chars()` 曾把整个 dict `str()` 成建议文案。"""
        assert dialog._known_chars()['ling'] == '凌跃捷'
        assert dialog._known_chars()['lan'] == '蓝'


# ==================================================================
#  ② 想抄有路：按钮 + 抄的规矩
# ==================================================================

class TestPullingThemFromTheTable:

    def test_the_button_only_shows_on_the_characters_section(self, dialog):
        ed = _editor(dialog)
        ed.set_section('characters')
        assert ed._btn_import.isVisibleTo(ed)
        for name in ('images', 'rules', 'outfit_codes', 'vocabulary'):
            ed.set_section(name)
            assert not ed._btn_import.isVisibleTo(ed), name
        assert '资源表' in ed._btn_import.toolTip()

    def test_copying_fills_names_aliases_and_keeps_the_table_order(self, dialog):
        ed = _editor(dialog)
        ed.set_section('characters')
        counts = ed.import_characters()
        data = dialog.current_data()['characters']
        assert counts['added'] == 5, counts          # jc / ye / xing / lan / ling
        # 顺序照资源表的出场顺序（不是字母序）：前几个就是主角。
        assert list(data) == ['jc', 'ye', 'xing', 'lan', 'ling'], list(data)
        assert data['ling'] == {'display': '凌跃捷', 'aliases': ['LTJ', '凌']}
        assert data['lan'] == {'display': '蓝'}
        assert ed._table.rowCount() == len(data)

    def test_names_equal_to_the_tag_are_not_copied(self, dialog):
        """`narrator → narrator`、表里没名字的 `u`：抄进去只是噪音。"""
        ed = _editor(dialog)
        ed.set_section('characters')
        counts = ed.import_characters()
        data = dialog.current_data()['characters']
        assert 'narrator' not in data
        assert 'u' not in data
        assert counts['blank'] == 2, counts

    def test_existing_rows_are_never_overwritten(self, qapp, tmp_path):
        """人自己写的名字优先：抄一遍不许把人改过的冲掉。"""
        from storygraph.ui_qt.mapping_dialog import MappingDialog
        proj = _project(tmp_path)
        (proj / 'storygraph.mapping.json').write_text(json.dumps(
            {'schema': 'storygraph.mapping/1',
             'characters': {'ling': {'display': '凌哥'}}},
            ensure_ascii=False), encoding='utf-8')
        d = MappingDialog(None, registry=_registry(), project_root=proj)
        try:
            ed = _editor(d)
            ed.set_section('characters')
            counts = ed.import_characters()
            data = d.current_data()['characters']
            assert data['ling'] == {'display': '凌哥'}
            assert counts['kept'] == 1
            assert 'jc' in data                     # 缺的照样补上
        finally:
            d.close()

    def test_the_button_goes_away_once_the_table_has_rows(self, dialog):
        ed = _editor(dialog)
        ed.set_section('characters')
        ed.import_characters()
        assert not ed._btn_import.isVisibleTo(ed)
        assert ed._empty.isVisibleTo(ed) is False

    def test_copying_says_it_is_not_saved_yet_and_who_wins(self, dialog):
        ed = _editor(dialog)
        seen = []
        ed.note.connect(lambda text, warn=False: seen.append((text, warn)))
        ed.set_section('characters')
        ed.import_characters()
        assert seen and seen[-1][1] is False, seen
        assert '还没保存' in seen[-1][0], seen[-1][0]
        assert '不会跟着变' in seen[-1][0], seen[-1][0]
        assert ed.is_dirty()

    def test_the_left_column_count_follows_immediately(self, dialog):
        """抄完左栏还写着「角色 0」= 人会以为没抄进去。"""
        ed = _editor(dialog)
        ed.set_section('characters')
        ed.import_characters()
        names = [ed._list.item(i).text() for i in range(ed._list.count())]
        assert names[0].startswith('角色') and names[0] != '角色  0', names

    def test_what_was_copied_can_be_read_back(self, dialog, tmp_path):
        """抄出来的结构要能被 `load_mapping` 原样读回（别名得是数组）。"""
        from storygraph.assets import asset_mapping as AM
        ed = _editor(dialog)
        ed.set_section('characters')
        ed.import_characters()
        path = tmp_path / 'copy.json'
        AM.save_mapping(str(path), dialog.current_data())
        back, warnings = AM.load_mapping(str(path))
        assert warnings == [], warnings
        assert back['characters']['ling']['aliases'] == ['LTJ', '凌']
