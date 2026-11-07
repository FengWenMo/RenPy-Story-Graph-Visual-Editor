# -*- coding: utf-8 -*-
"""
探针 2：跑一遍生产链路（.rpy → 表 → 带 --source 写回），**保留产物**，
把有差异的行按 repr 打出来，并把中间 xlsx 里的台词单元格也打出来。

跑法：
    D:\\Python\\python.exe tools\\_probe_fidelity_gaps2_20261102.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(r'D:\千连万理\RenPyTest\sr_general\game')
OUT = Path(r'D:\千连万理\Renpy_Story_Graph_Editor\.pip-tmp\fidprobe')

FILES = ['chapter3_3_3.rpy', 'chapter3_4_4.rpy', 'chapter4_1_2.rpy']


def dump_around(path: Path, needle: str, before=2, after=2, label=''):
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = text.split('\n')
    for i, ln in enumerate(lines):
        if needle in ln:
            print(f'\n--- {label} {path.name} 第 {i + 1} 行命中 {needle!r} ---')
            for j in range(max(0, i - before), min(len(lines), i + after + 1)):
                mark = '>>' if j == i else '  '
                print(f'  {mark}[{j + 1:>4}] {lines[j]!r}')
            return True
    print(f'  （{label} {path.name} 没找到 {needle!r}）')
    return False


def main() -> int:
    from storygraph.pipeline.compile import compile_table
    from storygraph.story.script_table import open_rpy_as_table
    import openpyxl

    OUT.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        src = ROOT / 'chapter' / name
        if not src.exists():
            print(f'跳过：{src}')
            continue
        xlsx = OUT / f'{src.stem}.xlsx'
        out = OUT / f'{src.stem}.out.rpy'
        print(f'\n\n######## {name} ########')
        open_rpy_as_table(str(src), str(xlsx))
        _p, _s, res = compile_table(
            str(xlsx), rpy_path=str(out), write_sheet=False, force=True,
            source_path=str(src), skip_empty=True, engine_lint=False)
        print(f'  used_patch={getattr(res, "used_patch", None)} '
              f'note={getattr(res, "patch_note", "")!r}')
        if not out.exists():
            print('  ! 没有产物')
            continue

        # 1) 中间表里的台词列（找含 \x05 或 注释 的格子）
        wb = openpyxl.load_workbook(str(xlsx), data_only=True)
        ws = wb.active
        headers = [c.value for c in ws[1]]
        print(f'  表头：{headers}')
        for row in ws.iter_rows(min_row=2, values_only=True):
            joined = ' | '.join('' if v is None else str(v) for v in row)
            if '\x05' in joined or ('#这里' in joined) or ('#后面' in joined):
                print(f'  表里这一行含可疑字符：')
                for h, v in zip(headers, row):
                    if v not in (None, ''):
                        print(f'      {h} = {v!r}')
        wb.close()

        # 2) 产物 vs 源：只打印含可疑字符的上下文
        dump_around(src, '\x05', label='源')
        dump_around(out, '\x05', label='产物')
        dump_around(out, '#这里出现名字', label='产物', before=3, after=3)
        dump_around(out, '#后面如果可以做', label='产物', before=3, after=3)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
