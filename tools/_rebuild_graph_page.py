"""重建 `window_graph_page.py`（一次性）：前面几刀都用了"子串搜索切点"，
而 `    def act(` 是 `        def act(` 的子串 —— 连切歪两次。
这次改成**先算好、先用 ast 验、再落盘**，切点用行首匹配。
"""
from __future__ import annotations

import ast
import pathlib

PG = pathlib.Path('storygraph/ui_qt/window_graph_page.py')
CARD = pathlib.Path('tools/_new_card_page.py')
TOOLS = pathlib.Path('tools/_new_toolbars.py')

HEAD = '    def _build_card_page(self) -> QWidget:'


def main() -> None:
    src = PG.read_text(encoding='utf-8')
    lines = src.splitlines(keepends=True)
    start = next(i for i, s in enumerate(lines) if s.startswith(HEAD))
    print(f'保留前 {start} 行（docstring / imports / REQUIRES / 第 0 页）')
    head_text = ''.join(lines[:start])

    card = CARD.read_text(encoding='utf-8').rstrip('\n') + '\n'
    tools = TOOLS.read_text(encoding='utf-8').rstrip('\n') + '\n'
    out = head_text + card + '\n' + tools

    tree = ast.parse(out)                          # ⚠ 验在前、写在后
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'GraphPageMixin')
    print(f'window_graph_page.py: {len(out.splitlines())} 行')
    for n in cls.body:
        if isinstance(n, ast.FunctionDef):
            print(f'  {n.end_lineno - n.lineno + 1:4d}  {n.name}()')
    assert out.count("act('") >= 30, out.count("act('")
    assert '_install_mapping_ui(self)' in out
    PG.write_text(out, encoding='utf-8')


if __name__ == '__main__':
    main()
