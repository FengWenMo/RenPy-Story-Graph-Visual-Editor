"""
tests/test_stage_projection.py
==============================
**Stage ↔ 结构化列：互为投影，但必须单向、幂等、只输出一次。**

用户 2026-09-30 批准重构（原话："Stage 与结构化列的投影语义，批准重构！"）。

三条契约，缺哪一条都会**静默改数据**：

  1. **结构化字段是唯一真源**；`Stage` 只承载"结构化列表达不了"的东西
     （复杂 show 子句、原生逃生指令）。
  2. `hydrate` 必须**幂等**：Stage 里的 scene/show/hide 吸进结构化字段之后，
     再吸一遍**不许翻倍** —— `hide cg03` → `cg03; cg03` 就是这个洞
     （`_join_show_items` 早就有去重，**`_join_hide_items` 漏了**）。
  3. 渲染时同一条指令**只由一个通道输出**：结构化列先出，Stage 尾段按
     **语义**判断"这条已经被承载了"就跳过。不能靠"输出字符串完全相等"
     （中文化写法与原生写法生成出来的串不见得一样）。

真实数据实测（`sr_general`，重构依据）：
  · `chapter1_2.xlsx` 有 **55 行"立绘只写在 Stage 列"**（老写法）→
    必须照样吸进结构化字段，不能因为"迁到新列"就把老表读空
  · `chapter1_2` 有 2 条复杂 show（`show cg … behind ye`）→ 各输出**一次**
  · `chapter1_3` / `chapter1_4` 的 Stage 列已清空（迁移完成）
"""

import pytest

from storygraph.assets.asset_registry import AssetRegistry
from storygraph.story.beat_build import hydrate_from_dsl
from storygraph.story.beat_model import (
    Beat, _join_hide_items, _join_show_items, compose_stage,
    hide_item_parts, show_item_parts,
)
from storygraph.story.beat_render import (
    _stage_op_covered, make_symbols, render_beats_to_rpy,
)


@pytest.fixture
def symbols():
    reg = AssetRegistry()
    reg.characters = {'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」',
                              'ImageTag': 'ling'}}
    reg.images = {
        'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                       'Attributes': 'happy'},
    }
    return make_symbols(reg)


def _op(raw):
    """一个只带 `raw` 的假 Stage op（够 `_stage_op_covered` 用）。"""
    return type('Op', (), {'raw': raw})()


def _rehydrate(beat, symbols):
    """**绕过那道"同一段原文只吸一次"的缓存**再吸一遍。

    真实世界里这条路是走得到的：从 Excel 读回来（`from_row`）时
    `_hydrated_stage` 是空的，Stage 原文与结构化列同时在场 —— 那才是最
    容易翻倍的场合。测试要打的正是它，不是被缓存挡住的第二次调用。
    """
    try:
        beat._hydrated_stage = None
    except Exception:                        # noqa: BLE001
        pass
    hydrate_from_dsl([beat], symbols)


# ==================================================================
#  一、hide 合并必须去重（本次的真凶）
# ==================================================================

class TestHideJoinIsIdempotent:
    def test_same_hide_twice_collapses_to_one(self):
        items = [{'image': 'cg03', 'with': ''}, {'image': 'cg03', 'with': ''}]
        assert _join_hide_items(items) == 'cg03'

    def test_different_transition_is_a_different_thing(self):
        items = [{'image': 'ye', 'with': ''}, {'image': 'ye', 'with': 'dissolve'}]
        assert _join_hide_items(items) == 'ye; ye@dissolve'

    def test_show_join_is_unchanged(self):
        """show 那条路本来就带这个去重，别顺手改坏。"""
        items = [{'clause': '', 'image': 'ling happy', 'at': 'left'},
                 {'clause': '', 'image': 'ling happy', 'at': 'left'}]
        assert _join_show_items(items) == 'ling happy@left'

    def test_parts_encoding_is_shared(self):
        """两种编码（`图像@变换` 与复杂子句）只有一份实现。"""
        assert show_item_parts('ling happy@left; cg cg2 at t behind ye') == {
            'ling happy@left', 'cg cg2 at t behind ye'}
        assert hide_item_parts('ye; lan@dissolve') == {'ye', 'lan@dissolve'}


# ==================================================================
#  二、hydrate 必须幂等
# ==================================================================

