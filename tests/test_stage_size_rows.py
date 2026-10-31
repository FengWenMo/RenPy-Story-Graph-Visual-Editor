"""
tests/test_stage_size_rows.py
=============================
**「尺寸」一格的数据**：这一拍能调尺寸的对象 + 预设匹配 + 写回计划
（2026-10-31，用户二轮反馈"bg/cg 不能改像素"）。

三层一起钉：

  ① `app.stage_render.size_rows` —— 背景 + 各 CG/UI 图层各一行，
     行说明写清"文件多大 / 引擎怎么画 / 现在是裁切还是留黑边 / 第几拍设的"；
  ② 每行的预设（铺满 / 适应 / 50%）**先按几何匹配项目已有 transform**，
     匹配不到就 `ok=False` + 一句人话（**不编 transform**）；
  ③ `app.stage.plan_size_edit`（含 `plan_at_edit(mode='clear')`）：
     "改尺寸"写的是**最初写下这一行的那一拍**；「原样」= 去掉 at，不是套空 transform。

真机依据（`sr_general`）：1013 个有背景的拍里 972 个的背景是**继承来的**，
只认本拍字段的话那个入口 96% 按不下去。
"""

from __future__ import annotations

import pytest

from PIL import Image

from storygraph.app.stage import StageFrame, plan_size_edit
from storygraph.app.stage_render import available, size_rows
from storygraph.story.beat_model import Beat

pytestmark = pytest.mark.skipif(not available(), reason='没装 Pillow')

SCREEN = (400, 300)

GUI = '''init python:
    gui.init(400, 300)
'''

TRANSFORMS = '''transform bgfit:
    fit "cover"

transform bgcontain:
    fit "contain"

transform half:
    zoom 0.5

transform anim:
    zoom 0.5
    on show:
        easein .2 alpha 1.0
'''

