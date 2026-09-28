"""
tools/check_panel_widgets.py — 用**真 tkinter** 验卡片字段写得进去。

为什么需要这个工具（而不是一条普通测试）：

2026-09-28 的事故——卡片上「说话人 / 表情 / 位置」三个格子全空，
只有「台词」有值。根因是 `_wset` 用 `hasattr(w, 'set')` 判能不能写，
而 **`tk.Entry` 没有 `set()` 方法**，于是单行字段**静默**写不进去。

**为什么测试没拦住**：无头环境里 tkinter 是桩，测试用的是"记录型控件桩"，
而桩给假 Entry 加了 `set()` —— **桩比真控件更聪明，把 bug 藏起来了**。
所以这个检查必须跑**真 Tk**，也就必须有显示器。

    python tools/check_panel_widgets.py

输出里每个字段前是 `OK`/`!!`；出现 `!!`（值为空）就说明单行字段又写不进去了。
无头环境会直接抛 TclError，跑不了属正常。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk                                                # noqa: E402

from beat_editor import BeatEditorPanel, beat_to_fields             # noqa: E402
from renpy_parser import ParseResultBuilder, RenPyParser            # noqa: E402
from script_table import (                                          # noqa: E402
    Beat, beats_from_parse_result, compose_stage, make_symbols,
)

REAL = Path(r'D:\千连万理\RenPyTest\sr_general\game\chapter\chapter1_3.rpy')


def main():
    root = tk.Tk()
    root.withdraw()                     # 不弹窗，只要控件能建起来

    def dump(panel, title):
        print(f'=== {title} ===')
        # 说话人 / 台词（单行 Entry 走 StringVar；多行走 Text）
        for name in ('Speaker', 'Text'):
            w = panel._widgets.get(name)
            val = panel._wget(name)
            flag = '  ' if val else '!! '
            print(f'  {flag}{name:11s} [{type(w).__name__:12s}] '
                  f'_wget={val!r}')
        # 立绘数组：每张立绘自己一行（表情/位置在行内）
        rows = getattr(panel, '_sprite_widgets', [])
        print(f'  立绘行数 = {len(rows)}')
        for i, r in enumerate(rows, 1):
            img = r['image'].get()
            expr = r['expression'].get()
            pos = r['position'].get()
            ok = bool(img)
            flag = '  ' if ok else '!! '
            print(f'  {flag}立绘{i} image={img!r} 表情={expr!r} 位置={pos!r}')
        # 条件
        cv = getattr(panel, '_condition_var', None)
        cond = cv.get() if cv is not None else ''
        print(f'  条件 = {cond!r}')
        # 继承提示
        iv = getattr(panel, '_inherit_var', None)
        hint = iv.get() if iv is not None else ''
        print(f'  继承提示 = {hint[:60]!r}')
        print()
    # 1) 造一个最小 Beat（含立绘），直接看控件
    beat = Beat(seq=1, label='t', kind='say', speaker='jc',
                text='最低限度台词', show='ling happy@left',
                condition='love >= 5')

    engine = None
    panel = BeatEditorPanel(root, engine)
    panel.pack()
    panel.load_beat(beat)
    root.update()                       # 让 tkinter 真正处理完
    dump(panel, '直接构造的 Beat：真控件里的值')

    # 2) 多立绘：三张立绘三行，各自独立位置
    multi = Beat(seq=2, label='t', kind='say', speaker='jc',
                 text='三人同台',
                 show='ling happy@left; mary smile@right; bob sad@center')
    panel.load_beat(multi)
    root.update()
    dump(panel, '多立绘（数组）：三张立绘三行')

    # 3) 真实章节第 50 拍
    print('=== 真实章节 #50：真控件里的值 ===')
    d = ROOT / 'demo' / '_rw'
    d.mkdir(exist_ok=True)
    fp = d / 'chapter1_3.rpy'
    fp.write_text(REAL.read_text(encoding='utf-8'), encoding='utf-8')
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(fp)))
    symbols = make_symbols(None)
    for b in beats:
        b.stage = compose_stage(b, symbols) or b.stage

    b50 = beats[49]
    print('  数据层: beat.speaker =', repr(b50.speaker),
          ' kind =', repr(b50.kind))
    print('  数据层: beat_to_fields[Speaker] =',
          repr(beat_to_fields(b50, symbols)['Speaker']))
    panel.load_beat(b50)
    root.update()
    dump(panel, '真实 #50')

    import shutil
    shutil.rmtree(d, ignore_errors=True)
    root.destroy()


if __name__ == '__main__':
    main()
