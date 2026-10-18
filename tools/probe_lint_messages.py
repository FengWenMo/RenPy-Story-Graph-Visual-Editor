"""
引擎 lint 的**报错措辞**样本表（2026-10-18，为 `engine_lint` 的 fail-closed 判据取证）。

为什么要有这个探针：`classify()` 要决定"这条报错算语法还是算语义"，而判据
只能来自**引擎真实输出**。凭印象写白名单，就会重犯 `_PARSE_MARK` 的错
（正向清单漏掉 `'{' was never closed`，闸门直接放行）。

跑法（需要 Ren'Py SDK）：

    python tools/probe_lint_messages.py            # 全部样本
    python tools/probe_lint_messages.py --only brace

输出：每条样本的 `parse_errors` / `semantic_errors` 分类 + 引擎原始输出里
**带行号的那几行**。改判据前后各跑一次，比对"归属有没有变"。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storygraph.pipeline.engine_lint import lint_text          # noqa: E402

#: 每条样本：`(键, 说明, 期望归类, 文本)`
#: 期望归类只有三种：`parse`（阻塞）/ `semantic`（不阻塞）/ `none`（不该报）
CASES = [
    ('brace', '未闭合的花括号（本轮 P0 的产物形态）', 'parse', '''
define bubble.expand_area = {
    "thought" : (0, 0, 0, 0),

label start:
    e "hi"
    return
'''),
    ('bracket', '未闭合的方括号 / 圆括号', 'parse', '''
init python:
    values = [
        1, 2, 3

label start:
    e "hi"
    return
'''),
    ('expected_colon', "`expected ':' not found`（Ren'Py 语句级语法错）", 'parse', '''
label start:
    menu "caption"
        "a":
            e "hi"
    return
'''),
    ('indent', '缩进错', 'parse', '''
label start:
e "hi"
    return
'''),
    ('bad_expr', 'say 语句里的表达式编译失败', 'parse', '''
define e = Character("E")

label start:
    e "hi" (1 +
    return
'''),
    ('missing_image', '缺图（语义：临时工程里没有图，但语法是好的）', 'semantic', '''
define e = Character("E")

label start:
    scene bg definitely_not_here
    e "hi"
    return
'''),
    ('undefined_char', '角色未定义（语义）', 'semantic', '''
label start:
    ghost "hi"
    return
'''),
    ('missing_label', '跳转到不存在的 label（语义）', 'semantic', '''
label start:
    jump nowhere_at_all
'''),
    ('missing_audio', '音频文件不存在（语义）', 'semantic', '''
label start:
    play music "audio/nope_not_here.ogg"
    return
'''),
    ('init_raise', 'init python 在 lint 时抛异常（产物加载即崩）', 'parse', '''
init python:
    def boom():
        return 1 / 0
    boom()

label start:
    e "hi"
    return
'''),
    ('unknown_style', '样式属性不认识（**加载时抛的异常** → 阻塞）', 'parse', '''
style foo:
    definitely_not_a_property 3

label start:
    e "hi"
    return
'''),
    ('dup_label', '重复 label（引擎不报：后一个直接覆盖前一个）', 'none', '''
label start:
    e "hi"
    return

label start:
    e "bye"
    return
'''),
    ('undefined_screen', 'show screen 了不存在的 screen（语义）', 'semantic', '''
label start:
    show screen nope_screen
    e "hi"
    return
'''),
    ('good', '干净样本（必须一条都不报）', 'none', '''
define e = Character("E")

label start:
    e "hi"
    return
'''),
]


def _with_lines(raw: str) -> list:
    """引擎输出里**带行号**的行（`File "...", line N:` 或 `game/script.rpy:N`）。"""
    out = []
    for ln in str(raw or '').splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith('File "') and 'line' in s:
            out.append(s)
        elif s.startswith('game/script.rpy:'):
            out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', action='append', default=[],
                    help='只跑这些键（可重复）')
    ap.add_argument('--raw', action='store_true', help='连引擎全文一起打')
    args = ap.parse_args()

    bad = 0
    for key, desc, want, text in CASES:
        if args.only and key not in args.only:
            continue
        rep = lint_text(text)
        got = ('parse' if rep.parse_errors
               else 'semantic' if rep.semantic_errors else 'none')
        mark = '✓' if got == want else '✗'
        if got != want:
            bad += 1
        print(f'{mark} {key:14s} 期望 {want:8s} 实得 {got:8s}  {desc}')
        for e in rep.parse_errors:
            print(f'    [parse]    {e}')
        for e in rep.semantic_errors:
            print(f'    [semantic] {e}')
        for ln in _with_lines(rep.raw):
            print(f'    [raw]      {ln}')
        if args.raw:
            print(rep.raw)
        print()
    print(f'—— {len([c for c in CASES if not args.only or c[0] in args.only])}'
          f' 条，{bad} 条与期望不符 ——')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
