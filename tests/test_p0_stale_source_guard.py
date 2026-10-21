"""
tests/test_p0_stale_source_guard.py
===================================
**表过期之后原地导出，不许静默吃掉源里的语句。**

场景（2026-10-21 实测，P0）：

  1. 策划把 `ch1.rpy` 导成表；
  2. 程序员在同一个 `.rpy` 里加了三行（`pause 0.5` / 一句新台词 / `$ flag = True`）；
  3. 策划在表里改一句台词，**导出回原文件**。

实测结果（改之前）：退出码 0，回执只有一句"增量未采用（return 数量从 1 变成 0）
→ 已按全量生成"，而源文件里程序员那三行**全部消失**。

为什么会漏：增量对位失败后按设计退回全量，而全量只认得表里的拍。
本该由"手写护栏"拦下，但 `write_generation` 里有一条豁免
`_copy_of_source(out_path, source_text)` —— 目标内容等于源文本就放行。
**原地覆盖时这条判据是套套逻辑**：源就是目标，读它必然相等。
于是护栏 100% 失效（"目标 == 底稿"这个让路条件本来是给**增量**的，
被这条豁免顺手也给了全量）。

判据（按十条铁律 2「要么全对、要么完全不动」）：

  · 原地 + 增量成功 → 照写（唯一安全的改法）；
  · 原地 + 增量失败 → **拒写**，源文件一个字节不动，并说清为什么；
  · 原地 + `force=True` → 人明确知道自己在做什么，放行（逃生舱）；
  · 另存到**别的路径** → 照旧写（那是新建文件，不是覆盖别人的章节）。
"""

from pathlib import Path

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.pipeline.compile import compile_beats, write_compiled
from storygraph.story.beat_build import beats_from_parse_result

SRC = '''label ch1:
    jc "第一句。"
    jc "第二句。"
    jc "第三句。"
    return
'''

#: 程序员后来加的三行（表里根本没有这三行）。
PROG_EXTRA = ('    pause 0.5\n'
              '    jc "程序员新加的一句。"\n'
              '    $ flag = True\n')


def _beats_of(fp: Path):
    """从**旧**源文件建拍（模拟"表是在程序员改之前导出的"）。"""
    return beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(str(fp))))


def _stale_setup(write_rpy, *, name='proj/game/ch1.rpy'):
    """
    造出"表过期"的局面，返回 `(源文件路径, 旧拍列表, 源文件当时的字节)`。

    步骤与真实节奏一致：先导表（解析旧源）→ 程序员改源 → 策划改表中一句。
    """
    fp = write_rpy(SRC, name)
    beats = _beats_of(fp)
    # 表格那条路：拍上没有行号（Excel 不存运行期元数据），对位要现算。
    for b in beats:
        b.src_first = 0
        b.src_last = 0
        b.insert_anchor = 0

    # 程序员的改动：插在「第二句」后面
    cur = fp.read_text(encoding='utf-8')
    fp.write_text(cur.replace('    jc "第二句。"\n',
                              '    jc "第二句。"\n' + PROG_EXTRA),
                  encoding='utf-8')
    before = fp.read_bytes()

    # 策划改表里的一拍
    for b in beats:
        if getattr(b, 'text', '') == '第三句。':
            b.text = '改过的一句。'
    return fp, beats, before


