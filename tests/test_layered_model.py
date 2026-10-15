"""
tests/test_layered_model.py
===========================
**合成立绘（layeredimage）通用模型** + **空姿势必须 hide**。

设计文档：`docs/archive/设计_合成立绘合成与姿势切换_2026-10-05.md`

⚠ 通用性护栏（设计文档 6.3）：这里的 fixture **一个真实项目的名字都不用**
—— 角色叫 `alpha`/`beta`，属性叫 `mood`/`tall`，蒙版叫 `_cut`/`_keep`，
专门用来证明"换个项目也能认"。
"""

from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
#  自造项目（通用性护栏：没有一个真实项目的名字）
# ----------------------------------------------------------------------

_RPY = '''
layeredimage alpha:
    image_format 'images/alpha/{image}.png'

    group mood when not tall and not gown:
        attribute calm default:
            'calm'
        attribute glad:
            'glad'
        attribute sad:
            'sad'

    group mood when tall:
        attribute calm default:
            part('calm', '_cut.png')
        attribute glad:
            part('glad', '_cut.png')

    group outfit when tall:
        attribute tall:
            outfit_piece('tall.png', '_keep.png')

    group outfit when gown:
        attribute gown:
            outfit_piece('gown.png', '_gown_keep.png')

init -1 python:
    def part(expr, cut_mask="_cut.png"):
        """表情差分：用蒙版只保留面部。"""
        return AlphaMask(
            "images/alpha/" + expr + ".png",
            "images/alpha/" + cut_mask
        )

    def outfit_piece(img, keep_mask):
        return AlphaMask(
            "images/alpha/" + img,
            "images/alpha/" + keep_mask
        )
'''

_POSES = '''
init python:
    CYCLE = {
        "alpha": ["", "tall", "gown"],
        "beta": ["", "sitting"],
    }
    CYCLE_LABEL = {
        "": "普通",
        "tall": "高个",
        "gown": "礼服",
        "sitting": "坐姿",
    }
'''


@pytest.fixture()
def project(tmp_path):
    game = tmp_path / 'game'
    (game / 'images' / 'alpha').mkdir(parents=True)
    for name in ('calm.png', 'glad.png', 'sad.png', 'tall.png', 'gown.png',
                 '_cut.png', '_keep.png', '_gown_keep.png'):
        (game / 'images' / 'alpha' / name).write_bytes(b'x')
    (game / 'defs.rpy').write_text(_RPY, encoding='utf-8')
    (game / 'poses.rpy').write_text(_POSES, encoding='utf-8')
    return tmp_path


# ----------------------------------------------------------------------
#  ① 解析
# ----------------------------------------------------------------------

class TestParsing:
    def test_it_reads_groups_attributes_and_image_format(self, project):
        from storygraph.story.layered_model import load_layer_model
        m = load_layer_model(project)
        img = m.images.get('alpha')
        assert img is not None, m.images.keys()
        assert img.image_format == 'images/alpha/{image}.png'
        conds = {g.condition for g in img.groups}
        assert 'not tall and not gown' in conds and 'tall' in conds
        assert set(img.all_attrs()) == {'calm', 'glad', 'sad', 'tall', 'gown'}

    def test_it_reads_the_helper_functions(self, project):
        from storygraph.story.layered_model import load_layer_model
        m = load_layer_model(project)
        assert 'part' in m.helpers and 'outfit_piece' in m.helpers


class TestConditionEvaluation:
    @pytest.mark.parametrize('expr,attrs,want', [
        ('not tall and not gown', set(), True),
        ('not tall and not gown', {'tall'}, False),
        ('tall', {'tall'}, True),
        ('tall or gown', {'gown'}, True),
        ('gown', {'tall'}, False),
        ('', set(), True),
    ])
    def test_known_expressions(self, expr, attrs, want):
        from storygraph.story.layered_model import evaluate_condition
        assert evaluate_condition(expr, attrs) is want

    def test_unparsable_returns_none_not_true(self):
        """认不出 → None（**绝不当成成立**，那样会画出错的层）。"""
        from storygraph.story.layered_model import evaluate_condition
        assert evaluate_condition('len(x) > 2', set()) is None
        assert evaluate_condition('a and b or (', set()) is None


