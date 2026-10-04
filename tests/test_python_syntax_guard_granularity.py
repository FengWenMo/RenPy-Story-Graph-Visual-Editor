"""
守卫的粒度：`PYTHON_SYNTAX_ERROR` 必须按「整块 / 整条」判，不许逐行
=====================================================================

拓展边界①（2026-10-05）：**`build` 在真实项目上必然失败** —— 而根因不是
产物坏了，是这道守卫**假阳性**。

真实项目 `D:/千连万理/RenPyTest/sr_general` 实测（`cli.py build`）：

    生成门禁拦下，产物未写入目标路径
    [block] PYTHON_SYNTAX_ERROR（第 2870 行）：'[' was never closed（`positions = [`）
    [block] PYTHON_SYNTAX_ERROR（第 2875 行）：unmatched ']'（`]`）
    [block] PYTHON_SYNTAX_ERROR（第 2912 行）：expected an indented block
                                          after 'for' statement on line 1
    … 共 10 条

这 10 条**全部**来自"把 `python:` 块体一行一行丢给编译器"：

    positions = [                ← 单看第一行：`'[' was never closed`
        (200, 200), …            ← 续行
    ]                            ← 单看这一行：`unmatched ']'`
    for i, digit in enumerate(x): ← 单看块头：`expected an indented block`
        …

把那份产物里 7 个 `python:` 块**整块** `dedent` 后编译，**一个都不错**。
（复现入口：`tools/probe_python_block.py` —— 它现在把这四档分开报：
在 / 行内注释 / 规范成 `$` / **真丢代码**。）
后果是门禁把一份完全合法的产物拦在门外 —— `build` 对真实项目
"永远失败"，而且理由指向一个不存在的问题（比不拦更坏：人会照着
假线索去改本来正确的代码）。

所以这个文件守两条：

  1. **不许假阳性**：多行语句 / `for` / `if` / 嵌套 / 块内空行 / `$` 续行
     都必须放行（它们是最常见的正常写法）；
  2. **不许假阴性**：块里真混进了 Ren'Py 语句、或括号真没闭合，照样拦。

第 2 条是这次修法的**代价边界**：整块编译比逐行编译宽松，必须证明
它仍然拦得住原本要拦的东西（`script.rpy` 的 `l oh "3"` 那一类）。

变异验证（"打回逐行编译会不会红"）：`tools/mut_python_syntax_guard.py`。
从仓库根执行：

    set PYTHONPATH=tools
    python -m pytest tests/test_python_syntax_guard_granularity.py -q -p mut_python_syntax_guard

实测：`TestNoFalsePositives` **4 条红**（多行列表 / `for` / `if-else` /
多行函数调用），"真错照样拦"那几条仍绿 —— 说明这一组用例确实在挡那个 bug。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _blocks(text: str) -> list:
    """产物里的 block 级发现（只剩 `PYTHON_SYNTAX_ERROR`）。"""
    from storygraph.story.generation_guard import generation_guard
    return [i for i in generation_guard(text, None)
            if i.code == 'PYTHON_SYNTAX_ERROR']


def _in_label(body: str, header: str = 'python:') -> str:
    """把一段块体包成 `label` 里的 `python:` 块（产物里的真实形状）。"""
    lines = [f'label a:', f'    {header}']
    lines += [('        ' + ln) if ln.strip() else '' for ln in body.split('\n')]
    lines.append('    e "hi"')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
#  1. 正常写法一律放行（假阳性 = 把 build 卡死的那个 bug）
# ---------------------------------------------------------------------------

#: 真实项目里出现过、且**旧的逐行判据全部误报**的形态。
NORMAL_BODIES = {
    '多行列表（续行）': (
        'positions = [\n'
        '    (200, 200), (600, 200),\n'
        '    (200, 400), (600, 400),\n'
        ']\n'
    ),
    'for 循环（块头 + 缩进体）': (
        'for i, digit in enumerate(correct_sequence):\n'
        '    available = [p for p in positions if p not in seen]\n'
        '    if available:\n'
        '        seen.append(available[0])\n'
    ),
    'if / else 分支': (
        'if score >= 3:\n'
        '    msg = "好"\n'
        'else:\n'
        '    msg = "继续"\n'
    ),
    '多行函数调用（含块内空行）': (
        'renpy.store.game = drag_puzzle(\n'
        '    puzzle_path,\n'
        '    rows=2,\n'
        '\n'
        '    cols=3,\n'
        '    drag_count=6)\n'
    ),
    'dict / 三元 / 注释混排': (
        '# 生成选项\n'
        'opts = {"a": 1,\n'
        '        "b": 2}\n'
        'flag = True if opts else False\n'
    ),
    'lambda / 推导式': (
        'f = lambda x: x + 1\n'
        'rows = [[i * j for j in range(3)] for i in range(3)]\n'
    ),
}


class TestNoFalsePositives:
    @pytest.mark.parametrize('name', sorted(NORMAL_BODIES))
    def test_normal_block_bodies_pass(self, name):
        issues = _blocks(_in_label(NORMAL_BODIES[name]))
        assert not issues, (
            f'{name} 被误判成 Python 语法错误 —— 这正是让 `build` 在真实'
            f'项目上"必然失败"的那个假阳性：\n'
            + '\n'.join('  ' + i.format() for i in issues))

    def test_init_python_block_is_checked_as_a_block(self):
        """顶层 `init python:` 也要按整块判（它现在会被保真带回产物）。"""
        text = ('init python:\n'
                '    import random\n'
                '\n'
                '    def helper(n):\n'
                '        return [x for x in range(n)]\n'
                '\n'
                'label a:\n'
                '    e "hi"\n')
        assert not _blocks(text)

    @pytest.mark.parametrize('stmt', [
        'x = [\n    1,\n    2,\n]',
        'y = drag_puzzle(\n    rows=2,\n    cols=3)',
        'z = {"a": 1}',
    ])
    def test_dollar_statement_with_continuation(self, stmt):
        """`$` 语句的续行算**同一条**（单看第一行 `$ x = [` 也是假阳性）。"""
        text = 'label a:\n    $ ' + stmt + '\n    e "hi"\n'
        assert not _blocks(text), stmt


# ---------------------------------------------------------------------------
#  2. 真错照样拦（修法的代价边界）
# ---------------------------------------------------------------------------

class TestStillCatchesRealErrors:
    def test_renpy_statement_inside_python_block(self):
        """块里混进 Ren'Py 语句 —— 这是这道门禁**本来**要拦的东西。"""
        text = _in_label('for i in range(3):\n'
                         '    window hide\n'
                         '    x = i\n')
        issues = _blocks(text)
        assert issues, '块里混进 `window hide` 却没拦下 —— 产物跑不起来'
        assert issues[0].level == 'block'

    def test_unclosed_bracket_in_block(self):
        issues = _blocks(_in_label('x = [1, 2,\n'))
        assert issues, '括号没闭合却没拦下'

    def test_unclosed_bracket_in_dollar_statement(self):
        issues = _blocks('label a:\n    $ x = (1, 2\n    e "hi"\n')
        assert issues, '`$` 语句括号没闭合却没拦下'

    def test_broken_malformed_dialogue_still_caught(self):
        """
        `$ l oh "3"`（把台词当代码补了 `$`）**仍然**必须拦。

        这是 `_python_syntax_issues` 存在的**唯一理由**（P0 事故形态）。
        """
        issues = _blocks('label a:\n    $ l oh "3"\n')
        assert issues, 'P0 那一类（`$ 台词`）没拦下'

    def test_block_header_forms_are_recognized(self):
        """几种 `python:` 块头写法都要认，不能只认 exactly `python:`。"""
        for header in ('python:', 'init python:', 'init 1 python:',
                       'python early:'):
            text = _in_label('window hide\n', header=header)
            assert _blocks(text), f'{header} 的块体没被检查'