class TestHydrateIsIdempotent:
    def test_stage_projection_absorbed_twice_does_not_double(self, symbols):
        """
        `chapter1_3` 实测：305 拍里 11 拍因为这条被改写
        （`hide cg03` → `cg03; cg03`，逐句校对必然踩中）。
        """
        b = Beat(label='a', text='x',
                 stage='show ling happy at left; hide cg03; scene bg bg6_2')
        _rehydrate(b, symbols)
        first = (b.show, b.hide, b.scene, b.stage_order)
        assert b.hide == 'cg03'
        assert b.show == 'ling happy@left'
        assert b.scene == 'bg bg6_2'

        _rehydrate(b, symbols)
        assert (b.show, b.hide, b.scene, b.stage_order) == first, \
            '同一段 Stage 原文吸两遍，结构化字段必须一模一样'

    def test_stage_only_sprites_are_still_absorbed(self, symbols):
        """老表（`chapter1_2` 那 55 行）：立绘**只**写在 Stage 里，必须吸进来。"""
        b = Beat(label='a', text='x', show='', hide='',
                 stage='show ling happy at left')
        _rehydrate(b, symbols)
        assert b.show == 'ling happy@left', \
            'Stage 里的立绘必须能进结构化列（否则老表迁移就是"读空"）'

    def test_clause_show_is_absorbed_as_clause(self, symbols):
        b = Beat(label='a', text='x',
                 stage='show cg cg02_2 at cg38402160 behind ye')
        _rehydrate(b, symbols)
        assert 'behind' in b.show, f'复杂子句必须原样保留，实际 {b.show!r}'


# ==================================================================
#  三、渲染：同一条指令只输出一次
# ==================================================================

class TestStageTailDoesNotDoubleEmit:
    def _stage_lines(self, out):
        return [l.strip() for l in out.splitlines()
                if l.strip().startswith(('scene ', 'show ', 'hide '))]

    def test_native_projection_renders_each_op_once(self, symbols):
        b = Beat(label='a', text='x',
                 stage='show ling happy at left; hide cg03; scene bg bg6_2')
        _rehydrate(b, symbols)
        out = render_beats_to_rpy([b], symbols=symbols)
        lines = self._stage_lines(out)
        for want in ('show ling happy at left', 'hide cg03', 'scene bg bg6_2'):
            assert lines.count(want) == 1, f'{want!r} 出现了 {lines.count(want)} 次'

    def test_clause_show_renders_once(self, symbols):
        """真实数据里的复杂 show：结构化列与 Stage 都会"知道"它，但只能出一次。"""
        b = Beat(label='a', text='x',
                 stage='show cg cg02_2 at cg38402160 behind ye')
        _rehydrate(b, symbols)
        out = render_beats_to_rpy([b], symbols=symbols)
        hits = [l for l in out.splitlines() if 'behind ye' in l]
        assert len(hits) == 1, f'复杂 show 被输出 {len(hits)} 次：{hits}'

    def test_stage_only_ops_are_not_swallowed(self, symbols):
        """Stage 独有的东西（`pause` / 认不出的指令）**不许被吞**。"""
        b = Beat(label='a', text='x', stage='pause 1.0; hide cg03')
        out = render_beats_to_rpy([b], symbols=symbols)
        assert 'pause 1.0' in out
        assert 'hide cg03' in out


# ==================================================================
#  四、"已被结构化列承载"的判定（语义比对，不是字符串比对）
# ==================================================================

class TestStageOpCovered:
    def test_plain_show_covered(self):
        assert _stage_op_covered('show ling happy', _op('show ling happy'),
                                 Beat(), show_item_parts('ling happy'),
                                 set()) is True

    def test_native_show_with_at_is_covered(self):
        """原生 `show X at Y` 与结构化列的 `X@Y` 是同一件事（两种编码都要认）。"""
        assert _stage_op_covered(
            'show ling happy at left', _op('show ling happy at left'),
            Beat(), show_item_parts('ling happy@left'), set()) is True

    def test_hide_covered(self):
        assert _stage_op_covered('hide cg03', _op('hide cg03'), Beat(),
                                 set(), hide_item_parts('cg03')) is True

    def test_scene_covered_only_when_it_matches(self):
        b = Beat(scene='bg bg5_0', scene_at='bgfit')
        assert _stage_op_covered('scene bg bg5_0 at bgfit',
                                 _op('scene bg bg5_0 at bgfit'), b,
                                 set(), set()) is True
        assert _stage_op_covered('scene bg bg5_0', _op('scene bg bg5_0'), b,
                                 set(), set()) is False, \
            '背景变换对不上就不是同一条指令'

    def test_pause_is_never_covered(self):
        assert _stage_op_covered('pause 1.0', _op('pause 1.0'), Beat(),
                                 set(), set()) is False

    def test_unknown_verb_is_never_covered(self):
        """认不出来的一律**不跳过**：宁可多一行，也不能吞掉作者手写的东西。"""
        assert _stage_op_covered('renpy.foo()', _op('renpy.foo()'), Beat(),
                                 set(), set()) is False

    def test_todo_code_never_covered(self):
        assert _stage_op_covered('# TODO: 缺少资源 立绘 x',
                                 _op('# TODO: 缺少资源 立绘 x'), Beat(),
                                 set(), set()) is False


# ==================================================================
#  六、Stage 里"表达不了"的东西不许被重投影吃掉
# ==================================================================

