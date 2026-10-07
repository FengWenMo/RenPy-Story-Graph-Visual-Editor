#!/usr/bin/env python
"""
幂等探针：把某个真实 .rpy 的 t1 / t2 摊开看（T3 也一起看）
==========================================================

`tools/audit_idempotence.py` 只说"第 N 行 A=… B=…"，看不到**上下文** ——
而这类缩进漂移的成因全在上下文里（前面那一段是什么、空行在不在）。
本脚本把 t1 / t2 / t3 各写进临时目录，并打印差异行前后若干行。

用法：
    python tools/probe_idem_context.py <文件相对路径> [--around 114] [--pad 12]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import (  # noqa: E402
    IncrementalRenPyAnalyzer, _top_level_region)
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy  # noqa: E402

DEFAULT_REAL = r'D:\千连万理\RenPyTest\sr_general'


def render(path: Path, text: str, tmp: Path, tag: str) -> str:
    f = tmp / f'{path.stem}.{tag}.rpy'
    f.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return render_beats_to_rpy(beats_from_parse_result(pr))


def show(label: str, text: str, lo: int, hi: int) -> None:
    lines = text.split('\n')
    print(f'--- {label} （共 {len(lines)} 行） ---')
    for i in range(max(0, lo - 1), min(len(lines), hi)):
        print(f'{i + 1:5} |{lines[i]}')
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('rel', help='相对项目根的 .rpy 路径')
    ap.add_argument('--root', default=DEFAULT_REAL)
    ap.add_argument('--around', type=int, default=0, help='t1 里要看的位置')
    ap.add_argument('--pad', type=int, default=14)
    ap.add_argument('--regions', action='store_true',
                    help='顺带打印 _top_level_region 的块清单')
    ap.add_argument('--head', type=int, default=6, help='块体打印的前 N 行')
    ap.add_argument('--tail', type=int, default=2, help='块体打印的后 N 行')
    args = ap.parse_args()

    src = Path(args.root) / args.rel
    if not src.is_file():
        print(f'找不到：{src}')
        return 2
    t0 = src.read_text(encoding='utf-8')
    tmp = Path(tempfile.mkdtemp(prefix='sgidemctx_'))
    t1 = render(src, t0, tmp, 'r1')
    t2 = render(src, t1, tmp, 'r2')
    t3 = render(src, t2, tmp, 'r3')

    print(f'源：{src}')
    print(f't1==t2 ? {t1 == t2}   t2==t3 ? {t2 == t3}')
    print()

    lo = max(1, args.around - args.pad) if args.around else 1
    hi = (args.around + args.pad) if args.around else 200
    show('t1', t1, lo, hi)
    show('t2', t2, lo, hi)
    if t2 != t3:
        show('t3', t3, lo, hi)

    if args.regions:
        for tag, path in (('t0', src), ('t1', tmp / f'{src.stem}.r1.rpy'),
                          ('t2', tmp / f'{src.stem}.r2.rpy')):
            print(f'--- _top_level_region({tag}) ---')
            for head, code, row in _top_level_region(path):
                body = [ln for ln in code.split('\n') if ln.strip()]
                print(f'  行 {row:4}: head={head!r}  非空块体={len(body)}')
                for ln in body[:args.head]:
                    print(f'          |{ln}')
                if len(body) > args.head + args.tail:
                    print(f'          … 另 {len(body) - args.head - args.tail} 行')
                for ln in body[len(body) - args.tail:]:
                    print(f'          |{ln}')
            print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
