"""
真实项目里这两条边界**到底有多严重**（决定先修哪一条）。只读，不写。

修掉第一版的粗糙：只有**菜单块里的选项**才算选项；行尾注释要分开数
"label 外面"（顶层块，现在随表逐字保真了 → 安全）和"label 里面"
（走拍/IR → 有风险）。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')

BLOCK_HEADS = ('if ', 'elif ', 'else', 'menu', 'for ', 'while ', 'python',
               'init ', 'screen ', 'style ', 'translate ')


def main(argv) -> int:
    project = Path(argv[1]) if len(argv) > 1 else REAL
    if not project.exists():
        print('真实项目不在，跳过')
        return 0

    from storygraph.parsing.renpy_parser import RenPyLexer

    files = sorted(f for f in (project / 'game').rglob('*.rpy')
                   if not f.name.endswith('.generated.rpy'))
    lexer = RenPyLexer()

    menus = 0
    options = 0
    complex_opts = 0                     # 选项体：>1 条语句，或内含块
    complex_files = Counter()
    opt_files = Counter()
    inline_in_label = Counter()
    inline_outer = Counter()

    def is_option(ln) -> bool:
        head = ln.text.strip()
        return head.startswith('"') or head.startswith("'")

    def walk(lines, in_label=False, parent_is_menu=False):
        nonlocal menus, options, complex_opts
        for ln in lines:
            head = ln.text.strip()
            if not in_label and (head.startswith('label ') and head.endswith(':')):
                walk(ln.subblock, in_label=True)
                continue
            if head.startswith('menu'):
                menus += 1
                walk(ln.subblock, in_label, parent_is_menu=True)
                continue
            if parent_is_menu and is_option(ln):
                options += 1
                body = [b for b in ln.subblock if b.text.strip()]
                has_block = any(b.text.strip().startswith(BLOCK_HEADS)
                                for b in body)
                if len(body) > 1 or has_block:
                    complex_opts += 1
                    complex_files[CURRENT[0]] += 1
                opt_files[CURRENT[0]] += 1
            walk(ln.subblock, in_label, parent_is_menu=False)

    CURRENT = ['']

    def line_numbers(lines) -> set:
        """一棵子树里所有逻辑行的行号（含嵌套）。"""
        out = set()
        for ln in lines:
            out.add(ln.line_number)
            out |= line_numbers(ln.subblock)
        return out

    for f in files:
        CURRENT[0] = f.name
        raw = f.read_text(encoding='utf-8', errors='replace').splitlines()
        try:
            tree = lexer.tokenize(f)
        except Exception:                 # noqa: BLE001
            continue
        # **label 里面**：按语法树位置判（不是按缩进 —— 缩进 >0 的
        # `screen` / `init python` 体在顶层，它们现在逐字保真、是安全的）
        in_label_lines: set = set()
        for ln in tree:
            if ln.text.strip().startswith('label ') and ln.text.strip().endswith(':'):
                in_label_lines |= line_numbers(ln.subblock)
        for ln in tree:
            for n in sorted(line_numbers([ln])):
                if n - 1 >= len(raw):
                    continue
                text = raw[n - 1]
                code = RenPyLexer._strip_line_comment(text)
                if not code.strip() or code.rstrip() == text.rstrip():
                    continue
                (inline_in_label if n in in_label_lines
                 else inline_outer)[f.name] += 1
        walk(tree)

    print(f'{project}')
    print(f'  文件 {len(files)}')
    print(f'\nA. **菜单选项体**（全量路径上"复杂选项体"会被压平 → 改语义）')
    print(f'   菜单块 {menus} 个 / 选项 {options} 个；'
          f'**复杂选项体 {complex_opts} 个**（占 '
          f'{(complex_opts / options * 100) if options else 0:.0f}%），'
          f'分布在 {len(complex_files)} 个文件')
    for name, n in complex_files.most_common(8):
        print(f'     {n:>4} 处 / {opt_files[name]:>4} 个选项   {name}')
    print(f'\nB. **行尾注释**（词法器直接剥掉 → 全量必丢；增量只保住没改的行）')
    print(f'   label **里面**（走拍/IR，有风险）：{sum(inline_in_label.values())} 行，'
          f'{len(inline_in_label)} 个文件')
    for name, n in inline_in_label.most_common(8):
        print(f'     {n:>4} 行   {name}')
    print(f'   label **外面**（顶层块，现在随表逐字保真 → 安全）：'
          f'{sum(inline_outer.values())} 行')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
