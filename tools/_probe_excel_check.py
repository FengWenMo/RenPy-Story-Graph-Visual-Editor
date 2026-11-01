# -*- coding: utf-8 -*-
"""探针：excel-check 子命令在正常表 / 炸弹 / 目录三种输入下的表现。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import openpyxl                                          # noqa: E402
from storygraph.cli import main                           # noqa: E402

work = ROOT / '.pip-tmp' / 'excel_check_probe'
work.mkdir(parents=True, exist_ok=True)

good = work / 'good.xlsx'
wb = openpyxl.Workbook()
wb.active['A1'] = '正常'
wb.save(str(good))

bomb = work / 'dim_bomb.xlsx'
with zipfile.ZipFile(bomb, 'w') as z:
    z.writestr('[Content_Types].xml', '<Types/>')
    z.writestr('xl/workbook.xml', '<workbook/>')
    z.writestr('xl/worksheets/sheet1.xml',
               '<worksheet><dimension ref="A1:XFD1048576"/><sheetData/></worksheet>')

notzip = work / 'fake.xlsx'
notzip.write_bytes(b'not a zip')

for argv in (['excel-check', str(good)],
             ['excel-check', str(bomb)],
             ['excel-check', str(notzip)],
             ['excel-check', str(good), '--json'],
             ['excel-check', str(work)],
             ['excel-check', str(work / 'nope')],
             ['excel-check', str(good), '--strict']):
    print('=' * 60)
    print(' '.join(argv))
    code = main(argv)
    print(f'→ exit={code}')
