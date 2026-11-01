# -*- coding: utf-8 -*-
"""探针：改一格 → 生成标记 → 下一轮为什么又没了。"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl                                          # noqa: E402
from storygraph.pipeline.compile import compile_table     # noqa: E402
from storygraph.story.script_table import open_rpy_as_table  # noqa: E402

SRC = '''# 头注释：这一行不该动
define e = Character("Eileen")
default flag = False

init python:
    persistent.x = 1

transform left:
    xalign 0.0

label ch01:
    scene bg room with dissolve
    show eileen happy at left
    e "第一句"
    if flag:
        e "第二句"
    menu:
        "去左边":
            jump left_1
    return
'''


def _show(tag: str, ch: Path, res) -> None:
    data = ch.read_bytes()
    head = data.split(b'\n')[0].decode('utf-8')[:40]
    print(f'{tag}: used_patch={res.used_patch} marker={res.marker_added} '
          f'changed={res.changed_lines} written={res.written} '
          f'bytes={len(data)} sha={hashlib.sha1(data).hexdigest()[:10]} '
          f'head={head!r}')
    print(f'   patch_note={res.patch_note!r}')


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix='sg-cycles-', dir=str(ROOT / '.pip-tmp')))
    game = root / 'game'
    game.mkdir(parents=True)
    ch = game / 'ch01.rpy'
    ch.write_bytes(SRC.encode('utf-8'))
    xlsx = ch.with_suffix('.xlsx')

    for i in range(1, 4):
        open_rpy_as_table(ch, xlsx)
        _rpy, _s, res = compile_table(xlsx, rpy_path=ch, source_path=ch,
                                      write_sheet=False, engine_lint=False)
        _show(f'空转 {i}', ch, res)

    # 改一格
    wb = openpyxl.load_workbook(xlsx)
    ws = wb['Beats']
    headers = [str(c.value or '') for c in ws[1]]
    col = headers.index('台词') + 1
    for row in range(2, ws.max_row + 1):
        cell = ws.cell(row=row, column=col)
        if str(cell.value or '') == '第一句':
            cell.value = '第一句（改过）'
            break
    wb.save(xlsx)

    for i in range(1, 4):
        _rpy, _s, res = compile_table(xlsx, rpy_path=ch, source_path=ch,
                                      write_sheet=False, engine_lint=False)
        _show(f'改后编译 {i}', ch, res)
        open_rpy_as_table(ch, xlsx)
        _rpy, _s, res2 = compile_table(xlsx, rpy_path=ch, source_path=ch,
                                       write_sheet=False, engine_lint=False)
        _show(f'改后空转 {i}', ch, res2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
