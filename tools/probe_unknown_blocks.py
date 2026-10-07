"""
探针：真实项目里**工具不认识的块语句**有哪些，以及它们过一轮全量会不会丢。

用法：
    python tools/probe_unknown_blocks.py [项目根] [--roundtrip]

只读。`--roundtrip` 会额外做一次 `.rpy → 拍 → 表 → .rpy`（纯全量路径），
再逐行核对那几块的原文是否**逐字**出现在产物里 —— 这是 M1 的真机判据。

输出三段：
  ① 未建模块清单（按语句头归类，带 file:line）
  ② 每一处的**行级**核对结论（保留 / 丢失）
  ③ 汇总
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402

SKIP_DIRS = {'tmp', 'build', 'build_out', '.git', '__pycache__', 'saves',
             'dist'}


def find_scripts(root: Path) -> list:
    out = []
    for p in sorted(root.rglob('*.rpy')):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        # 工具上一轮的产物：本来就该排除（见项目记忆的 label 计数口径）
        if p.name.endswith('.generated.rpy'):
            continue
        # 嵌套副本（子目录自带 game/）不是本项目的资源：
        # 本项目自己的脚本一定在 `<root>/game/…` 下，或者压根不含 `game/` 段；
        # 形如 `<root>/sr_general/game/…` 的是**同一份项目的副本**。
        parts = list(p.relative_to(root).parts)
        if 'game' in parts and parts[0] != 'game':
            continue
        out.append(p)
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    do_roundtrip = '--roundtrip' in sys.argv
    root = Path(args[0] if args else r'D:\千连万理\RenPyTest\sr_general')
    if not root.is_dir():
        print(f'项目根不存在：{root}')
        return 2

    scripts = find_scripts(root)
    print(f'项目根：{root}')
    print(f'扫描 .rpy：{len(scripts)} 个\n')

    az = IncrementalRenPyAnalyzer()
    records: list = []
    head_counter: Counter = Counter()
    per_file: dict = defaultdict(list)

    for f in scripts:
        try:
            pr, _ = az.analyze(files=[f], graph=None)
        except Exception as exc:                       # noqa: BLE001
            print(f'  !! 解析失败 {f.name}: {type(exc).__name__}: {exc}')
            continue
        for rec in (getattr(pr, 'unknown_blocks', None) or ()):
            records.append((f, rec))
            head_counter[(rec.get('head') or '').split('(')[0].strip()
                         or '(空)'] += 1
            per_file[f].append(rec)

    # —— 宿主注册表（M2）：项目**自己公布**的自定义语句 ——
    from storygraph.story.cds_registry import (format_cds_report,
                                               scan_project_cds)
    cds = scan_project_cds(root)
    print('⓪ 宿主注册表（Ren\'Py CDS：renpy.register_statement）')
    for line in format_cds_report(cds).splitlines():
        print('   ' + line)
    print()

    print('① 未建模块清单')
    if not records:
        print('   （无 —— 这个项目的剧情层没有工具不认识的块语句）')
    for (f, rec) in records:
        print(f"   {f.relative_to(root)}:{rec['line']}  "
              f"共 {rec['lines']} 行  {rec['head']}")
    print()

    if not records:
        print('③ 汇总：0 处**未识别**块。')
        print('   ⚠ 注意区分：`while` / `for` 这类循环块已经**内置建模**'
              '（D2 → 2026-10-07 第三轮加作用域：等级 T0，头行可编辑、'
              '块体也进了语句流），')
        print('   所以它们不出现在这份清单里 —— 它们由 `cli.py build` 的'
              '「工具可编辑覆盖率」那一行统计。')
        return 0

    print('② 按语句头归类')
    for head, n in head_counter.most_common():
        print(f'   {n:>4} 处  {head}')
    print()

    if not do_roundtrip:
        print('③ 汇总：见上。（加 --roundtrip 核对内容是否逐字保住）')
        return 0

    print('② 行级核对（.rpy → 拍 → 表 → .rpy，纯全量路径）')
    from storygraph.pipeline.compile import compile_beats
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.excel_export import export_beats_to_excel
    from storygraph.story.excel_import import import_beats_from_excel

    tmp = Path(__file__).resolve().parents[1] / '.pytest-tmp' / 'probe_ub'
    tmp.mkdir(parents=True, exist_ok=True)

    kept = lost = 0
    for f in sorted(per_file):
        src = f.read_text(encoding='utf-8')
        try:
            pr, _ = az.analyze(files=[f], graph=None)
            beats = beats_from_parse_result(pr)
            x = tmp / f'{f.stem}.xlsx'
            export_beats_to_excel(beats, x)
            out = compile_beats(import_beats_from_excel(x)).rpy_text
        except Exception as exc:                       # noqa: BLE001
            print(f'   {f.name}: 往返失败 {type(exc).__name__}: {exc}')
            continue
        for rec in per_file[f]:
            # 逐行核对：块体里每一条非空、非纯注释的原文行都该在产物里
            start = rec['line']
            lines = src.split('\n')
            head = lines[start - 1]
            base = len(head) - len(head.lstrip())
            body = []
            i = start
            while i < len(lines):
                cur = lines[i]
                if cur.strip() and (len(cur) - len(cur.lstrip())) <= base:
                    break
                body.append(cur.strip())
                i += 1
            missing = [ln for ln in body if ln and ln not in out]
            tag = 'OK  ' if not missing else 'LOST'
            if missing:
                lost += 1
            else:
                kept += 1
            print(f'   [{tag}] {f.relative_to(root)}:{start}  '
                  f'块体 {len(body)} 行'
                  + (f'  缺 {len(missing)}: {missing[:3]}' if missing else ''))

    print(f'\n③ 汇总：{len(records)} 处未建模块，'
          f'内容保留 {kept} / 丢失 {lost}')
    return 0 if not lost else 1


if __name__ == '__main__':
    raise SystemExit(main())
