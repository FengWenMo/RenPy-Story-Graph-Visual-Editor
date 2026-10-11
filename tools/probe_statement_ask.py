"""实验：真机上 `ask_engine` 对几种"待认识写法"给出的结论（校准判据用）。

    python tools/probe_statement_ask.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.pipeline import statement_probe as SP                    # noqa: E402

CASES = [
    ('sg_definitely_unknown_xyz', None, '一定不认识（对照组）'),
    ('pause', None, 'Ren\'Py 内置语句（应当认识、不带块）'),
    ('weather', None, '语法上不存在的头'),
]

for head, lines, note in CASES:
    print(f'\n=== {head}（{note}）')
    v = SP.ask_engine(REAL, head, lines=lines, timeout=900)
    print(f'    ok={v["ok"]} recognized={v["recognized"]} '
          f'blocks={v["blocks"]} params={v["params"]}（引擎退出码 {v["exit"]}）')
    print(f'    结论：{v.get("summary") or v.get("reason")}')
    for what, rec in (v.get('by_what') or {}).items():
        flag = '接受' if rec['accepted'] else '拒绝'
        print(f'      {what:8s} {flag}  {rec["lines"][0][:50]!r}'
              + (f'  错误：{rec["error"][:80]}' if rec['error'] else ''))
