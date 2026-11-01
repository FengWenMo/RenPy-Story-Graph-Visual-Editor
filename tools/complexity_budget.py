# -*- coding: utf-8 -*-
"""生成 `tests/complexity_budget.json`：当前复杂度的**上限快照**（棘轮）。

规则：取"最长 40 个"与"分支最多 40 个"的并集，只记 (文件, 函数名) → 上限。
以后谁把某个函数改得更长/更绕，`tests/test_complexity_budget.py` 会红。
函数被拆小或被改名 → 不入判据（那是改进，不是回归）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import complexity_report as CR                     # noqa: E402


def main() -> int:
    rows = CR.collect()
    by_lines = sorted(rows, key=lambda r: r['lines'], reverse=True)[:40]
    by_branch = sorted(rows, key=lambda r: r['branches'], reverse=True)[:40]
    picked = {}
    for r in by_lines + by_branch:
        key = f"{r['file']}::{r['name']}"
        prev = picked.get(key)
        if prev is None or (r['lines'], r['branches']) > (prev['lines'],
                                                          prev['branches']):
            picked[key] = {'lines': r['lines'], 'branches': r['branches']}
    payload = {
        'note': '复杂度上限（棘轮）：只许降，不许升。生成脚本 tools/complexity_report.py --budget',
        'threshold': {'long_lines': CR.LONG_FUNC, 'complex_branches': CR.COMPLEX_FUNC},
        'limit_new': {'lines': 500, 'branches': 150},
        'functions': dict(sorted(picked.items())),
    }
    out = ROOT / 'tests' / 'complexity_budget.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n',
                   encoding='utf-8')
    print(f'写了 {len(picked)} 条 → {out.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
