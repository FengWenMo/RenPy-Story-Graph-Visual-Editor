# -*- coding: utf-8 -*-
"""
P1 讨论探针（把关派）· ④ 资源表 write_asset_excel 表坏了才整份重建
======================================================================
复现「表坏了 → 隔离 + 整份重建」这条路：用户手写内容（列/页/批注/手写行）
的下落、文件名有没有被动、回执说了什么。对照组：表能读时扫描（P0-F）。

跑法：/d/Python/python tools/_p1debate_gate_asset.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_registry import (ensure_asset_tables,   # noqa: E402
                                              read_excel_rows,
                                              write_asset_excel)


def _make_project(with_images: bool = True):
    """最小项目：两个图片文件，让扫描能出 2 行。"""
    tmp = Path(tempfile.mkdtemp(prefix='p1gate_asset_'))
    game = tmp / 'game'
    (game / 'images' / 'eileen').mkdir(parents=True)
    (game / 'images' / 'eileen' / 'happy.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    (game / 'images' / 'eileen' / 'sad.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    (game / 'audio').mkdir()
    (game / 'audio' / 'bgm1.ogg').write_bytes(b'OggS')
    return tmp


def _user_stuff(table: Path):
    """给表加人手维护的内容：自定义列、整页、批注、手写行。"""
    import openpyxl
    wb = openpyxl.load_workbook(table)
    ws = wb.active
    # 自定义列表头 + 值
    col = ws.max_column + 1
    ws.cell(row=1, column=col, value='配音演员')
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=col, value=f'CV{r}')
    # 批注 + 底色
    from openpyxl.comments import Comment
    from openpyxl.styles import PatternFill
    ws.cell(row=2, column=2).comment = Comment('这张要重画', '策划')
    ws.cell(row=2, column=2).fill = PatternFill('solid', fgColor='FFF2CC')
    # 整页
    sheet = wb.create_sheet('分类说明')
    sheet['A1'] = '角色立绘按情绪分类'
    sheet['A2'] = 'happy=开心 / sad=难过'
    # 手写行（没有文件的占位）
    ws.append(['future', 'images/eileen/future.png', '', '', '', '还没画', '', ''])
    wb.save(table)


def _scan_headers(table: Path) -> list:
    try:
        rows = read_excel_rows(table)
        return list(rows[0].keys()) if rows else []
    except Exception as e:                                        # noqa: BLE001
        return [f'<读不出: {type(e).__name__}>']


def _snapshot(table: Path) -> dict:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(table)
    except Exception as e:                                        # noqa: BLE001
        return {'unreadable': f'{type(e).__name__}: {e}'}
    out = {'sheets': wb.sheetnames}
    ws = wb.active
    out['rows'] = ws.max_row
    out['headers'] = [c.value for c in ws[1]]
    marks = 0
    for row in ws.iter_rows():
        for c in row:
            if c.comment is not None:
                marks += 1
    out['comments'] = marks
    wb.close()
    return out


def probe():
    print('=' * 70)
    print('④ 资源表「表坏了 → 整份重建」')
    print('=' * 70)

    # ---------- 对照组：表能读时扫描（P0-F 应保住人的内容） ----------
    print('--- 对照 A：表能读 + 扫描（P0-F 现状）---')
    tmp = _make_project()
    res0 = ensure_asset_tables(tmp)
    table = Path(res0['path']) / 'images.xlsx'
    _user_stuff(table)
    before = _snapshot(table)
    res1 = ensure_asset_tables(tmp)          # 再扫（表可读，应早退）
    res2 = None
    # 直接强制走 write_asset_excel（模拟「扫描资源」写表）
    from storygraph.assets.asset_registry import scan_assets
    assets = scan_assets(tmp, include_ui=False)
    write_asset_excel(assets, table.parent, split_by_category=False)
    after = _snapshot(table)
    kept_cols = '配音演员' in (after.get('headers') or [])
    kept_sheet = '分类说明' in (after.get('sheets') or [])
    kept_marks = after.get('comments', 0) >= 1
    kept_row = after.get('rows', 0) >= before.get('rows', 0)
    print(f'  自定义列「配音演员」: {"保住" if kept_cols else "丢了"} '
          f'(表头={after.get("headers")})')
    print(f'  整页「分类说明」:     {"保住" if kept_sheet else "丢了"} '
          f'(页={after.get("sheets")})')
    print(f'  批注:                 {"保住" if kept_marks else "丢了"}')
    print(f'  手写行 future:        {"保住" if kept_row else "丢了"} '
          f'(行数 {before.get("rows")} → {after.get("rows")})')

    # ---------- 实验组：表坏了（zip 损坏）----------
    print('--- 实验 B：images.xlsx 字节写坏 → load_assets ---')
    tmp2 = _make_project()
    res0 = ensure_asset_tables(tmp2)
    table2 = Path(res0['path']) / 'images.xlsx'
    _user_stuff(table2)
    broken_names = []
    import openpyxl
    wb = openpyxl.load_workbook(table2)
    has_user = wb.active.max_column  # 记录有用户内容
    wb.close()
    table2.write_bytes(b'this is not a zip (corrupt xlsx)')
    # 先看能否抢救读出（openpyxl 应当读不出）
    try:
        read_excel_rows(table2)
        print('  ⚠ 坏文件居然读出来了（探针前提不成立）')
    except Exception as e:                                        # noqa: BLE001
        print(f'  坏文件 openpyxl 读不出: {type(e).__name__}: {e}')
    res = ensure_asset_tables(tmp2)
    print(f'  ensure 返回: ok={res["ok"]} repaired={res.get("repaired")} '
          f'error={str(res.get("error"))[:60]}')
    print(f'  repair_error (解析失败原因): {str(res.get("repair_error"))[:80]}')
    preserved = res.get('preserved') or {}
    print(f'  preserved 回执: {preserved}')
    # 现场盘点
    tables = Path(res['path'])
    names = sorted(p.name for p in tables.glob('*'))
    print(f'  表目录现状: {names}')
    new_snap = _snapshot(tables / 'images.xlsx')
    print(f'  新 images.xlsx: {new_snap}')
    broke = list(tables.glob('*.broken-*.xlsx'))
    if broke:
        # 用户内容只可能在 broken 文件里
        try:
            import openpyxl as _o
            wb = _o.load_workbook(broke[0])
            headers = [c.value for c in wb.active[1]]
            sheets = wb.sheetnames
            wb.close()
            print(f'  broken 文件里: 表头={headers} 页={sheets}')
            print(f'  → 用户的「配音演员」列/「分类说明」页下落: '
                  f'{"在 broken 里" if ("配音演员" in headers or "分类说明" in sheets) else "哪都没有!"}')
        except Exception as e:                                    # noqa: BLE001
            print(f'  broken 文件也读不出了: {e}')
    # 文件名有没有被动过？
    original_name_exists = (tables / 'images.xlsx').exists()
    renamed = any(p.name != 'images.xlsx' and 'broken' in p.name
                  for p in tables.glob('images*'))
    print(f'  原文件名还在: {original_name_exists}（新表顶了这个名字）；'
          f'原件被改名: {renamed}')

    # ---------- 实验 C：只有音乐表坏，图片表好的混合现场 ----------
    print('--- 实验 C：music.xlsx 坏、images.xlsx 好（好表不许陪葬）---')
    tmp3 = _make_project()
    res0 = ensure_asset_tables(tmp3)
    tables3 = Path(res0['path'])
    t_img = tables3 / 'images.xlsx'
    t_mus = tables3 / 'music.xlsx'
    _user_stuff(t_img)
    t_mus.write_bytes(b'corrupt')
    before_img = _snapshot(t_img)
    res3 = ensure_asset_tables(tmp3)
    after_img = _snapshot(t_img)
    ok_img = ('配音演员' in (after_img.get('headers') or [])
              and '分类说明' in (after_img.get('sheets') or []))
    print(f'  混合现场: ok={res3["ok"]} repaired={res3.get("repaired")}')
    print(f'  images.xlsx 的人手内容: {"保住" if ok_img else "被陪葬!"} '
          f'(表头={after_img.get("headers")})')
    print(f'  music.xlsx: {[p.name for p in tables3.glob("music*")]}')

    # ---------- 实验 D：坏表 + 回执是否点名"原件在哪、丢了什么" ----------
    print('--- 实验 D：回执文案体检（把关判据：不许静默）---')
    full = f'{res.get("error")} | {res.get("repair_error")} | ' \
           f'{res.get("repaired")} | {res.get("preserved")}'
    says_where = any('.broken-' in str(x) for x in (res.get('repaired') or []))
    says_loss = any(k in str(full) for k in ('丢', '手写', '批注', '自定义列', '人'))
    print(f'  回执提到原件改名去向: {"有" if says_where else "无"}')
    print(f'  回执提到"手写内容没进新表": {"有" if says_loss else "无"}')
    print(f'  回执全文片段: {full[:220]}')


if __name__ == '__main__':
    probe()
    print()
    print('探针跑完（临时目录未清理：用完自行删除 %TEMP%\\p1gate_asset_*）')
