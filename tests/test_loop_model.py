"""
`while` / `for` 的**作用域建模**：从 T2 到 T0b 再到 T0
======================================================

真机来源：`sr_general/game/minigame/mini_game1.rpy:50` 的**游戏主循环**。
M1 之后它的内容安全了，但报告里只能反反复复说"这是**给工具作者**的待办"
—— 一个**永远还不掉**的待办不是待办，是欠账。

三个阶段：

  · **D2**：头行结构化（`LoopHeader`：可编辑、可校验）+ 块体逐字（`Opaque`）
    → `T0b`（不许拿"接住了"冒充"完全理解了"）；
  · **2026-10-07 第三轮**：**给拍模型加作用域** —— 块体也解析成语句、带
    `nest_path='…>loop:n'` 进流，渲染层按结构位重新归组成 `while …:` 块
    → **`T0`**（循环体里的台词可以像普通台词一样逐句编辑）；
  · 老形态（整块逐字躺在 `Opaque` 里）**继续支持**，等级仍是 `T0b`。

这个文件钉四件事：

  ① **不许误伤台词**：`for "台词"`（说话人恰好叫 `for`）与 `while "x"`
     都不带尾冒号，**必须**仍然是台词。判据要能抓住"正则写宽了"；
  ② **字节不变**：没改头行 → 产物与原文逐字相同；
  ③ **改了只动一行**：头行改了 → 生效，块体一个字不动；
  ④ **作用域不多不少**：见 `tests/test_loop_scope.py`（新文件）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core import capability as cap
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.compile import compile_beats
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

SCRIPT = (
    'label a:\n'
    '    "第一句"\n'
    '    while current_round < game_rounds:\n'
    '        call mini_game_round from _call_mini_game_round\n'
    '        $ current_round += 1\n'
    '    "第二句"\n'
    '    for item in ["a", "b"]:\n'
    '        e "循环里的台词"\n'
    '    return\n'
)

#: 说话人**恰好叫** `for` / `while` 的台词 —— 正则写宽了就会吃掉它们
SAY_TRAP = (
    'label a:\n'
    '    for "我不是循环"\n'
    '    while "我也不是"\n'
    '    return\n'
)


def _parse(tmp_path, src=SCRIPT, name='a.rpy'):
    """返回 `(源文件路径, ParseResult)` —— **顺序就是 (path, pr)**，别记反。"""
    f = tmp_path / name
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return f, pr


def _roundtrip(tmp_path, path, mutate=None):
    pr = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)[0]
    beats = beats_from_parse_result(pr)
    if mutate:
        mutate(beats)
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


def _loops(beats):
    return [b for b in beats if str(getattr(b, 'loop_header', '') or '')]


# ---------------------------------------------------------------------------
#  ① 认出来，而且**不误伤台词**
# ---------------------------------------------------------------------------

def test_while_and_for_become_loops(tmp_path):
    _f, pr = _parse(tmp_path)
    loops = [s for s in pr.statements if s.kind == 'loop']
    assert len(loops) == 2, [(s.kind, s.payload) for s in pr.statements]
    headers = [s.payload['header'] for s in loops]
    assert headers[0].startswith('while '), headers
    assert headers[1].startswith('for '), headers
    # **块体已经进语句流**（作用域）：它就是普通语句，不再是一坨文本
    assert all(s.payload['walked'] for s in loops), [s.payload for s in loops]
    body = [s for s in pr.statements
            if str(getattr(s, 'nest_path', '') or '').startswith('loop:')]
    # 块体就是**普通语句**（`call` 的 payload 里是 target，不再是整块文本）
    assert any('mini_game_round' in str(s.payload) for s in body), \
        [s.payload for s in body]


def test_say_with_speaker_named_for_or_while_is_not_a_loop(tmp_path):
    """
    `for "台词"` / `while "台词"` 必须**仍然**是台词。

    判据的关键在于：`for` / `while` 后面跟的是**引号串**而不是条件 ——
    正则要求"以冒号收尾"，这两行都没有。写宽一点点，编剧的台词就没了。
    """
    _f, pr = _parse(tmp_path, SAY_TRAP)
    assert not [s for s in pr.statements if s.kind == 'loop'], (
        '把台词误判成循环了：'
        f'{[(s.kind, s.payload) for s in pr.statements]}')
    says = [s for s in pr.statements if s.kind == 'say']
    assert {s.payload['speaker'] for s in says} == {'for', 'while'}, says


def test_loop_condition_with_quotes_is_still_a_loop(tmp_path):
    """`while name == "小红":` 带引号但**以冒号收尾** → 仍然是循环。"""
    src = ('label a:\n'
           '    while name == "小红":\n'
           '        $ n += 1\n'
           '    return\n')
    _f, pr = _parse(tmp_path, src)
    loops = [s for s in pr.statements if s.kind == 'loop']
    assert len(loops) == 1, [(s.kind, s.payload) for s in pr.statements]


# ---------------------------------------------------------------------------
#  ② 字节不变
# ---------------------------------------------------------------------------

def test_loop_survives_roundtrip_verbatim(tmp_path):
    path, _pr = _parse(tmp_path)
    out = _roundtrip(tmp_path, path)
    for line in ('while current_round < game_rounds:',
                 'call mini_game_round from _call_mini_game_round',
                 '$ current_round += 1',
                 'for item in ["a", "b"]:',
                 'e "循环里的台词"'):
        assert line in out, f'{line!r} 丢了：\n{out}'


def test_untouched_loop_output_equals_source(tmp_path):
    """没改 → 那几行与原文**逐字相同**（含缩进）。"""
    path, _pr = _parse(tmp_path)
    src_lines = path.read_text(encoding='utf-8').split('\n')
    out = _roundtrip(tmp_path, path).split('\n')
    for ln in src_lines:
        s = ln.strip()
        if s and not s.startswith('#') and s != 'return':
            assert ln in out, f'原文这一行在产物里对不上：{ln!r}'


def test_editing_loop_header_changes_only_that_line(tmp_path):
    """头行改了 → 生效；**块体一个字不动**（逐行比对，只允许一行不同）。"""
    path, _pr = _parse(tmp_path)
    baseline = _roundtrip(tmp_path, path)

    def mutate(beats):
        _loops(beats)[0].loop_header = 'while current_round < game_rounds + 1:'

    changed = _roundtrip(tmp_path, path, mutate=mutate)
    assert 'game_rounds + 1' in changed, changed
    assert 'call mini_game_round from _call_mini_game_round' in changed
    before, after = baseline.split('\n'), changed.split('\n')
    assert len(before) == len(after), (baseline, changed)
    diff = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(diff) == 1, [before[i] for i in diff]


def test_loop_header_survives_excel(tmp_path):
    path, pr = _parse(tmp_path)
    beats = beats_from_parse_result(pr)
    x = tmp_path / 'o.xlsx'
    export_beats_to_excel(beats, x)
    back = import_beats_from_excel(x)
    assert _loops(back)[0].loop_header.startswith('while ')


# ---------------------------------------------------------------------------
#  ③ 能力等级：结构化 = T0；老形态（整块逐字）= T0b
# ---------------------------------------------------------------------------

def test_structured_loop_is_t0(tmp_path):
    """
    **块体进了语句流** → 它是真正的"结构化列全功能编辑" → T0。

    （D2 时这条判据写的是"必须 T0b"—— 那时块体还是一坨文本。现在它反过来
    钉"**别再退回 T0b**"：块体逐字那一档是留给老表兜底的。）
    """
    from storygraph.story.statement_registry import coverage_of_beats
    _path, pr = _parse(tmp_path)
    beats = beats_from_parse_result(pr)
    cov = coverage_of_beats(beats)
    assert cov.count_of(cap.T0B) == 0, cov.counts
    assert cov.count_of(cap.T2) == 0, cov.counts
    assert cov.count_of(cap.T0) >= 4, cov.counts      # 2 个头 + 3 条块体
    assert cov.ratio == 1.0, cov.to_dict()


def test_legacy_verbatim_loop_is_still_t0b(tmp_path):
    """
    **整块逐字**的老形态（`Opaque` 里有整块原文）必须仍是 `T0b`。

    这条是这一层的良心：老表拿不到作用域，就不能假装拿到了。
    """
    from storygraph.story.beat_model import Beat
    from storygraph.story.statement_registry import coverage_of_beats
    legacy = Beat(seq=1, label='a', kind='narration',
                  opaque='while x < 3:\n    $ x += 1',
                  loop_header='while x < 3:')
    cov = coverage_of_beats([legacy])
    assert cov.count_of(cap.T0B) == 1, cov.counts


def test_loop_no_longer_reported_as_unmanaged(tmp_path):
    """建模之后就不该再报"给工具作者的待办" —— 欠账还了。"""
    from storygraph.story.beat_render import render_beats_to_rpy
    from storygraph.story.generation_guard import evaluate_generation
    path, pr = _parse(tmp_path)
    beats = beats_from_parse_result(pr)
    text = render_beats_to_rpy(beats, label_name='a')
    codes = [i.code for i in evaluate_generation(
        text, tmp_path / 'o.rpy', beats=beats)]
    assert 'KNOWN_UNMODELED_BLOCK' not in codes, codes
    assert 'UNMANAGED_BLOCK' not in codes, codes


@pytest.mark.skipif(not REAL.is_dir(), reason='换机 skip')
def test_real_project_reaches_full_coverage():
    """
    真机：**没有一条语句是"工具不认识"的**（T3 = 0），循环块也不再是 T0b。

    ⚠ 2026-10-18 起，可编辑覆盖率**不再是 100%**：`show …:` 的 ATL 块形式
    （真机 35 处）没有块体载体，整块改走逐字保真（T2）—— 从前它们被算成
    T0，可渲染时块体是**丢掉**的（产物 `show …:` 空块，Ren'Py 直接报
    `show statement expects a non-empty block.`）。所以 100% 是假的，
    98% 才是真的：那 2% 是"看得懂形状、逐字抄对"，不是"接不住"。
    """
    from storygraph.parsing.discover import discover_scripts
    from storygraph.story.statement_registry import coverage_of_beats
    az = IncrementalRenPyAnalyzer()
    pr, _ = az.analyze(files=discover_scripts(REAL), graph=None)
    beats = beats_from_parse_result(pr)
    cov = coverage_of_beats(beats)
    assert cov.count_of(cap.T3) == 0, cov.counts
    #: 仅剩的不可编辑项：`show …:` 的 ATL 块（真机 35 处，逐字保真）
    assert cov.counts.get(cap.T2, 0) == 35, cov.counts
    assert cov.ratio >= 0.98, cov.to_dict()
    assert cov.count_of(cap.T0B) == 0, cov.counts
    text = cap.format_capability_report(cov)
    assert text.splitlines()[0].startswith('工具可编辑覆盖率 98.0%'), text
