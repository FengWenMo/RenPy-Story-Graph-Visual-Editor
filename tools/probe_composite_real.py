"""真机上"合成立绘展开"这条路的实机影响（只读）。

问题：`_expand_composite_call` 修好之后，真机有没有**行为变化**？
（有 = 修的是真问题；没有 = 只是未来风险的护栏。）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.layered_model import (load_layer_model,   # noqa: E402
                                           expand_displayable)

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

m = load_layer_model(REAL)
print(f'helpers   : {len(m.helpers)} 个')
print(f'模型条目  : {len(getattr(m, "items", {}) or {})}')
if not m.helpers and not getattr(m, 'items', None):
    print('（真机没有 layered_model 声明，这条路在真机上不生效）')
    raise SystemExit(0)

hits = 0
for key, item in (getattr(m, 'items', {}) or {}).items():
    for attr, expr in (getattr(item, 'attrs', {}) or {}).items():
        s = str(expr)
        if 'Composite' in s:
            hits += 1
            print(f'\n{key}.{attr} = {s[:120]}')
            out = expand_displayable(s, m.helpers, '')
            for o in out:
                print(f'    {o}')
print(f'\n带 Composite 的属性：{hits} 处')
