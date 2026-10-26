"""
tools/_multiagent_probe_menu_brief_roundtrip.py
===============================================
E1 的延伸：`menu main.route:`（点号 menu 名）整块掉进 opaque 后被折进
**后面那句台词**的拍 —— 往返渲染后**源码顺序还在不在**？

背景：`beat_render._emit_in_token_order` 的注释写着

    「beat_build 让未建模块**独占一张拍**并在台词**之前**落卡，
      所以"代码 → 原样块 → 台词"就是源码顺序」

但点号 menu 名触发的是另一条路（整块 + 台词挤进同一张拍）。
如果 opaque 落在台词**之后**吐 —— 菜单就从"台词前"挪到"台词后"，
运行时选择点出现的时机被改（静默改剧情）。

判据：构造期望**全文**，不是 diff。期望 = 原文 + 章尾裸 `return`
（一个 return 都没有时工具会补，这是 `beat_render` 的明文设计）。
    产物 == 期望 → 过；
    菜单块跑到 e "菜单之后" 后面 → 红 = P0 级顺序颠倒。

跑法：

    /d/Python/python tools/_multiagent_probe_menu_brief_roundtrip.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402

PASS, FAIL = '[PASS]', '[FAIL]'


def parse(tmp: Path, name: str, script: str):
    fp = tmp / f'{name}.rpy'
    fp.write_text(script, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    return pr


def roundtrip(tmp: Path, name: str, script: str) -> str:
    pr = parse(tmp, name, script)
    beats = beats_from_parse_result(pr)
    return render_beats_to_rpy(beats, include_transforms=False)


def strip_marker(text: str) -> str:
    """去生成标记（首行 `# 由剧本表自动生成…` + 空行），其余一字不动。"""
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        if '由剧本表自动生成' in lines[i]:
            i += 1
            if i < len(lines) and lines[i] == '':
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def check(name, expected, actual):
    ok = expected == actual
    print(f'{PASS if ok else FAIL} {name}')
    if not ok:
        print('------- 期望 -------')
        print(expected)
        print('------- 实际（产物）-------')
        print(actual)
        print('---------------------------')
    return ok


# 判据校准（2026-10-26 第一轮跑红后修正）：产物会在**一个 return 都没有**的
# label 尾补一条裸 `return`（beat_build._emit_return 的明文设计："章尾只在
# 确实一个 return 都没有时才补"）。所以期望 = 原文 + 章尾 return，不是原文。
# 第一轮拿"原文全文"当期望 → 两条全红，红因是期望写错，不是产品缺陷。


S_DOT = '''label main:
    menu main.route:
        "点号选":
            jump ch02
        "再选":
            jump ch03
    e "菜单之后"
'''

# 对照：同结构、但 menu 名是普通标识符
S_PLAIN = '''label main:
    menu plain_menu:
        "点号选":
            jump ch02
        "再选":
            jump ch03
    e "菜单之后"
'''


def main():
    tmp = Path(tempfile.mkdtemp(prefix='probe_mb_rt_'))
    results = []
    try:
        out = roundtrip(tmp, 'dot', S_DOT)
        print('===== 点号 menu 名：往返产物 =====')
        print(out)
        results.append(check('R1 点号 menu 名：往返后源码顺序不变（全文相等）',
                             S_DOT + '    return\n', strip_marker(out)))

        out2 = roundtrip(tmp, 'plain', S_PLAIN)
        results.append(check('R2 对照：普通 menu 名往返全文相等',
                             S_PLAIN + '    return\n', strip_marker(out2)))
        if S_PLAIN + '    return\n' != strip_marker(out2):
            print('（对照组差异如上 —— 若对照也红，问题不在点号名，'
                  '是通用往返回归）')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'\n{sum(1 for r in results if r)}/{len(results)} 绿')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
