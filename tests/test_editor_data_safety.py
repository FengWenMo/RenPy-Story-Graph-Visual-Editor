"""
tests/test_editor_data_safety.py
================================
**逐句校对不许改数据** —— 卡片编辑器的数据安全回归。

为什么单独开一个文件
--------------------
2026-09-30 用户报"说话人根本就不对！！！这是 chapter1_3"。拿真实数据
（`sr_general/game/chapter/chapter1_3.rpy`，305 拍）**逐句走一遍**当体检手段，
一口气挖出五类"静默改数据"的通道：

  1. 轮转把说话人写进**旁白拍** → `"旁白。"` 变成 `aaa "旁白。"`（32 处）
  2. 轮转把说话人写进**纯舞台拍**（`hide ling` 自成一行）→ 凭空多一句台词
  3. `beat.at` 被塞成"第一张立绘的位置"，渲染时又兜底套给**所有没写 `@`
     的立绘** → `show ye care` 变成 `show ye care at i11`（立绘错位）
  4. `Stage` 是结构化列的**投影**，被原样写回再 hydrate → `hide cg03`
     变成 `cg03; cg03`（305 拍里 11 拍被改写）
  5. 多属性立绘 `ling base_arms_akimbo sigh` 被"整串当成一个属性"归一
     → `sigh` 被吃掉（演出被改坏）

这些都不是"显示不对"，而是**改错数据**。所以这里逐条钉住。

2026-10-01：编辑语义搬进 `storygraph.app`（前端只剩 Qt），本文件随之
改为直接打**语义层**（`EditorSession.apply` / `app.beats`）。
用 `__new__` 造假控件的老写法全部删掉了 —— 那种测试"桩比真控件聪明"
（历史上正是它把"单行字段写不进去"藏了起来）。

验收标准（真实数据实测）：全章 305 拍逐句 `apply` 之后，`render_beats_to_rpy`
的产物**逐字节不变**（见 `TestWholeChapterIsByteIdentical`）。
"""

import pytest

from storygraph.story.beat_model import (
    Beat, KIND_EXTEND, KIND_MENU, KIND_NARRATION, KIND_SAY,
)
from storygraph.app.beats import (
    normalize_expression_words, uniform_sprite_position,
)


@pytest.fixture
def symbols():
    from storygraph.story.beat_render import make_symbols
    return make_symbols(None)


@pytest.fixture
def session(symbols):
    """一个不挂资产的会话（这些用例只关心舞台/立绘语义）。"""
    from storygraph.app.editor import EditorSession
    s = EditorSession(registry_provider=lambda: None)
    s.symbols = symbols
    return s


def _load_apply(session, beat, **over):
    """
    模拟前端的一次"载入 → 原样保存"：字段原封不动地写回。

    这正是逐句校对时最高频的动作，也是"自己吃自己"最容易发生的地方。
    """
    session.beats = [beat]
    fields = dict(session.fields(0))
    fields.update(over)
    return session.apply(0, fields, cards=[],
                         advanced_open=bool(over.get('_adv', True)),
                         stage_loaded=fields.get('Stage'))


# ==================================================================
#  一、翻页 = 纯导航（用户 2026-09-30 拍板）
# ==================================================================

class TestPagingIsPureNavigation:
    """
    > 回车不要自动填说话人！！！回车是下一张卡片的意思！！！

    上一轮只给轮转加了"三道闸"（不覆盖已有值 / 不写旁白 / 不写纯舞台拍），
    但用户要求的是**整个删掉** —— 只要它还挂在导航路径上，就有下次
    被改回来的机会。便利性改用「参与角色」双击（显式、只改当前拍）。

    现在导航住在 `MainWindowQt`（`next_beat` / `prev_beat`），
    这里锁"导航本身不碰数据"，并用**源码扫描**锁"轮转那套真的没了"。
    """

    def test_rotation_helpers_are_gone_from_the_codebase(self):
        """
        轮转（`_advance_speaker` / `_active_rotation` / `next_speaker` /
        `has_stage_content`）必须**在整套源码里都不存在** —— 不只是
        "类上没有这个方法"。只要还留着实现，就有下次被接回导航路径的机会。
        """
        import storygraph.app.beats as beats
        import storygraph.ui_qt.main_window as mw

        for mod, names in ((beats, ('next_speaker', 'has_stage_content',
                                    'advance_speaker', 'active_rotation')),
                           (mw, ('_advance_speaker', '_active_rotation'))):
            for name in names:
                assert not hasattr(mod, name), \
                    f'{mod.__name__} 里还留着轮转的痕迹：{name}'

    def test_navigation_does_not_touch_speaker(self):
        """
        纯导航的语义：`EditorSession` 里**没有任何"自动填说话人"的入口**。
        唯一的写说话人入口是 `set_speaker`（显式，由用户双击角色触发）。
        """
        from storygraph.app.editor import EditorSession
        import inspect

        src = inspect.getsource(EditorSession)
        assert 'rotation' not in src.lower(), \
            '会话里不该有轮转概念（说话人只能由用户显式指定）'


# ==================================================================
#  二、位置不许"兜底套给别人"（立绘错位）
# ==================================================================

