"""
tests/test_stage_render_scale.py
================================
**舞台预览的尺寸语义**（2026-10-30，⑦ P0/P1/P2）。

用户原话（二轮反馈）：
> CG / 背景图都调不了大小……预览里和游戏里不一样

根因不是"缺控件"，而是**预览在骗人**：
  · `scene bg X at bgfit` 的 `at` 在快照里丢了（只在写 scene 那一拍有）；
  · `atl.py` 不解析 `fit`，`bgfit` 落到"按名字猜位置"，原尺寸居中贴底；
  · 图层的 `at`（`show cg cg01 at cg38402160`）根本没进数据；
  · `_draw_layers` 永远原图贴 (0,0)。

这里用**合成项目 + 像素采样**把引擎语义逐条钉死（不依赖视觉）：
  · 无 at 大图 → 1:1 贴左上、右/下裁掉（**不许**"预览自动 fit"）；
  · `fit "cover"` / `"contain"` / `zoom` → 与 Ren'Py 文档的几何一致；
  · `scene_at` 继承（第 2 拍没写 scene 也带 fit）；
  · 图层 at 生效 + 多图层按顺序叠 + 无 at 图层原图裁切。
"""

from __future__ import annotations

import pytest

from PIL import Image, ImageDraw

from storygraph.app.stage import StageFrame, build_frames, frame_summary
from storygraph.app.stage_render import available, render_frame
from storygraph.story.atl import parse_transforms, scan_transforms
from storygraph.story.beat_model import Beat

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (400, 300)

GUI = '''init python:
    gui.init(400, 300)

define gui.textbox_height = 90
define gui.textbox_yalign = 1.0
define gui.text_size = 18
define gui.name_text_size = 20
'''

TRANSFORMS = '''transform bgfit:
    fit "cover"

transform bgcontain:
    fit "contain"

transform half:
    zoom 0.5
'''

#: 四象限颜色（左上/右上/左下/右下）——采样点都避开象限边界
RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)
BLUE = (0, 0, 255, 255)
YELLOW = (255, 255, 0, 255)
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)


