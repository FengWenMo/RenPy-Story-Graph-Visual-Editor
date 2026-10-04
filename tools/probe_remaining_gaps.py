"""
量一量剩下三个缺口的真实暴露面（决定先修哪个）。

  ① **多行逻辑语句**里的行尾注释（`python:` 块体、括号续行）—— 现在不采集；
  ② **`if …:` 头自己那一行**的注释 —— 现在不采集；
  ③ 顺带数一下 label 内单行语句的注释（C 已经修掉的那类）做对照。

只读真实项目。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main() -> int:
    from storygraph.parsing.renpy_parser import RenPyLexer

    def has_comment(text: str) -> bool:
        """这段（可能多行的）逻辑行里带行尾注释吗？"""
        for ln in str(text or '').split('\n'):
            if RenPyLexer._inline_comment(ln) or RenPyLexer._inline_comment(
                    ln.strip()):
                return True
        return False

    files = sorted(f for f in (REAL / 'game').rglob('*.rpy')
                   if not f.name.endswith('.generated.rpy'))
    inline_single = Counter()      # 单行语句（C 已修）
    inline_multi = Counter()       # 多行逻辑语句（①）
    if_head = Counter()            # `if …:` 那一行的注释（②）
    total = 0

    for f in files:
        raw = f.read_text(encoding='utf-8', errors='replace').splitlines()
        # ② `if`/`elif`/`else` 头那一行带注释
        for i, ln in enumerate(raw, 1):
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            code = RenPyLexer._strip_line_comment(ln)
            if code.rstrip() == ln.rstrip():
                continue
            if s.startswith(('if ', 'elif ', 'else')):
                if_head[f.name] += 1
                total += 0
        # ① 多行逻辑语句：用词法器自己的判断（`\n` in accumulated 且带注释）
        try:
            tree = RenPyLexer().tokenize(f)
        except Exception:                       # noqa: BLE001
            continue

        def walk(lines, in_label=False):
            for ln in lines:
                text = ln.text or ''
                if '\n' in text and has_comment(text):
                    if in_label:
                        inline_multi[f.name] += 1
                elif in_label and (ln.inline_comment or ''):
                    inline_single[f.name] += 1
                walk(ln.subblock, in_label)

        for ln in tree:
            s = (ln.text or '').strip()
            if s.startswith('label ') and s.endswith(':'):
                walk(ln.subblock, True)
            else:
                walk([ln], False)

    print(f'{REAL}')
    print(f'① 多行逻辑语句里的行尾注释（label 内）：**{sum(inline_multi.values())} 处**，'
          f'{len(inline_multi)} 个文件')
    for name, n in inline_multi.most_common(5):
        print(f'     {n:>3} 处  {name}')
    print(f'② `if/elif/else` 头那一行的注释：**{sum(if_head.values())} 处**，'
          f'{len(if_head)} 个文件')
    for name, n in if_head.most_common(5):
        print(f'     {n:>3} 处  {name}')
    print(f'③ 对照：label 内单行语句的注释（C 已修）：'
          f'{sum(inline_single.values())} 处')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
