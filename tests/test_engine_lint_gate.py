"""
引擎闸门必须 **fail-closed**（2026-10-18，build P1）
====================================================

真机现场：产物里有个没闭合的字典，Ren'Py 明明报了

    File "game/script.rpy", line 13850: '{' was never closed

而 `engine_lint._PARSE_MARK` 里只有 8 个"像语法错"的措辞，这句话一个都不
命中 → 归进 `semantic_errors` → 只 warn、`ok=True`、构建**照常写盘**。
那份产物拷进 `game/` 就是 `SyntaxError`。

根子在判据方向：**正向清单**（列出"哪些措辞算语法错"）永远会漏。
现在的判据是**出处**：

  · `File "…", line N: …`  —— 引擎加载/编译时**抛了异常**，脚本没读进去
    ⇒ 一律**阻塞**，不管那句话长什么样；
  · `game/script.rpy:N …`   —— 脚本读进去了，这是 lint **检查**的意见
    （缺图 / 缺角色 / 跳转目标不存在）⇒ 不阻塞；
  · traceback 帧（`File "…", line N, in xxx`）—— 引擎在 lint 期间崩了
    ⇒ 同样阻塞（`init python` 抛异常时引擎不打报错行、退出码还是 0）。

下面每条 `raw` 都是 **Ren'Py 8.4.1 的真实输出**（`tools/probe_lint_messages.py`
能复跑），不是编的 —— 编出来的措辞测不出"出处判据"。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.pipeline.engine_lint import classify      # noqa: E402

#: 用户报的那个现场（`screens.rpy` 的 `}` 被去重吃掉之后）
RAW_BRACE = (
    'File "game/script.rpy", line 13850: \'{\' was never closed\n'
    'define bubble.expand_area = {\n'
    '^\n'
)

#: 缺图：lint **检查**报的（脚本读进去了）
RAW_MISSING_IMAGE = (
    "game/script.rpy:5 'bg definitely_not_here' is not an image.\n"
)

#: 角色未定义 / 跳转目标不存在 —— 同一种格式
RAW_SEMANTIC_MANY = (
    "game/script.rpy:3 Could not evaluate 'ghost' in the who part of a "
    "say statement.\n"
    "game/script.rpy:7 The jump is to nonexistent label 'nowhere'.\n"
)

#: `init python` 抛异常：只有 traceback 帧，**没有**带冒号的报错行
RAW_CRASH = (
    'File "game/script.rpy", line 2, in script\n'
    'File "renpy/ast.py", line 1193, in execute\n'
    'File "renpy/python.py", line 1276, in py_exec_bytecode\n'
    'File "game/script.rpy", line 5, in <module>\n'
    'ZeroDivisionError: division by zero\n'
)


class TestExceptionLinesBlock:
    def test_the_reported_brace_error_blocks(self):
        parse, sem = classify(RAW_BRACE)
        assert parse == ["第 13850 行：'{' was never closed"], (parse, sem)
        assert not sem

    def test_an_unseen_wording_still_blocks(self):
        """没见过的措辞也拦 —— 判据是出处，不是"像不像语法错"。"""
        raw = 'File "game/script.rpy", line 9: 未来版本才会有的新报错\n'
        parse, sem = classify(raw)
        assert len(parse) == 1 and not sem, (parse, sem)

    def test_style_property_unknown_blocks(self):
        """`style property … is not known` 是**加载时抛的异常** → 产物起不来。"""
        raw = ('File "game/script.rpy", line 3: style property '
               'definitely_not_a_property is not known.\n')
        parse, _sem = classify(raw)
        assert parse, '这一条会让 Ren\'Py 直接开到错误页，必须阻塞'


class TestLintChecksAreSemantic:
    def test_missing_image_is_semantic(self):
        parse, sem = classify(RAW_MISSING_IMAGE)
        assert not parse
        assert sem == ["第 5 行：'bg definitely_not_here' is not an image."]

    def test_all_check_lines_are_reported(self):
        parse, sem = classify(RAW_SEMANTIC_MANY)
        assert not parse and len(sem) == 2, (parse, sem)


class TestCrashBlocks:
    def test_traceback_without_error_line_blocks(self):
        parse, _sem = classify(RAW_CRASH)
        assert parse and 'ZeroDivisionError' in parse[0], parse

    def test_normal_output_is_not_a_crash(self):
        parse, sem = classify(RAW_MISSING_IMAGE)
        assert not parse and sem, (parse, sem)


class TestDeclaredStatementsAreExempt:
    def test_custom_statement_head_is_skipped(self):
        """临时工程里没有 `register_statement` —— 那句话不是语法错。"""
        raw = ('File "game/script.rpy", line 4: expected statement.\n'
               'mycue "闪电" intensity=3\n'
               '^\n')
        parse, _sem = classify(raw, ignore_heads={'mycue'})
        assert not parse, parse

    def test_without_the_exemption_it_blocks(self):
        raw = ('File "game/script.rpy", line 4: expected statement.\n'
               'mycue "闪电" intensity=3\n')
        parse, _sem = classify(raw)
        assert parse, '没给 ignore_heads 时应当 Fail-closed'
