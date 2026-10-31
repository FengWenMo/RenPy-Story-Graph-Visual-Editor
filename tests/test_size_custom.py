"""
tests/test_size_custom.py
=========================
**「自定义倍数」**（2026-10-31）：`0.75×` 这种"人说得清、项目里却没有现成
transform"的尺寸怎么落地。

用户原话要的是「自定义 0.75×」——`50%` 只是 `0.5` 的特例，想填 0.75 / 1.25
就得有个输入框。这跟「铺满 / 适应」不是两套机制：**先按几何在项目里找效果
一样的**（`size_presets.match_preset`），找不到才生成 `sg_zoom_075`
（`story.sg_transforms` 那三条护栏一条不省）。

四层判据：

  ① 纯函数：认写法（`parse_zoom`）/ 说范围（`zoom_error`）——**越界就拒绝，
     绝不夹一个数**（夹了人以为自己填的是那个值）；
  ② 生成计划：`sg_zoom_075` + `zoom 0.75`，同名同内容不重写、已有等价变换
     就复用、手改过的文件照样拒绝；
  ③ 行与面板：`size_rows` 列不列这一条（**不需要知道图多大**）、输入框露不
     露、按钮灰不灰、请求里带什么、翻拍回来还记不记得；
  ④ 窗口端到端：填 0.75 → 确认 → 真的写 `game/sg_transforms.rpy` + 真的改到
     那一拍；取消 / 越界 → 一个字节都不动。

⚠ 这里**不测**"预设怎么匹配 transform"（`tests/test_size_presets.py`）与
"铺满/适应那几条预设"（`tests/test_stage_size_rows.py` / `test_size_generate.py`）——
只测"人填的那个数"多出来的那一段。
"""

from __future__ import annotations

import os

import pytest

from PIL import Image

from storygraph.app.stage import StageFrame, plan_size_edit
from storygraph.app.stage_render import available, size_rows
from storygraph.story import sg_transforms as SG
from storygraph.story import size_presets as SP

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (400, 300)

GUI = '''init python:
    gui.init(400, 300)
'''

TRANSFORMS = '''transform bgfit:
    fit "cover"

transform half:
    zoom 0.5

transform quarter:
    zoom 0.25
'''

