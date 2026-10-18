"""
tests/test_atl_block_card.py
============================
A1（用户 2026-10-19）：`image 名:` 的 ATL 块体 —— **做只读定位，别做编辑**。

用户原话：

> 1. image 名: 的 ATL 块体 —— 做只读定位，别做编辑
> A1 | 卡片显示「ATL 定义（贴图看源文件）」+ 行号 + 一键打开源文件 | 0.5 天 | 推荐

所以这里钉三件事：

① **定位准**：块头行号 + 只认"缩进更深"的块体（同缩进就停 —— 与 Ren'Py 同一口径）；
② **不骗人**：文件搬走 / 行号过期 / 那一行后面没有块体，都要说清原因，
   而不是给一段空文本；
③ **卡片上看得见**：ATL 行不再被写成"没有图片文件"（它本来就没有贴图），
   详情里给块体原文 + `文件:行号`，「打开源文件」只在定位得到时才可点。

（"读不出来" → `reason` 的口径与 `storygraph.assets.atl_block` 的 docstring 一致。）
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402

ATL_SRC = '''\
image logo blink:
    "logo.png"
    xpos 10
    ypos 20

    linear 0.5 alpha 0.0
    linear 0.5 alpha 1.0

image other = "x.png"

label start:
    return
'''


# ==================================================================
#  ① 定位：块体到哪儿结束
# ==================================================================

class TestLocateBlock:
    def test_body_is_dedented_and_stops_at_the_next_top_level_line(self,
                                                                  tmp_path):
        from storygraph.assets.atl_block import locate_block
        f = tmp_path / 'defs.rpy'
        f.write_text(ATL_SRC, encoding='utf-8')
        body, reason = locate_block(f, 1)
        assert reason == ''
        assert body[0] == '"logo.png"', body
        assert 'xpos 10' in body
        # 块里的空行保留（它在块中间，不是结束信号）
        assert '' in body
        assert body[-1] == 'linear 0.5 alpha 1.0', body
        # 后面那两条同缩进/更浅的行不许进来
        assert not any('image other' in ln for ln in body)
        assert not any('label start' in ln for ln in body)

    def test_a_bom_does_not_shift_the_line_numbers(self, tmp_path):
        from storygraph.assets.atl_block import locate_block
        f = tmp_path / 'defs.rpy'
        f.write_bytes(b'\xef\xbb\xbf' + ATL_SRC.encode('utf-8'))
        body, reason = locate_block(f, 1)
        assert reason == ''
        assert body[0] == '"logo.png"'

    def test_a_missing_file_says_so_instead_of_guessing(self, tmp_path):
        from storygraph.assets.atl_block import locate_block
        body, reason = locate_block(tmp_path / 'nope.rpy', 1)
        assert body == ()
        assert '源文件不在了' in reason, reason

    def test_a_stale_line_number_says_so(self, tmp_path):
        from storygraph.assets.atl_block import locate_block
        f = tmp_path / 'defs.rpy'
        f.write_text('image a = "a.png"\n', encoding='utf-8')
        body, reason = locate_block(f, 99)
        assert body == ()
        assert '行号对不上' in reason, reason

    def test_a_header_without_a_block_says_so(self, tmp_path):
        from storygraph.assets.atl_block import locate_block
        f = tmp_path / 'defs.rpy'
        f.write_text('image a:\n\nimage b = "b.png"\n', encoding='utf-8')
        body, reason = locate_block(f, 1)
        assert body == ()
        assert '没有缩进块' in reason, reason

    def test_a_very_long_block_is_cut_with_a_pointer(self, tmp_path):
        from storygraph.assets.atl_block import locate_block
        f = tmp_path / 'defs.rpy'
        lines = ['image long:'] + [f'    xpos {i}' for i in range(60)]
        f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        body, reason = locate_block(f, 1, max_lines=10)
        assert reason == ''
        assert len(body) == 11
        assert '还有 50 行' in body[-1], body[-1]


# ==================================================================
#  ② 资源表的一行 → 源文件 / 行号
# ==================================================================

class TestSourceOfRow:
    def test_private_fields_win(self):
        from storygraph.assets.atl_block import source_of_row
        row = {'Notes': 'old.rpy:3；ATL 定义（贴图写在块里，看源文件）',
               '_source_file': 'game/new.rpy', '_source_line': 7}
        assert source_of_row(row) == ('game/new.rpy', 7)

    def test_notes_are_the_fallback(self):
        from storygraph.assets.atl_block import source_of_row
        row = {'Notes': r'game\defs.rpy:12；ATL 定义（贴图写在块里，看源文件）'}
        assert source_of_row(row) == (r'game\defs.rpy', 12)

    def test_no_source_at_all(self):
        from storygraph.assets.atl_block import source_of_row
        assert source_of_row({'Notes': '没有位置的备注'}) == ('', 0)


class TestIsAtlRow:
    def test_the_private_flag(self):
        from storygraph.assets.atl_block import is_atl_row
        assert is_atl_row({'_atl': True, 'Path': ''})

    def test_old_rows_with_the_note_and_no_path(self):
        from storygraph.assets.atl_block import is_atl_row
        assert is_atl_row({'Path': '',
                           'Notes': 'defs.rpy:1；ATL 定义（贴图写在块里，看源文件）'})

    def test_a_normal_image_with_the_word_atl_in_notes_is_not_atl(self):
        from storygraph.assets.atl_block import is_atl_row
        assert not is_atl_row({'Path': 'game/images/a.png',
                               'Notes': 'ATL 定义见别处'})


class TestBlockOfRow:
    def test_relative_source_resolves_against_the_project_root(self, tmp_path):
        from storygraph.assets.atl_block import block_of_row
        proj = tmp_path / 'proj'
        (proj / 'game').mkdir(parents=True)
        (proj / 'game' / 'defs.rpy').write_text(ATL_SRC, encoding='utf-8')
        row = {'ID': 'logo_blink', '_atl': True,
               '_source_file': 'game/defs.rpy', '_source_line': 1}
        blk = block_of_row(row, proj)
        assert blk.found, blk.reason
        assert blk.location() == 'defs.rpy:1'
        assert '"logo.png"' in blk.body[0]

    def test_a_missing_source_is_reported_not_raised(self, tmp_path):
        from storygraph.assets.atl_block import block_of_row
        row = {'ID': 'logo_blink', '_atl': True,
               '_source_file': 'game/gone.rpy', '_source_line': 1}
        blk = block_of_row(row, tmp_path)
        assert not blk.found
        assert '源文件不在了' in blk.reason


# ==================================================================
#  ②b 表里的那一行到底带不带位置（`beat_build` 写进去）
# ==================================================================

class TestRegistryRowCarriesTheLocation:
    def test_an_atl_image_row_remembers_file_and_line(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.beat_build import apply_script_definitions

        reg = AssetRegistry()
        parse = SimpleNamespace(
            images={'logo blink': {'name': 'logo blink', 'expr': '', 'path': '',
                                   'solid': None, 'atl': True,
                                   'source_file': 'game/defs.rpy',
                                   'source_line': 34}},
            transforms={}, characters={})
        apply_script_definitions(reg, parse)
        row = reg.images['logo_blink']
        assert row['_atl'] is True
        assert row['_source_file'] == 'game/defs.rpy'
        assert row['_source_line'] == 34
        assert 'ATL 定义' in row['Notes']


# ==================================================================
#  ③ 卡片 / 详情：看得见，且不说假话
# ==================================================================

class _Engine:
    def __init__(self, reg, root):
        self._reg = reg
        self.project_root = root

    def get_registry(self):
        return self._reg


def _registry(rows):
    from storygraph.assets.asset_registry import AssetRegistry
    reg = AssetRegistry()
    reg.images = rows
    return reg


class TestAssetDetail:
    def test_the_atl_body_and_line_are_shown(self, tmp_path):
        from storygraph.app.beats import asset_detail
        proj = tmp_path / 'proj'
        (proj / 'game').mkdir(parents=True)
        (proj / 'game' / 'defs.rpy').write_text(ATL_SRC, encoding='utf-8')
        reg = _registry({'logo_blink': {
            'ID': 'logo_blink', 'Tag': 'logo', 'Attributes': 'blink',
            'Path': '', '_atl': True,
            '_source_file': 'game/defs.rpy', '_source_line': 1}})
        text = asset_detail(_Engine(reg, proj), 'image', 'logo_blink')
        assert 'ATL 定义' in text
        assert '"logo.png"' in text
        assert 'defs.rpy:1' in text

    def test_a_broken_location_is_reported_as_such(self, tmp_path):
        from storygraph.app.beats import asset_detail
        reg = _registry({'logo_blink': {
            'ID': 'logo_blink', 'Tag': 'logo', 'Path': '', '_atl': True,
            '_source_file': 'game/gone.rpy', '_source_line': 3}})
        text = asset_detail(_Engine(reg, tmp_path), 'image', 'logo_blink')
        assert '⚠' in text and '源文件不在了' in text, text

    def test_a_normal_image_gets_no_atl_section(self, tmp_path):
        from storygraph.app.beats import asset_detail
        reg = _registry({'bg1': {'ID': 'bg1', 'Tag': 'bg', 'Path': 'game/bg1.png'}})
        text = asset_detail(_Engine(reg, tmp_path), 'image', 'bg1')
        assert 'ATL 定义' not in text


@pytest.fixture
def picker(qapp, tmp_path, monkeypatch):
    """带一条 ATL 行 + 一条真没图的行。"""
    from PySide6.QtCore import QSettings
    from storygraph.ui_qt import asset_picker as AP

    ini = tmp_path / 'prefs.ini'
    monkeypatch.setattr(AP, 'settings_factory',
                        lambda: QSettings(str(ini), QSettings.IniFormat))

    proj = tmp_path / 'proj'
    (proj / 'game').mkdir(parents=True)
    (proj / 'game' / 'defs.rpy').write_text(ATL_SRC, encoding='utf-8')
    reg = _registry({
        'logo_blink': {'ID': 'logo_blink', 'Tag': 'logo', 'Attributes': 'blink',
                       'Path': '', '_atl': True,
                       '_source_file': 'game/defs.rpy', '_source_line': 1,
                       'Notes': 'game/defs.rpy:1；ATL 定义（贴图写在块里，看源文件）'},
        'ghost': {'ID': 'ghost', 'Tag': 'ui', 'Path': 'game/nope/ghost.png'},
    })
    dlg = AP.AssetPickerDialog(None, registry=reg, project_root=proj,
                               types=['image'], want_image_spec=True)
    yield dlg
    dlg.close()


class TestTheCardSaysWhatItIs:
    def _select(self, picker, needle):
        for i in range(picker._items.count()):
            it = picker._items.item(i)
            atype, aid = it.data(0x0100)          # Qt.UserRole
            if aid == needle:
                picker._items.setCurrentItem(it)
                return it
        raise AssertionError(f'没找到 {needle}')

    def test_an_atl_tile_is_not_reported_as_a_missing_picture(self, picker):
        it = self._select(picker, 'logo_blink')
        assert 'ATL 定义' in it.text(), it.text()
        assert '没有图片文件' not in it.text(), it.text()

    def test_the_atl_tile_is_counted_separately_in_the_hint(self, picker):
        assert picker._atl_tiles == 1, picker._atl_tiles
        assert picker._no_image == 1, picker._no_image
        assert '1 条是 ATL 定义（无贴图）' in picker._hint.text()
        assert '1 条没有图片文件' in picker._hint.text()

    def test_the_detail_shows_the_body_and_enables_opening(self, picker):
        self._select(picker, 'logo_blink')
        assert '"logo.png"' in picker._detail.text(), picker._detail.text()
        assert 'defs.rpy:1' in picker._detail.text()
        assert picker._open_btn.isEnabled()
        assert picker._detail_loc is not None
        assert Path(picker._detail_loc[0]).name == 'defs.rpy'

    def test_a_truly_missing_picture_keeps_the_old_warning(self, picker):
        self._select(picker, 'ghost')
        assert '不是"还在加载"' in picker._detail.text()
        assert not picker._open_btn.isEnabled()

    def test_a_normal_image_tile_has_no_atl_marking(self, picker):
        self._select(picker, 'ghost')
        assert picker._detail_loc is None
