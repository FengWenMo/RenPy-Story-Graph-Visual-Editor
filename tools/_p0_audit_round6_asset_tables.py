"""P0 审计·第六轮下半（P0-F）：**扫描资源不许抹掉策划维护的资源表**。

场景：`game/asset_tables/images.xlsx` 是策划维护的主数据 ——
他自己加了「配音演员」列、加了一页「分类说明」、给某一行标黄写批注、
手写了两行（还没画出来的立绘 / 临时占位）。然后点「扫描资源」。

判据：他的手写内容**要么还在，要么被明确告知**（删掉的只能有一种：
「记了真实文件、文件又真的不在了」）。

跑法： python tools/_p0_audit_round6_asset_tables.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl                                  # noqa: E402
from openpyxl.comments import Comment            # noqa: E402
from openpyxl.styles import PatternFill          # noqa: E402

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


def build(tmp: Path):
    """一张"有人的痕迹"的资源表 + 一次新的扫描结果。"""
    tables = tmp / 'game' / 'asset_tables'
    tables.mkdir(parents=True)
    imgs = tmp / 'game' / 'images' / 'chars'
    imgs.mkdir(parents=True)
    # 磁盘上真实存在的两张
    for name in ('a.png', 'b.png'):
        (imgs / name).write_bytes(b'\x89PNG\r\n\x1a\n')
    # 第三张"被删了"：表里记着、磁盘上没有
    (imgs / 'gone.png')  # 不创建

    xlsx = tables / 'images.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['编号', '真实文件', '标签', '立绘属性', '类别', '备注',
               '维度', '属性值', '配音演员'])
    ws.append(['a', 'game/images/chars/a.png', 'a', '', 'character', '第一张',
               '', '', '小林'])
    ws.append(['b', 'game/images/chars/b.png', 'b', '', 'character', '',
               '', '', ''])
    # 手写：有 ID、没真实文件（还没画出来的立绘）
    ws.append(['future', '', 'future', '', 'character', '还没画', '', '', '待定'])
    # 记了文件、文件真的不在了 → 这一行该删
    ws.append(['gone', 'game/images/chars/gone.png', 'gone', '', 'character',
               '', '', '', ''])
    # 没有 ID 的备注行 → 留
    ws.append(['', '', '', '', '', '总备注：这个项目不要 UI 图', '', '', ''])
    # 人的标记
    row_b = 3
    ws.cell(row=row_b, column=1).fill = PatternFill('solid', fgColor='FFF2CC')
    ws.cell(row=row_b, column=1).comment = Comment('这张要重画', '策划')
    # 人的页
    extra = wb.create_sheet('分类说明')
    extra['A1'] = 'character = 角色立绘'
    extra['A2'] = 'bg = 背景'
    wb.save(xlsx)
    return tmp, tables, xlsx


def scan_result(tmp: Path):
    """模拟一次扫描：a / b 仍在（路径改成相对项目根，和真实扫描一致）。"""
    def row(aid, name):
        return {'ID': aid, 'Path': f'game/images/chars/{name}', 'Tag': aid,
                'Attributes': '', 'Category': 'character', 'Notes': ''}
    return {'images': [row('a', 'a.png'), row('b', 'b.png')],
            'audio': {'music': [], 'sound': []}, 'voices': []}


def main():
    from storygraph.assets.asset_registry import write_asset_excel

    tmp = Path(tempfile.mkdtemp(prefix='sg_r6at_'))
    try:
        tmp, tables, xlsx = build(tmp)
        write_asset_excel(scan_result(tmp), tables, split_by_category=False)

        wb = openpyxl.load_workbook(xlsx)
        ws = wb.worksheets[0]
        header = [str(c.value or '') for c in ws[1]]
        rows = {}
        for r in range(2, ws.max_row + 1):
            aid = str(ws.cell(row=r, column=1).value or '')
            rows[aid] = [ws.cell(row=r, column=c).value for c in range(1, len(header) + 1)]

        check('A 人自加的「配音演员」列还在（值也带过来了）',
              '配音演员' in header
              and rows.get('a', [])[header.index('配音演员')] == '小林',
              f'表头={header[-3:]}；a 行={rows.get("a")}')
        check('B 手写的占位行（有 ID、没文件）还在',
              'future' in rows,
              f'现在的 ID={list(rows)}')
        check('C 没有 ID 的备注行还在',
              any('总备注' in str(v) for v in (rows.get('') or [])),
              f'空 ID 行={rows.get("")}')
        check('D 记了文件但文件真不在了 → 删掉', 'gone' not in rows,
              f'现在的 ID={list(rows)}')
        check('E 真在的两行还在', 'a' in rows and 'b' in rows)
        # 标记
        r_b = next((r for r in range(2, ws.max_row + 1)
                    if str(ws.cell(row=r, column=1).value or '') == 'b'), None)
        cm = getattr(ws.cell(row=r_b, column=1).comment, 'text', None) if r_b else None
        fill = ws.cell(row=r_b, column=1).fill.fgColor.rgb if r_b else None
        check('F 人标的颜色与批注还在', cm == '这张要重画' and str(fill) == '00FFF2CC',
              f'批注={cm!r} 底色={fill!r}')
        check('G 人加的「分类说明」页还在', '分类说明' in wb.sheetnames,
              f'页={wb.sheetnames}')

        # 收尾：统计要说出来
        from storygraph.story.xlsx_preserve import last_asset_stats
        st = last_asset_stats()
        # 留 2 行（`future` 占位 + 没有 ID 的备注行），删 1 行（文件真的不在了）
        check('H 回执说清"留了几行 / 删了几行"',
              st.get('kept', -1) == 2 and st.get('dropped', -1) == 1,
              f'stats={st}')

        ok_n = sum(1 for ok, _ in results if ok)
        print(f'\n汇总：{ok_n}/{len(results)} 通过')
        return 0 if ok_n == len(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