def _png(path, size, colors, marker=None):
    """生成四象限测试图；`marker=((x, y), rgba)` 再点一个独有像素。"""
    w, h = size
    img = Image.new('RGBA', size, colors[0])
    draw = ImageDraw.Draw(img)
    draw.rectangle((w // 2, 0, w - 1, h // 2 - 1), fill=colors[1])
    draw.rectangle((0, h // 2, w // 2 - 1, h - 1), fill=colors[2])
    draw.rectangle((w // 2, h // 2, w - 1, h - 1), fill=colors[3])
    if marker:
        (mx, my), rgba = marker
        img.putpixel((mx, my), rgba)
    img.save(path)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / 'images').mkdir(parents=True)
    (game / 'gui').mkdir()
    (game / 'gui.rpy').write_text(GUI, encoding='utf-8')
    (game / 'transform.rpy').write_text(TRANSFORMS, encoding='utf-8')
    (game / 'script.rpy').write_text(
        'image bg big = "images/big.png"\n'
        'image bg wide = "images/wide.png"\n'
        'image bg tall = "images/tall.png"\n'
        'image bg small = "images/small.png"\n'
        'image cg cg4k = "images/cg4k.png"\n'
        'image ui ui_a = "images/ui_a.png"\n'
        'label start:\n    return\n', encoding='utf-8')
    # 800×600（4:3）：无 at 时 1:1 裁成屏幕大小
    _png(game / 'images' / 'big.png', (800, 600),
         (RED, GREEN, BLUE, YELLOW), marker=((350, 250), WHITE))
    # 1200×600（2:1）：contain 后上下留黑边
    _png(game / 'images' / 'wide.png', (1200, 600), (RED, GREEN, BLUE, YELLOW))
    # 400×1200（1:3）：contain 后左右留黑边
    _png(game / 'images' / 'tall.png', (400, 1200), (RED, GREEN, BLUE, YELLOW))
    # 4K CG 的替身（800×600，zoom 0.5 正好铺满 400×300）
    _png(game / 'images' / 'cg4k.png', (800, 600), (YELLOW, RED, GREEN, BLUE))
    # UI 图层：整屏纯色，用来验证叠放顺序
    _png(game / 'images' / 'ui_a.png', (400, 300), (BLUE, BLUE, BLUE, BLUE))
    # 比屏幕小的图：无 at 时右/下留黑（引擎行为）
    _png(game / 'images' / 'small.png', (300, 200), (RED, RED, RED, RED))
    return root


@pytest.fixture(scope='module')
def transforms():
    return parse_transforms(TRANSFORMS)


def _frame(**kw):
    kw.setdefault('scene', '')
    kw.setdefault('scene_at', '')
    kw.setdefault('layers', '')
    kw.setdefault('layers_at', {})
    return StageFrame(index=0, line_id='t.0001', label='t', speaker='',
                      text='', **kw)


def _render(project, frame, transforms=None):
    index = transforms if transforms is not None else scan_transforms(project)
    got = render_frame(frame, project_root=project, transform_index=index)
    assert got is not None and got.image is not None
    return got, got.image.load()


class TestNoAtMeansCropLikeTheEngine:
    """无 at 的大图：引擎按原像素贴左上、右/下裁掉。**不许预览自动 fit**。"""

    def test_large_background_is_cropped_not_scaled(self, project):
        got, px = _render(project, _frame(scene='bg big'))
        # 画布 400×300，原图 800×600 → 只露出左上 1/4
        assert got.background_box == (0.0, 0.0, 800.0, 600.0)
        assert px[0, 0] == RED
        # 若被缩小到屏幕，右下角会是原图右下角的黄色；裁切则仍是左上红
        assert px[399, 299] == RED
        # 1:1 像素映射：原图 (350,250) 的标记点仍留在 (350,250)
        assert px[350, 250] == WHITE

    def test_smaller_background_keeps_black_bars(self, project):
        """原图比屏幕小 → 右/下留黑（引擎行为，不补边不放大）。"""
        got, px = _render(project, _frame(scene='bg small'))
        assert got.background_box == (0.0, 0.0, 300.0, 200.0)
        assert px[299, 199] == RED
        assert px[350, 250] == BLACK


class TestFitCover:
    def test_cover_fills_the_screen_and_crops_only_the_excess(self, project,
                                                              transforms):
        frame = _frame(scene='bg wide', scene_at='bgfit')
        got, px = _render(project, frame, transforms)
        # 1200×600 cover 到 400×300：比例 0.5 → 600×300，右边被裁
        assert got.background_box == (0.0, 0.0, 600.0, 300.0)
        assert px[0, 0] == RED
        # x=399 → 原图 x≈798（右半）→ 绿；屏幕高度正好 300，无黑边
        assert px[399, 0] == GREEN
        assert px[399, 299] == YELLOW

    def test_cover_square_image_crops_top_and_bottom(self, project, transforms):
        frame = _frame(scene='bg big', scene_at='bgfit')
        got, px = _render(project, frame, transforms)
        # 800×600 cover 到 400×300：比例 0.5 → 400×300，正好铺满
        assert got.background_box == (0.0, 0.0, 400.0, 300.0)
        assert px[0, 0] == RED
        assert px[399, 0] == GREEN
        assert px[0, 299] == BLUE
        assert px[399, 299] == YELLOW


class TestFitContain:
    def test_contain_keeps_the_whole_image_and_letterboxes(self, project,
                                                          transforms):
        got, px = _render(project, _frame(scene='bg wide',
                                         scene_at='bgcontain'), transforms)
        # 1200×600 contain 到 400×300：0.333 → 400×200，底部 100px 黑
        assert got.background_box[2:] == (400.0, 200.0)
        assert px[0, 0] == RED
        assert px[399, 0] == GREEN
        assert px[399, 199] == YELLOW      # 图片最后一行的右下
        assert px[399, 250] == BLACK       # letterbox

    def test_contain_tall_image_letterboxes_left_and_right(self, project,
                                                          transforms):
        got, px = _render(project, _frame(scene='bg tall',
                                         scene_at='bgcontain'), transforms)
        # 400×1200 contain 到 400×300：0.25 → 100×300
        assert got.background_box[2:] == (100.0, 300.0)
        assert px[10, 10] == RED          # 左半、上半
        assert px[10, 250] == BLUE        # 左半、下半
        assert px[200, 150] == BLACK      # 右侧 letterbox


class TestZoom:
    def test_zoom_scales_around_the_origin_when_no_pos_given(self, project,
                                                            transforms):
        got, px = _render(project, _frame(scene='bg wide', scene_at='half'),
                          transforms)
        # 1200×600 zoom 0.5 → 600×300（比屏幕宽）→ 右边裁掉
        assert got.background_box == (0.0, 0.0, 600.0, 300.0)
        assert px[0, 0] == RED
        # 屏幕 x=399 → 原图 x≈798（右半）→ 绿
        assert px[399, 0] == GREEN


class TestSceneAtInheritance:
    def test_second_beat_without_scene_still_has_fit(self, project, transforms):
        """R1 回归：fit 是持续状态，第 2 拍没写 scene 也按 fit 渲染。"""
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big', scene_at='bgfit'),
            Beat(seq=2, label='t', text=''),
            Beat(seq=3, label='t', text=''),
        ]
        frames = build_frames(beats, transform_index=transforms,
                              screen=SCREEN)
        assert frames[0].scene_at == 'bgfit'
        assert frames[1].scene_at == 'bgfit'
        assert frames[2].scene_at == 'bgfit'
        shots = [_render(project, f, transforms) for f in frames]
        for got, _px in shots:
            assert got.background_box == (0.0, 0.0, 400.0, 300.0)
        # 三拍背景像素应完全一致（同一张图、同一个 fit）
        assert shots[0][0].image.tobytes() == shots[1][0].image.tobytes()
        assert shots[1][0].image.tobytes() == shots[2][0].image.tobytes()

    def test_new_scene_drops_the_previous_fit(self, project, transforms):
        beats = [
            Beat(seq=1, label='t', text='', scene='bg big', scene_at='bgfit'),
            Beat(seq=2, label='t', text='', scene='bg wide'),
        ]
        frames = build_frames(beats, transform_index=transforms,
                              screen=SCREEN)
        assert frames[1].scene_at == ''
        got, _px = _render(project, frames[1], transforms)
        assert got.background_box == (0.0, 0.0, 1200.0, 600.0)


class TestLayerAt:
    def test_layer_with_at_is_scaled(self, project, transforms):
        frame = _frame(scene='bg big', layers='cg cg4k',
                       layers_at={'cg': 'half'})
        got, px = _render(project, frame, transforms)
        # 800×600 zoom 0.5 → 400×300，正好铺满：四角都是 CG 的颜色
        assert got.layer_boxes['cg'] == (0.0, 0.0, 400.0, 300.0)
        assert px[0, 0] == YELLOW
        assert px[399, 0] == RED
        assert px[0, 299] == GREEN
        assert px[399, 299] == BLUE

    def test_layer_without_at_is_cropped_like_the_engine(self, project,
                                                         transforms):
        frame = _frame(scene='bg big', layers='cg cg4k')
        got, px = _render(project, frame, transforms)
        assert got.layer_boxes['cg'] == (0.0, 0.0, 800.0, 600.0)
        # 原图 1:1 贴左上：屏幕右下角仍是原图的左上象限（黄）
        assert px[0, 0] == YELLOW
        assert px[399, 299] == YELLOW

    def test_layers_stack_in_order_later_on_top(self, project, transforms):
        """按快照的 tag 顺序叠：`cg` 先、`ui` 后 → 后画的盖住全屏。"""
        frame = _frame(scene='bg big', layers='cg cg4k; ui ui_a',
                       layers_at={'cg': 'half', 'ui': ''})
        got, px = _render(project, frame, transforms)
        assert px[0, 0] == BLUE
        assert px[399, 299] == BLUE

    def test_unknown_transform_is_not_guessed(self, project):
        """认不出的 at：按原图左上画 + 明说，**不猜一个居中位置**。"""
        frame = _frame(scene='bg big', scene_at='myfit_unknown')
        got, _px = _render(project, frame)
        assert got.background_box == (0.0, 0.0, 800.0, 600.0)
        assert any('认不出' in w for w in got.warnings), got.warnings


class TestFrameSummaryStaysHonest:
    def test_summary_does_not_claim_geometry_it_lost(self):
        """摘要只列图名/位置，不把 fit 说成"没有"（fit 走提示行）。"""
        frame = _frame(scene='bg big')
        assert 'bg big' in frame_summary(frame)


# ----------------------------------------------------------------------
#  P1：尺寸人话提示（只读、不替它 fit）
# ----------------------------------------------------------------------

def _notes(project, frame, transforms):
    from storygraph.app.stage_render import scale_notes
    return scale_notes(frame, project_root=project, screen=SCREEN,
                       transform_index=transforms)


class TestScaleNotes:
    def test_no_at_large_image_says_it_will_be_cropped(self, project,
                                                      transforms):
        notes = _notes(project, _frame(scene='bg big'), transforms)
        assert notes
        text = notes[0]
        assert '背景 bg big 800×600' in text
        assert '屏幕 400×300' in text
        assert '无 at' in text
        assert '裁切' in text

    def test_fit_cover_is_described_from_the_transform(self, project,
                                                      transforms):
        notes = _notes(project, _frame(scene='bg wide',
                                       scene_at='bgfit'), transforms)
        text = notes[0]
        assert 'at bgfit' in text
        assert 'fit "cover"' in text
        # 1200×600 cover 到 400×300 → 600×300，右边会被裁
        assert '裁切' in text

    def test_fit_contain_says_letterbox(self, project, transforms):
        notes = _notes(project, _frame(scene='bg wide',
                                       scene_at='bgcontain'), transforms)
        text = notes[0]
        assert 'fit "contain"' in text
        assert '留黑边' in text

    def test_layer_at_is_described(self, project, transforms):
        frame = _frame(scene='bg big', layers='cg cg4k',
                       layers_at={'cg': 'half'})
        notes = _notes(project, frame, transforms)
        layer_notes = [n for n in notes if n.startswith('CG')]
        assert layer_notes
        text = layer_notes[0]
        assert 'cg cg4k 800×600' in text
        assert 'zoom 0.5' in text
        assert '正好铺满' in text

    def test_unknown_transform_is_named_not_guessed(self, project, transforms):
        notes = _notes(project, _frame(scene='bg big',
                                       scene_at='myfit_unknown'), transforms)
        assert notes and '认不出' in notes[0]

    def test_missing_image_gives_no_size_claim(self, project, transforms):
        assert _notes(project, _frame(scene='bg nowhere'), transforms) == []


# ----------------------------------------------------------------------
#  P2：人工写回 at（只引用项目已有 transform；diff 确认；可撤销）
# ----------------------------------------------------------------------

def _at_project(tmp_path):
    """带 transform.rpy 的最小项目（P2 后端只读它取候选）。"""
    root = tmp_path / 'at_proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'transform.rpy').write_text(TRANSFORMS, encoding='utf-8')
    return root


class TestAtCandidates:
    def test_candidates_are_project_transforms_only(self, transforms):
        from storygraph.app.stage import at_transform_candidates
        got = at_transform_candidates(transform_index=transforms)
        names = [e['name'] for e in got]
        assert names == ['bgcontain', 'bgfit', 'half']   # 会改尺寸的在前
        bgfit = next(e for e in got if e['name'] == 'bgfit')
        assert bgfit['fit'] == 'cover'
        assert bgfit['changes_size'] is True
        assert 'fit "cover"' in bgfit['label']

    def test_empty_project_has_no_candidates(self, tmp_path):
        from storygraph.app.stage import at_transform_candidates
        assert at_transform_candidates(transform_index={}) == []


class TestAtEditPlan:
    def _beats(self):
        return [
            Beat(seq=1, label='t', text='', scene='bg big'),
            Beat(seq=2, label='t', text='', show='cg cg4k'),
            Beat(seq=3, label='t', text='', scene='bg wide',
                 scene_at='bgfit'),
            Beat(seq=4, label='t', text='', show='cg cg01 at bgfit behind ye'),
        ]

    def test_scene_edit_plans_applies_and_reverts(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 0, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.field == 'scene_at'
        assert edit.before == '' and edit.after == 'bgfit'
        assert 'scene bg big at bgfit' in edit.rpy_after
        assert 'scene_at' in edit.target_label
        assert '可撤销' in edit.diff_text()
        assert edit.apply()
        assert beats[0].scene_at == 'bgfit'
        assert edit.revert()
        assert beats[0].scene_at == ''

    def test_layer_edit_changes_the_show_at(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 1, target='layer', tag='cg',
                            transform='half', transform_index=transforms)
        assert edit.ok, edit.reason
        assert edit.field == 'show'
        assert edit.after == 'cg cg4k@half'
        assert 'show cg cg4k at half' in edit.rpy_after
        assert edit.apply()
        assert beats[1].show == 'cg cg4k@half'
        assert edit.revert()
        assert beats[1].show == 'cg cg4k'

    def test_only_project_transforms_are_allowed(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 0, target='scene', transform='nope',
                            transform_index=transforms)
        assert not edit.ok
        assert '项目里没有 transform' in edit.reason
        assert not edit.apply()
        assert beats[0].scene_at == ''

    def test_builtin_transform_is_not_a_project_transform(self, transforms):
        """`left` 是引擎内置、不是项目 transform —— 不写（只引用项目已有的）。"""
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 0, target='scene', transform='left',
                            transform_index=transforms)
        assert not edit.ok and '项目里没有 transform' in edit.reason

    def test_replanning_the_same_value_is_rejected(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 2, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert not edit.ok
        assert '已经是这个变换了' in edit.reason

    def test_scene_target_requires_a_background(self, transforms):
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 1, target='scene', transform='bgfit',
                            transform_index=transforms)
        assert not edit.ok and '没有背景' in edit.reason

    def test_complex_layer_clause_is_refused(self, transforms):
        """`behind ye` 这类复杂子句不在预览里猜（去高级区 Stage 手改）。"""
        from storygraph.app.stage import plan_at_edit
        beats = self._beats()
        edit = plan_at_edit(beats, 3, target='layer', tag='cg',
                            transform='half', transform_index=transforms)
        assert not edit.ok
        assert '复杂子句' in edit.reason

    def test_out_of_range_is_refused(self, transforms):
        from storygraph.app.stage import plan_at_edit
        edit = plan_at_edit(self._beats(), 999, target='scene',
                            transform='bgfit', transform_index=transforms)
        assert not edit.ok and '越界' in edit.reason


class TestPreviewEntryTargets:
    def test_entry_lists_background_and_layers(self):
        """「写回 at」的候选对象 = 背景 + 这一拍的每个图层（纯静态，不开窗口）。"""
        pytest.importorskip('PySide6')
        from storygraph.ui_qt.stage_preview import StagePreviewWindow
        frame = _frame(scene='bg big', layers='cg cg4k; ui ui_a',
                       layers_at={'cg': 'half', 'ui': ''})
        targets = StagePreviewWindow._at_targets_of(frame)
        labels = [t[0] for t in targets]
        data = [t[1] for t in targets]
        assert data[0] == ('scene', '')
        assert ('layer', 'cg') in data
        assert ('layer', 'ui') in data
        assert any('CG' not in label and '背景' in label for label in labels)
