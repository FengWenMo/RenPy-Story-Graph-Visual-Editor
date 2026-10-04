"""
产物**不许有 Python 语法错误**（P0：产物跑不起来）
=================================================

`audit_roundtrip.py` 在真实项目里一直报着一处：

    script.rpy: 第 80 行 'l oh "3"' —— invalid syntax (<unknown>, line 1)

原文第 93 行是作者手写的 `l oh "3"`（`l` 后面多打了一个 `oh`）。
Ren'Py 把**裸的 `词 词 "串"`** 当台词（`l oh "3"` = 角色 `l oh` 说 `"3"`，
Ren'Py 直到运行时才抱怨角色未定义，那是**脚本能跑**的范畴）。

而工具把它归成 `raw`，导出时按"裸 python 表达式"补了 `$`：

    $ l oh "3"        ← Python 语法错误 → Ren'Py 加载脚本就报错

这比原文的写法**更糟**：原文至少能跑。所以这是 P0。

修法（保守）：只在"这行**只可能**是台词"时才原样透传 ——
`标识符链 + 一个引号串 + 行尾`。唯一会被它放过的 Python 形态是
`f() "x"`，而那本身就是语法错误，透传反而更接近原文。
"""

from __future__ import annotations

import ast
import re

import pytest


# ---------------------------------------------------------------------------
#  1. 直接钉住守卫的判据
# ---------------------------------------------------------------------------

class TestLooksLikeDialogue:
    """`标识符链 + 引号串 + 行尾` → 认成台词（Ren'Py 语句），原样透传。"""

    @pytest.mark.parametrize('line', [
        'l oh "3"',                 # 真实项目那一行
        'ling "台词"',
        'e happy "带空格的角色名"',
        "e '单引号'",
        'narrator "旁白"',
        'l oh "带\\"转义\\"的串"',
    ])
    def test_dialogue_shapes_are_recognized(self, line):
        from storygraph.story.beat_render import _looks_like_dialogue
        assert _looks_like_dialogue(line), line

    @pytest.mark.parametrize('line', [
        'love += 1',                # 赋值
        'msg_add(1)',               # 函数调用
        'x = "s"',                  # 赋值 + 串
        'foo("a")',                 # 调用 + 串
        'print("a")',
        'a, b = 1, 2',
        'persistent.n = 1',
        'renpy.notify("hi")',
        'return',
        '',
        '   ',
        '# 注释',
    ])
    def test_python_shapes_are_not(self, line):
        from storygraph.story.beat_render import _looks_like_dialogue
        assert not _looks_like_dialogue(line), line


class TestKnownStatementVerbs:
    """
    已知动词（`show screen …` / `jump …`）是**原文真迹**，必须原样透传。

    这一档与台词形态是**并集**，谁也替代不了谁：
      · 动词白名单认 `show` / `jump`，但认不出台词（`l oh "3"` 的第一步
        是个**角色名**，不可能在白名单里）；
      · 台词形态认 `标识符链 + 引号串`，但认不出 `show screen my_hud`
        （它没有引号串）。

    第一版只做了台词形态，于是 `show screen my_hud` 被补成
    `$ show screen my_hud`（语法错误）—— `TestOpaquePreserved` 当场红。
    """

    @pytest.mark.parametrize('line', [
        'show screen my_hud',
        'hide screen my_hud',
        'jump external_minigame',
        'call chapter1_2',
        'pause 0.5',
        'window hide',
        'with dissolve',
        'play music "a.mp3"',
        'scene bg room',
        'return',
    ])
    def test_verbs_pass_through(self, line, tmp_path):
        from storygraph.story.beat_render import render_code_block
        assert render_code_block(line) == [line], line

    @pytest.mark.parametrize('line', ['if x:', 'elif y:', 'else:',
                                      'while True:', 'for i in r:'])
    def test_control_flow_is_not_a_statement(self, line):
        """
        `if` / `while` / `for` **不在**这一档里。

        它们在 `raw` 里出现时是**代码块内部的条件行**，当成 Ren'Py 语句
        透传会把块结构弄坏（第一版把 `while` 放进来，实测红了两个测试）。
        """
        from storygraph.story.beat_render import _looks_like_renpy_statement
        assert not _looks_like_renpy_statement(line), line


# ---------------------------------------------------------------------------
#  2. 端到端：产物里不许有语法坏掉的 `$` 行
# ---------------------------------------------------------------------------

