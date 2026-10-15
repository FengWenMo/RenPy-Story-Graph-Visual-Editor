#!/usr/bin/env python
"""
幂等审计：**导出一次**与**再导出一次**必须逐字节相同
====================================================

    t0 = 原文件
    t1 = render(parse(t0))
    t2 = render(parse(t1))          ← t2 必须 == t1
    t3 = render(parse(t2))          ← t3 必须 == t2

为什么单独一个工具：已有的两个审计都**看不见这类缺陷**——

  · `tools/audit_roundtrip.py` 比的是**语句语义指纹**（kind + payload），
    文件级的"多一行注释""排版漂移"它一律不算；
  · `tools/hell_edit_rounds.py` 的 A2 轴比的是**归一化后**的文本
    （它要过滤"我故意插卡"的噪声），于是"生成标记多一行"被它抹平了。

而这类缺陷恰恰是用户最能感知的：**"我只是又点了一次导出，文件就变了。"**

判据口径（写在这里，免得数字好看但说不清）：

  · `t0 → t1` 有差异是**正常的**：首次导出要补生成标记、统一缩进。
    这里只**统计**，不当失败。
  · `t1 → t2` 与 `t2 → t3` 的差异是**失败**：那是"每导出一次就变一次"。
  · 只读，不写任何东西进项目。

用法：
    python tools/audit_idempotence.py [项目根] [--limit N] [--excel] [-v]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.discover import discover_scripts  # noqa: E402
from storygraph.parsing.renpy_parser import (
    IncrementalRenPyAnalyzer)  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402

DEFAULT_REAL = r'D:\千连万理\RenPyTest\sr_general'


def _analyzer():
    return IncrementalRenPyAnalyzer()


def _render(path: Path, text: str, tmp: Path, tag: str) -> str:
    f = tmp / f'{path.stem}.{tag}.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = _analyzer().analyze(files=[f], graph=None)
    return render_beats_to_rpy(beats_from_parse_result(pr))


def _render_via_excel(path: Path, text: str, tmp: Path, tag: str) -> str:
    """走**产品那条路**：拍 → 表 → 拍 → .rpy。"""
    from storygraph.pipeline.compile import compile_beats
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    f = tmp / f'{path.stem}.{tag}.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = _analyzer().analyze(files=[f], graph=None)
    beats = beats_from_parse_result(pr)
    x = tmp / f'{path.stem}.{tag}.xlsx'
    export_beats_to_excel(beats, x)
    return compile_beats(import_beats_from_excel(x)).rpy_text


def _first_diff(a: str, b: str, limit: int = 4) -> list:
    la, lb = a.split('\n'), b.split('\n')
    out = []
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else '<缺行>'
        y = lb[i] if i < len(lb) else '<缺行>'
        if x != y:
            out.append(f'第 {i + 1} 行  A={x!r}  B={y!r}')
            if len(out) >= limit:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default=DEFAULT_REAL)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--only', default='',
                    help='只看文件名里含这个子串的脚本（triage 用，例：flipcard）')
    ap.add_argument('--excel', action='store_true',
                    help='走"拍 → 表 → 拍"那条路（慢，但那是产品主路）')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f'项目根不存在：{root}')
        return 2
    scripts = discover_scripts(root)
    if args.only:
        scripts = [p for p in scripts if args.only in Path(p).name]
    if args.limit:
        scripts = scripts[:args.limit]
    render = _render_via_excel if args.excel else _render
    mode = '经 Excel' if args.excel else '纯渲染'

    print(f'项目根：{root}')
    print(f'脚本：{len(scripts)} 个 · 路径：{mode}')
    print()

    tmp = Path(tempfile.mkdtemp(prefix='sgidem_'))
    first_pass_changed = 0
    bad: list = []
    for p in scripts:
        try:
            t0 = p.read_text(encoding='utf-8', errors='ignore')
            t1 = render(p, t0, tmp, 'r1')
            t2 = render(p, t1, tmp, 'r2')
            t3 = render(p, t2, tmp, 'r3')
        except Exception as exc:                # noqa: BLE001
            bad.append((p.name, f'审计本身出错：{type(exc).__name__}: {exc}', []))
            continue
        if t1 != t0:
            first_pass_changed += 1
        if t2 != t1:
            bad.append((p.name, '第 1→2 轮变形（**不幂等**）',
                        _first_diff(t1, t2)))
        elif t3 != t2:
            bad.append((p.name, '第 2→3 轮才变形（**不幂等**）',
                        _first_diff(t2, t3)))
        elif args.verbose:
            print(f'  [ ok ] {p.name}  ({len(t1.splitlines())} 行)')

    print()
    print(f'首轮有变化（正常：补生成标记 / 统一缩进）：{first_pass_changed} 个')
    if not bad:
        print('✅ **幂等通过**：每个文件导出一次与再导出一次逐字节相同')
        return 0
    print(f'❌ 幂等失败：{len(bad)} 个文件')
    for name, why, diff in bad:
        print(f'  · {name} —— {why}')
        for line in diff:
            print(f'      {line}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