class TestInPlaceFullRegenerationIsRefused:
    def test_source_untouched_when_increment_fails(self, write_rpy):
        """原地导出 + 增量没成功 → 拒写，且源文件逐字节不变。"""
        fp, beats, before = _stale_setup(write_rpy)

        result = compile_beats(beats)
        out = write_compiled(result, fp, beats=beats, source_path=fp,
                             engine_lint=False)

        assert out.written is False, (
            '增量没成功却写了盘 —— 这就是"退出码 0 + 程序员的 3 行消失"')
        assert fp.read_bytes() == before, '源文件被改了，铁律 2 要求完全不动'
        # 拒绝的理由要能看懂：不能只说"这是手写文件"
        text = ' '.join(str(i) for i in out.guard_issues)
        assert '增量' in text or '表' in text, f'拒绝理由没说清原因：{text}'

    def test_programmer_lines_survive(self, write_rpy):
        """核心判据：程序员的 3 行必须还在（拒写之后天然成立，但要显式钉住）。"""
        fp, beats, _ = _stale_setup(write_rpy)
        write_compiled(compile_beats(beats), fp, beats=beats,
                       source_path=fp, engine_lint=False)
        now = fp.read_text(encoding='utf-8')
        assert 'pause 0.5' in now
        assert '程序员新加的一句。' in now
        assert '$ flag = True' in now
        # 策划的修改没有落盘（拒写）—— 这是"完全不动"的另一半
        assert '改过的一句。' not in now

    def test_force_still_works(self, write_rpy):
        """`force=True` 是逃生舱：人明确要求全量覆盖时放行。"""
        fp, beats, _ = _stale_setup(write_rpy)
        out = write_compiled(compile_beats(beats), fp, beats=beats,
                             source_path=fp, force=True, engine_lint=False)
        assert out.written is True
        now = fp.read_text(encoding='utf-8')
        assert '改过的一句。' in now


class TestSafePathsStillWork:
    def test_incremental_in_place_still_writes(self, write_rpy):
        """表与源完全对得上时，原地增量导出照旧能写（不能把正常路堵死）。"""
        fp = write_rpy(SRC, 'proj/game/ch1.rpy')
        beats = _beats_of(fp)
        for b in beats:
            b.src_first = 0
            b.src_last = 0
            b.insert_anchor = 0
        for b in beats:
            if getattr(b, 'text', '') == '第三句。':
                b.text = '改过的一句。'

        out = write_compiled(compile_beats(beats), fp, beats=beats,
                             source_path=fp, engine_lint=False)
        assert out.written is True, f'正常增量被拦了：{out.guard_issues}'
        now = fp.read_text(encoding='utf-8')
        assert '改过的一句。' in now
        assert '第三句。' not in now

    def test_copy_to_another_path_still_writes(self, write_rpy):
        """另存到别的路径不算覆盖（目标不存在）→ 照旧写。"""
        fp, beats, _ = _stale_setup(write_rpy)
        target = fp.parent.parent / 'exports' / 'ch1.exported.rpy'
        out = write_compiled(compile_beats(beats), target, beats=beats,
                             source_path=fp, engine_lint=False)
        assert out.written is True, f'另存被拦了：{out.guard_issues}'
        assert target.exists()

    def test_copy_of_source_exemption_still_works(self, write_rpy):
        """
        `_copy_of_source` 那条豁免（P1-1）不能因为这次修复失效：
        "零改动复制出来的副本（无生成标记），下次真有改动再导到同一路径"
        必须仍然放行 —— 否则用户第二次导出就被自己的护栏拦住。
        """
        fp = write_rpy(SRC, 'proj/game/ch1.rpy')
        copy = fp.parent.parent / 'exports' / 'ch1.copy.rpy'
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(fp.read_bytes())          # 零改动复制品（没有生成标记）

        # 拍从**源**建，产物写到**副本** —— 底稿 != 目标，
        # 走的正是 `_copy_of_source` 那条豁免（不是 merging_onto）。
        beats = _beats_of(fp)
        for b in beats:
            b.src_first = 0
            b.src_last = 0
            b.insert_anchor = 0
        for b in beats:
            if getattr(b, 'text', '') == '第三句。':
                b.text = '改过的一句。'

        out = write_compiled(compile_beats(beats), copy, beats=beats,
                             source_path=fp, engine_lint=False)
        assert out.written is True, f'复制品的二次导出被拦了：{out.guard_issues}'
        assert '改过的一句。' in copy.read_text(encoding='utf-8')
