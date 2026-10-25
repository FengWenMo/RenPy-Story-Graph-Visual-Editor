# -*- coding: utf-8 -*-
"""第二轮 P0 审计探针：资产表自动同步的删行语义。

要验证的问题（docstring 与实现不一致）：
  · `delete_rows_from_table` docstring（asset_registry.py:1087）：
    「人工录入的行绝不走这条（由侧车 `_autoscan.json` 判归属）」；
  · `sync_asset_tables` docstring（:761）：「不做的事：删行」；
  · 但 `_missing_paths`（:505）实际：只要 `Path` 解析不到真实文件就删，
    只豁免占位/模板行，**不查侧车、不认是谁加的**。

本探针构造一张表，混合七种行，跑真实的打开项目路径
（`sync_asset_tables(root, walk=FileWalkIndex(root))`），看谁被删：

  A 绝对路径指向项目内真实文件   —— walk.match 直接拒绝对对路径（:946）
  B 跳过目录 tmp/ 下真实文件     —— _walk 跳过 _SKIP_SCAN_DIRS
  C 跳过目录 cache/ 下真实文件   —— 同上
  D `../` 指向项目外真实文件     —— _probe_outside 兜底（应存活）
  E 用户手写、指向真缺失文件     —— 按用户 2026-10-05 要求删（应删+备份）
  F 占位行（Notes=占位）缺失文件 —— 应存活
  G 模板路径 {image}.png          —— 应存活
  H 普通相对路径真实文件（对照）  —— 应存活

判据：行在不在表里（读回 xlsx）+ 有没有 .bak 备份 + sync 返回值。
顺带对照 walk=None 路（resolve_thumb_source）对同一行的判断，
证明两条路对同一行的裁决不一致。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from storygraph.assets.asset_registry import (  # noqa: E402
    FileWalkIndex, ensure_asset_tables, sync_asset_tables)
from storygraph.story.excel_export import resolve_thumb_source  # noqa: E402

FAIL = []


def check(cond, msg):
    tag = 'PASS' if cond else 'FAIL'
    print(f'  [{tag}] {msg}')
    if not cond:
        FAIL.append(msg)


def read_rows(xlsx: Path):
    """读回 xlsx；表头是中文（编号/真实文件/备注…），这里直接按中文取。"""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    rows = []
    for r in range(2, ws.max_row + 1):
        d = {headers[i]: ws.cell(row=r, column=i + 1).value
             for i in range(len(headers))}
        rows.append(d)
    wb.close()
    return rows


def add_row(xlsx: Path, row: dict):
    """按中文表头追加一行（列顺序与现有表一致）。"""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    ws.append([row.get(h, '') for h in headers])
    wb.save(xlsx)
    wb.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sg_autoscan_'))
    root = tmp / 'proj'
    (root / 'game' / 'images').mkdir(parents=True)
    (root / 'game' / 'audio' / 'music').mkdir(parents=True)
    (root / 'game' / 'tmp').mkdir(parents=True)          # 跳过目录
    (root / 'game' / 'cache').mkdir(parents=True)        # 跳过目录
    (tmp / 'sibling').mkdir(parents=True)                # 项目外
    real_a = root / 'game' / 'images' / 'real_a.png'
    real_b = root / 'game' / 'images' / 'real_b.png'
    hidden = root / 'game' / 'tmp' / 'hidden.png'
    cached = root / 'game' / 'cache' / 'cached.png'
    shared = tmp / 'sibling' / 'shared.png'
    for p in (real_a, real_b, hidden, cached, shared):
        p.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)
    (root / 'game' / 'audio' / 'music' / 'theme.ogg').write_bytes(b'OggS' + b'\x00' * 32)

    print('== 建表（ensure_asset_tables，走真实打开路径的第一段）==')
    made = ensure_asset_tables(root)
    check(made.get('ok'), f'ensure_asset_tables ok（error={made.get("error")!r}）')
    tables_dir = root / 'game' / 'asset_tables'
    images_xlsx = tables_dir / 'images.xlsx'
    check(images_xlsx.is_file(), f'images.xlsx 已生成：{images_xlsx.name}')
    if not images_xlsx.is_file():
        print('建表失败，探针中止'); print('FAILS:', FAIL); return 2

    # —— 注入攻击行（用户手写场景）——
    print('== 注入七种行 ==')
    cases = [
        # (key, Path, Notes)
        ('A_abs_inside',  str(real_a),            ''),      # 绝对路径·项目内·文件在
        ('B_tmp_dir',     'game/tmp/hidden.png',  ''),      # 跳过目录·文件在
        ('C_cache_dir',   'game/cache/cached.png', ''),     # 跳过目录·文件在
        ('D_outside_rel', '../sibling/shared.png', ''),     # 项目外相对路径·文件在
        ('E_user_gone',   'game/images/gone.png', '用户手写：等美工'),  # 真缺失·无占位标记
        ('F_placeholder', 'game/images/tbd.png',  '占位：待出图'),      # 缺失·有占位标记
        ('G_template',    'cc_preview/lan/{image}.png', ''),  # 模板路径
    ]
    for key, path, notes in cases:
        add_row(images_xlsx, {'编号': key, '真实文件': path, '备注': notes})
    before = {r['编号']: r['真实文件'] for r in read_rows(images_xlsx)}
    print(f'  注入后 {len(before)} 行：{sorted(before)}')

    print('== 打开项目路径：sync_asset_tables(walk=FileWalkIndex) ==')
    walk = FileWalkIndex(root)
    info = sync_asset_tables(root, walk=walk)
    print(f'  sync ok={info.get("ok")} added={info.get("added")} '
          f'removed={info.get("removed")} removed_by_kind={info.get("removed_by_kind")}')

    after = {r['编号']: r['真实文件'] for r in read_rows(images_xlsx)}
    print(f'  同步后 {len(after)} 行：{sorted(after)}')
    bak = tables_dir / 'images.bak.xlsx'
    print(f'  备份存在：{bak.is_file()}')

    print('== 逐行裁决 ==')
    expect_deleted = {'E_user_gone'}   # 用户 2026-10-05 明确要求：没有就删
    for key, path, _notes in cases:
        alive = key in after
        should_die = key in expect_deleted
        verdict = '删' if not alive else '活'
        ok = (not alive) == should_die
        check(ok, f'{key:14s} Path={path!r:45s} → {verdict}'
                  f'（期望 {"删" if should_die else "活"}）')

    print('== 对照：同一行走 walk=None 路（resolve_thumb_source）==')
    for key, path, _notes in cases:
        hit = resolve_thumb_source(path, root)
        w = walk.match(path)
        print(f'  {key:14s} walk.match={w is not None!r:5s} '
              f'resolve_thumb_source={hit is not None!r:5s}'
              + ('   ← 两条路裁决不一致' if (w is None) != (hit is None) else ''))

    print('== 对照：真缺失用户行的备份 ==')
    if 'E_user_gone' not in after:
        check(bak.is_file(), '删掉用户手写行前有 .bak.xlsx 备份（可恢复）')

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAIL:
        print(f'探针命中 {len(FAIL)} 条与预期不符：')
        for f in FAIL:
            print('  -', f)
        return 1
    print('全部符合预期。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
