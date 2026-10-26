#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""④ 探针：`salvage_rows` 对打不开的 xlsx 能不能救回用户行。

    /d/Python/python tools/_p1_verify_salvage.py

三种形态：
  A. workbook.xml 损坏（openpyxl 拒载）→ sheet XML 完好 → 该救回全部行
  B. styles.xml 缺失 → openpyxl 拒载 → 该救回全部行
  C. zip 整个截断 → 救不回 → 返回 ([], [])（调用方才隔离+重建）
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl
from storygraph.story.xlsx_preserve import salvage_rows

FAILS: list = []


def check(tag, ok, expect, got):
    print(f'  [{"PASS" if ok else "FAIL"}] {tag}')
    if not ok:
        print(f'         期望: {expect!r}')
        print(f'         实得: {got!r}')
        FAILS.append(tag)


def _make(path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['ID', 'Path', '配音演员', '审核'])
    ws.append(['img1', 'a.png', '张三', '过'])
    ws.append(['img2', 'b.png', '李四', '改'])
    ws.append(['', '', '没 ID 的备注行', ''])
    wb.save(path)


def _rewrite_without(src: Path, dst: Path, drop: str):
    """把 zip 里某一项剔掉（或换成坏内容）后另存 —— 造"openpyxl 打不开"的表。"""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w') as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == drop:
                continue
            zout.writestr(item, data)


def _rewrite_corrupt_member(src: Path, dst: Path, target: str):
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w') as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == target:
                data = b'<broken>NOT-XML<'
            zout.writestr(item, data)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='p1_salvage_'))

    # ---------- 形态 A：workbook.xml 损坏 ----------
    print('形态 A：workbook.xml 损坏')
    a = tmp / 'a.xlsx'
    _make(tmp / 'a_good.xlsx')
    _rewrite_corrupt_member(tmp / 'a_good.xlsx', a, 'xl/workbook.xml')
    try:
        openpyxl.load_workbook(a)
        check('openpyxl 确实打不开（探针自证）', False, '拒载', '居然载入了')
    except Exception as e:                      # noqa: BLE001
        check('openpyxl 确实打不开（探针自证）', True, '拒载', type(e).__name__)
    h, rows = salvage_rows(a)
    check('A 表头 4 列', h == ['ID', 'Path', '配音演员', '审核'], ['ID', 'Path', '配音演员', '审核'], h)
    check('A 救回 3 行（含备注行）', len(rows) == 3, 3, len(rows))
    check("A 张三那行完整", rows and rows[0].get('配音演员') == '张三'
          and rows[0].get('ID') == 'img1', '张三/img1', rows[:1])
    check("A 备注行在（没 ID 有内容）",
          any(r.get('配音演员') == '没 ID 的备注行' for r in rows), True,
          [r for r in rows if '备注' in str(r.get('配音演员', ''))])

    # ---------- 形态 B：缺 styles.xml ----------
    # 期望校准（探针自证）：openpyxl 对**缺 styles.xml 是宽容的**（照样载入），
    # 所以这条不判"拒载"，只判"抢救通道对缺 styles 的表照样读得出"——
    # 真正触发"打不开"的是 workbook.xml 损坏（形态 A）与 zip 截断（形态 C）。
    print('形态 B：缺 styles.xml')
    b = tmp / 'b.xlsx'
    _rewrite_without(tmp / 'a_good.xlsx', b, 'xl/styles.xml')
    h, rows = salvage_rows(b)
    check('B 救回 3 行', len(rows) == 3, 3, len(rows))
    check('B 表头仍在', h and h[0] == 'ID', 'ID', h[:1])

    # ---------- 形态 C：zip 截断 ----------
    print('形态 C：zip 整个截断（该救不回）')
    c = tmp / 'c.xlsx'
    data = (tmp / 'a_good.xlsx').read_bytes()
    c.write_bytes(data[:len(data) // 2])
    h, rows = salvage_rows(c)
    check('C 救不回 → 空', (h, rows) == ([], []), ([], []), (h, rows))

    print()
    if FAILS:
        print(f'== 不达标 {len(FAILS)} 条 ==')
        for t in FAILS:
            print('  -', t)
        return 1
    print('== 全部达标 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
