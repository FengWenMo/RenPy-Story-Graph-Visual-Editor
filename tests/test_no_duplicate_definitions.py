"""
**同一个模块里不许有两次同名顶层定义** —— 静态判据（2026-10-14 加）。

为什么要有它（这条判据是"用血换来的"）：

`module.py` 里写

    _X = re.compile(...)        # 我改的是这一行
    ...
    _X = re.compile(...)        # 活的是这一行（后定义的覆盖前面的）

**没有任何报错、没有任何警告** —— Python 只是静静地用后面那个。表现为
"源码明明改了、行为照旧"，而我为此在一个函数上排查了很久（`pattern.py`
的 `match_line`、`learn_statement.py` 的 `_STMT_RE` 各踩一次）。
静态判据是唯一能在**改之前**就挡住它的东西。

判据范围（故意保守，只查**顶层的**同名）：
  · `def` / `async def` / `class` 顶两次同名；
  · 顶层**常量/变量赋值**（含 `x: T = …`）两次同名。

⚠ 为什么只查顶层：函数**内部**重名是合法的、常见的
（两个分支各写一个 `_runs`），查进去只会逼人改名，那不是这条判据的目的。
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / 'storygraph'


def _top_level_names(tree) -> dict:
    """顶层定义/赋值 → `{名字: [行号, …]}`（按出现顺序）。"""
    seen: dict = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            seen.setdefault(node.name, []).append(node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    seen.setdefault(target.id, []).append(node.lineno)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            seen.setdefault(node.target.id, []).append(node.lineno)
    return seen


def _package_files() -> list:
    return sorted(p for p in PKG.rglob('*.py') if p.is_file())


def test_no_module_defines_the_same_top_level_name_twice():
    """
    ⭐⭐ 扫全包：每个模块的顶层名字**只能定义一次**。

    两次同名不会有任何报错，只会让**前面那次悄悄失效** ——
    这正是"改了源码、行为照旧"的根源（比语法错难查得多）。
    """
    assert _package_files(), '没扫到任何包内文件（路径写错了？）'
    bad: dict = {}
    for fp in _package_files():
        try:
            tree = ast.parse(fp.read_text(encoding='utf-8'))
        except SyntaxError as e:                 # 语法错由别的判据抓
            bad.setdefault(str(fp.relative_to(REPO)), {})['<语法错>'] = \
                [str(e.lineno), 0]
            continue
        dupes = {name: lines
                 for name, lines in _top_level_names(tree).items()
                 if len(lines) > 1}
        if dupes:
            bad[str(fp.relative_to(REPO))] = dupes
    assert not bad, (
        '顶层同名定义了两次 —— 前面那次会**静默失效**（改了没反应）：\n'
        + '\n'.join(f'  {f}: ' + '，'.join(f'{n} 在行 {ls}'
                                          for n, ls in d.items())
                    for f, d in bad.items()))


def test_the_scan_itself_would_catch_a_real_duplicate(tmp_path):
    """
    ⭐ **判据自己也要有判据**：造一个真有重复定义的文件，确认扫得出来。

    为什么必须写这条：上面那条是"全包没有重复"—— 它**恒真**也可能是因为
    扫描逻辑根本没在看（比如 `tree.body` 取错、`isinstance` 写反）。
    这条用一份**故意的坏样本**把它钉住。
    """
    sample = tmp_path / 'sample.py'
    sample.write_text(
        'import re\n'
        '_X = re.compile("a")\n'
        'def f():\n'
        '    return 1\n'
        '_X = re.compile("b")\n'          # ← 故意的
        'def f():\n'                       # ← 故意的
        '    return 2\n'
        'class C:\n'
        '    pass\n'
        'class C:\n'                       # ← 故意的
        '    pass\n'
        'def g():\n'
        '    def h(): return 1\n'          # 函数**内部**重名：合法，不算
        '    def h(): return 2\n'
        '    return h\n',
        encoding='utf-8')
    names = _top_level_names(ast.parse(sample.read_text(encoding='utf-8')))
    assert sorted(n for n, ls in names.items() if len(ls) > 1) == \
        ['C', '_X', 'f'], names
    assert names['h' if 'h' in names else 'g'] == [12], (
        '函数内部的重名不该被算成顶层重名', names)
