"""
tests/test_transition_catalog.py
================================
**项目转场目录**（`storygraph.story.transition_catalog`）的判据。

用户的过场是项目自己 `define` 的特效（`Dissolve` / `Fade` / `Pixellate` /
`ImageDissolve` / `MultipleTransition` …），不是"淡入"。这一层把
`define 名字 = <表达式>` 递归解析成 `Effect`，预览据此演化；**认不出就
`kind='unknown'` + label 写明"无法精确预览"**，绝不假装淡入。

判据分四类：
  ① **解析得对**：时长 / 颜色 / ramp / 多段递归 / 名字引用；
  ② **诚实**：未知构造器、认不出的 define、只读的字面量配置、状态型列表项；
  ③ **通用**：临时目录里另造一个**不同布局、不同命名**的项目也能扫出来
    （证明没有写死 `sr_general` 的任何东西）；
  ④ **只读**：扫完项目文件一个字节都不变。
"""

from __future__ import annotations

import hashlib

import pytest

from storygraph.story.transition_catalog import (
    Effect, describe_effect, effect_of, parse_define_transitions,
    scan_project_transitions)
from storygraph.story.transition_expr import (
    parse_python_transition, parse_with_clause)

PROJECT_SRC = '''\
# 自定义转场（仿真实项目 transform.rpy 的写法：define 一个名字，章节里 with/renpy.transition 用）
define dissolve = Dissolve(0.25)
define fade = Fade(0.5)
define flicker = Fade(0.5, 0.0, 0.5, "#400")
define pixellate = Pixellate(0.5, 0.5)
define wipe = ImageDissolve("images/menu/wipe.png", 0.5, ramplen=64)
define wipe2 = ImageDissolve(image="images/wipe2.png", time=1.25)

define tpause = Pause(0.25)
define black = Solid("#000")

# 组合转场：状态 / 过渡交替（False/True/Solid 是状态，不是过渡段）
define dissolve_scene_full = MultipleTransition([
    False, Dissolve(1.0),          # 出
    Solid("#000"), Pause(1.0),     # 黑着停 1 秒
    Solid("#000"), Dissolve(1.0),  # 入
    True])

define combo = MultipleTransition([False, dissolve, Solid("#000"), tpause, True])
define blink = MultipleTransition([False, True])
define alias_dissolve = dissolve
define same_line = Dissolve(0.3)   # 尾注释
define variable_time = Dissolve(delay_var)
define weird = WeirdTransition(0.5)
define silent_move = MoveTransition(0.4)
define crop = CropMove(0.6)
define composed = ComposeTransition(Dissolve(0.2), Fade(0.2))
define atl_trans = lambda x: x

# 下面这些不是转场：纯字面量配置 / gui 命名空间
define text_font = "fonts/regular.ttf"
define gui.text_size = 33
define config.fade = dissolve
'''


def _write_project(root, src: str = PROJECT_SRC,
                   rel: str = 'game/scripts/transitions.rpy'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding='utf-8')
    return root


@pytest.fixture
def project(tmp_path):
    return _write_project(tmp_path / 'proj')


@pytest.fixture
def catalog(project):
    return scan_project_transitions(project)


# ---------------------------------------------------------------------------
#  ① 解析得对
# ---------------------------------------------------------------------------

def test_dissolve_duration(catalog):
    effect = catalog['dissolve']
    assert (effect.kind, effect.duration) == ('dissolve', 0.25)
    assert effect.label


def test_fade_color_and_total_duration(catalog):
    """`Fade(0.5)`：出 0.5 + 停 0 + 入 0.5 = 1 秒，默认色黑。"""
    fade = catalog['fade']
    assert (fade.kind, fade.duration, fade.color) == ('fade', 1.0, '#000')
    assert (fade.out_time, fade.hold_time, fade.in_time) == (0.5, 0.0, 0.5)
    flicker = catalog['flicker']
    assert (flicker.kind, flicker.duration, flicker.color) == \
        ('fade', 1.0, '#400')
    # 三段时长要带出来：渲染引擎据此演"淡出 → 停 → 淡入"，不把总时长均分
    assert (flicker.out_time, flicker.hold_time, flicker.in_time) == \
        (0.5, 0.0, 0.5)


