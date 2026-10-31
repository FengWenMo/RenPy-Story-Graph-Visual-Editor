# -*- coding: utf-8 -*-
"""只读探针：把真机项目的「代号表」扫出来，看 `scan_project` 给的是不是真话。

用法：
    python tools/_probe_mapping_real.py            # 两个候选根都扫
    python tools/_probe_mapping_real.py <root>     # 只扫一个

为什么要有它：`sprite_codes.scan_project` 的判据（票数 / 两份编号表打架 /
证据太弱留空）只能在**真项目**上看出对不对；假样本永远全绿。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.sprite_codes import (          # noqa: E402
    read_expression_tables, scan_project, template_rows)

DEFAULT_ROOTS = [Path(r'D:\千连万理\sr_general'),
                 Path(r'D:\千连万理\RenPyTest\sr_general')]


def show(root: Path) -> dict:
    print('=' * 72)
    print(f'项目：{root}  （存在={root.is_dir()}）')
    if not root.is_dir():
        return {}
    scan = scan_project(root)
    rows = template_rows(scan)
    print(f'服装 {len(rows["outfit_codes"])} 行 / 表情 {len(rows["expression_codes"])} 行')
    print('-- 服装（字母 ← 剧本注释数出来的）--')
    for r in rows['outfit_codes']:
        print(f'  {r["char"]:>6}  {r["code"] or "·":>2}  {r["attr"]:<18} {r["label"]}')
    print('-- 表情（编号 ← 对应.txt）--')
    for r in rows['expression_codes']:
        print(f'  {r["char"]:>6}  {r["no"]:>3}  {r["attr"]:<18} {r["label"]}')
    print(f'-- notes（{len(scan.notes)}）--')
    for n in scan.notes:
        print('  · ' + n)
    # 每个角色文件夹里到底有几份编号表（两份打架是真事）
    base = root / 'game' / 'images' / 'characters'
    print('-- 编号表文件 --')
    if base.is_dir():
        for child in sorted(p for p in base.iterdir() if p.is_dir()):
            files, conflicts = read_expression_tables(child)
            if files:
                print(f'  {child.name}: {list(files)}  conflicts={len(conflicts)}')
                for c in conflicts:
                    print('      ! ' + c)
    return rows


def main(argv) -> int:
    roots = [Path(argv[1])] if len(argv) > 1 else DEFAULT_ROOTS
    out = {str(r): show(r) for r in roots}
    dump = Path(__file__).with_name('_probe_mapping_real.json')
    dump.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n已写：{dump}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
