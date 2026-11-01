# -*- coding: utf-8 -*-
"""生成 `tests/tech_debt_budget.json`：技术债**上限快照**（棘轮）。

只钉"只许降"的量：超大函数数、高分支数、print 数、swallow 数、宽泛 except 数、
TODO 数、超长文件数、导入环（个数 + 参与的模块集合）。
总行数 / 导入边数**不钉**：功能在长，那些数字本来就会涨。

生成：`python tools/debt_budget.py`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import debt_report as DR             # noqa: E402


def main() -> int:
    data = DR.collect()
    cycles = data['import_cycles_all']
    payload = {
        'note': '技术债上限（棘轮）：只许降，不许升。生成：python tools/debt_budget.py',
        'thresholds': data['thresholds'],
        'limits': {
            'long_functions': data['long_functions'],
            'complex_functions': data['complex_functions'],
            'print_calls': data['print_calls'],
            'print_library': data['print_library'],
            'swallow_calls': data['swallow_calls'],
            'broad_excepts': data['broad_excepts'],
            'todos': data['todos'],
            'long_files': len(data['long_files']),
            'import_cycles_all': len(cycles),
            'import_cycles_top': len(data['import_cycles_top']),
            'modules_in_cycles_all': sum(len(c) for c in cycles),
        },
        'cycle_modules': sorted({m for c in cycles for m in c}),
        'cycle_shapes': sorted('|'.join(c) for c in cycles),
        'snapshot': {
            'modules': data['modules'],
            'total_lines': data['total_lines'],
            'functions': data['functions'],
            'file_edges_all': data['file_edges_all'],
        },
    }
    out = ROOT / 'tests' / 'tech_debt_budget.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n',
                   encoding='utf-8')
    print(f'写了 → {out.relative_to(ROOT)}')
    for k, v in payload['limits'].items():
        print(f'  {k} = {v}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
