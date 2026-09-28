"""
tests/test_fidelity_bytewise.py
===============================
往返保真的**分级**验收（见 `docs/定位重审.md` §7.1）。

三级判据，从可判定的强到弱：

  **L1 结构化投影逐字节一致** —— 「立绘」列产生的 `show` 语句必须逐字节相同。
      这是工具自己写出来的形态，没有借口。

  **L2 全量语义等价** —— 整章的**每一类**指令条数一致、`at` 位置不丢。
      允许"形态不同但演出一样"（Ren'Py 里 `show ye_hi` 与 `show ye hi` 等价）。

  **L3 差异清单** —— 剩余差异逐条列出并定性，见 §7.1。

为什么不再用"整文件逐字节相同"当唯一标准：那条标准把
"形态不同但语义等价"也算失败，结果永远关不掉。判据改成
**"没有未定性的差异"**。

本文件里的用例按下面分工：

  · `TestStructuredProjectionBytewise` —— L1：立绘条数 / StageOrder / 归一后形态
  · `TestSemanticEquivalence`          —— L2：每类条数 + 位置 + 同类内顺序
  · `TestCrossKindOrder`               —— **差异 2 已修**：跨类别交错顺序（正向断言）
  · `TestOpOrderFallback`              —— 没有顺序信息时**不许丢操作**
  · `TestKnownDifferencesArePinned`    —— 差异 1 仍然存在，钉住它

还剩的 L3 差异（§7.1）：

  1. 图像名形态：无资源表时 `show ye hi` → `show ye_hi`（语义等价）
  2. ~~跨类别顺序~~ —— **已修**（`Beat.op_order`，2026-09-28）
  3. 行尾内联注释位置
  4. `define Character(...)` / `transform` 块不在往返链路里
"""

import re
from pathlib import Path

import pytest

from script_table import (
    Beat, import_beats_from_excel, open_rpy_as_table, render_beats_to_rpy,
)

DEMO_SCRIPT = (Path(__file__).resolve().parent.parent
               / 'demo' / 'game' / 'script' / 'ch01.rpy')

VERBS = ('scene', 'show', 'hide', 'play', 'queue', 'stop', 'voice')
LINE_RE = re.compile(r'^\s*(%s)\s+(.+?)\s*$' % '|'.join(VERBS))


def statement_seq(text):
    """所有舞台/音频语句，按出现顺序 → [(动词, 余下内容)]。"""
    out = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if m:
            out.append((m.group(1), re.sub(r'\s+', ' ', m.group(2)).strip()))
    return out


def counts(text, verb):
    return sum(1 for l in text.splitlines() if l.strip().split()[:1] == [verb])


def show_specs(text):
    """只取 show 的图像名部分（去掉 at/with 后缀），用于比形态。"""
    out = []
    for verb, rest in statement_seq(text):
        if verb == 'show':
            out.append(re.split(r'\s+(?:at|with)\s+', rest)[0])
    return out


# ==================================================================
#  L1：结构化投影逐字节一致
# ==================================================================

class TestStructuredProjectionBytewise:
    """
    L1 的口径（比"整文件逐字节"窄，但**可判定**）：

      · 立绘的**条数**必须一致；
      · 每一拍的舞台指令**顺序**（StageOrder）必须一致；
      · 无资源表时形态按下划线归一后必须一致。

    这几条都是工具自己写的，没有借口不达标。
    """

    @pytest.fixture
    def roundtrip(self, tmp_path):
        if not DEMO_SCRIPT.exists():
            pytest.skip('demo 章节不在仓库里')
        table = tmp_path / 'ch01.xlsx'
        open_rpy_as_table(DEMO_SCRIPT, table)
        beats = import_beats_from_excel(table)
        return DEMO_SCRIPT.read_text(encoding='utf-8'), render_beats_to_rpy(beats)

    def test_show_count_matches_exactly(self, roundtrip):
        original, produced = roundtrip
        assert counts(original, 'show') == counts(produced, 'show')

    def test_show_specs_match_after_underscore_normalization(self, roundtrip):
        """
        无资源表时图像名写成下划线形态（L3 差异 1）。
        归一之后**必须逐字节一致**——否则就是真的改了演出。
        """
        original, produced = roundtrip

        def norm(xs):
            return [s.replace('_', ' ') for s in xs]

        assert norm(show_specs(original)) == norm(show_specs(produced))

    def test_stage_order_is_preserved(self, roundtrip):
        """`hide` + `show`（重置立绘）的顺序不能倒。"""
        original, produced = roundtrip

        def stage_verbs(text):
            return [v for v, _ in statement_seq(text)
                    if v in ('scene', 'show', 'hide')]

        assert stage_verbs(original) == stage_verbs(produced)

    def test_no_chinese_image_tag_in_product(self, roundtrip):
        _original, produced = roundtrip
        for line in produced.splitlines():
            parts = line.strip().split()
            if parts[:1] in (['show'], ['hide'], ['scene']) and len(parts) > 1:
                assert all(ord(c) < 128 for c in parts[1]), line