class TestStageOnlyOpsSurvive:
    """
    编辑器每次提交都会**重新投影** `beat.stage`（结构化列是唯一真源）。
    重投影只该重写"结构化列表达得了的"部分；`[停顿] 1.0` 这类只能写在
    Stage 里的指令必须原样留下 —— 否则"打开卡片、点一下下一句"就丢了。
    """

    def test_pause_is_kept(self, symbols):
        from storygraph.story.beat_build import stage_only_ops
        assert 'pause 1.0' in stage_only_ops('[停顿] 1.0', symbols)

    def test_expressible_ops_are_dropped(self, symbols):
        from storygraph.story.beat_build import stage_only_ops
        assert stage_only_ops(
            'show ling happy at left; [显示] 凌跃捷 开心 at f11',
            symbols) == ''

    def test_native_pause_kept_alongside_expressible(self, symbols):
        from storygraph.story.beat_build import stage_only_ops
        out = stage_only_ops('show ling happy; pause 1.0', symbols)
        assert 'pause 1.0' in out
        assert 'show' not in out

    def test_empty_is_empty(self, symbols):
        from storygraph.story.beat_build import stage_only_ops
        assert stage_only_ops('', symbols) == ''
        assert stage_only_ops('   ', symbols) == ''


# ==================================================================
#  七、`compose_stage` 必须与渲染器同源
# ==================================================================

class TestComposeStageMatchesRenderer:
    """
    "表里看到的"和"生成的 .rpy"必须一致 —— 不一致就是"我明明看见的是
    这个，生成出来却是那个"。
    """

    def test_scene_uses_scene_at_not_the_sprite_position(self):
        b = Beat(scene='bg bg9_5', scene_at='bgfit', at='i11')
        text = compose_stage(b)
        assert 'scene bg bg9_5 at bgfit' in text, \
            '背景自己的变换是 scene_at，不是立绘的 at'
        assert 'at i11' not in text

    def test_show_falls_back_to_the_beat_level_position(self):
        """立绘自己没写 `@` 时，整拍的「位置」列生效（与渲染器一致）。"""
        b = Beat(show='ling happy', at='left')
        assert 'show ling happy at left' in compose_stage(b)

    def test_with_attaches_to_the_last_show_when_there_is_no_scene(self):
        b = Beat(show='ling happy', with_='dissolve')
        assert compose_stage(b).endswith('show ling happy with dissolve')

    def test_with_stays_on_the_scene_when_there_is_one(self):
        b = Beat(scene='bg room', show='ling happy', with_='dissolve')
        assert 'scene bg room with dissolve' in compose_stage(b)


# ==================================================================
#  五、顺序列不全时**看到的就是生成的**（用户 2026-10-05 实测报）
# ==================================================================

class TestStageVerbsInventTheMissingVerbs:
    """
    用户原话：

    > 我已经保存该卡片了，可是当前舞台变化没有生效，并且舞台演出预览没有变化

    卡片上加一张立绘只写 `beat.show`，而 `stage_order` / `op_order` 还是
    解析时那一拍的 `'scene'`（原文只有 `scene black`）：

      · **渲染器**会把漏掉的 stage verb 补在后面（"绝不因为顺序信息不全就
        丢掉操作"）→ 产物里 `show ling angry at center` ✓
      · **舞台快照 / 演出预览**从前只回放列出来的 verb → 「立绘：（台上没人）」

    同一次保存两种真相 = "看到的 ≠ 生成的"（`compose_stage` 那条契约最贵的一类）。
    现在两边共用 `Beat.stage_verbs`。
    """

    def test_missing_show_is_appended_to_the_order(self):
        b = Beat(scene='black', show='ling angry', stage_order='scene',
                 op_order='scene')
        assert b.stage_verbs == ['scene', 'show']

    def test_recorded_order_wins_over_the_fallback(self):
        """顺序仍按记下来的真迹排（`hide` 在 `show` 前面就不许被重排）。"""
        b = Beat(show='ling happy', hide='ling sad',
                 stage_order='hide,show', op_order='hide,show')
        assert b.stage_verbs == ['hide', 'show']

    def test_no_order_info_falls_back_to_the_historical_order(self):
        b = Beat(scene='bg room', show='ling happy', hide='cg03')
        assert b.stage_verbs == ['scene', 'show', 'hide']

    def test_sync_writes_it_back_without_losing_the_recorded_part(self):
        b = Beat(scene='black', show='ling angry', stage_order='scene',
                 op_order='scene')
        assert b.sync_stage_order() == 'scene,show'
        # 再同步一次不该翻倍
        assert b.sync_stage_order() == 'scene,show'

    def test_everything_without_content_is_not_invented(self):
        b = Beat(scene='black', stage_order='scene', op_order='scene')
        assert b.stage_verbs == ['scene'], '没有 show 就别凭空补一个'

    def test_the_snapshot_and_the_renderer_agree(self, symbols):
        """
        端到端：顺序列停在 `'scene'`，但卡片上加了立绘 ——
        **快照（当前舞台/预览的数据源）与产物必须都看到这个人**。
        """
        from storygraph.story.stage_snapshot import snapshot_rows
        b = Beat(seq=1, label='a', kind='say', speaker='e', text='一句',
                 scene='black', show='ling happy', stage_order='scene',
                 op_order='scene')
        row = snapshot_rows([b])[0]
        assert 'ling happy' in row['actors'], (
            f'快照没看到刚加的立绘：{row["actors"]!r}')
        text = render_beats_to_rpy([b], label_name='a')
        assert 'show ling happy' in text, text
