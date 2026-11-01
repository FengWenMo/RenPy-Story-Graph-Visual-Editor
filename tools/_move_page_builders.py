"""一次性手术刀：把 `_build_card_page` / `_build_toolbars` 连同它们用到的三个
模块级帮手（`toggle_card_column` / `CardColumnExpandFilter` / `CardActionRow`）
从 main_window.py 搬进 `window_graph_page.GraphPageMixin`。

**只搬不拆**：先让"搬"这一步单独绿，下一刀再拆 268 / 227 行。
"""
from __future__ import annotations

import ast
import pathlib

MW = pathlib.Path('storygraph/ui_qt/main_window.py')
PG = pathlib.Path('storygraph/ui_qt/window_graph_page.py')

TOP = ('toggle_card_column', 'CardColumnExpandFilter', 'CardActionRow')
METHODS = ('_build_card_page', '_build_toolbars')

TOP_NOTE = '''# ==================================================================
#  卡片页的两个"帮手"（2026-11-01 从 main_window 搬来）
# ==================================================================
# `toggle_card_column` 是模块级函数（不是主类方法）：主类成员受
# `tests/test_main_window_mixins.py` 的"每个成员都要认领"约束，
# 而它只操作窗口状态，不需要成为主类 API。`MW.toggle_card_column` 仍可用
# —— main_window 里 re-export 了。
#
# `CardActionRow` / `CardColumnExpandFilter` 是**卡片页**的控件与事件过滤器，
# 与它们的用户（`_build_card_page`）住在一起才不会越搬越远。


'''


def node_src(lines, node) -> str:
    return ''.join(lines[node.lineno - 1:node.end_lineno])


def main() -> None:
    mw_lines = MW.read_text(encoding='utf-8').splitlines(keepends=True)
    mw_tree = ast.parse(''.join(mw_lines))
    main_cls = next(n for n in mw_tree.body
                    if isinstance(n, ast.ClassDef)
                    and n.name == 'MainWindowQt')

    tops = {n.name: n for n in mw_tree.body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))
            and n.name in TOP}
    assert set(tops) == set(TOP), sorted(tops)
    meths = {n.name: n for n in main_cls.body
             if isinstance(n, ast.FunctionDef) and n.name in METHODS}
    assert set(meths) == set(METHODS), sorted(meths)

    top_text = '\n\n'.join(node_src(mw_lines, tops[n]).rstrip('\n')
                           for n in TOP)
    meth_text = '\n'.join(node_src(mw_lines, meths[n]).rstrip('\n')
                          for n in METHODS)

    # ---- 写进 mixin：帮手放在类前面，两个构建器接到类末尾 ----
    pg = PG.read_text(encoding='utf-8')
    anchor = 'class GraphPageMixin:'
    assert pg.count(anchor) == 1
    pg = pg.replace(anchor, TOP_NOTE + top_text + '\n\n\n' + anchor, 1)
    pg = pg.rstrip('\n') + '\n\n' + meth_text + '\n'
    PG.write_text(pg, encoding='utf-8')

    # ---- 从 main_window 删掉（倒序删，行号不漂）----
    drop = [tops[n] for n in TOP] + [meths[n] for n in METHODS]
    # 连带两条"§ 第 1 页 / § 工具栏"的分节注释
    extra = []
    for i, s in enumerate(mw_lines):
        if s.startswith('    # ---------------- 第 1 页：卡片编辑'):
            extra.append((i, i + 2))               # 注释 + 紧随的空行
        if s.startswith('    # ---------------- 工具栏（两组'):
            extra.append((i, i + 2))
    spans = [(n.lineno - 1, n.end_lineno) for n in drop] + extra
    for lo, hi in sorted(spans, reverse=True):
        del mw_lines[lo:hi]

    new_mw = ''.join(mw_lines)
    note = (
        '# 卡片页的两个构建器（`_build_card_page` / `_build_toolbars`）连同\n'
        '# `toggle_card_column` / `CardActionRow` / `CardColumnExpandFilter`\n'
        '# 一起搬进 `window_graph_page.GraphPageMixin`（2026-11-01）：\n'
        '# 主类那两个"方法位"就是留给它们的（预算 ≤35，见 mixins 守卫）。\n'
        '# 这里 re-export，`MW.toggle_card_column` 之类的老写法（含测试）不变。\n'
        'from storygraph.ui_qt.window_graph_page import (  # noqa: F401\n'
        '    CardActionRow, CardColumnExpandFilter, toggle_card_column)\n'
    )
    new_mw = new_mw.replace(
        'from storygraph.ui_qt.window_graph_page import GraphPageMixin\n',
        'from storygraph.ui_qt.window_graph_page import GraphPageMixin\n' + note,
        1)
    MW.write_text(new_mw, encoding='utf-8')

    # ---- 回读校验 ----
    left = {n.name for n in ast.parse(new_mw).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert not (left & set(TOP)), sorted(left & set(TOP))
    left_cls = next(n for n in ast.parse(new_mw).body
                    if isinstance(n, ast.ClassDef)
                    and n.name == 'MainWindowQt')
    left_m = {n.name for n in left_cls.body if isinstance(n, ast.FunctionDef)}
    assert not (left_m & set(METHODS)), sorted(left_m & set(METHODS))
    pg_tree = ast.parse(PG.read_text(encoding='utf-8'))
    pg_cls = next(n for n in pg_tree.body
                  if isinstance(n, ast.ClassDef) and n.name == 'GraphPageMixin')
    pg_m = {n.name for n in pg_cls.body if isinstance(n, ast.FunctionDef)}
    assert set(METHODS) <= pg_m, sorted(set(METHODS) - pg_m)
    pg_top = {n.name for n in pg_tree.body
              if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert set(TOP) <= pg_top, sorted(set(TOP) - pg_top)
    print(f'main_window.py: {len(mw_lines)} -> {len(new_mw.splitlines())} 行')
    print(f'window_graph_page.py: {len(pg.splitlines())} 行')
    print('mixin 方法数：', len(pg_m), '| 主类方法数：', len(left_m))


if __name__ == '__main__':
    main()