SCRIPT = '''image bg big = "images/big.png"
image cg cg1 = "images/cg1.png"
image cg cg_missing = "images/nope.png"
label start:
    return
'''


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    (game / 'transform.rpy').write_text(TRANSFORMS, encoding='utf-8')
    (game / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    Image.new('RGBA', (800, 600), (255, 0, 0, 255)).save(
        game / 'images' / 'big.png')
    Image.new('RGBA', (1600, 600), (0, 255, 0, 255)).save(
        game / 'images' / 'cg1.png')
    return root


def _frame(**kw):
    base = dict(index=0, line_id='t.0001', label='t', speaker='', text='',
                scene='')
    base.update(kw)
    return StageFrame(**base)


def _rows(frame, project):
    return size_rows(frame, project_root=project, screen=SCREEN)


def _item(row, key):
    for item in row['items']:
        if item['key'] == key:
            return item
    raise AssertionError(f'没有这一项：{key}；有：{[i["key"] for i in row["items"]]}')


def _sg_file(root):
    return root / 'game' / 'sg_transforms.rpy'


# ==================================================================
#  ① 认写法 / 说范围（纯函数，唯一判据）
# ==================================================================

class TestParseZoom:
    def test_accepts_the_ways_people_write_a_multiplier(self):
        for text in ('0.75', '0.75x', '0.75X', '0.75×', '0.75倍', '75%',
                     ' 0.75 ', '.75'):
            assert SP.parse_zoom(text) == (0.75, ''), text

    def test_trailing_zeros_are_the_same_number(self):
        """`0.750` 与 `0.75` 必须是同一个数，否则 transform 名会分成两条。"""
        assert SP.parse_zoom('0.750') == (0.75, '')
        assert SG.transform_name('custom', 0.75) == \
            SG.transform_name('custom', SP.parse_zoom('0.750')[0])

    def test_a_bare_percent_number_is_refused_with_both_spellings(self):
        """`75` 是 75 倍，但人多半想写 75% —— 不替人换算，两个写法都告诉他。"""
        value, err = SP.parse_zoom('75')
        assert value is None
        assert '0.75' in err and '75%' in err

    def test_empty_asks_for_a_number_instead_of_complaining(self):
        value, err = SP.parse_zoom('')
        assert value is None and '先填一个倍数' in err

    def test_gibberish_says_what_it_accepts(self):
        value, err = SP.parse_zoom('大一点')
        assert value is None and '认不出来' in err and '0.75' in err

    def test_out_of_range_is_refused_not_clamped(self):
        small, err_small = SP.parse_zoom('0.01')
        big, err_big = SP.parse_zoom('6')
        assert small is None and '太小' in err_small
        assert big is None and '太大' in err_big
        assert repr(SP.ZOOM_MIN) in err_small or '0.05' in err_small

    def test_the_bounds_themselves_are_allowed(self):
        assert SP.parse_zoom(f'{SP.ZOOM_MIN:g}') == (SP.ZOOM_MIN, '')
        assert SP.parse_zoom(f'{SP.ZOOM_MAX:g}') == (SP.ZOOM_MAX, '')

    def test_zoom_error_survives_broken_numbers(self):
        assert '数' in SP.zoom_error(None)
        assert '数' in SP.zoom_error('')
        assert '数' in SP.zoom_error(float('nan'))
        assert '有限' in SP.zoom_error(float('inf'))
        assert SP.zoom_error(0.75) == ''

    def test_the_label_is_the_number_people_see(self):
        assert SP.zoom_label(0.75) == '0.75×'
        assert SP.zoom_label(1.25) == '1.25×'
        assert SP.zoom_label(None) == '自定义倍数'


# ==================================================================
#  ② 生成计划：`sg_zoom_075` = `zoom 0.75`
# ==================================================================

class TestPlanCustom:
    def test_a_custom_zoom_becomes_a_predictable_definition(self, project):
        plan = SG.plan_for_project(project, 'custom', 0.75)
        assert plan.action == SG.CREATE and plan.name == 'sg_zoom_075'
        assert plan.block == 'transform sg_zoom_075:\n    zoom 0.75'
        plan.apply()
        assert SG.inspect(project).stmt_of('sg_zoom_075') == 'zoom 0.75'

    def test_the_same_zoom_twice_writes_nothing(self, project):
        SG.plan_for_project(project, 'custom', 0.75).apply()
        before = _sg_file(project).read_text(encoding='utf-8')
        plan = SG.plan_for_project(project, 'custom', 0.75)
        assert plan.action == SG.NOOP and plan.writes is False
        assert _sg_file(project).read_text(encoding='utf-8') == before

    def test_a_different_zoom_is_appended_next_to_it(self, project):
        SG.plan_for_project(project, 'custom', 0.75).apply()
        plan = SG.plan_for_project(project, 'custom', 1.25)
        assert plan.action == SG.APPEND and plan.name == 'sg_zoom_125'
        plan.apply()
        sg = SG.inspect(project)
        assert sg.names() == ['sg_zoom_075', 'sg_zoom_125']
        assert sg.stmt_of('sg_zoom_125') == 'zoom 1.25'

    def test_an_existing_equivalent_transform_is_reused(self, project):
        """项目里已经有 `zoom 0.25`（叫 `quarter`）→ 复用它，一个字节都不写。

        `index` 必须显式给：`plan_define` 的规矩是"没有 transform 表就不查重"，
        不猜（窗口层给的是 `session.transform_index()`）。
        """
        from storygraph.story.atl import scan_transforms

        plan = SG.plan_for_project(project, 'custom', 0.25,
                                   index=scan_transforms(project),
                                   natural=(800, 600), screen=SCREEN)
        assert plan.action == SG.REUSE and plan.name == 'quarter'
        assert plan.writes is False and not _sg_file(project).exists()

    def test_a_missing_zoom_is_refused_before_anything_is_written(self, project):
        plan = SG.plan_for_project(project, 'custom', None)
        assert plan.action == SG.REFUSE and not plan.writes
        assert '没填好' in plan.reason
        assert not _sg_file(project).exists()

    def test_out_of_range_is_refused_before_anything_is_written(self, project):
        for zoom in (0.01, 6, 'x'):
            plan = SG.plan_for_project(project, 'custom', zoom)
            assert plan.action == SG.REFUSE, zoom
            assert '没填好' in plan.reason
            assert not _sg_file(project).exists()

    def test_a_hand_edited_file_still_stops_it(self, project):
        f = _sg_file(project)
        f.write_text('# 我自己写的\ntransform mine:\n    zoom 2\n',
                     encoding='utf-8')
        plan = SG.plan_for_project(project, 'custom', 0.75)
        assert plan.action == SG.REFUSE and '不是工具生成的' in plan.reason
        assert f.read_text(encoding='utf-8') \
            == '# 我自己写的\ntransform mine:\n    zoom 2\n'

    def test_a_taken_name_does_not_overwrite_someone_else(self, project):
        """别人已经写过 `transform sg_zoom_075: zoom 3` → 退到 `_2`。"""
        (project / 'game' / 'mine.rpy').write_text(
            'transform sg_zoom_075:\n    zoom 3\n', encoding='utf-8')
        from storygraph.story.atl import scan_transforms
        plan = SG.plan_for_project(project, 'custom', 0.75,
                                   index=scan_transforms(project))
        assert plan.action == SG.CREATE and plan.name == 'sg_zoom_075_2'
        plan.apply()
        text = _sg_file(project).read_text(encoding='utf-8')
        assert 'transform sg_zoom_075_2:' in text and 'zoom 0.75' in text
        assert 'transform sg_zoom_075:' not in text


class TestDedupeWithoutTheImage:
    """
    图找不到时（`natural=None`）**也能查重**：纯缩放的框与图尺寸无关。

    真机依据（`sr_general`）：`zoom 0.5` 早就叫 `cg38402160`
    （`zoom 0.5; anchor (0,0)`）。拿不到图就不查重的话，人会白拿一条
    `sg_zoom_050` —— 护栏 ② 要防的就是这种重复。
    """

    def test_a_pure_zoom_is_found_without_knowing_the_image(self):
        from storygraph.story.atl import parse_transforms

        index = parse_transforms('transform aa:\n    zoom 0.5\n'
                                 'transform bb:\n    zoom 0.5\n    xpos 0.25\n')
        hit = SP.match_without_natural('custom', SCREEN, index, zoom=0.5)
        assert hit and hit['name'] == 'aa'      # 位置在左上角那条
        assert hit['brief'] == 'zoom 0.5'

    def test_the_position_participates(self):
        """`zoom 0.5` 但挪了位置 ≠ 左上角 0.5（别拿它冒充"铺满屏幕"）。"""
        from storygraph.story.atl import parse_transforms

        index = parse_transforms('transform moved:\n    zoom 0.5\n'
                                 '    xpos 0.25\n'
                                 'transform lower:\n    zoom 0.5\n'
                                 '    ypos 0.9\n')
        assert SP.match_without_natural('custom', SCREEN, index, zoom=0.5) is None

    def test_a_fit_transform_never_matches_a_zoom(self):
        from storygraph.story.atl import parse_transforms

        index = parse_transforms('transform bgcontain:\n    fit "contain"\n')
        assert SP.match_without_natural('custom', SCREEN, index, zoom=0.5) is None

    def test_a_broken_zoom_number_is_not_a_match(self, project):
        assert SP.match_without_natural('custom', SCREEN, {}, zoom=None) is None

    def test_an_anchored_zoom_counts_as_the_same_effect(self, tmp_path):
        """`zoom 0.5; anchor (0,0)`（真机那条 `cg38402160` 的样子）。

        它的语句和 `zoom 0.5` **不一样**（所以"逐字相同"那条查重漏掉它），
        但框一模一样 —— 拿不到图时也要认出来。
        """
        from storygraph.story.atl import scan_transforms

        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        (root / 'game' / 't.rpy').write_text(
            'transform cg4k:\n    zoom 0.5\n    anchor (0, 0)\n',
            encoding='utf-8')
        plan = SG.plan_for_project(root, 'custom', 0.5,
                                   index=scan_transforms(root))
        assert plan.action == SG.REUSE and plan.name == 'cg4k'
        assert '不用看图尺寸' in plan.reason
        assert not _sg_file(root).exists()

    def test_identical_statements_count_as_equivalent(self, project):
        """逐字相同的 ATL 体 = 同一个效果（`cover` → 已有的 `bgfit`）。"""
        from storygraph.story.atl import scan_transforms

        plan = SG.plan_for_project(project, 'cover',
                                   index=scan_transforms(project))
        assert plan.action == SG.REUSE and plan.name == 'bgfit'
        assert '等价变换' in plan.reason


# ==================================================================
#  ③ 行：这一条在不在、说的是什么
# ==================================================================

class TestRow:
    def test_the_row_lists_a_custom_multiplier(self, project):
        row = _rows(_frame(scene='bg big'), project)[0]
        item = _item(row, 'custom')
        assert item['at'] == '' and item['zoom'] is None
        assert item['ok'] is False and '填一个倍数' in item['why']
        # 它迟早要走"查重/生成"那条路，所以生来就是 generate=True
        assert item['generate'] is True

    def test_it_is_the_only_preset_that_works_without_the_image_size(
            self, project):
        """找不到图 → 铺满/适应只能"算不出尺寸"，但倍数不需要知道图多大。"""
        row = _rows(_frame(scene='', layers='cg cg_missing'), project)[0]
        assert row['natural'] is None
        assert _item(row, 'cover')['ok'] is False
        assert _item(row, 'custom')['at'] == ''
        # 这一条本身也不"可写"，但原因不是"算不出尺寸"，而是"还没填数"
        assert '填一个倍数' in _item(row, 'custom')['why']

    def test_it_sits_after_the_fixed_presets_and_before_the_pick_list(
            self, project):
        row = _rows(_frame(scene='bg big'), project)[0]
        keys = [item['key'] for item in row['items']]
        assert keys[:len(SP.PRESET_ORDER)] == list(SP.PRESET_ORDER)
        assert keys[len(SP.PRESET_ORDER)] == 'custom'
        assert 'pick' in keys[keys.index('custom') + 1:]

    def test_a_cross_chapter_row_gets_it_too(self, project):
        """出处不在这章（`source=-1`）时补一条 show 也要能填倍数。"""
        frame = _frame(scene='bg big', scene_src=-1, entry_state=True)
        row = _rows(frame, project)[0]
        assert row['source'] == -1
        assert _item(row, 'custom')['generate'] is True


class TestPlanSizeEdit:
    def test_a_custom_row_without_a_name_says_which_step_was_missed(self, project):
        from storygraph.story.beat_model import Beat

        beats = [Beat(seq=0, label='t', text='', scene='bg big')]
        row = {'kind': 'scene', 'tag': '', 'spec': 'bg big', 'source': 0}
        plan = plan_size_edit(beats, 0, row, key='custom', at='',
                             project_root=project, screen=SCREEN)
        assert not plan.ok
        assert '还没算出 transform 名' in plan.reason

    def test_a_named_custom_transform_is_written_like_any_preset(self, project):
        from storygraph.story.beat_model import Beat

        beats = [Beat(seq=0, label='t', text='', scene='bg big')]
        row = {'kind': 'scene', 'tag': '', 'spec': 'bg big', 'source': 0}
        plan = plan_size_edit(beats, 0, row, key='custom', at='quarter',
                              project_root=project, screen=SCREEN)
        assert plan.ok, plan.reason
        plan.apply()
        assert beats[0].scene_at == 'quarter'


# ==================================================================
#  ④ 面板：输入框露不露、灰不灰、请求里带什么
# ==================================================================

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QMessageBox                     # noqa: E402

from storygraph.ui_qt import main_window as MW                # noqa: E402
from storygraph.ui_qt.size_panel import SizePanel             # noqa: E402


def _panel_rows():
    """两行：背景（第 4 拍设的）+ 一个 CG 图层，各带一条「自定义倍数」。"""
    def custom():
        return {'key': 'custom', 'at': '', 'zoom': None,
                'text': '自定义倍数…（先填一个数）', 'ok': False,
                'why': '先在下面填一个倍数（0.05–5），例如 0.75',
                'current': False, 'generate': True, 'note': 'note'}

    return [
        {'index': 0, 'kind': 'scene', 'tag': '', 'spec': 'bg big',
         'label': '背景', 'at': '', 'source': 3, 'natural': (800, 600),
         'note': '背景 bg big 800×600', 'why': '',
         'items': [
             {'key': 'as_is', 'at': '', 'text': '原样（已经是原样）',
              'ok': False, 'why': '这一行本来就没有 at', 'current': True,
              'note': ''},
             {'key': 'cover', 'at': 'bgfit', 'text': '铺满 → bgfit',
              'ok': True, 'why': '', 'current': False, 'note': ''},
             custom(),
         ]},
        {'index': 1, 'kind': 'layer', 'tag': 'cg', 'spec': 'cg cg1',
         'label': 'CG', 'at': 'bgfit', 'source': 1, 'natural': (1600, 600),
         'note': 'CG cg cg1 1600×600', 'why': '',
         'items': [
             {'key': 'as_is', 'at': '', 'text': '原样（不套变换）',
              'ok': True, 'why': '', 'current': False, 'note': ''},
             {'key': 'cover', 'at': 'bgfit', 'text': '铺满 → bgfit',
              'ok': False, 'why': '已经是这个变换了', 'current': True,
              'note': ''},
             custom(),
         ]},
    ]


class TestPanel:
    def test_the_input_box_only_shows_for_the_custom_item(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        assert slot['zoom_box'].isVisibleTo(panel) is False      # 默认选「原样」
        slot['combo'].setCurrentIndex(slot['custom_index'])
        assert slot['zoom_box'].isVisibleTo(panel) is True
        slot['combo'].setCurrentIndex(1)
        assert slot['zoom_box'].isVisibleTo(panel) is False

    def test_the_combo_item_names_the_number_and_the_body(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        assert slot['zoom_edit'].text() == '0.75'
        text = slot['combo'].itemText(slot['custom_index'])
        assert '0.75×' in text and 'zoom 0.75' in text

    def test_typing_a_number_updates_the_item_and_the_hint(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(slot['custom_index'])
        slot['zoom_edit'].setText('1.25')
        assert '1.25×' in slot['combo'].itemText(slot['custom_index'])
        assert 'zoom 1.25' in slot['zoom_hint'].text()
        assert 'sg_zoom_125' in slot['zoom_hint'].text()
        assert slot['button'].isEnabled() is True

    def test_a_bad_number_disables_the_button_and_says_why(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(slot['custom_index'])
        slot['zoom_edit'].setText('abc')
        assert slot['button'].isEnabled() is False
        assert '认不出来' in slot['zoom_hint'].text()
        assert '认不出来' in slot['button'].toolTip()
        assert panel.request(0) is None
        # 条目**仍然可选**：填错了也要停在自己填的那条上看得见错在哪
        assert slot['combo'].currentIndex() == slot['custom_index']

    def test_out_of_range_never_reaches_a_request(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(slot['custom_index'])
        got = []
        panel.size_requested.connect(got.append)
        slot['zoom_edit'].setText('75')
        slot['button'].click()
        assert got == []
        assert '太大' in slot['zoom_hint'].text()

    def test_the_request_carries_the_number_and_the_generate_flag(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(slot['custom_index'])
        slot['zoom_edit'].setText('0.75')
        req = panel.request(0)
        assert req['key'] == 'custom' and req['at'] == ''
        assert req['zoom'] == 0.75 and req['generate'] is True
        assert req['natural'] == (800, 600)
        assert req['source'] == 3

    def test_the_scene_request_carries_it_too(self, qapp):
        """换景也能"新背景套 0.75×"——与「改」同一条路（`_plan_sg_from_card`）。"""
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(slot['custom_index'])
        slot['zoom_edit'].setText('1.25')
        req = panel.scene_request(0)
        assert req['key'] == 'custom' and req['zoom'] == 1.25
        assert req['generate'] is True and req['kind'] == 'scene'

    def test_the_number_is_remembered_per_row_across_refreshes(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        panel.slots()[1]['zoom_edit'].setText('1.25')      # 只改 CG 那一行
        panel.set_rows(_panel_rows())                      # 翻拍 / 刷新
        assert panel.slots()[0]['zoom_edit'].text() == '0.75'
        assert panel.slots()[1]['zoom_edit'].text() == '1.25'

    def test_a_bad_typed_number_is_remembered_verbatim(self, qapp):
        """填错了那串**原样**留着：被悄悄换成 0.75 才是最糟的"我没写这个"。"""
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        panel.slots()[0]['zoom_edit'].setText('75')
        panel.set_rows(_panel_rows())
        assert panel.slots()[0]['zoom_edit'].text() == '75'
        assert '太大' in panel.slots()[0]['zoom_hint'].text()

    def test_a_cross_chapter_row_says_where_it_will_land(self, qapp):
        """出处不在这章（`source=-1`）→ 按下去之前就说"本章第一拍补一条"。"""
        rows = _panel_rows()
        rows[0]['source'] = -1
        panel = SizePanel()
        panel.set_rows(rows)
        slot = panel.slots()[0]
        slot['combo'].setCurrentIndex(slot['custom_index'])
        slot['zoom_edit'].setText('0.75')
        tip = slot['button'].toolTip()
        assert '本章第一拍' in tip and '不改上一章' in tip
        assert '最初写下这一行' not in tip

    def test_the_row_data_is_not_mutated(self, qapp):
        """面板只在**副本**上改条目：`size_rows` 的产物是会话缓存，不许动。"""
        rows = _panel_rows()
        panel = SizePanel()
        panel.set_rows(rows)
        panel.slots()[0]['zoom_edit'].setText('1.25')
        assert rows[0]['items'][2]['text'] == '自定义倍数…（先填一个数）'
        assert rows[0]['items'][2]['zoom'] is None

    def test_the_input_box_does_not_widen_the_panel(self, qapp):
        panel = SizePanel()
        panel.set_rows(_panel_rows())
        panel.slots()[0]['combo'].setCurrentIndex(
            panel.slots()[0]['custom_index'])
        panel.show()
        try:
            qapp.processEvents()
            assert panel.minimumSizeHint().width() <= 220, \
                panel.minimumSizeHint().width()
        finally:
            panel.close()


# ==================================================================
#  ⑤ 窗口端到端：填 0.75 → 确认 → 真的写文件 + 真的改到那一拍
# ==================================================================

def _yes(*_a, **_k):
    return QMessageBox.StandardButton.Yes


def _no(*_a, **_k):
    return QMessageBox.StandardButton.No


def _scene_beat_index(win) -> int:
    for i, b in enumerate(win._session.beats):
        if str(getattr(b, 'scene', '') or '').strip():
            return i
    raise AssertionError('这一章没有 scene，测不了尺寸面板')


def _custom_slot(win, index=0):
    panel = win._panel.size_panel()
    slot = panel.slots()[index]
    idx = int(slot['custom_index'])
    assert idx >= 0, [it['key'] for it in panel.rows()[index]['items']]
    slot['combo'].setCurrentIndex(idx)
    return slot


class TestApplyFromCard:
    def test_filling_075_writes_the_definition_and_the_beat(self, ui_window,
                                                            monkeypatch):
        win = ui_window
        monkeypatch.setattr(MW.QMessageBox, 'question', staticmethod(_yes))
        i = _scene_beat_index(win)
        win._show_index(i)
        assert not str(getattr(win._session.beats[i], 'scene_at', '') or '')
        slot = _custom_slot(win)
        slot['zoom_edit'].setText('0.75')
        assert slot['button'].isEnabled()
        slot['button'].click()
        assert win._session.beats[i].scene_at == 'sg_zoom_075'
        text = (win._project_root / 'game' / 'sg_transforms.rpy') \
            .read_text(encoding='utf-8')
        assert 'transform sg_zoom_075:' in text and 'zoom 0.75' in text
        # 卡片跟着刷了：这一行现在就是那条定义（「自定义」列表里带着它）
        rows = win._panel.size_panel().rows()
        assert rows[0]['at'] == 'sg_zoom_075'
        pick = next(it for it in rows[0]['items']
                    if it['key'] == 'pick' and it['at'] == 'sg_zoom_075')
        assert pick['current'] is True

    def test_the_confirm_box_names_the_file_and_the_line(self, ui_window,
                                                        monkeypatch):
        win = ui_window
        seen = {}

        def _capture(_parent, _title, text, *_a, **_k):
            seen['text'] = text
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(MW.QMessageBox, 'question',
                            staticmethod(_capture))
        win._show_index(_scene_beat_index(win))
        slot = _custom_slot(win)
        slot['zoom_edit'].setText('0.75')
        slot['button'].click()
        text = seen.get('text') or ''
        assert 'game/sg_transforms.rpy' in text
        assert '+ transform sg_zoom_075:' in text
        assert 'scene_at' in text

    def test_cancel_touches_nothing(self, ui_window, monkeypatch):
        win = ui_window
        monkeypatch.setattr(MW.QMessageBox, 'question', staticmethod(_no))
        i = _scene_beat_index(win)
        win._show_index(i)
        slot = _custom_slot(win)
        slot['zoom_edit'].setText('0.75')
        slot['button'].click()
        assert not str(getattr(win._session.beats[i], 'scene_at', '') or '')
        assert not (win._project_root / 'game' / 'sg_transforms.rpy').exists()

    def test_a_zoom_the_project_already_has_is_reused_without_a_file(
            self, ui_window, monkeypatch):
        """项目里已有 `transform quarter: zoom 0.25` → 用它，不新建文件。"""
        win = ui_window
        monkeypatch.setattr(MW.QMessageBox, 'question', staticmethod(_yes))
        (win._project_root / 'game' / 'mine.rpy').write_text(
            'transform quarter:\n    zoom 0.25\n', encoding='utf-8')
        win._session.refresh_transform_index()
        i = _scene_beat_index(win)
        win._show_index(i)
        slot = _custom_slot(win)
        slot['zoom_edit'].setText('0.25')
        slot['button'].click()
        assert win._session.beats[i].scene_at == 'quarter'
        assert not (win._project_root / 'game' / 'sg_transforms.rpy').exists()

    def test_a_hard_request_without_a_number_says_so(self, ui_window,
                                                     monkeypatch):
        """绕过面板硬发（没有 zoom）→ 不许静默：控制台说清哪一步没填。"""
        win = ui_window
        monkeypatch.setattr(MW.QMessageBox, 'question', staticmethod(_yes))
        i = _scene_beat_index(win)
        win._show_index(i)
        plan = MW.apply_size_from_card(win, {
            'row': 0, 'key': 'custom', 'at': '', 'zoom': None,
            'kind': 'scene', 'tag': '', 'spec': 'bg classroom', 'source': 0,
            'generate': True, 'natural': (1, 1)})
        assert plan is None
        assert '自定义倍数没填好' in win._console.toPlainText()

    def test_scene_change_can_carry_a_custom_zoom(self, ui_window, monkeypatch):
        """换景那条路也走同一个 `_plan_sg_from_card`（不是各写一份）。"""
        win = ui_window
        monkeypatch.setattr(MW.QMessageBox, 'question', staticmethod(_yes))
        monkeypatch.setattr(win, '_pick_asset_for',
                            lambda what, current=None: 'bg rooftop')
        i = _scene_beat_index(win)
        win._show_index(i)
        slot = _custom_slot(win)
        slot['zoom_edit'].setText('0.75')
        slot['swap'].click()
        beat = win._session.beats[i]
        assert str(beat.scene or '') == 'bg rooftop'
        assert beat.scene_at == 'sg_zoom_075'
        text = (win._project_root / 'game' / 'sg_transforms.rpy') \
            .read_text(encoding='utf-8')
        assert 'zoom 0.75' in text