# ==================================================================
#  L2：全量语义等价
# ==================================================================

#: 刻意**不**在同一拍里交错 stage 与音频——那是 L3 差异 2（见文件头）。
#: 这里测的是"同一类指令内部"的保真。
CHAPTER = '''label t:
    scene bg room
    show e a
    e "one"
    show e b at left
    e "two"
    hide e
    show e c at right
    hide e with dissolve
    show f x at left
    e "four"
    play music theme fadein 1.0
    stop music
    voice v_001
    return
'''


class TestSemanticEquivalence:
    """形态可以不同，但**每一类的条数与位置**必须一致。"""

    @pytest.fixture
    def roundtrip(self, write_rpy, tmp_path):
        src = write_rpy(CHAPTER, 'proj/game/t.rpy')
        table = tmp_path / 't.xlsx'
        open_rpy_as_table(src, table)
        return render_beats_to_rpy(import_beats_from_excel(table))

    def test_statement_counts_match(self, roundtrip):
        for verb in ('show', 'hide', 'scene', 'play', 'stop', 'voice'):
            assert counts(CHAPTER, verb) == counts(roundtrip, verb), \
                f'{verb} 条数变了'

    def test_positions_survive(self, roundtrip):
        for pos in ('left', 'right'):
            assert f'at {pos}' in roundtrip

    def test_each_kind_keeps_its_own_order(self, roundtrip):
        """同类指令内部的相对顺序是演出语义，必须一致。"""
        for verb in ('show', 'hide', 'scene', 'play', 'stop'):
            a = [rest for v, rest in statement_seq(CHAPTER) if v == verb]
            b = [rest for v, rest in statement_seq(roundtrip) if v == verb]
            # 形态归一（下划线 ↔ 空格）后比较顺序
            assert [x.replace('_', ' ') for x in a] == \
                [x.replace('_', ' ') for x in b], f'{verb} 的顺序变了'


# ==================================================================
#  L3：把还剩的已知差异钉住（改好了就来改这里）
# ==================================================================

class TestKnownDifferencesArePinned:
    """
    这些用例**故意断言"当前是有差异的"**。

    它们的作用是：差异一旦被修掉，测试会失败，提醒人来更新文档——
    避免"以为修好了其实没有"或者"修好了没人知道"。

    差异 2（跨类别顺序）**已经修掉**，所以它的"钉住用例"搬去了
    `TestCrossKindOrder`，改成正向断言。
    """

    @pytest.fixture
    def roundtrip(self, tmp_path):
        if not DEMO_SCRIPT.exists():
            pytest.skip('demo 章节不在仓库里')
        table = tmp_path / 'ch01.xlsx'
        open_rpy_as_table(DEMO_SCRIPT, table)
        beats = import_beats_from_excel(table)
        return DEMO_SCRIPT.read_text(encoding='utf-8'), render_beats_to_rpy(beats)

    def test_L3_1_image_form_differs_without_registry(self, roundtrip):
        """差异 1：无资源表时 `show ye hi` → `show ye_hi`（语义等价）。

        口径见 §7.1：**有资源表时按"能否解析"择优**，无资源表才统一成
        下划线形态。归一之后必须逐字节一致，所以它不是"改了演出"。
        """
        original, produced = roundtrip
        a, b = show_specs(original), show_specs(produced)
        assert a != b, '图像名形态忽然一致了——请更新 §7.1 的差异清单'
        assert [x.replace('_', ' ') for x in a] == \
            [x.replace('_', ' ') for x in b]

    def test_image_form_is_stable_when_registry_resolves(self, tmp_path):
        """
        有资源表、且作者的写法能解析出来时，**必须原样保留**——
        `show eileen happy` 不许被改写成 `eileen_happy`。
        """
        from asset_registry import AssetRegistry
        from script_table import registry_from_parse_result
        from renpy_parser import ParseResultBuilder, RenPyParser

        script = tmp_path / 'r.rpy'
        script.write_text(
            'label t:\n    scene bg room\n    show eileen happy\n    e "x"\n',
            encoding='utf-8')
        reg = AssetRegistry()
        reg.images = {
            'bg_room': {'ID': 'bg_room', 'Tag': 'bg',
                        'Attributes': 'room', 'Path': 'images/bg/room.png'},
            'eileen_happy': {'ID': 'eileen_happy', 'Tag': 'eileen',
                             'Attributes': 'happy',
                             'Path': 'images/eileen/happy.png'},
        }
        table = tmp_path / 'r.xlsx'
        open_rpy_as_table(script, table, registry=reg)
        produced = render_beats_to_rpy(
            import_beats_from_excel(table, registry=reg), registry=reg)
        assert 'show eileen happy' in produced, produced
        assert 'eileen_happy' not in produced, \
            f'能解析出来的写法被改写坏了：\n{produced}'