def test_pixellate_duration(catalog):
    effect = catalog['pixellate']
    assert (effect.kind, effect.duration) == ('pixellate', 0.5)


def test_image_dissolve_ramp(catalog):
    """`ImageDissolve`：遮罩图（ramp）与 ramplen 都要带出来。"""
    wipe = catalog['wipe']
    assert (wipe.kind, wipe.duration) == ('imagedissolve', 0.5)
    assert wipe.ramp == 'images/menu/wipe.png'
    assert wipe.ramplen == 64
    wipe2 = catalog['wipe2']                  # 关键字写法也要认
    assert (wipe2.kind, wipe2.duration) == ('imagedissolve', 1.25)
    assert wipe2.ramp == 'images/wipe2.png'


def test_multiple_transition_segments(catalog):
    """`MultipleTransition` 递归：只看过渡段，状态（False/Solid/True）不算。"""
    effect = catalog['dissolve_scene_full']
    assert effect.kind == 'multiple'
    assert effect.duration == 3.0
    assert [seg.kind for seg in effect.segments] == \
        ['dissolve', 'pause', 'dissolve']
    assert [seg.duration for seg in effect.segments] == [1.0, 1.0, 1.0]
    assert '组合' in effect.label


def test_name_reference_and_nested_multiple(catalog):
    """`define A = dissolve`、`MultipleTransition([…, dissolve, …])` 要递归解析。"""
    alias = catalog['alias_dissolve']
    assert (alias.kind, alias.duration) == ('dissolve', 0.25)
    combo = catalog['combo']
    assert combo.kind == 'multiple'
    assert [seg.kind for seg in combo.segments] == ['dissolve', 'pause']
    assert combo.duration == 0.5


def test_dotted_define_name(catalog):
    effect = catalog['config.fade']
    assert (effect.kind, effect.duration) == ('dissolve', 0.25)


# ---------------------------------------------------------------------------
#  ② 诚实（不猜 / 不假装淡入）
# ---------------------------------------------------------------------------

def test_unknown_constructor_is_unknown_not_fade(catalog):
    effect = catalog['weird']
    assert effect.kind == 'unknown'
    assert 'WeirdTransition' in effect.label
    assert '无法精确预览' in effect.label
    assert effect.duration == 0.0


def test_unparseable_definition_is_still_listed_as_unknown(catalog):
    """认不出的 `define`（ATL / lambda…）不能当"项目里没有"处理。"""
    effect = catalog['atl_trans']
    assert effect.kind == 'unknown'
    assert 'atl_trans' in effect.label
    assert '无法精确预览' in effect.label


def test_variable_duration_is_not_guessed(catalog):
    """`Dissolve(delay_var)` 的时长是变量 → 0（未知），不能编一个数。"""
    effect = catalog['variable_time']
    assert effect.kind == 'dissolve'
    assert effect.duration == 0.0
    assert '无法精确预览' in effect.label


def test_literal_config_definitions_are_skipped(catalog):
    for name in ('text_font', 'gui.text_size'):
        assert name not in catalog


def test_move_like_constructors_are_named_but_unknown(catalog):
    for name, marker in (('silent_move', 'MoveTransition'),
                         ('crop', 'CropMove'),
                         ('composed', 'ComposeTransition')):
        effect = catalog[name]
        assert effect.kind == 'unknown', name
        assert marker in effect.label
        assert '无法精确预览' in effect.label


def test_multiple_without_transition_segments_is_honest(catalog):
    effect = catalog['blink']                 # [False, True] 全是状态
    assert effect.kind == 'multiple'
    assert effect.segments == ()
    assert '没有可识别的过渡段' in effect.label


# ---------------------------------------------------------------------------
#  ③ 调用点 → Effect（预览真正消费的入口）
# ---------------------------------------------------------------------------

