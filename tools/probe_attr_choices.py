"""实测：立绘"属性/表情"候选从哪来？能否按项目扩展（服装 `swim_suit` 要的就是它）。

    python tools/probe_attr_choices.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真机项目不在本机，跳过')
    raise SystemExit(0)

from storygraph.pipeline.build import load_asset_registry          # noqa: E402
from storygraph.story.beat_build import _vocabulary_for            # noqa: E402

reg = load_asset_registry(REAL)
print('角色：', len(reg.characters), '| 图片行：', len(reg.images))

print('\n=== 图片行的 Tag / Attributes（前 12 条）')
for i, (k, row) in enumerate(reg.images.items()):
    if i >= 12:
        break
    print(f"  {k:22s} Tag={row.get('Tag')!r:12s} "
          f"Attributes={row.get('Attributes')!r}")

from collections import Counter                                       # noqa: E402
tags = Counter(str(r.get('Tag') or '') for r in reg.images.values())
print('\nTag 分布：', tags.most_common(8))

try:
    vocab = _vocabulary_for(reg)
    print('\n词汇表字段：', [f for f in dir(vocab) if not f.startswith('_')])
    for f in ('expressions', 'positions', 'attrs'):
        v = getattr(vocab, f, None)
        if v:
            print(f'  {f}（{len(v)}）示例：', list(v)[:12])
except Exception as exc:                                             # noqa: BLE001
    print('\n_vocabulary_for 调不通：', type(exc).__name__, exc)

# 真机某角色的属性实际有哪些（这就是"表情"下拉的来源）
for tag in ('lyj', 'jc', 'l'):
    attrs = sorted({str(r.get('Attributes') or '')
                    for k, r in reg.images.items()
                    if str(r.get('Tag') or '') == tag and r.get('Attributes')})
    print(f'\n{tag} 的属性候选（{len(attrs)}）：', attrs[:20])
