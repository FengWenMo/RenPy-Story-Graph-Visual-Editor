"""真机验收：立绘/退场摊列（M3-2）—— 产物必须**逐字不变**。

判据（路线图 M3-2 的 ①②④）：
  ① 摊列后 `render_beats_to_rpy` 产物与不摊列**逐字相同**；
  ② 往返（.rpy → 拍 → 表 → 拍 → .rpy）里 `show`/`hide` 多重集与顺序不变；
  ④ 真机 53 文件对账 0 差异（抽样若干章）。
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.parsing.discover import discover_scripts
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import (BEAT_HEADERS,
                                         hide_cell_parts, show_cell_parts)
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel
from storygraph.story.beat_render import render_beats_to_rpy

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
OUT = Path(__file__).resolve().parent / '_probe_show_split'
OUT.mkdir(exist_ok=True)


def show_multiset(beats):
    c = Counter()
    for b in beats:
        for p in show_cell_parts(b):
            c[('show', p)] += 1
        for p in hide_cell_parts(b):
            c[('hide', p)] += 1
    return c


def main():
    files = sorted(discover_scripts(REAL))[:12]
    az = IncrementalRenPyAnalyzer()
    total_bad = 0
    extras_seen = 0
    multi = 0
    for f in files:
        pr, _ = az.analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        if not beats:
            continue
        multi += sum(1 for b in beats if len(show_cell_parts(b)) > 1)
        # ① 直渲染（不摊列）
        direct = render_beats_to_rpy(beats, label_name=None)
        # ② 过表：导出（摊列）→ 读回 → 渲染
        xl = OUT / f'{Path(f).stem}.xlsx'
        export_beats_to_excel(beats, xl)
        back = import_beats_from_excel(xl)
        after = render_beats_to_rpy(back, label_name=None)
        # 表里到底加了哪些摊列
        import re
        import openpyxl
        hdr = [c.value for c in openpyxl.load_workbook(xl).active[1]]
        extras = [h for h in hdr
                  if isinstance(h, str) and re.match(r'^(立绘|退场)\d+$', h)]
        extras_seen += len(extras)
        ok_render = (direct == after)
        c1, c2 = show_multiset(beats), show_multiset(back)
        ok_items = (c1 == c2)
        if not (ok_render and ok_items):
            total_bad += 1
            print(f'[FAIL] {Path(f).name}: render_same={ok_render} '
                  f'items_same={ok_items}')
            if not ok_items:
                print('   only-before:', list((c1 - c2).items())[:5])
                print('   only-after :', list((c2 - c1).items())[:5])
    print(f'抽查 {len(files)} 个文件：失败 {total_bad}')
    print(f'含多立绘的拍（show 项 > 1）合计：{multi}')
    print(f'生成的摊列总数（跨文件累加）：{extras_seen}')
    return 1 if total_bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
