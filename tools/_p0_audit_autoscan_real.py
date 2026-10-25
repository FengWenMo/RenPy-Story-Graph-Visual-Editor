# -*- coding: utf-8 -*-
"""真机核查：sr_general 资源表里，有没有「walk.match 判缺失、
resolve_thumb_source 判存在」的行（= 打开项目时会被误删的行）。

同时测目录联接（junction，Windows 无需管理员权限即可建）场景：
共享素材库通过联接挂进项目 → _walk 用 is_dir(follow_symlinks=False)
跳过 reparse point → 指向联接内真实文件的行全部误删。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from storygraph.assets.asset_registry import (  # noqa: E402
    FileWalkIndex, ensure_asset_tables, sync_asset_tables)
from storygraph.story.excel_export import resolve_thumb_source  # noqa: E402

REAL = Path('D:/千连万理/RenPyTest/sr_general')


def read_paths(xlsx: Path):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    pi = headers.index('真实文件') if '真实文件' in headers else (
        headers.index('Path') if 'Path' in headers else None)
    out = []
    if pi is not None:
        for row in ws.iter_rows(min_row=2, values_only=True):
            v = row[pi]
            if v:
                out.append(str(v))
    wb.close()
    return out


def real_project_check():
    print('== 真机 sr_general：两路裁决不一致的行 ==')
    if not (REAL / 'game' / 'asset_tables' / 'images.xlsx').is_file():
        print('  真机没有 images.xlsx，跳过')
        return
    walk = FileWalkIndex(REAL)
    would_delete = []      # walk=False（打开项目就会删）但磁盘上文件在
    walk_yes_res_no = []   # walk=True、resolve=False（音频行正常，不是风险）
    other = []
    for tbl in ('images.xlsx', 'music.xlsx', 'sounds.xlsx', 'voices.xlsx'):
        f = REAL / 'game' / 'asset_tables' / tbl
        if not f.is_file():
            continue
        for p in read_paths(f):
            w = walk.match(p)
            r = resolve_thumb_source(p, REAL)
            if w is None:
                # 真机上文件到底在不在（ground truth）
                from pathlib import Path as _P
                cands = [REAL / p, REAL / 'game' / p, REAL.parent / p]
                parts = [q for q in p.split('/') if q not in ('', '.')]
                if len(parts) > 1:
                    cands += [REAL / '/'.join(parts[1:]),
                              REAL / 'game' / '/'.join(parts[1:]),
                              REAL.parent / '/'.join(parts[1:])]
                truth = any(c.is_file() for c in cands)
                would_delete.append((tbl, p, truth, r is not None))
            elif r is None:
                walk_yes_res_no.append((tbl, p))
            else:
                other.append((tbl, p))
    print(f'  walk 判缺失（打开项目即删）：{len(would_delete)} 行'
          f'（其中磁盘上真实存在 {sum(1 for x in would_delete if x[2])} 行）')
    for tbl, p, truth, rh in would_delete:
        mark = '★文件在却被删' if truth else '（真缺失，按用户要求删）'
        print(f'    {tbl} {p!r} 磁盘存在={truth} resolve={rh} {mark}')
    print(f'  walk 判存在、resolve 判缺失：{len(walk_yes_res_no)} 行'
          f'（音频行走图片解析器，正常，非风险）')
    print(f'  两路都判存在：{len(other)} 行')


def junction_check():
    print('== 目录联接（共享素材库）场景 ==')
    tmp = Path(tempfile.mkdtemp(prefix='sg_junction_'))
    root = tmp / 'proj'
    (root / 'game' / 'images').mkdir(parents=True)
    shared = tmp / 'shared_lib'
    (shared / 'bg').mkdir(parents=True)
    pic = shared / 'bg' / 'shared_room.png'
    pic.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)
    link = root / 'game' / 'images' / 'shared_lib'
    r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(shared)],
                       capture_output=True, encoding='mbcs', errors='replace')
    print(f'  mklink /J → rc={r.returncode} {(r.stdout or "").strip()[:80]}')
    if r.returncode != 0:
        print('  建不了联接，跳过')
        return
    made = ensure_asset_tables(root)
    print(f'  建表 ok={made.get("ok")}')
    tables = root / 'game' / 'asset_tables'
    import openpyxl
    xlsx = tables / 'images.xlsx'
    wb = openpyxl.load_workbook(xlsx)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    ws.append([{'编号': 'shared_row', '真实文件': 'game/images/shared_lib/bg/shared_room.png',
                '备注': '共享库'}.get(h, '') for h in headers])
    wb.save(xlsx)
    wb.close()
    print('  注入一行指向联接内真实文件（磁盘上确实存在）')
    walk = FileWalkIndex(root)
    print(f'  walk.match = {walk.match("game/images/shared_lib/bg/shared_room.png") is not None}')
    info = sync_asset_tables(root, walk=walk)
    print(f'  sync removed={info.get("removed")} by_kind={info.get("removed_by_kind")}')
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb.active
    ids = [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]
    wb.close()
    print(f'  同步后剩余行：{ids}')
    print(f'  文件仍在磁盘：{pic.is_file()}')
    subprocess.run(['cmd', '/c', 'rmdir', str(link)], capture_output=True,
                   encoding='mbcs', errors='replace')


if __name__ == '__main__':
    real_project_check()
    print()
    junction_check()
