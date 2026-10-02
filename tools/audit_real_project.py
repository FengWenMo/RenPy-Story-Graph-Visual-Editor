#!/usr/bin/env python
"""
真实项目实测（审计用，不入库）：
  1) 解析覆盖率：88 个 .rpy 逐文件解析，统计 Statement kind 分布与降级比例
  2) 往返保真：rpy -> parse -> beats -> xlsx -> beats -> rpy，与原文逐行比对
"""
from __future__ import annotations

import sys
import traceback
from collections import Counter
from pathlib import Path

PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa
from storygraph.story.beat_build import beats_from_parse_result        # noqa
from storygraph.story.excel_export import export_beats_to_excel        # noqa
from storygraph.story.excel_import import import_beats_from_excel      # noqa
from storygraph.pipeline.compile import compile_beats                  # noqa
from storygraph.pipeline.build import load_asset_registry              # noqa


def norm(line: str) -> str:
    return line.strip()


def meaningful(path: Path):
    """原文里的"有意义行"：去掉空行与纯注释后的内容行。"""
    out = []
    for ln in path.read_text(encoding='utf-8').splitlines():
        s = ln.strip()
        if not s or s.startswith('#'):
            continue
        out.append(s)
    return out


def main() -> int:
    scripts = sorted(p for p in PROJECT.rglob('*.rpy')
                     if 'build' not in p.parts[:2] or True)
    # 只测 game/ 下的作者脚本
    scripts = [p for p in scripts if 'game' in p.parts]
    print(f'待解析脚本：{len(scripts)}')

    analyzer = IncrementalRenPyAnalyzer()
    try:
        registry = load_asset_registry(PROJECT)
        reg_n = len(getattr(registry, 'rows', registry) or [])
        print(f'资源表载入：{type(registry).__name__} 条目~{reg_n}')
    except Exception as e:
        print(f'资源表载入失败：{e}')
        registry = None

    kinds = Counter()
    per_file = []
    failures = []

    for p in scripts:
        try:
            pr, _ = analyzer.analyze(files=[p], graph=None)
            beats = beats_from_parse_result(pr)
        except Exception as e:
            failures.append((p, f'{type(e).__name__}: {e}'))
            continue

        # 统计 statement 分布
        n_stmt = 0
        for b in beats:
            for s in getattr(b, 'statements', None) or []:
                kinds[s.kind] += 1
                n_stmt += 1
        per_file.append((p, len(beats), n_stmt))

    print()
    print('=== 解析失败 ===')
    if failures:
        for p, e in failures:
            print(f'  {p.relative_to(PROJECT)}: {e}')
    else:
        print('  无')

    total_stmt = sum(kinds.values())
    print()
    print(f'=== Statement 分布（共 {total_stmt} 条）===')
    for k, v in kinds.most_common():
        pct = v * 100.0 / total_stmt if total_stmt else 0
        print(f'  {k:<12} {v:>6}  {pct:5.1f}%')
    degraded = kinds.get('raw', 0) + kinds.get('unknown', 0)
    if total_stmt:
        print(f'  --- 降级（raw+unknown）: {degraded} '
              f'({degraded*100.0/total_stmt:.1f}%)')

    # ---------- 往返保真 ----------
    print()
    print('=== 往返保真（rpy -> xlsx -> rpy，逐行比对）===')
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix='audit_rt_'))
    exact, lossy, err = 0, [], []
    for p in scripts:
        try:
            pr, _ = analyzer.analyze(files=[p], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            x = tmp / (p.stem + '.xlsx')
            export_beats_to_excel(beats, x, registry=registry)
            back = import_beats_from_excel(x, registry=registry)
            res = compile_beats(back, registry=registry)
            got = [norm(l) for l in res.rpy_text.splitlines()
                   if norm(l) and not norm(l).startswith('#')]
            want = meaningful(p)
            if got == want:
                exact += 1
            else:
                # 记录差异规模
                import difflib
                sm = difflib.SequenceMatcher(None, want, got)
                ratio = sm.ratio()
                lossy.append((p.relative_to(PROJECT), ratio,
                              len(want), len(got)))
        except Exception as e:
            err.append((p.relative_to(PROJECT), f'{type(e).__name__}: {e}'))

    ok_n = exact
    print(f'  完全一致（逐字节级行比对）：{ok_n}/{len(scripts)}')
    print(f'  有差异：{len(lossy)}   异常：{len(err)}')
    print()
    print('  --- 差异最大的 12 个文件（相似度升序）---')
    for name, ratio, nw, ng in sorted(lossy, key=lambda x: x[1])[:12]:
        print(f'  {ratio:5.1%}  原文{nw:>4}行 -> 产物{ng:>4}行  {name}')
    if err:
        print()
        print('  --- 异常 ---')
        for name, e in err[:12]:
            print(f'  {name}: {e}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
