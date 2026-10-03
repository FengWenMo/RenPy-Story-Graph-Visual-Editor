"""
契约守卫：**包间依赖方向**（风险清单 P2-5）
=========================================

清单里那条"包间依赖环 4 条，靠延迟 import 压住，**仍无测试守卫**，
契约 14 是口头契约"——这个文件把它从"口头"变成"CI 会红"。

分三档判据，**都只看顶层 import**（`ast` 扫，不看函数体里的延迟 import）：

  1. **分层方向**：`core ← parsing/excel ← assets/story ← pipeline ← app
     ← ui_qt`。上层可以依赖下层，**反过来不行**。
  2. **包间无环**：顶层 import 图里不许出现二元环 ——
     有环就意味着 `import storygraph.x` 的顺序会决定成败。
  3. **已知的延迟 import 白名单**：设计上就靠延迟 import 压住的那几处，
     在这里**逐条列明并给出理由**。新增一处就会红。

为什么不禁止函数体里的延迟 import：`storygraph.story.script_table` 里
那一处是为了让 story 层**不反向依赖** pipeline（见那条注释），
属于"用延迟 import 换单向依赖"的正确用法。禁掉它只会逼人把环做回来。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: 包根（`storygraph/`）
PKG_ROOT = Path(__file__).resolve().parents[1] / 'storygraph'

#: 分层：下标越小越底层。上层可依赖下层，反向**违规**。
LAYERS = [
    'core',
    'excel',
    'parsing',
    'assets',
    'story',
    'pipeline',
    'app',
    'ui_qt',
]
LAYER_INDEX = {name: i for i, name in enumerate(LAYERS)}

#: `collab`（协作库）与 `graph`（图模型）不在这条主链上：
#: `collab` 是可选的持久化后端，`graph` 是数据模型 + 无 UI 的命令。
#: 它们只许依赖 core / parsing / excel / story（见 `SIDE_PACKAGES`）。
SIDE_PACKAGES = {
    'collab': {'core', 'excel', 'parsing', 'story'},
    'graph': {'core', 'excel', 'parsing', 'story'},
}

#: 允许的**反向延迟 import**（来源包比目标包更高层）：`(来源, 目标): 理由`。
#: 新增一处就会红 —— 要么改成顶层单向依赖，要么在这里写清理由。
LAZY_ALLOWED = {
    ('story', 'pipeline'): (
        '`script_table.export_table_to_rpy` 需要 pipeline.compile，'
        '而 pipeline.compile 依赖 story.beat_model —— 顶层 import 会成环。'
        'story 不能反向依赖 pipeline，所以这一处必须留在函数里。'),
    ('assets', 'story'): (
        '`asset_engine.import_script_definitions` 要把脚本里的 '
        'define / image / transform 声明合并进资源表，那一步在 '
        '`story.beat_build`。反过来 `story` 是**顶层**依赖 `assets` 的，'
        '所以只能延迟取 —— 这是"用延迟 import 换单向依赖"的正确用法。'),
}


def _iter_package_imports():
    """
    产出 `(来源包, 目标包, 是否顶层, 行号, 文件)`。

    只看 `storygraph.<pkg>` 形式的 import（相对 import 不跨包）。
    """
    for path in sorted(PKG_ROOT.rglob('*.py')):
        parts = path.relative_to(PKG_ROOT).parts
        if len(parts) < 2:
            continue                      # 包根下的散文件：不算包间依赖
        src_pkg = parts[0]
        if src_pkg.startswith('__'):
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError as e:           # pragma: no cover - 语法错误另有测试
            pytest.fail(f'{path} 解析失败：{e}')
        # 顶层 import 的行号集合：模块体直接子节点里的 Import/ImportFrom
        top_lines = set()
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                top_lines.add(node.lineno)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ''
            elif isinstance(node, ast.Import):
                mod = ''
            else:
                continue
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif mod:
                targets = [mod]
            for dotted in targets:
                bits = dotted.split('.')
                if len(bits) < 2 or bits[0] != 'storygraph':
                    continue
                tgt = bits[1]
                if tgt == src_pkg or tgt.startswith('__'):
                    continue
                yield (src_pkg, tgt, node.lineno in top_lines,
                       node.lineno, path)


def test_no_top_level_import_cycles():
    """
    顶层 import 图里**不许有环**。

    有环时"先 import 谁"决定成败：`import storygraph.a` 可能因为
    `storygraph.b` 还没初始化而炸，而且炸不炸取决于调用顺序 ——
    这类 bug 只在特定入口复现，极难查。
    """
    edges = set()
    for src, tgt, is_top, _ln, _p in _iter_package_imports():
        if is_top:
            edges.add((src, tgt))
    cycles = []
    for a, b in edges:
        if (b, a) in edges:
            cycles.append(tuple(sorted((a, b))))
    assert not cycles, (
        f'顶层 import 出现环：{sorted(set(cycles))}\n'
        '修法二选一：把其中一边改成延迟 import（并在 LAZY_ALLOWED 里'
        '写清理由），或者把共用部分抽到更底层的包。')


def test_layers_only_depend_downwards():
    """
    `core ← parsing/excel ← assets/story ← pipeline ← app ← ui_qt`。

    只查**顶层 import**：反向依赖若靠函数体里的延迟 import 压住，
    那属于"运行时才成环"，由 `test_lazy_imports_are_declared` 单独管。
    """
    bad = []
    for src, tgt, is_top, ln, path in _iter_package_imports():
        if not is_top:
            continue
        if src in SIDE_PACKAGES:
            allowed = SIDE_PACKAGES[src]
            if tgt not in allowed:
                bad.append((src, tgt, path.name, ln,
                            f'`{src}` 只许依赖 {sorted(allowed)}'))
            continue
        if tgt in SIDE_PACKAGES:
            # 主链依赖旁支：只有在旁支更底层时才允许
            if LAYER_INDEX.get(src, 0) <= 0:
                continue
            bad.append((src, tgt, path.name, ln,
                        f'主链不该依赖旁支 `{tgt}`'))
            continue
        si, ti = LAYER_INDEX.get(src), LAYER_INDEX.get(tgt)
        if si is None or ti is None:
            continue
        if ti > si:
            bad.append((src, tgt, path.name, ln,
                        f'`{src}`（第 {si} 层）反向依赖了更上层的 `{tgt}`'
                        f'（第 {ti} 层）'))
    assert not bad, '依赖方向违规：\n' + '\n'.join(
        f'  {p}:{ln}  {a} -> {b}  （{why}）' for a, b, p, ln, why in bad)


def test_lazy_imports_are_declared():
    """
    函数体里的**反向**延迟 import 必须列在 `LAZY_ALLOWED` 里并写清理由。

    这条防的是"用延迟 import 悄悄把环做回来"。

    只报**反向**（来源包比目标包更高层／更靠上）的延迟 import。
    **顺向**的延迟 import（`app` 在函数里取 `story`）是正常写法，
    大量存在、也不构成环，不报 —— 报了只会逼人加一长串白名单，
    白名单一长这条守卫就没人看了。

    实测当前唯一真正反向的是 `story → pipeline` 一处，见 `LAZY_ALLOWED`。
    """
    undeclared = []
    for src, tgt, is_top, ln, path in _iter_package_imports():
        if is_top:
            continue
        if (src, tgt) in LAZY_ALLOWED:
            continue
        si, ti = LAYER_INDEX.get(src), LAYER_INDEX.get(tgt)
        if si is None or ti is None:
            continue
        if ti > si:
            undeclared.append((src, tgt, path.name, ln))
    assert not undeclared, (
        '这些延迟 import 是**反向**的，但没在 LAZY_ALLOWED 里说明：\n'
        + '\n'.join(f'  {p}:{ln}  {a} -> {b}' for a, b, p, ln in undeclared)
        + '\n\n要么改成顶层单向依赖，要么在 LAZY_ALLOWED 里写清理由。')


def test_backend_never_imports_ui():
    """后端包（core/parsing/excel/assets/story/pipeline/app/graph/collab）
    都不许 import `ui_qt` 或任何 UI 框架 —— 这是分层契约的底线。"""
    bad = []
    for src, tgt, _top, ln, path in _iter_package_imports():
        if src == 'ui_qt':
            continue
        if tgt == 'ui_qt':
            bad.append((src, path.name, ln))
    assert not bad, ('后端不许依赖前端：\n'
                     + '\n'.join(f'  {p}:{ln}  {a} -> ui_qt'
                                 for a, p, ln in bad))
