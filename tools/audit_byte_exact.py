#!/usr/bin/env python
"""
逐字节保真体检：`.rpy → .xlsx → .rpy --source` 到底有几个字节变了
================================================================

体检报告（`docs/评估_代码完成度与真实项目体检_2026-10-18.md`）P1-1 的判据：

    真机项目的每一个 .rpy，摊成剧本表，再用 `--source` 增量写回，
    产物与原文**逐字节相同**（只允许首部多一条生成标记 + 它后面那个空行）。

    python tools/audit_byte_exact.py --project D:\\千连万理\\renpytest\\sr_general
    python tools/audit_byte_exact.py --project <tutorial> --engine-lint-sample 3

参数：
  `--project`   项目根（含 `game/`）或直接的 `game/` 目录
  `--pattern`   只跑匹配的文件（默认 `*.rpy`）
  `--skip-dir`  额外跳过的目录名（逗号分隔；默认已跳 `build/cache/saves`）
  `--limit`     只跑前 N 个文件（冒烟用）
  `--verbose`   每个文件都打一行
  `--keep`      保留中间产物目录（出问题时用来对照）

判定（与报告同源）：
  · **identical** —— 剥掉生成标记后，字节完全相同；
  · **differs**   —— 打印第一处差异的行号与两侧内容。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 生成标记（`story/pipeline` 写盘时加在首部的那一行）
BANNER = '# 由剧本表自动生成，请勿在此文件手工改动结构'

#: 不算"章节"的目录：引擎产物、缓存、存档
_SKIP_DIRS = {'build', 'cache', 'saves', 'log', '.git', '__pycache__'}


def _split_bom(data: bytes) -> tuple[bytes, bytes]:
    if data.startswith(b'\xef\xbb\xbf'):
        return b'\xef\xbb\xbf', data[3:]
    return b'', data


def strip_banner(data: bytes) -> tuple[bytes, bool]:
    """
    去掉首部生成标记（**以及紧跟它的那个空行**），返回 `(正文, 有没有剥)`。

    BOM 要原样留住：它正是 P0-1 的判据（BOM 只允许在文件最开头）。
    """
    bom, body = _split_bom(data)
    if not body.startswith(BANNER.encode('utf-8')):
        return data, False
    nl = body.find(b'\n')
    if nl == -1:
        return bom, True
    rest = body[nl + 1:]
    # 标记之后工具会写一个空行（`\n` 或 `\r\n`）——一并剥掉，
    # 这样"原文没有空行"的文件也能判成逐字节相同。
    if rest.startswith(b'\r\n'):
        rest = rest[2:]
    elif rest.startswith(b'\n'):
        rest = rest[1:]
    return bom + rest, True


def _first_diff(a: bytes, b: bytes) -> str:
    """第一处差异的人话描述（按行定位）。"""
    la = a.decode('utf-8', 'replace').splitlines()
    lb = b.decode('utf-8', 'replace').splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else '<文件结束>'
        y = lb[i] if i < len(lb) else '<文件结束>'
        if x != y:
            return (f'第 {i + 1} 行：原文 {x!r}\n'
                    f'                  产物 {y!r}')
    return '行内容一致（差异在换行符/BOM/结尾空行上）'


def _iter_rpy(base: Path, pattern: str, skip: set):
    for p in sorted(base.rglob(pattern)):
        if any(part in skip for part in p.relative_to(base).parts[:-1]):
            continue
        if p.name.startswith('.'):
            continue
        yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='逐字节保真体检')
    ap.add_argument('--project', required=True,
                    help='项目根（含 game/）或直接给 game/ 目录')
    ap.add_argument('--pattern', default='*.rpy')
    ap.add_argument('--skip-dir', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args(argv)

    from storygraph.pipeline.build import load_asset_registry
    from storygraph.pipeline.compile import compile_table
    from storygraph.story.script_table import open_rpy_as_table

    root = Path(args.project)
    base = root / 'game' if (root / 'game').is_dir() else root
    if not base.is_dir():
        print(f'找不到目录：{base}', file=sys.stderr)
        return 2

    skip = set(_SKIP_DIRS) | {s for s in args.skip_dir.split(',') if s}
    files = list(_iter_rpy(base, args.pattern, skip))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print(f'{base} 下没有匹配 {args.pattern} 的文件', file=sys.stderr)
        return 2

    registry = None
    try:
        registry = load_asset_registry(root if (root / 'game').is_dir() else base)
    except Exception as exc:                   # noqa: BLE001 - 没有资源表也能跑
        print(f'（没有资源表，按裸表跑：{type(exc).__name__}: {exc}）')

    tmp = Path(tempfile.mkdtemp(prefix='sgbyte_'))
    same = []
    diff = []
    failed = []
    for i, src in enumerate(files, start=1):
        rel = src.relative_to(base)
        xlsx = tmp / f'{i:04d}.xlsx'
        out = tmp / f'{i:04d}.rpy'
        try:
            open_rpy_as_table(str(src), str(xlsx), registry=registry)
            _p, _s, res = compile_table(
                str(xlsx), rpy_path=out, registry=registry,
                write_sheet=False, force=True, source_path=str(src),
                skip_empty=True, engine_lint=False)
        except Exception as exc:               # noqa: BLE001
            failed.append((rel, f'{type(exc).__name__}: {exc}'))
            continue
        if not res.written or not out.exists():
            failed.append((rel, '没有产物（门禁拦下或空表）'))
            continue
        src_b = src.read_bytes()
        out_b = out.read_bytes()
        # **两侧都剥**：有些文件本身就是上一次工具生成的（首行就有标记），
        # 只剥产物那一侧会把它当成"丢了一行"。
        body, _had = strip_banner(out_b)
        src_body, _src_had = strip_banner(src_b)
        if body == src_body:
            same.append((rel, bool(res.used_patch)))
            if args.verbose:
                print(f'  ✓ {rel}')
        else:
            diff.append((rel, _first_diff(src_body, body),
                         bool(res.used_patch)))
            print(f'  ✗ {rel}')
            if args.verbose:
                print('     ' + diff[-1][1].replace('\n', '\n     '))

    print()
    print('=' * 72)
    total = len(files)
    print(f'{base}')
    print(f'文件 {total} · 逐字节相同 {len(same)} · 有差异 {len(diff)} '
          f'· 出错 {len(failed)}')
    patch_ok = sum(1 for _r, p in same if p)
    print(f'（其中 {patch_ok} 个走的是**增量补丁**；'
          f'{len(same) - patch_ok} 个原样透传/无需改动）')
    if diff:
        print()
        print('有差异的文件：')
        for rel, why, patched in diff:
            print(f'  ✗ {rel}（{"增量" if patched else "全量/透传"}）')
            print('     ' + why.replace('\n', '\n     '))
    if failed:
        print()
        print('出错的文件：')
        for rel, why in failed:
            print(f'  ! {rel}：{why}')
    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f'\n中间产物：{tmp}')
    return 0 if not diff and not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