# ----------------------------------------------------------------------
#  ② 展开 displayable（多行 return + 辅助函数 + AlphaMask）
# ----------------------------------------------------------------------

class TestDisplayableExpansion:
    def test_helper_with_multiline_return(self, project):
        from storygraph.story.layered_model import (expand_displayable,
                                                    load_layer_model)
        m = load_layer_model(project)
        out = expand_displayable("part('glad', '_cut.png')", m.helpers,
                                 'images/alpha/{image}.png')
        assert len(out) == 1, out
        assert out[0]['path'].endswith('glad.png'), out
        assert out[0]['mask'].endswith('_cut.png'), out

    def test_default_parameter_is_used(self, project):
        from storygraph.story.layered_model import (expand_displayable,
                                                    load_layer_model)
        m = load_layer_model(project)
        out = expand_displayable("part('calm')", m.helpers,
                                 'images/alpha/{image}.png')
        assert out[0]['mask'].endswith('_cut.png'), out

    def test_image_format_substitution(self, project):
        from storygraph.story.layered_model import (expand_displayable,
                                                    load_layer_model)
        m = load_layer_model(project)
        out = expand_displayable("'sad'", m.helpers, 'images/alpha/{image}.png')
        assert out[0]['path'] == 'images/alpha/sad.png', out

    def test_unknown_displayable_is_reported_not_guessed(self):
        from storygraph.story.layered_model import expand_displayable
        out = expand_displayable('Frobnicate("a.png")', {}, '')
        assert out and out[0].get('path') is None
        assert 'Frobnicate' in out[0]['note']

    def test_composite_expands_instead_of_crashing(self):
        """
        ⚠ 2026-10-10 静态网（`test_no_undefined_names`）抓到的真缺陷：
        `_expand_composite_call` 的签名上挂着一个 `idx`，调用点也写了 `..., idx)`
        —— 可那个作用域里**根本没有 `idx`**，于是任何 `Composite(...)`
        都是 `NameError`（而不是"展开失败"）。这条判据钉住"能展开、不抛"。

        格式按官方文档：`Composite(size, (x,y), 图, (x,y), 图, …)`
        —— 真机 `definitions.rpy` 里就是这个形状（一行文件里 9 处）。
        """
        from storygraph.story.layered_model import expand_displayable
        out = expand_displayable(
            'Composite((300, 600), (0, 0), "body.png", (50, 60), "face.png")',
            {}, '')
        assert [o.get('path') for o in out] == ['body.png', 'face.png'], out
        assert [o.get('offset') for o in out] == [(0, 0), (50, 60)], out

    def test_live_composite_expands_all_layers(self):
        """真机 `effects.rpy`：`LiveComposite((1280,720), (0,0), "a", (0,0), "b")`。"""
        from storygraph.story.layered_model import expand_displayable
        out = expand_displayable(
            'LiveComposite((1280, 720), (0, 0), "a.png", (0, 0), "b.png")',
            {}, '')
        assert [o.get('path') for o in out] == ['a.png', 'b.png'], out

    def test_composite_with_a_single_layer_is_not_dropped(self):
        """
        ⚠ 旧实现把这一支整幅丢掉（返回"没展开出层"）：
        位置元组后面只有一个图 → 循环条件 `idx + 1 < len(args) + 1` 直接不成立。

        `offset` 是 `None`（= 没写位置）而不是 `(0,0)` —— 消费方把两者当同一件事
        （`app/layer_compose.py` 里 `ly.get('offset') or (0, 0)`），
        所以这里**不**假装我们知道坐标。
        """
        from storygraph.story.layered_model import expand_displayable
        out = expand_displayable('Composite((300, 600), "only.png")', {}, '')
        assert [o.get('path') for o in out] == ['only.png'], out
        assert not out[0]['offset'], out


# ----------------------------------------------------------------------
#  ③ 叠层：按组选、按顺序叠
# ----------------------------------------------------------------------

