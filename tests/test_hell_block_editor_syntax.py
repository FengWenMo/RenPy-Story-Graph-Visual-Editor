"""
地狱测试 · 块编辑器「语法检查」+ 产物 Python 语法门禁（2026-10-08）
==================================================================

这里其实是**两个**"语法检查"，判据完全不同，别混：

  ① **块编辑器（双击模块 → 编辑剧情变量）里的那个按钮**
     `NodeOpsDialog` → `EditorSession.check_variable_syntax`。
     它的职责是"在写回之前告诉作者这一行写坏了"。作者是策划/文案，
     写错是常态 —— 所以这个按钮的价值全在"**能报错**"。
     一个永远说 ✔ 的检查器不只是没用，它会**教会用户不看它**。

  ② **落盘门禁（build / 导出）里的 `PYTHON_SYNTAX_ERROR`**
     `generation_guard._python_syntax_issues`。它的职责是"这份产物写进
     `game/` 会不会让 Ren'Py 连加载都加载不了"。判据要**又准又稳**：
     真的坏必须拦，正常的续行/多行/注释**不许**误报（历史上一版逐行编译
     把 `build` 在真实项目上卡死）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.editor import EditorSession                    # noqa: E402
from storygraph.story.generation_guard import _python_syntax_issues  # noqa: E402


@pytest.fixture()
def session():
    return EditorSession()


# ===========================================================================
#  ① 块编辑器：语法检查必须有牙齿
# ===========================================================================

#: 明显写坏的行 —— 检查器**必须**报出来。
BROKEN_LINES = [
    'x = = 5',
    'love += ',
    'x = (',
    'def f(:',
    '@@@@@',
    'if x',
    'y == ',
    'a[1',
    'x = 1 +',
    'val = "abc',
]


@pytest.mark.parametrize('line', BROKEN_LINES)
def test_block_editor_syntax_check_reports_broken(session, line):
    errors = session.check_variable_syntax([line])
    assert errors, f'坏行 {line!r} 被判成"能解析"了 —— 语法检查是空转的'
    assert any(str(line) in e for e in errors), errors


def test_block_editor_syntax_check_locates_the_bad_line(session):
    """报错要指到**第几行**（作者照着改），不是笼统一句"有错"。"""
    lines = ['love += 1', 'x = = 5', 'flag = True']
    errors = session.check_variable_syntax(lines)
    assert errors
    assert any('2' in e for e in errors), errors


#: 正常行 —— 检查器**不许**误报。
GOOD_LINES = [
    'love += 1',
    'flag = True',
    'score = score + 1',
    'visited_roof = False',
    'x = max(a, b)',
    'name = "蒋忱"',
    'd["k"] = 1',
    'a, b = 1, 2',
    'x = foo(1, 2)',
    '$ x = 1',                 # 带 `$` 也要能过（Ren'Py 写法）
    '# 这是注释',               # 注释不算坏
    '',                        # 空行不算坏
]


@pytest.mark.parametrize('line', GOOD_LINES)
def test_block_editor_syntax_check_accepts_valid(session, line):
    errors = session.check_variable_syntax([line])
    assert errors == [], f'正常行 {line!r} 被误报：{errors}'


def test_block_editor_syntax_check_mixed(session):
    """好行坏行混在一起：只报坏的那一行，好行不背锅。"""
    errors = session.check_variable_syntax(
        ['love += 1', 'x = = 5', 'flag = True'])
    assert len(errors) == 1, errors
    assert 'x = = 5' in errors[0]


def test_empty_input_is_clean(session):
    assert session.check_variable_syntax([]) == []
    assert session.check_variable_syntax(None) == []


def test_string_input_is_not_split_into_characters(session):
    """
    传**一个字符串**时要按行拆，不许 `list('x = 1')` 变成逐字符
    （`['x',' ','=',' ','1']` —— 那个孤立的 `=` 会被冤枉成语法错误）。
    """
    assert session.check_variable_syntax('x = 1') == []
    assert session.check_variable_syntax('love += 1\nflag = True') == []
    errs = session.check_variable_syntax('x = =')
    assert len(errs) == 1 and 'x = =' in errs[0], errs


def test_undefined_names_are_not_syntax_errors(session):
    """
    `love += 1` 里 `love` 没定义，那是**运行期**的事，不是语法错误 ——
    块编辑器只做语法检查，不许把"变量没声明"报成"写坏了"，否则
    策划每写一行都要被红一下，检查器就没人看了。
    """
    assert session.check_variable_syntax(['brand_new_var += 1']) == []


# --- 对话框那一层：按钮点下去要真的把错误显示出来 ---------------------------

def _qt_or_skip():
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def test_dialog_shows_errors_when_check_fails(session):
    _qt_or_skip()
    from storygraph.ui_qt.dialogs import NodeOpsDialog
    dlg = NodeOpsDialog(None, label_name='a',
                        variables=['x = = 5'],
                        on_check=session.check_variable_syntax)
    dlg._check()
    text = dlg._check_label.text()
    assert '⚠' in text and 'x = = 5' in text, text


def test_dialog_shows_ok_when_all_valid(session):
    _qt_or_skip()
    from storygraph.ui_qt.dialogs import NodeOpsDialog
    dlg = NodeOpsDialog(None, label_name='a',
                        variables=['love += 1', 'flag = True'],
                        on_check=session.check_variable_syntax)
    dlg._check()
    text = dlg._check_label.text()
    assert text.startswith('✔'), text
    assert '⚠' not in text, text


# ===========================================================================
#  ② 产物 Python 语法门禁（PYTHON_SYNTAX_ERROR）
# ===========================================================================

def _codes(text):
    return [i.code for i in _python_syntax_issues(text)]


# --- 必须拦：真坏 -----------------------------------------------------------

def test_guard_catches_renpy_line_turned_into_code():
    """作者手写的错句 `l oh "3"` 被 `$` 化 —— 这是历史事故的复现。"""
    assert 'PYTHON_SYNTAX_ERROR' in _codes('$ l oh "3"\n')


def test_guard_catches_broken_python_block():
    assert 'PYTHON_SYNTAX_ERROR' in _codes(
        '    python:\n        window hide\n        x = 1\n')


def test_guard_catches_unclosed_bracket_statement():
    # 单行未闭合 —— 注意这里没有续行，所以是真坏
    assert 'PYTHON_SYNTAX_ERROR' in _codes('$ x = [1, 2\n')


def test_guard_catches_bad_indent_in_block():
    assert 'PYTHON_SYNTAX_ERROR' in _codes(
        '    python:\n        x = 1\n      y = 2\n')


# --- 不许误报：正常写法 -----------------------------------------------------

def test_guard_allows_multiline_dollar_continuation():
    """`$ x = [` 的续行 —— 逐行编译会误报，整条编译才对。"""
    text = '$ positions = [\n    (200, 200), (600, 200),\n]\n'
    assert _codes(text) == [], _codes(text)


def test_guard_allows_block_with_continuation_and_for():
    text = (
        '    python:\n'
        '        positions = [\n'
        '            (1, 2),\n'
        '            (3, 4),\n'
        '        ]\n'
        '        for i, p in enumerate(positions):\n'
        '            print(i, p)\n'
    )
    assert _codes(text) == [], _codes(text)


def test_guard_allows_comment_only_statement():
    assert _codes('$ # 只是注释\n') == []
    assert _codes('$x = 1  # 行尾注释\n') == []


def test_guard_allows_semicolon_and_fstrings():
    text = '$ x = 1; y = 2\n$ name = f"{x}-{y}"\n'
    assert _codes(text) == [], _codes(text)


def test_guard_allows_empty_python_block():
    assert _codes('    python:\n\n    "台词"\n') == []


def test_guard_reports_line_number():
    issues = _python_syntax_issues('    python:\n        x = 1\n        y = = 2\n')
    assert issues and issues[0].line_no, issues