class TestUniformSpritePosition:
    """
    `beat.at` 是**整拍兜底位置**，渲染时套给所有没写 `@` 的 show
    （见 `beat_render._stage_lines`）。所以它只能是"大家都一样"的那个值。
    """

    def test_all_same_position_keeps_it(self):
        rows = [{'at': 'i11'}, {'at': 'i11'}]
        assert uniform_sprite_position(rows) == 'i11'

    def test_different_positions_must_not_leak(self):
        """
        真实数据实测（chapter1_3）：`show ye happy at i11` + `show ye care`
        曾经被写成 `show ye care at i11` —— 第二个人凭空多了个位置。
        """
        rows = [{'at': 'i11'}, {'at': ''}]
        assert uniform_sprite_position(rows) == '', \
            '位置不统一时必须留空，否则会兜底套到别的立绘头上'

    def test_two_different_positions_are_ambiguous(self):
        assert uniform_sprite_position([{'at': 'left'}, {'at': 'right'}]) == ''

    def test_single_sprite(self):
        assert uniform_sprite_position([{'at': 'f11'}]) == 'f11'

    def test_no_rows(self):
        assert uniform_sprite_position([]) == ''
        assert uniform_sprite_position(None) == ''

    def test_chip_edit_only_touches_the_beat_being_edited(self):
        """
        改第 0 拍的位置，**不许**动第 1 拍 —— 位置是"整拍"的属性，
        而兜底路径最容易把它撒到邻居头上。
        """
        a = Beat(label='a', text='x', show='ye happy@i11; ye care')
        b = Beat(label='a', text='y', show='ling oh')
        from storygraph.app.editor import EditorSession
        s = EditorSession(registry_provider=lambda: None)
        s.beats = [a, b]
        cards = s.cards(0)
        cards[0]['position'] = 'i11'
        s.apply(0, s.fields(0), cards=cards, advanced_open=True)
        assert b.show == 'ling oh', '邻居的立绘被改了'


# ==================================================================
#  三、多属性立绘不能被压成一个属性
# ==================================================================

class TestMultiAttributeSprites:
    """
    `show ling base_arms_akimbo sigh` 是合法的多属性写法。卡片上的表情词
    本来就是逐属性拼的（`base_arms_akimbo 无语`），所以归一时必须**逐词**。
    整串归一 = 把 `sigh` 静默吃掉（2026-09-30 真实数据：chapter1_3 第 91 拍）。
    """

    def test_expr_with_unknown_word_does_not_match_a_thing(self, symbols):
        """`base_arms_akimbo` 没有中文词 → 原样保留；`无语` → `sigh`。"""
        words = normalize_expression_words(
            'base_arms_akimbo 无语', 'ling base_arms_akimbo sigh', symbols)
        assert words == ['base_arms_akimbo', 'sigh'] or words == [
            'base_arms_akimbo', '无语'], \
            f'逐词归一必须保住两个属性，实际 {words!r}'

    def test_single_word_is_unchanged(self, symbols):
        assert normalize_expression_words('oh', 'ling oh', symbols) == ['oh']

    def test_empty_expression(self, symbols):
        assert normalize_expression_words('', 'ling oh', symbols) == []
        assert normalize_expression_words('   ', 'ling oh', symbols) == []


# ==================================================================
#  四、写回：Stage 是投影（不许自己吃自己）
# ==================================================================

