#!/usr/bin/env python
"""
把 `_parse_statement` 的 375 行 if 链换成薄分派（P3-12 第五步，手写版）。

处理函数已经手写并插在它前面了（见文件里的 `_stmt_*`）；这一步只换函数体。

⚠ 上一轮我用"从 `ast` 取每段行区间"的生成器做这件事，跑通了但结果是错的
（两次都是"`ast.parse` 通过、跑真实项目才炸"）。所以这次**只做一件事**：
定位整个函数、整段替换。边界的找法写得尽量笨：
从 `def _parse_statement(` 起，到下一个**与它同缩进的 `def `** 为止。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'storygraph' / 'parsing' / 'renpy_parser.py'

#: 分派顺序**就是匹配优先级**，不能重排（与 `_stmt_*` 的定义顺序一致）。
ORDER = ['comment', 'label', 'define', 'menu', 'if', 'jump_expression',
         'call_expression', 'call_screen', 'jump', 'call', 'return',
         'dollar', 'python_block', 'layeredimage', 'image_def', 'screen_op',
         'show', 'scene', 'hide', 'audio', 'voice', 'extend', 'say',
         'transform', 'unmodeled']


def main() -> int:
    lines = P.read_text(encoding='utf-8').splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines)
                 if ln.lstrip().startswith('def _parse_statement('))
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        s = lines[end]
        if s.strip() and (len(s) - len(s.lstrip())) <= indent \
                and s.lstrip().startswith('def '):
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    old = end - start
    if old < 100:
        print(f'** 旧函数只有 {old} 行，看着不对，拒绝改')
        return 1
    pad = ' ' * (indent + 4)
    out = [
        lines[start],                       # def 那一行（含类型注解）
        f'{pad}"""一条逻辑行 → AST 节点（按语句种类分派）。\n',
        '\n',
        f'{pad}每个分支各自住在 `_stmt_*` 里（P3-12 拆的：原本是 375 行 /\n',
        f'{pad}圈复杂度 60 的扁平 if 链）。**分派顺序就是匹配优先级**，\n',
        f'{pad}不能重排 —— 它必须与 `_stmt_*` 的定义顺序一致：\n',
        '\n',
    ]
    for i, slug in enumerate(ORDER):
        out.append(f'{pad}    {i + 1:2d}. `{slug}`\n')
    out += [
        '\n',
        f'{pad}`_stmt_*` 返回 `self._NO_MATCH` 表示"这条语句我认不出，\n',
        f'{pad}试下一个分支"；返回别的（含 `None`）表示"有结论了，\n',
        f'{pad}就此结束" —— 两者**必须分开**，否则一句坏掉的 `if` 会被\n',
        f'{pad}后面的 `_stmt_say` 当作台词吃掉。\n',
        f'{pad}"""\n',
    ]
    for slug in ORDER:
        out.append(f'{pad}node = self._stmt_{slug}(line, text)\n')
        out.append(f'{pad}if node is not self._NO_MATCH:\n')
        out.append(f'{pad}    return node\n')
    out.append(f'{pad}return None\n')

    new = ''.join(lines[:start]) + ''.join(out) + ''.join(lines[end:])
    try:
        ast.parse(new)
    except SyntaxError as e:
        print('** 改完不可解析：', e)
        bad = new.splitlines()
        if e.lineno:
            for k in range(max(0, e.lineno - 4), min(len(bad), e.lineno + 3)):
                print('%5d| %s' % (k + 1, bad[k]))
        return 1
    P.write_text(new, encoding='utf-8')
    print(f'旧函数 {old} 行 → 分派 {len(out)} 行')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