BROKEN_SRC = (
    'label t:\n'
    '    show lan arrogant at f11\n'
    '    l "1"\n'
    '    l oh "3"\n'
    '    hide ling\n'
    '    xqlz "4"\n'
)


def _syntax_bad_python_lines(text: str) -> list:
    """
    产物里语法坏掉的 `$ …` / `python:` 块。

    ⚠ **粒度是"整块 / 整条"**（2026-10-05 与守卫同步修）：这份判据第一版
    逐行编译块体，于是 `positions = [` / `for i in …:` 这种**正常**写法
    全被判成语法错误 —— 与 `generation_guard._python_syntax_issues` 当初
    把真实项目的 `build` 卡死是同一个错。测试自己写错判据比没有判据更坏：
    它会逼着人去"修"一个不存在的 bug。
    """
    import textwrap

    bad = []
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        # —— `python:` 块：整块 dedent 后编译 ——
        if stripped == 'python:':
            base = len(lines[i]) - len(lines[i].lstrip())
            body = []
            j = i + 1
            while j < n and lines[j].strip() and \
                    (len(lines[j]) - len(lines[j].lstrip())) > base:
                body.append(lines[j])
                j += 1
            code = textwrap.dedent('\n'.join(body))
            if code.strip():
                try:
                    compile(code, '<py>', 'exec')
                except SyntaxError as e:
                    bad.append((i + 1, body[0] if body else '', str(e)))
            i = max(j, i + 1)
            continue
        # —— `$` 语句：续行算同一条（括号平衡）——
        if stripped.startswith('$'):
            chunk = [stripped[2:] if stripped.startswith('$ ')
                     else stripped[1:]]
            depth = _bracket_delta(chunk[0])
            j = i + 1
            while depth > 0 and j < n:
                chunk.append(lines[j])
                depth += _bracket_delta(lines[j])
                j += 1
            try:
                compile('\n'.join(chunk), '<py>', 'exec')
            except SyntaxError as e:
                bad.append((i + 1, lines[i], str(e)))
            i = max(j, i + 1)
            continue
        i += 1
    return bad


def _bracket_delta(line: str) -> int:
    """这一行的括号净变化（引号内与 `#` 之后不算）——与渲染层同口径。"""
    from storygraph.story.beat_render import _bracket_delta as d
    return d(line, '')


class TestProductHasNoPythonSyntaxErrors:

    def test_malformed_dialogue_is_passed_through_verbatim(self, tmp_path):
        """
        `l oh "3"` 原样回到产物里 —— **不补 `$`**。

        补了 `$` 就是 Python 语法错误（产物加载不了）；原样透传则与作者
        写的完全一致，Ren'Py 到运行时才会抱怨角色不存在（那是可诊断的、
        且**原文本来就这样**）。
        """
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        from storygraph.pipeline.compile import compile_beats

        src = tmp_path / 'x.rpy'
        src.write_text(BROKEN_SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
        beats = beats_from_parse_result(pr)

        xl = tmp_path / 'x.xlsx'
        export_beats_to_excel(beats, xl)
        text = compile_beats(import_beats_from_excel(xl)).rpy_text

        assert '$ l oh "3"' not in text, (
            f'又把它当裸 python 补了 `$`（产物跑不起来）：\n{text}')
        assert 'l oh "3"' in text, f'原句丢了：\n{text}'

    def test_no_python_syntax_error_in_product(self, tmp_path):
        """更强的一条：产物里的 `$` / `python:` 行**全都能过编译器**。"""
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.excel_export import export_beats_to_excel
        from storygraph.story.excel_import import import_beats_from_excel
        from storygraph.pipeline.compile import compile_beats

        src = tmp_path / 'x.rpy'
        src.write_text(BROKEN_SRC, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
        beats = beats_from_parse_result(pr)
        xl = tmp_path / 'x.xlsx'
        export_beats_to_excel(beats, xl)
        text = compile_beats(import_beats_from_excel(xl)).rpy_text

        bad = _syntax_bad_python_lines(text)
        assert not bad, f'产物里有语法坏掉的 Python 行：{bad}\n{text}'

    def test_real_lines_are_still_python(self, tmp_path):
        """**别误伤**：真正的裸 python 表达式仍然要补 `$`。"""
        from storygraph.story.beat_render import render_code_block
        assert render_code_block('love += 1') == ['$ love += 1']
        assert render_code_block('msg_add(1)') == ['$ msg_add(1)']
        assert render_code_block('x = "s"') == ['$ x = "s"']
        assert render_code_block('$ x += 1') == ['$ x += 1']
