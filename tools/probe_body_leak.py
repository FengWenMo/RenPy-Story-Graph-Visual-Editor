"""
② 的真身：**选项体里的舞台指令漏到菜单外、变成无条件执行**。

用户报到的是"选项文案带换行 → 第二轮选项体变 `pass`、丢内容"。
探针打出来的是更狠的一条（同一根因）：

    原文                                  产物
    menu:                                 menu:
        "选项":                               "选项":
            show bg room        ← 选中才播       pass
    e "体"                                show bg room   ← **无条件播**
                                          e "体"

病根不在转义，在**拍的合并**：`show`（路径 `menu:1>A`）已经被吸进 pending，
后面那句 `e "体"`（路径 `''`）**只比条件、不比位置** —— 条件都是空串，
于是两者并成**同一张拍**，`mark_struct` 又把这张拍的结构位覆盖成 `''`
（最后那条语句的路径）。舞台指令就这么出了菜单。

`pass` 只是这个根因最显眼的形态：空选项体补的 `pass` 也是一条"在体内"的语句，
它被并进菜单外那张拍，于是产物里**多出一个顶层 `pass`**（第二轮 ≠ 第一轮）。

用法：
    python tools/probe_body_leak.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASES = {
    'stage_leak': '''label a:
    menu:
        "选项":
            show bg room
    e "体"
    return
''',
    'if_in_body_leak': '''label a:
    menu:
        "选项":
            show x
            if f:
                show y
    e "尾"
    return
''',
    'nested_body_leak': '''label a:
    menu:
        "选项":
            show bg deep
    if flag:
        e "尾"
    return
''',
}


def main() -> int:
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_render import render_beats_to_rpy

    tmp = Path(tempfile.mkdtemp(prefix='leak_'))
    for name, src in CASES.items():
        print('=' * 74)
        print(f'[{name}] 原文：')
        for ln in src.splitlines():
            print(f'    |{ln}')
        f = tmp / f'{name}.rpy'
        f.write_text(src, encoding='utf-8')
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        print('  解析出的拍：')
        for i, b in enumerate(beats):
            print(f'    [{i}] kind={b.kind:<12} show={b.show!r:<16} '
                  f'text={b.text!r:<8} cond={(b.condition or "").strip()!r:<8} '
                  f'nest={b.nest_path!r}')
        out = render_beats_to_rpy(beats, label_name='a')
        print('  产物：')
        for ln in out.splitlines():
            if ln.strip() and not ln.startswith('#'):
                print(f'    |{ln}')
        # 产物再解析一轮（"第二轮"）
        f2 = tmp / f'{name}_2.rpy'
        f2.write_text(out, encoding='utf-8')
        pr2, _ = IncrementalRenPyAnalyzer().analyze(files=[f2], graph=None)
        out2 = render_beats_to_rpy(beats_from_parse_result(pr2),
                                   label_name='a')
        extra = [ln.strip() for ln in out2.splitlines()
                 if ln.strip() and ln.strip() not in
                 [x.strip() for x in out.splitlines()]]
        print(f'  第二轮多出来的行：{extra}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
