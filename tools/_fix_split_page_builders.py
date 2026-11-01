"""修上一刀切歪的地方（一次性）：`str.index('    def ', ...)` 会命中
8 空格缩进的 `        def act(...)` —— 于是 `_build_toolbars` 被切成两半，
插进去的片段后面还挂着一截 `    def act(...)`。

修法：从"那截旧尾巴"的开头截断，再把工具栏片段接上去。然后逐行按下标切，
不再用子串搜索。
"""
from __future__ import annotations

import ast
import pathlib

PG = pathlib.Path('storygraph/ui_qt/window_graph_page.py')
TOOLS = pathlib.Path('tools/_new_toolbars.py')

LEFTOVER = '    def act(text, fn, tip=None, primary=False):'


def main() -> None:
    src = PG.read_text(encoding='utf-8')
    i = src.index(LEFTOVER)
    frag = TOOLS.read_text(encoding='utf-8').rstrip('\n') + '\n'
    src = src[:i] + frag
    PG.write_text(src, encoding='utf-8')

    tree = ast.parse(src)                          # 先确认语法过
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'GraphPageMixin')
    print(f'window_graph_page.py: {len(src.splitlines())} 行')
    for n in cls.body:
        if isinstance(n, ast.FunctionDef):
            print(f'  {n.end_lineno - n.lineno + 1:4d}  {n.name}()')
    assert src.count("act('") >= 30, src.count("act('")
    assert 'def act(text, fn, tip=None, primary=False):' in src
    assert src.rstrip().endswith('_install_mapping_ui(self)')


if __name__ == '__main__':
    main()
