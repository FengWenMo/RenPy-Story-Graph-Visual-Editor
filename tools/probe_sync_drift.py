"""找出 sync 指纹里还不稳定的字段（重构前必须钉死）。"""
import difflib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from probe_sync_fingerprint import (build_project, result_fingerprint,
                                    table_fingerprint)   # noqa: E402

from storygraph.assets.asset_registry import (FileWalkIndex,   # noqa: E402
                                              sync_asset_tables)

runs = []
_raw = []
for _ in range(2):
    base = Path(tempfile.mkdtemp(prefix='sync_diff_'))
    root = build_project(base)
    res = sync_asset_tables(root, walk=FileWalkIndex(root))
    runs.append({
        'table': table_fingerprint(root / 'game' / 'asset_tables', base),
        'result': result_fingerprint(res, base),
    })
    # 逐格明细（找差异用）
    import openpyxl
    from storygraph.assets.asset_registry import _STANDARD_TABLES
    cells = []
    for name in sorted(_STANDARD_TABLES.values()):
        p = root / 'game' / 'asset_tables' / name
        if not p.is_file():
            continue
        ws = openpyxl.load_workbook(p).active
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            for c, v in enumerate(row, 1):
                cells.append(f'{name}|{r}|{c}|{v}')
    _raw.append(cells)
    import shutil
    shutil.rmtree(base, ignore_errors=True)

a, b = _raw
print(f'表格数：{len(a)} vs {len(b)}')
for i, (x, y) in enumerate(zip(a, b)):
    if x != y:
        print(f'  差异 #{i}:\n    A: {x[:160]}\n    B: {y[:160]}')

print('table 一致:', runs[0]['table'] == runs[1]['table'])
if runs[0]['result'] == runs[1]['result']:
    print('result 一致')
else:
    a = json.dumps(json.loads(runs[0]['result']), indent=1,
                   ensure_ascii=False, sort_keys=True).splitlines()
    b = json.dumps(json.loads(runs[1]['result']), indent=1,
                   ensure_ascii=False, sort_keys=True).splitlines()
    print('result 差异：')
    for ln in difflib.unified_diff(a, b, lineterm='', n=1):
        print('  ' + ln[:200])
