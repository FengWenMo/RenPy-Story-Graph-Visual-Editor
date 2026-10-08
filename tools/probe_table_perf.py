#!/usr/bin/env python
"""
建表性能探针（用户报："打开剧情演出表过久 → 程序未响应"）
============================================================

分三段量**真机**（`sr_general`，53 个脚本）：

    ① 解析（parse + beats_from_parse_result）—— 建表的前置
    ② 写表 `export_beats_to_excel`（不带缩略图）
    ③ 写表**带缩略图**（`include_thumbnails=True`，图库那条路）

再按章节量一次（最大的几章单独计时），最后可选 cProfile 打热点。

    python tools/probe_table_perf.py [--profile] [项目根]
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts          # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result    # noqa: E402
from storygraph.story.excel_export import export_beats_to_excel    # noqa: E402


def _registry(root: Path):
    try:
        from storygraph.assets.asset_registry import load_asset_registry
        return load_asset_registry(root)
    except Exception as e:                        # noqa: BLE001
        print(f'  （资源表没读到，按无注册表跑：{e}）')
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', action='store_true')
    ap.add_argument('root', nargs='?',
                    default=r'D:\千连万理\RenPyTest\sr_general')
    args = ap.parse_args()
    root = Path(args.root)
    files = discover_scripts(root)
    print(f'项目 {root} · 脚本 {len(files)} 个')

    # ---------- ① 解析 ----------
    t0 = time.perf_counter()
    per_file = []
    all_beats = []
    for f in files:
        t = time.perf_counter()
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        per_file.append((time.perf_counter() - t, len(beats), Path(f).name))
        all_beats.extend(beats)
    parse_s = time.perf_counter() - t0
    print(f'① 解析 53 文件：{parse_s:.3f}s · 拍 {len(all_beats)}')
    worst = sorted(per_file, reverse=True)[:5]
    for dt, n, name in worst:
        print(f'     {dt:6.3f}s  {n:5d} 拍  {name}')

    tmp = Path(tempfile.mkdtemp(prefix='probe_table_perf_'))
    reg = _registry(root)

    # ---------- ② 写表 ----------
    def _timed(tag, **kw):
        out = tmp / f'{tag}.xlsx'
        t = time.perf_counter()
        export_beats_to_excel(all_beats, out, **kw)
        dt = time.perf_counter() - t
        mb = out.stat().st_size / 1e6 if out.exists() else 0
        print(f'{tag:26s} {dt:7.3f}s   {mb:5.1f} MB')
        return dt

    print()
    d2 = _timed('② 全项目写表', registry=reg)
    d3 = _timed('③ 全项目写表+缩略图', registry=reg, include_thumbnails=True)
    print(f'    → 缩略图那一段额外花了 {d3 - d2:.3f}s')

    # ---------- 单章（建模表最常走的粒度）----------
    print()
    biggest = sorted(per_file, reverse=True)[:3]
    for _dt, _n, name in biggest:
        f = next(x for x in files if Path(x).name == name)
        pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
        beats = beats_from_parse_result(pr)
        for b in beats:
            b.stage = b.stage or ''
        out = tmp / f'one_{name}.xlsx'
        t = time.perf_counter()
        export_beats_to_excel(beats, out, registry=reg)
        print(f'    单章 {name:24s} {len(beats):5d} 拍  '
              f'{time.perf_counter() - t:6.3f}s')

    if args.profile:
        print('\n======== cProfile（全项目写表）========')
        out = tmp / 'prof.xlsx'
        pr = cProfile.Profile()
        pr.enable()
        export_beats_to_excel(all_beats, out, registry=reg)
        pr.disable()
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).sort_stats('cumulative').print_stats(24)
        for ln in buf.getvalue().splitlines()[-32:]:
            print('   ' + ln)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
