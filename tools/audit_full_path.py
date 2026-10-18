#!/usr/bin/env python
"""
P1-1 判据：**不给 `--source`** 走一遍 `rpy → xlsx → rpy`，逐字节还差多少？
=====================================================================

体检报告 P1-1 的原话：

    「全量重生成会重排整份文件：CRLF→LF、连续空行压成 1 个、
      顶层语句之间各插一个空行、行尾空格删除；
      真机 52 个写盘文件里 30 个有差异；`definitions.rpy` 1120 → 1288 行」

那是在**没有底稿**的年代。现在表里记着来源（隐藏表「表信息」），
`compile_table` 会自动接上它走增量 —— 这个脚本量的就是"还给用户之后
到底还差几个字节"。

跑法：

    python tools/audit_full_path.py --project D:\\千连万理\\renpytest\\sr_general
    python tools/audit_full_path.py --project D:\\software\\renpy-8.4.1-sdk\\tutorial

判定：
  · **identical** —— 剥掉首部生成标记（以及紧随它的空行）后**字节相同**；
  · **rewrapped** —— 只有行尾/空行/换行符这类排版差异（P1-1 的正身）；
  · **differs**    —— 内容不同（那是真 bug，不是排版）。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit_byte_exact import strip_banner  # noqa: E402

_SKIP_DIRS = {'build', 'cache', 'saves', 'log', '.git', '__pycache__', 'tl'}


def _iter_rpy(base: Path):
    for p in sorted(base.rglob('*.rpy')):
        if any(part in _SKIP_DIRS for part in p.relative_to(base).parts[:-1]):
            continue
        if p.name.startswith('.'):
            continue
        yield p


def _equivalent(a: bytes, b: bytes) -> bool:
    """忽略空行/行尾空白/换行符之后是否等价（排版差异）。"""
    la = [ln.rstrip() for ln in a.decode('utf-8', 'replace').splitlines()]
    lb = [ln.rstrip() for ln in b.decode('utf-8', 'replace').splitlines()]
    return ([x for x in la if x] == [x for x in lb if x])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='不给 --source 的往返保真')
    ap.add_argument('--project', required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args(argv)

    from storygraph.story.script_table import open_rpy_as_table
    from storygraph.pipeline.compile import compile_table

    root = Path(args.project)
    base = root / 'game' if (root / 'game').is_dir() else root
    files = list(_iter_rpy(base))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f'{base} 下没有 .rpy', file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix='sgfull_'))
    same, rewrap, diff, failed = [], [], [], []
    for i, src in enumerate(files, start=1):
        rel = src.relative_to(base)
        xlsx = tmp / f'{i:04d}.xlsx'
        out = tmp / f'{i:04d}.rpy'
        try:
            open_rpy_as_table(str(src), str(xlsx))
            _p, _s, res = compile_table(
                str(xlsx), rpy_path=out, write_sheet=False, force=True,
                skip_empty=True, engine_lint=False)   # ← 刻意不给 source
        except Exception as exc:                   # noqa: BLE001
            failed.append((rel, f'{type(exc).__name__}: {exc}'))
            continue
        if not out.exists():
            failed.append((rel, '没有产物（门禁拦下 / 空表）'))
            continue
        a, b = src.read_bytes(), out.read_bytes()
        # **两侧都剥**：有些文件本身就是上一次工具生成的（首行就有标记），
        # 只剥产物那一侧会把它当成"丢了一行"。
        body, _ = strip_banner(b)
        src_body, _ = strip_banner(a)
        if body == src_body:
            same.append(rel)
            if args.verbose:
                print(f'  ✓ {rel}')
        elif _equivalent(src_body, body):
            rewrap.append(rel)
            print(f'  ~ {rel}（只是排版差异）')
        else:
            diff.append(rel)
            print(f'  ✗ {rel}（内容不同）')

    print()
    print('=' * 72)
    print(f'{base}')
    print(f'文件 {len(files)} · 逐字节相同 {len(same)} · 排版差异 {len(rewrap)}'
          f' · 内容不同 {len(diff)} · 出错 {len(failed)}')
    if rewrap:
        print('\n排版差异（说明这一份没接上底稿）：')
        for rel in rewrap:
            print(f'  ~ {rel}')
    if diff:
        print('\n内容不同（**这是 bug**）：')
        for rel in diff:
            print(f'  ✗ {rel}')
    if failed:
        print('\n出错：')
        for rel, why in failed:
            print(f'  ! {rel}：{why}')
    return 0 if not diff and not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