def test_effect_of_with_clause_resolves_project_name(catalog):
    call = parse_with_clause('with dissolve_scene_full')[0]
    effect = effect_of(call, catalog)
    assert (effect.kind, effect.duration) == ('multiple', 3.0)
    assert [seg.kind for seg in effect.segments] == \
        ['dissolve', 'pause', 'dissolve']


def test_effect_of_code_call_keeps_layer(catalog):
    call = parse_python_transition(
        '$ renpy.transition(wipe, layer="master")')[0]
    assert call.layer == 'master'
    effect = effect_of(call, catalog)
    assert effect.kind == 'imagedissolve'
    assert effect.ramp == 'images/menu/wipe.png'


def test_effect_of_unknown_name_is_honest(catalog):
    call = parse_with_clause('with no_such_transition')[0]
    effect = effect_of(call, catalog)
    assert effect.kind == 'unknown'
    assert 'no_such_transition' in effect.label
    assert '无法精确预览' in effect.label


def test_effect_of_inline_builtin_constructor_does_not_need_catalog():
    """裸构造器调用（没 define 过）也能给出效果；目录只服务"名字引用"。"""
    call = parse_with_clause('with Dissolve(0.4)')[0]
    effect = effect_of(call, {})
    assert (effect.kind, effect.duration) == ('dissolve', 0.4)


def test_describe_effect_is_human_readable(catalog):
    assert '闪黑' in describe_effect(catalog['fade'])
    assert '组合' in describe_effect(catalog['dissolve_scene_full'])
    assert '无法精确预览' in describe_effect(catalog['weird'])
    assert describe_effect(None) == '（无过场）'
    fallback = Effect(kind='dissolve', duration=0.3)
    assert describe_effect(fallback) == '溶解 0.3s'


# ---------------------------------------------------------------------------
#  ④ 通用性：另造一个布局/命名都不同的项目
# ---------------------------------------------------------------------------

def test_generality_other_project_layout_and_names(tmp_path):
    """
    没有 `game/` 目录、文件放在 `scripts/`、名字是中文 —— 照样能扫。

    这条专门钉"通用性"：实现里不许出现 `sr_general` / `game/` 这类假设。
    """
    root = tmp_path / '青空物语'
    (root / 'scripts' / 'fx').mkdir(parents=True)
    (root / 'scripts' / 'fx' / 'transitions.rpy').write_text(
        'define 溶解 = Dissolve(0.15)\n'
        'define 黑场 = Fade(1.0, 0.5, 1.0, "#000")\n'
        'define 组合 = MultipleTransition([False, 溶解, Solid("#000"), '
        'Pause(0.2), True])\n',
        encoding='utf-8')
    effects = scan_project_transitions(root)
    assert set(effects) == {'溶解', '黑场', '组合'}
    assert (effects['溶解'].kind, effects['溶解'].duration) == \
        ('dissolve', 0.15)
    assert (effects['黑场'].kind, effects['黑场'].duration) == ('fade', 2.5)
    combo = effects['组合']
    assert [seg.kind for seg in combo.segments] == ['dissolve', 'pause']
    call = parse_with_clause('with 黑场')[0]
    assert effect_of(call, effects).kind == 'fade'


def test_parse_define_transitions_handles_comments_and_multiline():
    nodes = parse_define_transitions(
        'define a = Dissolve(0.2)   # 尾注释\n'
        'define b = MultipleTransition([\n'
        '    False, Dissolve(0.5),  # 注释里的 ) ] ,\n'
        '    True])\n'
        'define c = 1\n')
    assert set(nodes) == {'a', 'b'}
    assert nodes['b'] is not None


# ---------------------------------------------------------------------------
#  ⑤ 只读
# ---------------------------------------------------------------------------

def test_scan_does_not_modify_the_project(tmp_path):
    root = _write_project(tmp_path / 'ro')
    files = sorted(p for p in root.rglob('*.rpy'))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in files}
    scan_project_transitions(root)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in files}
    assert before == after, '扫描必须纯读：项目文件一个字节都不许变'
