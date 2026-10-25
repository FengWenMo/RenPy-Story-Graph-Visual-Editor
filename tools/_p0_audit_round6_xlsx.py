"""P0 审计·第六轮：**重新摊表会不会抹掉用户在这张 xlsx 里手写的东西**。

场景（真实节奏）：策划把 `ch1.rpy` 摊成 `ch1.xlsx`，然后在同一份工作簿里
加了「角色设定」页、加了一列「配音状态」、给几行标了颜色、写了几条批注。
后来脚本改了几行 → 又点一次「生成演出表」。

判据：用户的每一样东西，**要么还在，要么被明确告知会丢（并且有备份）**。

跑法： python tools/_p0_audit_round6_xlsx.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl  # noqa: E402
from openpyxl.comments import Comment  # noqa: E402

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


SCRIPT = '''# 第一章
label ch1:
    jc "第一句"
    jc "第二句"
    return
'''


def main():
    from storygraph.story.script_table import open_rpy_as_table

    tmp = Path(tempfile.mkdtemp(prefix='sg_r6_'))
    try:
        game = tmp / 'game'
        game.mkdir(parents=True)
        ch = game / 'ch1.rpy'
        ch.write_text(SCRIPT, encoding='utf-8')
        xlsx = ch.with_suffix('.xlsx')

        # ① 第一次摊表
        open_rpy_as_table(ch, xlsx)
        print(f'① 首次摊表：{xlsx.name}，工作表 {openpyxl.load_workbook(xlsx).sheetnames}')

        # ② 策划在这张表里手写东西（完全合法、产品鼓励的用法）
        wb = openpyxl.load_workbook(xlsx)
        ws = wb['Beats']
        # 加一列自己的（表头 + 两行值）
        free_col = ws.max_column + 1
        ws.cell(row=1, column=free_col, value='配音状态')
        ws.cell(row=2, column=free_col, value='已录')
        ws.cell(row=3, column=free_col, value='待录')
        # 给一行上色 + 批注
        from openpyxl.styles import PatternFill
        ws.cell(row=2, column=1).fill = PatternFill('solid', fgColor='FFF2CC')
        ws.cell(row=2, column=1).comment = Comment('这句要重录', '策划')
        # 自己加一整页（角色设定 / 大纲）
        mine = wb.create_sheet('角色设定')
        mine['A1'] = '小雨'
        mine['B1'] = '17 岁，怕水'
        mine['A2'] = '妈妈'
        mine['B2'] = '出场三章'
        # 冻结首行（阅读习惯）
        ws.freeze_panes = 'A2'
        wb.save(xlsx)

        before_sheets = openpyxl.load_workbook(xlsx).sheetnames
        print(f'② 策划手写之后：{before_sheets}')

        # ③ 脚本改一句 → 再摊一次表（用户点「生成演出表」）
        ch.write_text(SCRIPT.replace('第一句', '第一句（改过）'), encoding='utf-8')
        open_rpy_as_table(ch, xlsx)

        wb2 = openpyxl.load_workbook(xlsx)
        after_sheets = wb2.sheetnames
        print(f'③ 重新摊表之后：{after_sheets}')

        check('A 自己加的「角色设定」页还在', '角色设定' in after_sheets,
              f'before={before_sheets}\nafter={after_sheets}')
        ws2 = wb2['Beats']
        hdr = [str(c.value or '') for c in ws2[1]]
        check('B 自己加的「配音状态」列还在', '配音状态' in hdr,
              f'表头={hdr[-4:]}…（共 {len(hdr)} 列）')
        check('C 那一格的批注还在',
              bool(ws2.cell(row=2, column=1).comment),
              f'批注={getattr(ws2.cell(row=2, column=1).comment, "text", None)!r}')
        check('D 那一格的底色还在',
              str(ws2.cell(row=2, column=1).fill.fgColor.rgb or '') != '00000000',
              f'fill={ws2.cell(row=2, column=1).fill.fgColor.rgb!r}')
        check('E 冻结首行还在', ws2.freeze_panes == 'A2',
              f'freeze_panes={ws2.freeze_panes!r}')
        baks = sorted(p.name for p in xlsx.parent.glob('*.bak*'))
        check('F 覆盖前留了备份（丢了也能找回）', bool(baks),
              f'同目录备份={baks}')

        ok_n = sum(1 for ok, _ in results if ok)
        print(f'\n汇总：{ok_n}/{len(results)} 通过')
        return 0 if ok_n == len(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