SCRIPT = '''image bg big = "images/big.png"
image bg wide = "images/wide.png"
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
        game / 'images' / 'wide.png')
    Image.new('RGBA', (1600, 600), (0, 0, 255, 255)).save(
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
    raise AssertionError(f'没有这项预设：{key}；有：{ [i["key"] for i in row["items"]] }')


def _pick(row, name):
    for item in row['items']:
        if item['key'] == 'pick' and item['at'] == name:
            return item
    raise AssertionError(f'「自定义」里没有 {name}')


# ==================================================================
#  ① 行：谁在台上、多大、第几拍设的
# ==================================================================

class TestRows:
    def test_scene_row_carries_the_size_note(self, project):
        rows = _rows(_frame(scene='bg big'), project)
        assert len(rows) == 1
        row = rows[0]
        assert (row['kind'], row['tag'], row['spec']) == ('scene', '', 'bg big')
        assert row['label'] == '背景'
        assert row['natural'] == (800, 600)
        assert '800×600 → 屏幕 400×300' in row['note']
        assert '会被裁切' in row['note']        # 800×600 在 400×300 屏上就是裁
        assert '无 at' in row['note']

    def test_layer_rows_are_listed_with_their_own_at(self, project):
        rows = _rows(_frame(layers='cg cg1; ui hud',
                            layers_at={'cg': 'half'}), project)
        # `ui hud` 找不到图 → 仍然有一行（尺寸未知），但不猜尺寸
        assert [r['kind'] for r in rows] == ['layer', 'layer']
        cg = rows[0]
        assert (cg['tag'], cg['spec'], cg['at']) == ('cg', 'cg cg1', 'half')
        assert 'at half' in cg['note'] and 'zoom 0.5' in cg['note']
        assert rows[1]['tag'] == 'ui'
        assert rows[1]['natural'] is None
        assert '尺寸未知' in rows[1]['note']

    def test_source_is_named_when_it_is_not_this_beat(self, project):
        rows = _rows(_frame(index=5, scene='bg big', scene_src=3), project)
        assert rows[0]['source'] == 3
        assert rows[0]['note'].endswith('（第 4 拍设的）')

    def test_source_is_not_repeated_on_the_beat_that_wrote_it(self, project):
        rows = _rows(_frame(index=3, scene='bg big', scene_src=3), project)
        assert '拍设的' not in rows[0]['note']

    def test_zero_is_a_real_source(self, project):
        """第 0 拍写的 = 0（别用 `or -1` 把它说成"不知道"）。"""
        rows = _rows(_frame(index=4, scene='bg big', scene_src=0), project)
        assert rows[0]['source'] == 0
        assert '（第 1 拍设的）' in rows[0]['note']

    def test_layer_rows_are_capped(self, project):
        rows = _rows(_frame(scene='bg big',
                            layers='cg cg1; ui a; cg b; cg c; cg d'),
                     project)
        assert len(rows) == 1 + 3            # SIZE_ROW_LAYERS = 3

    def test_no_scene_no_layers_means_no_rows(self, project):
        assert _rows(_frame(), project) == []

    def test_frame_none_is_empty(self, project):
        assert size_rows(None, project_root=project, screen=SCREEN) == []

    def test_an_inherited_line_says_so_in_the_tooltip(self, project):
        """跨章继承（出处 -1）：行照样显示尺寸，但 tooltip 先说清会发生什么。"""
        rows = _rows(_frame(scene='bg big'), project)      # scene_src 缺省 -1
        assert rows[0]['source'] == -1
        # 2026-10-31 起不是死路：按「改」= **本章第一拍补一条 show**。
        assert '上一章' in rows[0]['why']
        assert '本章第一拍' in rows[0]['why'] and '补一条' in rows[0]['why']


# ==================================================================
#  ② 预设：先匹配项目已有的 transform；匹配不到不编
# ==================================================================

class TestPresetItems:
    def test_cover_and_contain_find_their_transforms(self, project):
        row = _rows(_frame(scene='bg big'), project)[0]
        cover, contain = _item(row, 'cover'), _item(row, 'contain')
        assert cover['at'] == 'bgfit' and cover['ok']
        assert 'fit "cover"' in cover['text']
        assert contain['at'] == 'bgcontain' and contain['ok']

    def test_half_prefers_the_zoom_transform(self, project):
        row = _rows(_frame(scene='bg big'), project)[0]
        half = _item(row, 'half')
        assert half['at'] == 'half', half
        assert 'zoom 0.5' in half['text']

    def test_as_is_is_disabled_when_there_is_no_at(self, project):
        row = _rows(_frame(scene='bg big'), project)[0]
        item = _item(row, 'as_is')
        assert item['ok'] is False and item['current'] is True
        assert '本来就没有 at' in item['why']

    def test_as_is_is_enabled_when_there_is_an_at(self, project):
        row = _rows(_frame(scene='bg big', scene_at='bgfit'), project)[0]
        item = _item(row, 'as_is')
        assert item['ok'] is True and item['current'] is False

    def test_the_current_transform_is_marked(self, project):
        row = _rows(_frame(scene='bg big', scene_at='bgfit'), project)[0]
        cover = _item(row, 'cover')
        assert cover['current'] is True and cover['ok'] is False
        assert cover['why'] == '已经是这个变换了'

    def test_animated_transforms_are_not_offered(self, project):
        row = _rows(_frame(scene='bg big'), project)[0]
        names = [i['at'] for i in row['items'] if i['key'] == 'pick']
        assert 'anim' not in names, names
        assert 'bgfit' in names

    def test_unmatched_preset_offers_to_generate_and_writes_nothing(
            self, project):
        """只有 bgfit 的项目里，8:3 的图要「适应」→ 提议生成 `sg_fit_contain`。

        **只算不写**：光是翻到这一拍不该碰磁盘（确认之后才写）——
        这是"生成"这件事最容易做错的地方。
        """
        game = project / 'game'
        (game / 'transform.rpy').write_text(
            'transform bgfit:\n    fit "cover"\n', encoding='utf-8')
        row = _rows(_frame(scene='bg wide'), project)[0]
        item = _item(row, 'contain')
        assert item['ok'] is True, item
        assert item['at'] == 'sg_fit_contain'
        assert item['generate'] is True
        assert '生成 sg_fit_contain' in item['text']
        assert 'sg_transforms.rpy' in item['note']
        assert not (game / 'sg_transforms.rpy').exists(), \
            '只是翻到这一拍就写了文件'

    def test_a_hand_written_file_is_not_offered_and_says_why(self, project):
        """`game/sg_transforms.rpy` 不是工具生成 → 不提议生成，说清原因。"""
        game = project / 'game'
        (game / 'transform.rpy').write_text(
            'transform bgfit:\n    fit "cover"\n', encoding='utf-8')
        (game / 'sg_transforms.rpy').write_text(
            '# 我自己写的\ntransform mine:\n    zoom 2\n', encoding='utf-8')
        row = _rows(_frame(scene='bg wide'), project)[0]
        item = _item(row, 'contain')
        assert item['ok'] is False and item['at'] == ''
        assert item['generate'] is False
        assert '不是工具生成的' in item['why']

    def test_missing_image_only_offers_as_is_and_picks(self, project):
        row = _rows(_frame(layers='cg cg_missing'), project)[0]
        cover = _item(row, 'cover')
        assert cover['ok'] is False
        assert '算不出尺寸' in cover['why']
        assert _pick(row, 'bgfit')['ok'] is True


# ==================================================================
#  ③ 计划：写回最初那一拍 / 「原样」= 去掉 at
# ==================================================================

def _beats():
    """4 拍：背景第 0 拍设、CG 第 2 拍 show，之后都是继承。"""
    return [
        Beat(seq=1, label='t', text='', scene='bg big'),
        Beat(seq=2, label='t', text=''),
        Beat(seq=3, label='t', text='', show='cg cg1'),
        Beat(seq=4, label='t', text=''),
    ]


def _scene_row(project, at=''):
    return _rows(_frame(index=3, scene='bg big', scene_at=at,
                        scene_src=0), project)[0]


def _layer_row(project, at=''):
    return _rows(_frame(index=3, layers='cg cg1', layers_at={'cg': at},
                        layer_src={'cg': 2}), project)[0]


class TestPlanSizeEdit:
    def test_cover_writes_the_source_beat(self, project):
        beats = _beats()
        row = _scene_row(project)
        edit = plan_size_edit(beats, 3, row, key='cover', at='bgfit',
                              project_root=project, screen=SCREEN)
        assert edit.ok, edit.reason
        assert edit.beat_index == 0 and edit.requested_index == 3
        assert edit.field == 'scene_at' and edit.after == 'bgfit'
        assert edit.affects == (0, 3)          # 影响第 1–4 拍
        assert edit.apply()
        assert beats[0].scene_at == 'bgfit'
        assert beats[3].scene_at == ''         # 继承的那一拍字段没被改

    def test_as_is_clears_the_at(self, project):
        beats = _beats()
        beats[0].scene_at = 'bgfit'
        row = _scene_row(project, at='bgfit')
        edit = plan_size_edit(beats, 3, row, key='as_is',
                              project_root=project, screen=SCREEN)
        assert edit.ok, edit.reason
        assert edit.clear is True and edit.transform == ''
        assert edit.after == ''
        assert '原样' in edit.target_label and '去掉 at' in edit.target_label
        assert '原样' in edit.diff_text()
        assert edit.apply()
        assert beats[0].scene_at == ''

    def test_as_is_on_a_clean_line_is_refused(self, project):
        row = _scene_row(project)              # 本来就没有 at
        edit = plan_size_edit(_beats(), 3, row, key='as_is',
                              project_root=project, screen=SCREEN)
        assert not edit.ok
        assert '原样' in edit.reason

    def test_layer_as_is_strips_only_that_items_at(self, project):
        beats = [Beat(seq=1, label='t', text='', show='cg cg1@half; ui hud')]
        row = _rows(_frame(index=0, layers='cg cg1; ui hud',
                           layers_at={'cg': 'half', 'ui': ''},
                           layer_src={'cg': 0, 'ui': 0}), project)[0]
        edit = plan_size_edit(beats, 0, row, key='as_is',
                              project_root=project, screen=SCREEN)
        assert edit.ok, edit.reason
        assert edit.field == 'show'
        assert edit.after == 'cg cg1; ui hud'
        assert edit.apply()
        assert beats[0].show == 'cg cg1; ui hud'

    def test_layer_cover_writes_the_source_beat(self, project):
        beats = [Beat(seq=1, label='t', text=''),
                 Beat(seq=2, label='t', text='', show='cg cg1')]
        row = _layer_row(project)              # 说明里 source 默认是第 2 拍
        row['source'] = 1
        edit = plan_size_edit(beats, 1, row, key='cover', at='bgfit',
                              project_root=project, screen=SCREEN)
        assert edit.ok, edit.reason
        assert edit.apply()
        assert beats[1].show == 'cg cg1@bgfit'

    def test_no_row_is_refused(self, project):
        edit = plan_size_edit(_beats(), 0, {}, key='cover', at='bgfit',
                              project_root=project)
        assert not edit.ok and '没有选中' in edit.reason

    def test_missing_transform_is_refused(self, project):
        row = _scene_row(project)
        edit = plan_size_edit(_beats(), 3, row, key='cover', at='',
                              project_root=project, screen=SCREEN)
        assert not edit.ok and '没有可写的 transform' in edit.reason

    def test_a_show_bg_line_says_where_to_edit_instead(self, project):
        """背景是 `show bg X` 换的（不是 scene）：说清去高级区改那条 show。

        这种行没有 `scene_at` 可改 —— 从前会报"这一拍没有背景"，人对着
        一个有背景的拍看到这句话只会以为工具坏了。
        """
        beats = [Beat(seq=1, label='t', text='', show='bg big@bgfit'),
                 Beat(seq=2, label='t', text='')]
        row = _rows(_frame(index=1, scene='bg big', scene_src=0), project)[0]
        edit = plan_size_edit(beats, 1, row, key='cover', at='bgcontain',
                              project_root=project, screen=SCREEN)
        assert not edit.ok
        assert 'show bg' in edit.reason and '高级区' in edit.reason

    def test_an_inherited_background_is_added_at_the_first_beat(
            self, project):
        """
        跨章继承（出处 -1）：不猜一个拍号 —— 在本章**第一拍**补一条 `show`。

        （2026-10-31 之前这里是"拒绝"；现在它是唯一能改的路：出处不在本剧本
        里，改不了"那一行"，写 `scene` 又会清台。判据仍是**不猜**：落点明写
        第 1 拍，且只加一条 `show`。）
        """
        beats = [Beat(seq=1, label='t', text='')]      # 这一章没有任何 scene
        row = _rows(_frame(index=0, scene='bg big'), project)[0]
        edit = plan_size_edit(beats, 0, row, key='cover', at='bgfit',
                              project_root=project, screen=SCREEN)
        assert edit.ok, edit.reason
        assert edit.inserted is True
        assert edit.beat_index == 0
        assert edit.after == 'bg big@bgfit'          # 不带 at：**不写 scene**
        assert '补一条' in edit.diff_text()

    def test_an_inherited_layer_is_added_at_the_first_beat(self, project):
        beats = [Beat(seq=1, label='t', text='')]
        row = _rows(_frame(index=0, layers='cg cg1'), project)[0]
        edit = plan_size_edit(beats, 0, row, key='cover', at='bgfit',
                              project_root=project, screen=SCREEN)
        assert edit.ok, edit.reason
        assert edit.inserted is True and edit.target == 'layer'
        assert edit.after == 'cg cg1@bgfit'
