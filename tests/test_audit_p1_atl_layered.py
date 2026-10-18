"""
审计 P1 的回归：ATL 的 `on show`（P1-4）与 layeredimage 的**部分失败**（P1-5）
================================================================================

P1-4（审计原话）："ATL 只算静态位置：`on show` / `parallel` / 动画帧整块忽略，
且静默退回'首字母猜位置'"。

P1-5："layeredimage 部分层失败仍判 ok：不认识的 displayable 只在 note 里，
调用方不会退回老路"。

两条都是"**看起来算出来了，其实是猜的**" —— 这类比直接报错坏得多，
因为下游（预览、卡片、舞台快照）会把它当事实用。判据因此钉在
"**认不出就必须说自己认不出**"上。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.atl import parse_transforms, placement_of  # noqa: E402


# ---------------------------------------------------------------------------
#  P1-4：`on show:` 的最后一帧就是静止态
# ---------------------------------------------------------------------------

ON_SHOW_SRC = '''
transform slide_in:
    on show:
        xoffset -120
        linear .3 xoffset 0
    xalign 0.5

transform anim_only:
    on show:
        xalign 1.0
        linear .2 xalign 0.2

transform plain:
    xalign 0.0
'''


def test_on_show_block_is_parsed():
    idx = parse_transforms(ON_SHOW_SRC)
    assert idx['slide_in'].on_blocks['show'] == ['xoffset -120',
                                                 'linear .3 xoffset 0']
    assert idx['plain'].on_blocks == {}


def test_on_show_last_frame_wins_for_placement():
    """ATL 顺序执行：`on show` 的**最后一帧**才是玩家看到的静止态。"""
    idx = parse_transforms(ON_SHOW_SRC)
    p = placement_of('anim_only', idx)
    assert p.from_atl, '位置没从 ATL 算出来（退回了猜）'
    assert abs(p.x - 0.2) < 1e-6, f'用的是第一帧 1.0 而不是最后一帧 0.2：{p.x}'


def test_on_show_fills_in_what_top_level_does_not_set():
    """`on show` 只动 xoffset、顶层给 xalign —— 两样都要生效。"""
    idx = parse_transforms(ON_SHOW_SRC)
    p = placement_of('slide_in', idx)
    assert p.from_atl
    assert abs(p.x - 0.5) < 1e-6, p.x
    assert abs(p.xoffset) < 1e-6, 'xoffset 应取动画结束时的 0'


# ---------------------------------------------------------------------------
#  P1-5：部分层失败 → ok=False（调用方才知道该退回老路）
# ---------------------------------------------------------------------------

_RPY = '''
layeredimage alpha:
    image_format 'images/alpha/{image}.png'

    group mood:
        attribute calm default:
            'calm'
        attribute glad:
            'glad'

    group outfit when tall:
        attribute tall:
            AlphaMask("images/alpha/tall.png", "images/alpha/_keep.png")
'''

_POSES = '''
init python:
    CYCLE = {"alpha": ["", "tall"]}
'''


def _project(tmp_path, files):
    game = tmp_path / 'game'
    (game / 'images' / 'alpha').mkdir(parents=True)
    for name in files:
        (game / 'images' / 'alpha' / name).write_bytes(b'x')
    (game / 'defs.rpy').write_text(_RPY, encoding='utf-8')
    (game / 'poses.rpy').write_text(_POSES, encoding='utf-8')
    return tmp_path


def test_complete_layers_are_ok(tmp_path):
    from storygraph.story.layered_model import load_layer_model, resolve_layers
    proj = _project(tmp_path, ['calm.png', 'glad.png', 'tall.png', '_keep.png'])
    m = load_layer_model(proj)
    plan = resolve_layers(m, 'alpha', ['tall', 'glad'], proj)
    assert plan.ok and len(plan.layers) == 2, plan


def test_one_missing_file_makes_the_whole_plan_not_ok(tmp_path):
    """
    少一张图 → `ok=False`。

    从前判据是"有层、且不是**全部**层都没成" —— 于是少一张图的立绘照样
    `ok=True`，调用方不会退回老路，画出来是**缺一块**的立绘。
    """
    from storygraph.story.layered_model import load_layer_model, resolve_layers
    proj = _project(tmp_path, ['calm.png', 'glad.png', 'tall.png'])  # 缺 _keep
    m = load_layer_model(proj)
    plan = resolve_layers(m, 'alpha', ['tall', 'glad'], proj)
    assert not plan.ok, f'三层里有一层找不到文件，却仍然 ok：{plan.layers}'
    assert any(ly.get('error') for ly in plan.layers), plan.layers


def test_unknown_displayable_makes_the_plan_not_ok(tmp_path):
    """不认识的 displayable 不许"只在 note 里"就算过。"""
    from storygraph.story.layered_model import load_layer_model, resolve_layers
    game = tmp_path / 'game'
    (game / 'images' / 'alpha').mkdir(parents=True)
    for name in ('calm.png', 'glad.png', 'tall.png'):
        (game / 'images' / 'alpha' / name).write_bytes(b'x')
    (game / 'defs.rpy').write_text(
        'layeredimage alpha:\n'
        "    image_format 'images/alpha/{image}.png'\n"
        '    group g:\n'
        '        attribute calm default:\n'
        '            Frobnicate("x.png")\n', encoding='utf-8')
    m = load_layer_model(game.parent)
    plan = resolve_layers(m, 'alpha', ['calm'], game.parent)
    assert not plan.ok
    assert any('Frobnicate' in str(ly.get('note')) for ly in plan.layers)
