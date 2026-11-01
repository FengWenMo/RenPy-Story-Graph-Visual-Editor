# -*- coding: utf-8 -*-
"""
探针：`rpy → xlsx → rpy` 连续往返，到底在第几次收敛、字节差在哪。

只用来给 `tests/test_hell_excel_cycles.py` 定判据，不是产品代码。
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.compile import compile_table      # noqa: E402
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


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix='sg-cycles-', dir=str(ROOT / '.pip-tmp')))
    try:
        game = root / 'game'
        game.mkdir(parents=True)
        ch = game / 'ch01.rpy'
        ch.write_bytes(SRC.encode('utf-8'))
        original = ch.read_bytes()
        xlsx = ch.with_suffix('.xlsx')
        print('原始', len(original), hashlib.sha1(original).hexdigest()[:12])

        for i in range(1, 6):
            open_rpy_as_table(ch, xlsx)
            _rpy, _sheet, res = compile_table(
                xlsx, rpy_path=ch, source_path=ch, write_sheet=False,
                engine_lint=False)
            now = ch.read_bytes()
            print(f'第 {i} 轮  used_patch={res.used_patch} '
                  f'written={res.written} 字节={len(now)} '
                  f'sha={hashlib.sha1(now).hexdigest()[:12]} '
                  f'changed={res.changed_lines} '
                  f'marker={res.marker_added} '
                  f'patch_note={res.patch_note!r}')
            if i == 1:
                Path(root / 'after1.rpy').write_bytes(now)
        after1 = (root / 'after1.rpy').read_bytes()
        if after1 != original:
            import difflib
            a = original.decode('utf-8').splitlines(keepends=True)
            b = after1.decode('utf-8').splitlines(keepends=True)
            print('--- 第一轮与原始的差异 ---')
            for line in difflib.unified_diff(a, b, 'original', 'after1', n=1):
                print(line.rstrip('\n'))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
