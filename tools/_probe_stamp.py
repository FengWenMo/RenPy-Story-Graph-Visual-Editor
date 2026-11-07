# -*- coding: utf-8 -*-
"""量 `rpy_stamp()`：只 stat 93 个 `.rpy` 要多久（决定能不能进 Symbols.__init__）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r'D:\千连万理\Renpy_Story_Graph_Editor')
from storygraph.parsing.discover import discover_scripts            # noqa: E402

ROOT = Path(r'D:\千连万理\sr_general')


def rpy_stamp(root: Path):
    files = sorted(discover_scripts(str(root)))
    rows = []
    for p in files:
        try:
            st = Path(p).stat()
        except OSError:
            continue
        rows.append((Path(p).relative_to(root).as_posix(),
                     st.st_mtime_ns, st.st_size))
    return len(rows), rows


for i in range(3):
    t0 = time.time()
    n, rows = rpy_stamp(ROOT)
    print(f'第 {i + 1} 次：{n} 个 .rpy，{(time.time() - t0) * 1000:.1f} ms')

# 对照：旧的全量 stamp（含几千张图）
t0 = time.time()
total = 0
for p in ROOT.rglob('*'):
    if p.is_file():
        total += 1
print(f'对照：全项目 rglob {total} 个文件，{(time.time() - t0) * 1000:.1f} ms')
