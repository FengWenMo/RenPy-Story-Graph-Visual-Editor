"""一次性手术刀：用 `tools/_new_spec_args.py` 的片段替换 card_editor 里的

    `# ---------- 语句参数那一组`  ...  到 `def _spec_values` 之前

并把四个"其实没环"的函数内 import 提到顶层。改完自己回读校验。
"""
from __future__ import annotations

import ast
import pathlib

CE = pathlib.Path('storygraph/ui_qt/card_editor.py')
FRAG = pathlib.Path('tools/_new_spec_args.py')

OLD_HEAD = '    # ---------- 语句参数那一组（`app.beats.spec_arg_*`） ----------\n'
NEW_NEXT = '    def _spec_values(self) -> dict:'


def main() -> None:
    src = CE.read_text(encoding='utf-8')
    frag = FRAG.read_text(encoding='utf-8')
    start = src.index(OLD_HEAD)
    end = src.index(NEW_NEXT)
    old = src[start:end]
    print('旧块：', len(old.splitlines()), '行')
    print('新块：', len(frag.splitlines()), '行')

    src = src[:start] + frag + '\n' + src[end:]

    # 四个函数内 import 提顶层（都是叶子，没有回边 —— 见 commit 说明）
    for old_imp, new_imp in (
        ('        from storygraph.story.flow import is_valid_label, render_flow_line\n',
         ''),
        ('        from storygraph.core.cell_limits import CELL_TEXT_LIMIT\n\n', ''),
        ('        from storygraph.ui_qt.choice_picker import choose_from\n', ''),
    ):
        assert old_imp in src, old_imp
        src = src.replace(old_imp, new_imp)

    top = ('from storygraph.core.cell_limits import CELL_TEXT_LIMIT\n'
           'from storygraph.core.safeguard import swallow\n'
           'from storygraph.story.flow import is_valid_label, render_flow_line\n'
           'from storygraph.story.spec_args import is_dynamic_column\n')
    src = src.replace('from storygraph.core.safeguard import swallow\n', top, 1)
    src = src.replace(
        'from storygraph.ui_qt.code_edit import CodeEditor\n',
        'from storygraph.ui_qt.choice_picker import choose_from\n'
        'from storygraph.ui_qt.code_edit import CodeEditor\n', 1)

    CE.write_text(src, encoding='utf-8')

    tree = ast.parse(src)
    lazy = [n for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            and not any(isinstance(p, ast.Module) for p in [])]
    tops = {n.lineno for n in tree.body
            if isinstance(n, (ast.Import, ast.ImportFrom))}
    inner = [f'{n.lineno}: {ast.unparse(n)}' for n in lazy
             if n.lineno not in tops]
    print('还剩的函数内 import：', inner or '无')
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'BeatCardEditor')
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    print('类里方法数：', len(names), '| 新方法是否都在：',
          all(m in names for m in ('_clear_spec_form', '_spec_groups',
                                   '_spec_caption', '_add_spec_group_caption',
                                   '_add_spec_cell', '_spec_cell_label',
                                   '_spec_cell_editor', '_spec_cell_pick',
                                   '_spec_multi_hint', '_apply_spec_values',
                                   '_absorb_spec_baseline')))
    print('文件行数：', len(src.splitlines()))


if __name__ == '__main__':
    main()
