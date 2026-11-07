# -*- coding: utf-8 -*-
"""
**死槽**守卫：`connect(...)` 连的方法真的存在吗？（2026-11-07 第九轮）

为什么要有这条
--------------
上一轮给扩展窗口加了「导出… / 导入…」两个按钮，连的是：

```python
b_export.clicked.connect(lambda: self._export_archive())
b_import.clicked.connect(lambda: self._import_archive())
```

而类里定义的是 `export_archive()` / `import_archive()`（**没有下划线**）。
PySide6 里槽函数抛的异常只会打到 stderr，**进程不崩、界面不变** ——
用户看到的就是"点了没反应"。上一轮的判据直接调 `dlg.export_archive()`
（长名字），于是按钮那第二跳从来没被走一遍。

这条守卫扫的是**源码**：`connect(lambda ...: self.<名字>(...))` 里的每个名字，
必须在 `storygraph/ui_qt/` 里被声明过（`def` / 类级赋值 / `self.<名字> =`）。

⚠ 它挡的是"名字**整个不存在**"这一类，不是完整的名字解析：
  同名的 `def` 写在别的类里也会算通过（跨模块 mixin 就是这种情况 ——
  `GraphPageMixin.insert_card` 真正定义在 `window_card.py`）。
  完整性留给"点一下真生效"那种判据（`test_ext_carry.py::TestWindow`）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UI = ROOT / 'storygraph' / 'ui_qt'


def _declared_names() -> set:
    """整个 `ui_qt` 包里"出现过的名字"：函数 / 类 / 赋值（含 `self.x =`）。"""
    names = set()
    for path in sorted(UI.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    names.update(_target_names(tgt))
            elif isinstance(node, ast.AnnAssign):
                names.update(_target_names(node.target))
    return names


def _target_names(tgt) -> set:
    if isinstance(tgt, ast.Name):
        return {tgt.id}
    if isinstance(tgt, ast.Attribute):
        return {tgt.attr}
    return set()


def _slot_names(tree) -> list:
    """`connect(lambda ...)` 里的 `self.<名字>(...)` → [(名字, 行号)]。"""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == 'connect'):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.Lambda):
                continue
            for inner in ast.walk(arg.body):
                if not isinstance(inner, ast.Call):
                    continue
                target = inner.func
                if isinstance(target, ast.Attribute) \
                        and isinstance(target.value, ast.Name) \
                        and target.value.id == 'self':
                    out.append((target.attr, inner.lineno))
    return out


def test_every_slot_method_in_the_ui_really_exists():
    declared = _declared_names()
    assert declared, '一个名字都没扫到（判据本身失效了）'
    bad = []
    for path in sorted(UI.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for name, line in _slot_names(tree):
            if name not in declared:
                bad.append(f'{path.name}:{line} → self.{name}')
    assert not bad, ('这些按钮/菜单连的方法根本不存在（点下去只会在 stderr 里'
                     '留一行 traceback，界面表现为"点了没反应"）：'
                     + ' | '.join(bad))


def test_the_guard_catches_a_made_up_name():
    """守卫自己也得有判据：凭空一个名字必须被报出来。"""
    tree = ast.parse(
        'from x import y\n'
        'class A(QDialog):\n'
        '    def _build(self):\n'
        '        btn = QPushButton()\n'
        '        btn.clicked.connect(lambda: self._no_such_method())\n'
        '    def _real_method(self):\n'
        '        pass\n')
    declared = _declared_names() | {'_real_method'}
    found = [n for n, _line in _slot_names(tree) if n not in declared]
    assert found == ['_no_such_method'], found
