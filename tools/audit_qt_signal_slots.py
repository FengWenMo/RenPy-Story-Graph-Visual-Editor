"""
审计：`clicked.connect(槽)` 有没有把**信号的 bool** 当成槽的参数吃掉。

`QPushButton.clicked` 的签名是 `clicked(bool checked = False)`，PySide 会把
这个 bool 当**第一个位置参数**喂给槽函数。于是

    self._insert_btn.clicked.connect(self.insert_card)      # insert_card(after=True)
    self._insert_btn.clicked.connect(self.insert_card)      # → 实参 after=False

后者才是实际发生的 —— 用户看到的症状是"「插入卡片」与「插到前面」一模一样"
（2026-10-05 实报）。

判据（**只报"默认值与信号发的值不一致"那一种**，宁少报不误伤）：

  · 槽的**第一个可选位置参数**的默认值是 `False`（或无默认值）→ 正常
    （PySide 发 `False`，与作者写的默认一致）；
  · 默认值是别的（如 `after=True`）→ **报**：点一下就会拿 `False` 跑，
    与作者写下的默认值不同 —— 那正是缺陷形态。

用法：python tools/audit_qt_signal_slots.py
退出码 0 = 干净，1 = 有可疑接线。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / 'storygraph' / 'ui_qt'

#: 这些 Qt 信号都是 `(bool)` 签名（`triggered` 也发 checked）
BOOL_SIGNALS = ('clicked', 'triggered')


def _methods_of(tree: ast.Module) -> dict:
    """{类名: {方法名: (位置参数名列表, 默认值列表)}}"""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {}
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = item.args
            names = [a.arg for a in args.posonlyargs + args.args]
            # 参数默认值在尾部对齐
            defaults = [None] * (len(names) - len(args.defaults)) + \
                [ast.unparse(d) for d in args.defaults]
            methods[item.name] = (names, defaults)
        out[node.name] = methods
    return out


def _index_all_modules() -> dict:
    """
    `{方法名: {(类名, 参数名, 默认值), …}}` —— **跨文件**的槽索引。

    为什么必须跨文件（2026-10-05 变异验证抓到）：接线在 `main_window.py`，
    而 `insert_card` 定义在 `window_card.py` 的 mixin 里。只在本文件的 AST 里
    找槽函数，`insert_card` 根本查不到 → 审计**假绿**（把坏接线放回去也不报）。
    界面层刻意拆成一堆 mixin，所以"槽和接线不在同一个文件"是常态。
    """
    index = {}
    for path in sorted(UI_DIR.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for cls_name, methods in _methods_of(tree).items():
            for name, sig in methods.items():
                index.setdefault(name, set()).add(
                    (cls_name, tuple(sig[0]), tuple(sig[1])))
    return index


def _suspect(methods: dict, slot: str, index: dict = None):
    """槽会把信号发的 `False` 吃掉、且默认值与它不一致吗？"""
    sig = methods.get(slot)
    if sig is None and index is not None:
        # 不在本类里 → 去跨文件索引里找（mixin 常态）
        cands = index.get(slot) or set()
        if len(cands) != 1:
            return None
        _cls, names, defaults = cands.pop()
        sig = (list(names), list(defaults))
    if sig is None:
        return None
    names, defaults = sig
    positional = names[1:]              # 去掉 self
    if not positional:
        return None
    default = defaults[1] if len(defaults) > 1 else None
    if default is None:
        return None                     # 必填参数 → PySide 会报错，不静默
    if default.strip() == 'False':
        return None                     # 与信号发的值一致 → 作者的意图就是它
    return f'{slot}({positional[0]}={default})'


def _wired_slots(tree: ast.Module):
    """
    产出 `(行号, 信号/入口, 槽表达式)`。

    两种接线方式都要看：

      · `btn.clicked.connect(self.slot)` / `act.triggered.connect(self.slot)`
        —— 信号带 `(bool)`；
      · `act('文字', self.slot, '提示')` —— 本仓库往工具栏挂动作的那套写法
        （`QAction.triggered` 同样带 `(bool)`）。少了这一种，"挂上去的回调"
        里同样的坑就查不到（`_insert_btn` 是前者，但 `act(...)` 那一批也得看）。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else '')
        if name == 'connect':
            yield node.lineno, ast.unparse(fn.value), node.args[0]
        elif name == 'act' and len(node.args) > 1:
            yield node.lineno, 'act(...)', node.args[1]


def _act_wraps_callback(tree: ast.Module) -> bool:
    """
    这个模块里的 `act(...)` 会把回调**包成 lambda** 再 connect 吗？

    为什么需要它：工具栏那批动作走的是本仓库自己的 `act('文字', self.回调)`
    写法，而 `act()` 内部已经统一
    `a.triggered.connect(lambda _checked=False: fn())` —— 于是 Qt 的 bool
    进不去回调，`add_node(at=None)` 这类就不再是缺陷。

    但这条**不能无条件放行**：一旦有人把 `act()` 里的 lambda 去掉，
    `act(...)` 那一批回调会立刻全部回到"被喂 bool"的状态。所以这里查的是
    "**`act` 的定义里真的有一个 lambda-wrapping 的 connect**"——
    有就跳过 `act(...)` 的用例，没有就照报。
    （判据写在定义处，而不是在调用处逐个判断：一处收口，一处检查。）
    """
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == 'act'):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == 'connect'
                    and sub.args
                    and isinstance(sub.args[0], ast.Lambda)):
                return True
    return False


def main() -> int:
    index = _index_all_modules()
    problems = []
    for path in sorted(UI_DIR.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        act_wrapped = _act_wraps_callback(tree)
        per_class = _methods_of(tree)
        # 类内方法 → 它属于哪个类
        owner = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                        owner.setdefault(item.name, node.name)
        for line, signame, arg in _wired_slots(tree):
            if signame == 'act(...)' and act_wrapped:
                # `act()` 内部已经包了 lambda（见 `_act_wraps_callback`）
                continue
            if not isinstance(arg, ast.Attribute):   # lambda / 别的均可
                continue
            slot = arg.attr
            cls_name = owner.get(slot)
            methods = per_class.get(cls_name or '', {})
            hit = _suspect(methods, slot, index)
            if hit:
                problems.append((path, line, signame, hit))
    if not problems:
        print('干净：没有"信号 bool 被当成参数"的接线')
        return 0
    print(f'发现 {len(problems)} 处可疑接线：')
    for path, line, signame, hit in problems:
        print(f'  {path.relative_to(ROOT)}:{line}  {signame} → {hit}')
    print('\n修法：包一层 lambda（`lambda: self.slot()` 或写清实参），'
          '别让 Qt 的 bool 变成槽的第一个参数。')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