class TestCrossKindOrder:
    """
    差异 2（**已修**，2026-09-28）：同一拍里 stage / 音频 / 语音的**交错顺序**。

    修之前：渲染固定按 `舞台 → 音频 → 语音` 输出，原文里写在 `show` 之前的
    `play music` 会跑到立绘后面。同一次交互最终画面一样，但**回放与时间轴
    看到的顺序是错的**——顺序本身就是演出语义（"音乐先起还是立绘先亮"）。

    修法：`Beat.op_order` 记录本拍全部操作的源码顺序（隐藏列往返），
    `render_beat_to_rpy` 按它输出；没有记录的（老表/手工新增行）
    退回历史固定顺序，**绝不因为顺序信息不全就丢操作**。
    """

    #: image tag 全是 ASCII，避免下划线归一干扰"顺序"的判定。
    INTERLEAVED = '''label t:
    play music theme fadein 1.0
    scene bg room
    show e boy
    e "one"
    voice v_001
    play sound door
    m "two"
    show m girl
    stop music
    return
'''

    @staticmethod
    def kinds(text):
        return [v for v, _ in statement_seq(text)]

    def test_interleaved_order_is_preserved(self, write_rpy, tmp_path):
        src = write_rpy(self.INTERLEAVED, 'proj/game/t.rpy')
        table = tmp_path / 't.xlsx'
        open_rpy_as_table(src, table)
        produced = render_beats_to_rpy(import_beats_from_excel(table))
        assert self.kinds(self.INTERLEAVED) == self.kinds(produced), (
            '跨类别顺序没保住：\n'
            f'  原文 {self.kinds(self.INTERLEAVED)}\n'
            f'  产物 {self.kinds(produced)}')

    def test_play_before_show_stays_before(self, write_rpy, tmp_path):
        """最直观的一条：`play music` 写在 `scene`/`show` 之前就得在前。"""
        src = write_rpy(
            'label t:\n'
            '    play music theme\n'
            '    scene bg room\n'
            '    show e boy\n'
            '    e "x"\n',
            'proj/game/o.rpy')
        table = tmp_path / 'o.xlsx'
        open_rpy_as_table(src, table)
        produced = render_beats_to_rpy(import_beats_from_excel(table))
        lines = [l.strip() for l in produced.splitlines() if l.strip()]
        play = next(i for i, l in enumerate(lines) if l.startswith('play'))
        scene = next(i for i, l in enumerate(lines) if l.startswith('scene'))
        assert play < scene, f'play 跑到 scene 后面了：{lines}'

    def test_voice_before_sound_stays_before(self, write_rpy, tmp_path):
        src = write_rpy(
            'label t:\n'
            '    e "x"\n'
            '    voice v_001\n'
            '    play sound door\n'
            '    m "y"\n',
            'proj/game/v.rpy')
        table = tmp_path / 'v.xlsx'
        open_rpy_as_table(src, table)
        produced = render_beats_to_rpy(import_beats_from_excel(table))
        lines = [l.strip() for l in produced.splitlines() if l.strip()]
        voice = next(i for i, l in enumerate(lines) if l.startswith('voice'))
        sound = next(i for i, l in enumerate(lines) if l.startswith('play sound'))
        assert voice < sound, f'voice 跑到 play sound 后面了：{lines}'


class TestOpOrderFallback:
    """
    没有 `op_order` 的 Beat（老表 / 手工新增行 / 直接构造）必须
    **退回历史固定顺序**，而不是"什么都不输出"。
    """

    def test_missing_op_order_still_emits_everything(self):
        b = Beat(seq=1, label='t', text='x', speaker='e',
                 scene='bg room', show='eileen happy',
                 music='theme', voice='v1')
        text = render_beats_to_rpy([b])
        for frag in ('scene bg_room', 'show eileen_happy', 'play music theme',
                     'voice v1'):
            assert frag in text, f'{frag} 丢了：\n{text}'

    def test_partial_op_order_does_not_drop_operations(self):
        """只记了 'stage' 时，audio/voice 要补在后面而不是被忽略。"""
        b = Beat(seq=1, label='t', text='x', speaker='e',
                 scene='bg room', music='theme', voice='v1',
                 op_order='stage')
        text = render_beats_to_rpy([b])
        assert 'scene bg_room' in text
        assert 'play music theme' in text, f'audio 被顺序信息吃掉了：\n{text}'
        assert 'voice v1' in text, f'voice 被顺序信息吃掉了：\n{text}'
