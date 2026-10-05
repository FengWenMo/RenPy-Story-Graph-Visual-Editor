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

from storygraph.story.script_table import (
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

class TestAudioParamsSurviveExcel:
    """
    **音频参数不能在 xlsx 往返里丢**（2026-09-28 实测的数据丢失）。

    用户对比原文与 build 产物时发现：

        原文: stop music fadeout 1.0   × 6
        产物: stop music               × 6     ← 参数没了

    这条语句被**三个层各剥一层**才丢干净，只修一处看不出来：

      1. `compose_audio_cell` 伪值分支 `return back.get(action,''), ''`
         —— 把参数一起丢了；
      2. `Beat.to_row` 的"文本列优先"分支把参数硬编码成 `''`
         （`bgm, ''`）；
      3. `_hydrate_audio` 用 `op.params` **无条件覆盖**已解析参数，
         而伪值 `[停止音乐]` 本身不含参数 → 冲成空。

    所以这里**端到端**验（而不是只验某个函数）：真实形态走一遍
    `rpy → xlsx → rpy`，逐类数参数个数。
    """

    SRC = '''label t:
    scene bg room
    play music theme fadein 1.0
    e "一"
    stop music fadeout 1.0
    e "二"
    play sound door fadeout 0.5
    e "三"
'''

    def _roundtrip(self, write_rpy, tmp_path):
        src = write_rpy(self.SRC, 'proj/game/a.rpy')
        table = tmp_path / 'a.xlsx'
        open_rpy_as_table(src, table)
        return render_beats_to_rpy(import_beats_from_excel(table))

    def test_stop_fadeout_survives(self, write_rpy, tmp_path):
        out = self._roundtrip(write_rpy, tmp_path)
        assert 'stop music fadeout 1.0' in out, \
            f'stop 的参数在往返里丢了：\n{out}'

    def test_play_fadein_survives(self, write_rpy, tmp_path):
        out = self._roundtrip(write_rpy, tmp_path)
        assert 'play music theme fadein 1.0' in out, out

    def test_sound_params_survive(self, write_rpy, tmp_path):
        out = self._roundtrip(write_rpy, tmp_path)
        assert 'play sound door fadeout 0.5' in out, out

    def test_no_bare_stop_when_params_existed(self, write_rpy, tmp_path):
        """反向约束：原来带参数的 stop 不许退化成裸 stop。"""
        out = self._roundtrip(write_rpy, tmp_path)
        naked = [l for l in out.splitlines()
                 if l.strip() == 'stop music']
        assert not naked, f'出现了裸 stop music（参数被吃）：{out}'

    def test_volume_param_is_a_known_parser_gap(self, write_rpy, tmp_path):
        """
        **已知缺口**（钉住它，修好了这条会失败、提醒更新文档）：

        `play sound door volume 0.5` 的 `volume` **解析器根本不认**
        （payload 里只有 `fadein`/`fadeout`），所以它**不是往返丢的**，
        而是从来没被读进来过。要修得扩解析器的音频参数白名单。
        """
        src = write_rpy(
            'label t:\n'
            '    play sound door volume 0.5\n'
            '    e "x"\n',
            'proj/game/v.rpy')
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        res = ParseResultBuilder().build(RenPyParser().parse(src))
        play = [s for s in res.statements if s.kind == 'play']
        assert play, '没解析出 play 语句'
        assert 'volume' not in play[0].payload, \
            'volume 似乎被支持了（太好了）——请更新这条与文档'


class TestRealChapterAudioFidelity:
    """真实章节：`fadein` / `fadeout` / `play music` 条数必须与原文一致。"""

    CH = Path(r'D:\千连万理\RenPyTest\sr_general\game\chapter')

    @pytest.mark.parametrize('name', ['chapter1_3.rpy', 'chapter1_4.rpy'])
    def test_audio_param_counts_match(self, name, tmp_path):
        src = self.CH / name
        if not src.exists():
            pytest.skip('本机没有该章节')
        orig = src.read_text(encoding='utf-8')
        table = tmp_path / f'{name}.xlsx'
        open_rpy_as_table(src, table)
        out = render_beats_to_rpy(import_beats_from_excel(table))
        for pat in (r'fadein\s+[\d.]+', r'fadeout\s+[\d.]+',
                    r'play music', r'stop music\s+fadeout'):
            a = len(re.findall(pat, orig))
            b = len(re.findall(pat, out))
            assert a == b, f'{name} 的 {pat} 条数不一致：原文 {a} 产物 {b}'


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

    def test_L3_1_image_form_is_preserved_without_registry(self, roundtrip):
        """差异 1 **已修**（2026-10-05）：无资源表时**原样保留**图像名。

        从前这里是"无资源表 → 空格换下划线"（`show ye hi` → `show ye_hi`），
        当成"语义等价"钉住。**它不等价**：真实项目用 Ren'Py 原生
        `tag + attribute` 命名（`image bg bg1_1`），全工程没有 `bg_bg1_1`，
        改写之后运行时找不到图（实测 7 个文件 17 处），还会跟 `with` 抢词。

        现在没给 resolver 就**一个字都不改**；有 resolver 才按"能否解析"择优。
        """
        original, produced = roundtrip
        a, b = show_specs(original), show_specs(produced)
        assert a == b, ('图像名形态应当原样保留（无资源表时不许改写）：'
                        f'\n原文={a}\n产物={b}')

    def test_image_form_is_stable_when_registry_resolves(self, tmp_path):
        """
        有资源表、且作者的写法能解析出来时，**必须原样保留**——
        `show eileen happy` 不许被改写成 `eileen_happy`。
        """
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import registry_from_parse_result
        from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser

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

    def test_scene_play_show_interleave_is_preserved(self, write_rpy, tmp_path):
        """
        **同类内交错**：`scene → play → show`。

        只记"类别"时（`stage,audio`）渲染会把整个舞台组一次输出，
        得到 `scene, show, play` —— `play` 被挤到 `show` 后面。
        实测来源：demo ch01 原文 `play music night` 在
        `show eileen happy` 之前，重构期间差点漏掉。

        修法：`op_order` 记 **verb 级**（`scene,audio,show`），
        渲染逐条输出，音频才能插进 `scene` 与 `show` 之间。
        """
        src = write_rpy(
            'label t:\n'
            '    scene bg classroom\n'
            '    play music night fadein 1.0\n'
            '    show eileen happy at left\n'
            '    e "台词"\n',
            'proj/game/sp.rpy')
        table = tmp_path / 'sp.xlsx'
        open_rpy_as_table(src, table)
        produced = render_beats_to_rpy(import_beats_from_excel(table))
        assert self.kinds(produced) == ['scene', 'play', 'show'], (
            f'play 被挤到 show 后面了：{self.kinds(produced)}\n{produced}')

    def test_audio_between_two_shows_is_a_known_gap(self, write_rpy, tmp_path):
        """
        **已知局限**（钉住它，修好了这条会失败，提醒更新文档）：
        一拍里有两个 `show` 且 `play` 夹在中间时，顺序还原不了 ——
        `op_order` 是 verb **集合**（按首现排序），同 verb 的**实例**顺序丢失。

            show a x
            play music m      ← 夹在两个 show 之间
            show b y

        产物会变成 `show a x; show b y; play music m`。
        条数不错、画面终态一样，但"音乐在第几个立绘之后起"变了。
        真实项目 `chapter1_3` / `chapter1_4` 实测都命中这一类。
        要彻底修需记"每个舞台 verb 实例之间的音频位置"（即整拍语句级顺序）。
        """
        src = write_rpy(
            'label t:\n'
            '    show e a\n'
            '    play music m\n'
            '    show f b\n'
            '    e "台词"\n',
            'proj/game/gap.rpy')
        table = tmp_path / 'gap.xlsx'
        open_rpy_as_table(src, table)
        produced = render_beats_to_rpy(import_beats_from_excel(table))
        kinds = self.kinds(produced)
        # 条数必须对（这是硬要求）
        from collections import Counter
        assert Counter(kinds) == Counter(['show', 'play', 'show'])
        # 目前 play 会落在两个 show 之后 —— 已知局限
        assert kinds == ['show', 'show', 'play'], \
            f'这个局限似乎被修好了（太好了）——请更新文档：{kinds}'

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


class TestANativeStageLineKeepsItsVerbPosition:
    """
    一拍里"多条 `show` + 后面还有 `hide`"时，`hide` 不许被插到第二条 `show` 前面
    （2026-10-05 修，导出缺陷清单 O2）。

    形状：

        show ling happy at left
        show ling angry at right with dissolve   ← 复杂子句 → 住在 Stage 列
        hide ling with fade

    第二条 `show` 是"原生真迹"（`Stage` 列），过表之后它原本被**整块拖到
    舞台尾段** —— 也就是所有 verb 之后 → 跑到 `hide` 后面：

        show ling happy at left
        hide ling with fade                 ← 收尾画面变了（该淡出的人还站着）
        show ling angry at right with dissolve

    修法：原文跟着它在结构化列里的**影子条目**走（`beat_build` 记
    `_stage_native_raw`，`beat_render._stage_lines` 按条目位置逐字出），
    于是它和别的舞台行一样排在自己的 verb 位上。
    """

    SRC = ('label t:\n'
           '    show ling happy at left\n'
           '    show ling angry at right with dissolve\n'
           '    hide ling with fade\n')

    def _lines(self, rpy: str) -> list:
        return [ln.strip() for ln in rpy.splitlines()
                if ln.strip() and not ln.startswith('#')
                and ln.strip() not in ('label t:', 'return')]

    def _two_paths(self, write_rpy, tmp_path):
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        src = write_rpy(self.SRC, 'proj/game/st.rpy')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
        direct = render_beats_to_rpy(beats_from_parse_result(pr))
        table = tmp_path / 'st.xlsx'
        open_rpy_as_table(src, table)
        via_table = render_beats_to_rpy(import_beats_from_excel(table))
        return self._lines(direct), self._lines(via_table)

    def test_the_two_paths_agree(self, write_rpy, tmp_path):
        direct, via_table = self._two_paths(write_rpy, tmp_path)
        assert direct == via_table, f'{direct}\n---\n{via_table}'

    def test_the_hide_stays_last(self, write_rpy, tmp_path):
        _direct, via_table = self._two_paths(write_rpy, tmp_path)
        assert via_table[0].startswith('show ling happy'), via_table
        assert via_table[1].startswith('show ling angry'), via_table
        assert via_table[2].startswith('hide ling'), via_table


class TestCodeBlockStaysAfterTheWholeStage:
    """
    **代码块（`$ …`）不许跑到舞台指令前面**（2026-10-05 修）。

    真实项目 `chapter1_2.rpy` 拍 136/152 的形状：

        hide ye
        show cg cg02_2 at cg38402160 behind ye   ← 复杂子句 → 住在 Stage 列
        $ persistent.lock_lst["cg02.2"]= True

    从 .rpy 直接渲染时那条 `show` 由**结构化列**出（在代码块之前）；
    过一趟表之后 `hydrate_from_dsl` 把它标成"原生真迹的影子"，
    结构化通道跳过它、改由**舞台尾段**出 —— 而尾段当时排在 `emit_code()`
    **之后**，于是 `$ persistent…` 跑到了 `show` 前面：

        hide ye
        $ persistent.lock_lst["cg02.2"]= True
        show cg cg02_2 at cg38402160 behind ye

    舞台两行、代码一行都在，**条数看不出差别**，只有"谁在前"变了 ——
    而这两行是同一拍里的执行顺序（`$ renpy.transition(...)` 就在同型的位置），
    必须两条路逐字节一致。
    """

    SRC = ('label t:\n'
           '    hide ye\n'
           '    show cg cg02_2 at cg38402160 behind ye\n'
           '    $ persistent.lock_lst["cg02.2"]= True\n'
           '    e "台词"\n')

    def test_direct_and_table_paths_are_byte_identical(self, write_rpy,
                                                       tmp_path):
        src = write_rpy(self.SRC, 'proj/game/cg.rpy')
        table = tmp_path / 'cg.xlsx'
        open_rpy_as_table(src, table)
        via_table = render_beats_to_rpy(import_beats_from_excel(table))
        # 直接渲染（同一份源码，不经表）
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
        direct = render_beats_to_rpy(beats_from_parse_result(pr))
        assert via_table == direct, f'{direct}\n---\n{via_table}'

    def test_the_show_comes_before_the_code_block(self, write_rpy, tmp_path):
        src = write_rpy(self.SRC, 'proj/game/cg2.rpy')
        table = tmp_path / 'cg2.xlsx'
        open_rpy_as_table(src, table)
        produced = render_beats_to_rpy(import_beats_from_excel(table))
        lines = [l.strip() for l in produced.splitlines() if l.strip()]
        show = next(i for i, l in enumerate(lines) if l.startswith('show cg'))
        code = next(i for i, l in enumerate(lines) if l.startswith('$'))
        assert show < code, f'代码块跑到舞台前了：{lines}'


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
        # 名字**原样保留**（2026-10-05：没资源表不改写，见 L3_1 那条）
        for frag in ('scene bg room', 'show eileen happy', 'play music theme',
                     'voice v1'):
            assert frag in text, f'{frag} 丢了：\n{text}'

    def test_partial_op_order_does_not_drop_operations(self):
        """只记了 'stage' 时，audio/voice 要补在后面而不是被忽略。"""
        b = Beat(seq=1, label='t', text='x', speaker='e',
                 scene='bg room', music='theme', voice='v1',
                 op_order='stage')
        text = render_beats_to_rpy([b])
        assert 'scene bg room' in text
        assert 'play music theme' in text, f'audio 被顺序信息吃掉了：\n{text}'
        assert 'voice v1' in text, f'voice 被顺序信息吃掉了：\n{text}'
