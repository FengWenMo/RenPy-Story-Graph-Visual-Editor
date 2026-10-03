"""
探针：几个 mixin 的"依赖声明"有没有撒谎。

`EditorSession` 拆成 5 个 mixin 之后，每个 mixin 都声明了 `REQUIRES`
（"宿主必须给我这些东西"）。但 `REQUIRES` 是**手写的**，而代码里真正
用到的 `self.X` 是**事实**。两者可能对不上：

  · 用了却没声明 → 现在恰好能跑（宿主有），哪天宿主改了就炸；
  · 声明了却没用 → 守卫在守一个不存在的依赖，白占一条断言。

这个脚本用 `ast` 扫出事实，再和声明比对。只读，不写盘。

用法：
    D:/Python/python.exe tools/audit_mixin_contract.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

BLOCKS = {
    'session_table': ('storygraph.app.session_table', 'TableSessionMixin'),
    'session_graph': ('storygraph.app.session_graph', 'GraphSessionMixin'),
    'session_assets': ('storygraph.app.session_assets', 'AssetRegistryMixin'),
    'session_export': ('storygraph.app.session_export', 'ExportMixin'),
    'session_collab': ('storygraph.app.session_collab', 'CollabMixin'),
}

APP = ROOT / 'storygraph' / 'app'


def _class_def(path: Path, cls_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            return node
    raise SystemExit(f'{path.name} 里找不到 {cls_name}')


def _own_members(cls: ast.ClassDef) -> set:
    """这个类自己定义的成员名（方法 / 类属性）。"""
    out = set()
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(item.name)
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            out.add(item.target.id)
    return out


def _self_attrs(cls: ast.ClassDef) -> tuple:
    """
    类体里**读取**的 `self.X`（Load 上下文）。

    只看 Load：`self.beats = []` 是**造**一个实例属性，不是依赖宿主
    —— 把 Store 算进来会让每个 mixin 都"依赖"一大堆自己的私有状态。
    """
    loads, stores = set(), set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == 'self'):
            (loads if isinstance(node.ctx, ast.Load) else stores).add(node.attr)
    return loads, stores


def _requires_literal(cls: ast.ClassDef) -> list:
    """类体里 `REQUIRES = (...)` / `[...]` 的字面值。"""
    for item in cls.body:
        if isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name) and t.id == 'REQUIRES':
                    try:
                        return list(ast.literal_eval(item.value))
                    except ValueError:
                        return []
    return []


def main() -> int:
    import importlib
    from storygraph.app.editor import EditorSession

    session = EditorSession()
    problems = 0

    for key, (mod_name, cls_name) in sorted(BLOCKS.items()):
        path = APP / f'{key}.py'
        cls = _class_def(path, cls_name)
        own = _own_members(cls)
        loads, _stores = _self_attrs(cls)
        declared = set(_requires_literal(cls))

        # 读了 self.X，但 X 不是本 mixin 自己的 → 这是对外依赖
        external = loads - own
        undeclared = sorted(external - declared)
        unused_decl = sorted(declared - external)

        # 真正会在运行时炸的：既没声明、宿主实例上也取不到
        unreachable = [n for n in undeclared if not hasattr(session, n)]

        print(f'\n=== {cls_name} ({key}.py) ===')
        print(f'  自己定义 {len(own)} 个成员；读到 self.* {len(loads)} 个；'
              f'声明 REQUIRES {len(declared)} 个')
        if undeclared:
            print(f'  ⚠ 用了却没声明（{len(undeclared)}）: {undeclared}')
        if unreachable:
            print(f'  ✗ 宿主上也取不到（必炸）: {unreachable}')
            problems += len(unreachable)
        if unused_decl:
            print(f'  · 声明了却没用到: {unused_decl}')

    # 反方向：mixin 上的公开方法有没有被 BLOCKS 清单登记
    print('\n=== 反向：mixin 定义的方法 vs 清单 ===')
    test_file = ROOT / 'tests' / 'test_editor_session_split.py'
    tree = ast.parse(test_file.read_text(encoding='utf-8'))
    registered = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            registered.add(node.value)
    for key, (mod_name, cls_name) in sorted(BLOCKS.items()):
        path = APP / f'{key}.py'
        cls = _class_def(path, cls_name)
        public = {n for n in _own_members(cls)
                  if not n.startswith('__') and n != 'REQUIRES'}
        missing = sorted(n for n in public if n not in registered)
        if missing:
            print(f'  ⚠ {cls_name} 有 {len(missing)} 个方法没登记进守卫清单: '
                  f'{missing}')

    print('\n结论：', '有必炸的依赖' if problems else '没有必炸的依赖')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
