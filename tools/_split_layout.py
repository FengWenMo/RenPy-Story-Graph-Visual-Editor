#!/usr/bin/env python
"""
P3-12：把 `graph/layout.py::layout_graph`（复杂度 147 / 358 行）按**天然接缝**拆开。

它原来是**一个 358 行的函数**，里面塞着 7 个闭包 +
一整段主流程。复杂度 147 是**全项目第一名**（第二 111）。

拆法：把"有名字、有明确输入输出"的那几段提成**模块级私有函数** ——
它们本来就是以 `def` 形式写在里面的（说明原作者也想分开，
只是没搬出去）：

    break_cycles        → _break_cycles(adj)
    ensure_dag          → _ensure_dag(dag_adj, nodes)
    （分层主循环）        → _assign_layers(dag_adj, nodes, start_id)
    （虚拟节点 + 排序）   → _build_order(...)
    count_crossings     → _count_crossings(edge_segments, max_layer)
    is_virtual          → _is_virtual(nid)
    barycenter_once     → _barycenter_once(...)
    （交叉数优化）        → _reduce_crossings(...)

**行为必须逐字节不变**：这个函数决定剧情图的排版，`tests/test_layout*`
与主窗口快照都盯着它。所以只搬位置、不加也不减任何一步 ——
参数全部显式传入（原来靠闭包捕获的那几个也传进去），
不引入任何新的"全局状态"。

⚠ 本脚本**一次性**用（它按 `ast` 定位后重写整个文件）；
重复运行会认不出锚点而报错，不会造成二次破坏。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'storygraph' / 'graph' / 'layout.py'


def main() -> int:
    src = P.read_text(encoding='utf-8')
    if '_break_cycles' in src:
        print('已经拆过了（找到 _break_cycles）—— 不重复动手')
        return 0

    tree = ast.parse(src)
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'layout_graph':
            fn = node
    if fn is None:
        print('** 找不到 layout_graph')
        return 1

    lines = src.splitlines(keepends=True)

    # —— 1. 取出要搬走的闭包源码（含 def 行到 end_lineno）——
    want = {'break_cycles': '_break_cycles', 'ensure_dag': '_ensure_dag',
            'count_crossings': '_count_crossings',
            'is_virtual': '_is_virtual',
            'barycenter_once': '_barycenter_once'}
    seg = {}
    for sub in ast.walk(fn):
        if isinstance(sub, ast.FunctionDef) and sub.name in want:
            seg[sub.name] = ''.join(
                lines[sub.lineno - 1:sub.end_lineno])
    missing = set(want) - set(seg)
    if missing:
        print('** 找不到这些内层函数：', sorted(missing))
        return 1

    # —— 2. 删掉内层定义（从后往前删，行号不漂）——
    spans = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.FunctionDef) and sub.name in want:
            spans.append((sub.lineno, sub.end_lineno))
    new_lines = list(lines)
    for a, b in sorted(spans, reverse=True):
        del new_lines[a - 1:b]
    text = ''.join(new_lines)

    # —— 3. 调用点改名 ——
    for old, new in want.items():
        text = text.replace(f'{old}(', f'{new}(')
    # `break_cycles(adj)` 已经变成 `_break_cycles(adj)`

    # —— 4. 闭包捕获的变量改成显式传参 ——
    # `_is_virtual` 无捕获；`_count_crossings` 捕获 edge_segments/max_layer；
    # `_barycenter_once` 捕获 max_layer/ext_neighbors；
    # `_break_cycles`/`_ensure_dag` 无捕获。
    text = text.replace('_count_crossings(order)',
                        '_count_crossings(order, edge_segments, max_layer)')
    text = text.replace('_barycenter_once(order_ext)',
                        '_barycenter_once(order_ext, max_layer, ext_neighbors)')
    # `_ensure_dag` 里 `is_virtual` 不存在；`_barycenter_once` 里用到
    # `is_virtual` → 也改成模块级名
    text = text.replace('is_virtual(nb)', '_is_virtual(nb)')

    # —— 5. 把内层函数按模块级形式重写 ——
    def module_level(name: str, extra_params: str, extra_doc: str) -> str:
        body = seg[name]
        old_sig = f'def {name}('
        i = body.index(old_sig)
        j = body.index('):', i) + 2
        head = body[:i]
        rest = body[j:]
        # 去掉多余的 4 空格缩进（原来在函数里，深一层）
        dedented = []
        for ln in rest.splitlines(keepends=True):
            dedented.append(ln[4:] if ln.startswith('    ') else ln)
        sig = f'{old_sig}{name.lstrip("_")}'
        # 参数在调用点已经传了第三个/第二个，按需要补到签名里
        return f'{sig}{extra_params}' + ''.join(dedented) + '\n\n'

    raise SystemExit('未使用')


if __name__ == '__main__':
    sys.exit(main())
