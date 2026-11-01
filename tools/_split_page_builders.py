"""一次性手术刀：把 mixin 里的 `_build_card_page`（268 行）和
`_build_toolbars`（227 行）按"屏"拆开。片段来自 `_new_card_page.py` /
`_new_toolbars.py`（这两份是新的完整实现，注释原样保留）。
"""
from __future__ import annotations

import ast
import pathlib

PG = pathlib.Path('storygraph/ui_qt/window_graph_page.py')
CARD = pathlib.Path('tools/_new_card_page.py')
TOOLS = pathlib.Path('tools/_new_toolbars.py')


def splice(src: str, head: str, frag_text: str) -> str:
    a = src.index(head)
    b = src.index('    def ', a + len(head))
    return src[:a] + frag_text.rstrip('\n') + '\n\n' + src[b:]


def main() -> None:
    src = PG.read_text(encoding='utf-8')
    n0 = len(src.splitlines())
    src = splice(src, '    def _build_card_page(self) -> QWidget:',
                 CARD.read_text(encoding='utf-8'))
    src = splice(src, '    def _build_toolbars(self):',
                 TOOLS.read_text(encoding='utf-8'))
    PG.write_text(src, encoding='utf-8')

    tree = ast.parse(src)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'GraphPageMixin')
    print(f'window_graph_page.py: {n0} -> {len(src.splitlines())} 行')
    for n in cls.body:
        if isinstance(n, ast.FunctionDef):
            print(f'  {n.end_lineno - n.lineno + 1:4d}  {n.name}()')
    # `act(...)` 的词法形态必须还在（两条扫描器按它工作）
    assert src.count("act('") >= 30, src.count("act('")
    assert 'def act(text, fn, tip=None, primary=False):' in src


if __name__ == '__main__':
    main()
