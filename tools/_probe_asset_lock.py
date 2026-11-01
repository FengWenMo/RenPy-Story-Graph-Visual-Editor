# -*- coding: utf-8 -*-
"""探针：去掉锁之后，那个并发用例真的会红吗（证明判据不是摆设）。"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets import asset_registry as AR   # noqa: E402


def run(lock) -> tuple:
    AR._ASSET_WRITE_LOCK = lock
    tmp = Path(tempfile.mkdtemp(prefix='sg-lock-', dir=str(ROOT / '.pip-tmp')))
    errors = []

    def worker(n):
        try:
            for j in range(4):
                AR.append_rows_to_table(
                    tmp, 'sounds',
                    [{'ID': f'sfx-{n}-{j}', 'Path': f'sfx/{n}-{j}.wav'}])
        except BaseException as exc:               # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = AR.read_excel_rows(tmp / 'sounds.xlsx')
    ids = {str(r.get('ID') or '') for r in rows}
    want = {f'sfx-{n}-{j}' for n in range(8) for j in range(4)}
    return len(want - ids), len(errors), len(rows)


real = AR._ASSET_WRITE_LOCK
for name, lock in (('有锁', real), ('无锁', contextlib.nullcontext())):
    lost, errs, rows = run(lock)
    print(f'{name}: 丢行={lost} 异常={errs} 表里行数={rows}')
AR._ASSET_WRITE_LOCK = real