# ---------------------------------------------------------------------------
#  3. 端到端：带多行 python 块的章节**必须能写出去**
# ---------------------------------------------------------------------------

MINIGAME_SRC = (
    'default score = 0\n'
    '\n'
    'label mini:\n'
    '    python:\n'
    '        seq = [5, 7, 9]\n'
    '        positions = [\n'
    '            (200, 200), (600, 200),\n'
    '            (200, 400), (600, 400),\n'
    '        ]\n'
    '        for i, digit in enumerate(seq):\n'
    '            if digit > 5:\n'
    '                positions.append((i, digit))\n'
    '    "第[seq[0]]轮"\n'
    '    return\n'
)


class TestEndToEndWriteSucceeds:
    def test_minigame_shaped_chapter_can_be_written(self, tmp_path):
        """
        真实项目的形状（多行列表 + `for` + `if`）**必须写得出去**。

        这条直接对应"边界①"的症状：假阳性让门禁把产物挡在门外，
        目标路径上一个字节都没有。
        """
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        from storygraph.pipeline.compile import compile_beats
        from storygraph.story.generation_guard import write_generation

        src = tmp_path / 'mini.rpy'
        src.write_text(MINIGAME_SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
        beats = beats_from_parse_result(pr)
        xl = tmp_path / 'mini.xlsx'
        export_beats_to_excel(beats, xl)
        text = compile_beats(import_beats_from_excel(xl)).rpy_text

        target = tmp_path / 'out' / 'mini.generated.rpy'
        art = write_generation(text, target)
        assert art.written, (
            '合法产物被门禁拦下了（= `build` 对真实项目永远失败）：\n'
            + '\n'.join('  ' + i.format() for i in art.blocking))
        assert target.exists()
