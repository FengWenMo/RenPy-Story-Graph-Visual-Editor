"""
验收（第 3 条缺口）：**顶层内容在"全量重生成"里还丢不丢**。

判据用 `generation_guard.opaque_loss(源, 产物)`：源文件里每一条
**顶层语句**（`default` / `define` / `image` / `transform` / `screen` /
`init python` / `style` / `translate` …）都必须能在产物里逐字找到。

走的是**纯全量**那条路（`.rpy → 拍 → xlsx → 读回 → 渲染`）——
也就是"导出到新路径"和"补丁失败退回全量"会走的那条路。
增量那条路本来就保真，不在本探针管辖内。

只读真实项目，产物写临时目录。
用法：python tools/probe_opaque_fidelity.py [项目根]
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def main(argv) -> int:
    project = Path(argv[1]) if len(argv) > 1 else REAL
    if not project.exists():
        print('真实项目不在，跳过')
        return 0

    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.pipeline.compile import compile_beats
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel
    from storygraph.story.generation_guard import opaque_loss

    files = sorted((project / 'game').rglob('*.rpy'))
    files = [f for f in files if not f.name.endswith('.generated.rpy')]
    from storygraph.story.source_style import read_text as read_source

    bad, total_chunks, lost_total = [], 0, 0
    with tempfile.TemporaryDirectory(prefix='opaque_') as td:
        tmp = Path(td)
        for i, f in enumerate(files):
            # ⚠ 必须走 `source_style`（剥 BOM、换行归一）：
            # 直接 `read_text('utf-8')` 会留下 `\ufeff`，于是第一条顶层语句
            # 永远"在产物里找不到" → 5 个 BOM 文件全被误报成"丢 1 处"。
            # 生产那条路（`compile.write_compiled`）本来就这么读，探针也要同口径，
            # 否则**审计工具自己的假阳性**会把真 bug 淹掉（2026-10-23 实测）。
            text = read_source(f)[0]
            a = IncrementalRenPyAnalyzer()
            pr, _ = a.analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            xl = tmp / f'{i}.xlsx'
            export_beats_to_excel(beats, xl)
            out = compile_beats(import_beats_from_excel(xl)).rpy_text
            lost = opaque_loss(text, out)
            total_chunks += len(lost) + 0
            if lost:
                lost_total += len(lost)
                bad.append((f, lost))
    print(f'{project}（逐文件 · 纯全量：rpy → 拍 → xlsx → 读回 → 渲染）')
    print(f'  文件 {len(files)}；顶层语句丢失的文件 {len(bad)}；丢失处数 {lost_total}')
    for f, lost in bad[:8]:
        print(f'\n  ✗ {f.relative_to(project)}：丢 {len(lost)} 处')
        for line_no, head in lost[:6]:
            print(f'      第 {line_no} 行  {head[:70]}')
    if not bad:
        print('  → 顶层内容**一处都没丢**（全量重生成现在是保真的）')
    return 1 if lost_total else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