class TestApplyProjectionSafety:
    """
    `EditorSession.apply` 的字段范围与"投影不回灌"契约。

    这是原来用 `BeatEditorPanel.__new__` 造面板测的那一批 —— 现在直接打
    语义层（真 `EditorSession`，不是桩），测的是**同一批不变量**。
    """

    def test_scene_bgm_voice_comment_are_written_without_advanced(self, session):
        """
        **主区四项永远可编辑，就必须永远写回**。此前它们和高级区共用一个
        `_adv_open` 闸门，收起时改了**静默丢失**（默认就是收起）。
        """
        beat = Beat(label='c1', text='x', scene='bg bg1_2')
        session.beats = [beat]
        b = session.apply(0, {
            'Speaker': 'lyj', 'Text': 'x', 'Scene': 'bg bg9_5',
            'BGM': '[音乐] bgm/bgm03.mp3 fadein 2.0',
            'Voice': 'v_ling_001', 'Comment': '# 备注',
        }, cards=None, advanced_open=False)
        assert b.scene == 'bg bg9_5'
        assert b.bgm == '[音乐] bgm/bgm03.mp3 fadein 2.0'
        assert b.voice == 'v_ling_001'
        assert b.comment == '# 备注'

    def test_advanced_fields_are_left_alone_when_collapsed(self, session):
        """高级区收起 → 核心字段一个字都不许动（"改台词把转场弄没了"）。"""
        beat = Beat(label='c1', text='x', stage='[转场] dissolve',
                    sfx='se1', code='$ f()', kind=KIND_SAY)
        session.beats = [beat]
        b = session.apply(0, {'Speaker': 'jc', 'Text': 'y'},
                          cards=None, advanced_open=False)
        assert b.sfx == 'se1'
        assert b.code == '$ f()'

    def test_stage_projection_is_not_reabsorbed(self, session):
        """
        `Stage` 是结构化列的投影。把投影当输入再 hydrate 一次 →
        `hide cg03` 变成 `cg03; cg03`（逐句校对必然踩中）。
        """
        beat = Beat(label='c1', text='x', show='ling oh@i11', hide='cg03',
                    scene='bg bg6_2',
                    stage='[显示] 凌跃捷 oh at i11; hide cg03; scene bg bg6_2')
        session.beats = [beat]
        fields = dict(session.fields(0), Speaker='lyj')
        b = session.apply(0, fields, cards=[],
                          advanced_open=True,
                          stage_loaded=fields['Stage'])
        assert b.hide == 'cg03', f'退场指令被回灌成 {b.hide!r}'
        assert b.show == 'ling oh@i11', f'立绘被回灌成 {b.show!r}'
        assert b.scene == 'bg bg6_2'

    def test_hand_written_stage_is_authoritative(self, session):
        """
        用户真的在 Stage 框里改了字 → 以人写的为准，并且结构化舞台字段
        要让位（否则"旧投影 + 新文本"会叠加成两份指令）。
        """
        beat = Beat(label='c1', text='x', show='ling oh', hide='cg03')
        session.beats = [beat]
        b = session.apply(0, {'Speaker': 'lyj', 'Text': 'x',
                              'Stage': 'hide cg03'},
                          cards=[], advanced_open=True,
                          stage_loaded='show ling oh; hide cg03')
        assert b.stage == 'hide cg03'
        assert b.hide == 'cg03', '按人写的文本重新解析应得到同一条退场'
        assert b.show == '', '结构化立绘要让位给手写的高级舞台'

    def test_empty_stage_never_wipes_structured_columns(self, session):
        """
        把 Stage 框清空是很容易的误操作，它的后果却是抹掉这一拍的
        scene/show/hide —— 所以"空值"一律走**重新投影**，不算手改。
        （**永远不因为空 Stage 丢数据**。）
        """
        beat = Beat(label='c1', text='x', show='ling oh', hide='cg03',
                    scene='bg bg1')
        session.beats = [beat]
        # 前端给的是**完整表单**（cards 也一起给），只是 Stage 那一格被清空
        cards = session.cards(0)
        fields = dict(session.fields(0), Speaker='lyj', Stage='')
        b = session.apply(0, fields, cards=cards, advanced_open=True,
                          stage_loaded='show ling oh')
        assert b.show == 'ling oh'
        assert b.hide == 'cg03'
        assert b.scene == 'bg bg1'

    def test_stage_only_ops_survive_a_no_edit_roundtrip(self, session):
        """
        没改 Stage 时重投影，但"结构化列永远表达不了"的 `pause 1.0`
        必须原样留下 —— 否则打开卡片点一下下一句，作者写的东西就没了。
        """
        beat = Beat(label='c1', text='x', scene='bg bg1',
                    stage='scene bg bg1; pause 1.0')
        b = _load_apply(session, beat)
        assert 'pause 1.0' in b.stage, f'手写的 pause 被重投影吃掉：{b.stage!r}'
        assert b.scene == 'bg bg1'


# ==================================================================
#  五、端到端：整章逐句校对之后，生成结果逐字节不变
# ==================================================================

class TestWholeChapterIsByteIdentical:
    """
    这是本文件存在的**理由**，也是上面每一条的验收标准。

    拿真实章节（默认 `sr_general/game/chapter/chapter1_3.rpy`，305 拍）
    逐句"载入 → 原样写回"，然后比对 `render_beats_to_rpy` 的产物：
    **必须逐字节相同**。任何一个"静默改数据"的通道都会在这里现形。

    换机器时用 `STORYGRAPH_REAL_PROJECT` 指到自己那份；文件不存在就 skip。
    """

    def test_305_beats_roundtrip_byte_identical(self):
        import os
        from pathlib import Path

        root = Path(os.environ.get(
            'STORYGRAPH_REAL_PROJECT', r'D:\千连万理\RenPyTest\sr_general'))
        script = root / 'game' / 'chapter' / 'chapter1_3.rpy'
        if not script.exists():
            pytest.skip(f'本机没有真实工程：{script}')

        from storygraph.app.editor import EditorSession
        from storygraph.story.beat_render import render_beats_to_rpy

        session = EditorSession(registry_provider=lambda: None,
                                project_root=root)
        n = session.load(script)
        assert n > 100, f'真实章节应该有很多拍，实际 {n}'
        before = render_beats_to_rpy(session.beats, label_name=None,
                                     registry=None)

        for i in range(n):
            fields = dict(session.fields(i))
            session.apply(i, fields, cards=[],
                          advanced_open=True,
                          stage_loaded=fields.get('Stage'))

        after = render_beats_to_rpy(session.beats, label_name=None,
                                    registry=None)
        if before != after:
            import difflib
            diff = list(difflib.unified_diff(
                before.splitlines(), after.splitlines(),
                lineterm='', n=0))[:30]
            pytest.fail('逐句校对改了数据：\n' + '\n'.join(diff))
