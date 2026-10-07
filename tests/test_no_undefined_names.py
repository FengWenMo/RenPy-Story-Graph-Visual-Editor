"""
tests/test_no_undefined_names.py
================================
**静态网：模块里引用了、但本模块根本看不到的全局名。**

为什么单独一条（2026-10-05 用户实测报的"导出没反应"）：

P3-14 把 `export_script` 从 `main_window.py` 搬进 `window_export.py`，
而它用到的自由函数 `_overwritable` **留在原地** ——

    NameError: name '_overwritable' is not defined

触发条件是"导出被拦下"那一条路（`if not res.get('ok') and _overwritable(res)`），
症状是最糟的那种：**点了导出什么都不发生**（没有对话框、没有文件、状态栏也不说话）。

为什么现有的守卫都没抓住它：

  · `test_main_window_mixins.py` 静态扫的是 **`self.*`** —— 自由函数不在
    `REQUIRES` 契约里，扫不到；
  · 单测直接 `from ... import _overwritable` 调那个函数 —— 从没走过
    `export_script` 里那一行；
  · Python 是运行时解析名字，没有 import 期检查。

所以补一张**静态网**（标准库 `symtable`，不引第三方 lint）：模块级符号表里
没有、builtins 里也没有、又不是本模块自己定义的名字 —— 一旦走到那一行就是
NameError。它当场还抓出 `main_window._build_graph_page` 里的 `left`
（那个 `NameError` 被 `except Exception` 吞掉，于是"右栏最窄 300 / 最宽 420"
两条约束从来没生效过）。

**抓不到的**（诚实记着，别把这张网当万能的）：属性名（`self.x` / `mod.x`）、
`getattr` 动态取值、以及"名字存在但语义错了"。它只管"这个名字根本不存在"。
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'storygraph'


def _undefined_names(path: Path) -> list:
    """`[(行号, 名字)]`：引用了但本模块看不到的全局名。"""
    try:
        top = symtable.symtable(path.read_text(encoding='utf-8'),
                                str(path), 'exec')
    except SyntaxError as e:                        # 语法错另有测试管
        pytest.fail(f'{path} 语法都不对：{e}')
    module_names = {s.get_name() for s in top.get_symbols()}
    bad: list = []

    def walk(table):
        for sym in table.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced() or not sym.is_global():
                continue
            if name in module_names or hasattr(builtins, name):
                continue
            bad.append((getattr(table, 'get_lineno', lambda: 0)(), name))
        for child in table.get_children():
            walk(child)

    walk(top)
    return bad


def _modules() -> list:
    return sorted(p for p in ROOT.rglob('*.py')
                  if '__pycache__' not in p.parts)


def test_every_module_only_uses_names_it_can_see():
    """
    一眼看全：哪个模块用了自己看不见的名字。

    失败时按提示看那两行 —— 那种错**只在特定分支上炸**，所以静态网比
    "多写几条用例"可靠得多（这条规则本身就被两次实测打脸过：
    `_overwritable` 只在"导出被拦下"时炸，`left` 只在 `try` 里炸）。
    """
    offenders = []
    for p in _modules():
        for line, name in _undefined_names(p):
            offenders.append(f'{p.relative_to(REPO)}:{line}: {name!r}')
    assert not offenders, (
        '这些名字在本模块里根本不存在（走到那一行就是 NameError）：\n  '
        + '\n  '.join(offenders))


def test_the_scanner_itself_catches_a_known_shape(tmp_path):
    """
    **变异验证**：把 `_overwritable` 那种形状写进临时文件，扫描器必须报出来。

    为什么这条必须有：这张网如果自己坏了（比如 `is_global` 用错），
    它会安静地永远通过 —— 而"永远绿的网"比没有网更危险。
    """
    sample = tmp_path / 'sample.py'
    sample.write_text('def f():\n    return _nowhere_defined(1)\n',
                      encoding='utf-8')
    got = [name for _line, name in _undefined_names(sample)]
    assert got == ['_nowhere_defined'], got
