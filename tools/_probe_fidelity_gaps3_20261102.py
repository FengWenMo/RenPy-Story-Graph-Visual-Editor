# -*- coding: utf-8 -*-
"""
探针 3：缺陷 2 是不是**每导出一次多一行**（幂等性破坏）。

做法：拿 chapter3_4_4.rpy 复制到临时目录，连跑 3 轮
    .rpy -> 表 -> 带 --source 写回（写回同一个文件）
每轮记：文件行数、含 "不会忘了吧" 的行数、含 "小伙子这么激动" 的行数。

跑法：
    D:\\Python\\python.exe tools\\_probe_fidelity_gaps3_20261102.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = Path(r'D:\千连万理\RenPyTest\sr_general\game\chapter\chapter3_4_4.rpy')
WORK = Path(r'D:\千连万理\Renpy_Story_Graph_Editor\.pip-tmp\fidprobe\growth')

NEEDLES = ['不会忘了吧', '小伙子这么激动']


def count(path: Path) -> tuple:
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = text.split('\n')
    return len(lines), {n: sum(1 for ln in lines if n in ln) for n in NEEDLES}


def main() -> int:
    from storygraph.pipeline.compile import compile_table
    from storygraph.story.script_table import open_rpy_as_table

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    target = WORK / 'chapter3_4_4.rpy'
    shutil.copy2(SRC, target)

    print(f'源：{SRC.name}')
    n0, c0 = count(target)
    print(f'  第 0 轮（原始）  行数 {n0}  {c0}')

    for rnd in range(1, 4):
        xlsx = WORK / f'r{rnd}.xlsx'
        open_rpy_as_table(str(target), str(xlsx))
        _p, _s, res = compile_table(
            str(xlsx), rpy_path=str(target), write_sheet=False, force=True,
            source_path=str(target), skip_empty=True, engine_lint=False)
        n, c = count(target)
        print(f'  第 {rnd} 轮  行数 {n} (+{n - n0})  {c}  '
              f'used_patch={getattr(res, "used_patch", None)}')
        n0 = n
        # 把每轮里连续两行的原文打出来，看是不是重复
        if rnd == 1:
            lines = target.read_text(encoding='utf-8').split('\n')
            for i, ln in enumerate(lines):
                if '不会忘了吧' in ln:
                    print(f'    [{i + 1}] {ln!r}')
    print(f'\n产物留在：{WORK}')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