class TestResolveLayers:
    def test_no_outfit_uses_the_full_body_expression(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    resolve_layers)
        m = load_layer_model(project)
        plan = resolve_layers(m, 'alpha', ['glad'], project)
        assert plan.ok and len(plan.layers) == 1, plan
        assert str(plan.layers[0]['path']).endswith('glad.png')
        assert plan.pose == ''

    def test_outfit_plus_expression_gives_two_layers(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    resolve_layers)
        m = load_layer_model(project)
        plan = resolve_layers(m, 'alpha', ['tall', 'glad'], project)
        assert plan.ok and len(plan.layers) == 2, plan
        face, body = plan.layers
        assert str(face['path']).endswith('glad.png') and \
            str(face['mask']).endswith('_cut.png')
        assert str(body['path']).endswith('tall.png') and \
            str(body['mask']).endswith('_keep.png')
        assert plan.pose == 'tall'

    def test_the_default_attribute_is_used_when_none_is_given(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    resolve_layers)
        m = load_layer_model(project)
        plan = resolve_layers(m, 'alpha', ['tall'], project)
        assert plan.layers and str(plan.layers[0]['path']).endswith('calm.png'), plan

    def test_a_tag_without_layeredimage_is_not_guessed(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    resolve_layers)
        m = load_layer_model(project)
        plan = resolve_layers(m, 'nobody', ['x'], project)
        assert plan.ok is False and plan.layers == []
        assert any('layeredimage' in u for u in plan.unresolved), plan


# ----------------------------------------------------------------------
#  ④ 姿势维度 + 空姿势
# ----------------------------------------------------------------------

class TestPoseDimension:
    def test_pose_table_is_detected_by_shape(self, project):
        from storygraph.story.layered_model import load_layer_model
        m = load_layer_model(project)
        assert m.poses.get('alpha') == ['', 'tall', 'gown'], m.poses
        # ⚠ **不是 layeredimage tag 的键必须被忽略**（通用性护栏：
        # 项目里别的同名 dict 不许被当成姿势表）
        assert 'beta' not in m.poses, m.poses

    def test_pose_labels_are_read(self, project):
        from storygraph.story.layered_model import load_layer_model
        m = load_layer_model(project)
        labels = {}
        for _n, table in m.pose_labels.items():
            labels.update(table)
        assert labels.get('') == '普通', m.pose_labels
        assert labels.get('tall') == '高个', m.pose_labels

    def test_pose_can_be_derived_from_groups_without_a_table(self, project):
        """没有循环表时，从 `when` 条件里推出姿势维度。"""
        from storygraph.story.layered_model import load_layer_model
        m = load_layer_model(project)
        m.poses = {}                       # 把表拿掉
        assert m.pose_options('alpha') == ['', 'gown', 'tall'], \
            m.pose_options('alpha')


class TestEmptyPoseNeedsHide:
    def test_switching_back_to_the_empty_pose_is_reported(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    pose_switch_problem)
        m = load_layer_model(project)
        p = pose_switch_problem(m, 'alpha', ['tall', 'glad'], ['glad'])
        assert p and p['kind'] == 'empty_pose_needs_hide', p
        assert p['from'] == 'tall' and p['to'] == ''
        assert p['to_label'] == '普通'
        assert 'hide alpha' in p['advice'], p['advice']

    def test_switching_between_two_poses_is_fine(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    pose_switch_problem)
        m = load_layer_model(project)
        assert pose_switch_problem(m, 'alpha', ['tall', 'glad'],
                                   ['gown', 'glad']) is None

    def test_staying_on_the_empty_pose_is_fine(self, project):
        from storygraph.story.layered_model import (load_layer_model,
                                                    pose_switch_problem)
        m = load_layer_model(project)
        assert pose_switch_problem(m, 'alpha', ['glad'], ['sad']) is None

    def test_frame_warnings_skip_frames_that_hide_first(self, project):
        """用户自己写了 `hide alpha` 的拍**不该再报**（那是正确写法）。"""
        from storygraph.story.layered_model import (frame_pose_warnings,
                                                    load_layer_model)

        class _Sp:
            def __init__(self, image):
                self.image = image

        class _Frame:
            def __init__(self, sprites, hide=''):
                self.sprites = sprites
                self.hide = hide
                self.hides = []

        m = load_layer_model(project)
        frames = [
            _Frame([_Sp('alpha tall glad')]),
            _Frame([_Sp('alpha glad')]),                    # ✗ 没 hide
            _Frame([_Sp('alpha tall glad')]),
            _Frame([_Sp('alpha glad')], hide='alpha'),      # ✓ 有 hide
        ]
        warns = frame_pose_warnings(frames, m)
        assert [i for i, _p in warns] == [1], warns
