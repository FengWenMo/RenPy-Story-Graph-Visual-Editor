"""实测：给角色加一件"服装"（`swim_suit`）之后，编辑器的候选里会不会出现它。

用户："现在是最多只有不同表情……想个办法？"

做法（这才是"服装"的正解）：
  1. 图登记进 images.xlsx，`Tag=lyj`、`Attributes=swim_suit`（或 `swim_suit_close`）；
  2. 脚本写 `show lyj swim_suit` —— 引擎会把它当这个 tag 的一个属性；
  3. 编辑器那一格的下拉候选 = **这个 tag 在资源表里的属性** → 自动出现 `swim_suit`。

    python tools/probe_costume_attr.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_registry import (                       # noqa: E402
    _TABLE_FIELDS, write_dict_to_excel)
from storygraph.pipeline.build import load_asset_registry            # noqa: E402
from storygraph.story.beat_render import make_symbols                # noqa: E402

td = Path(tempfile.mkdtemp(prefix='costume_'))
adir = td / 'game' / 'asset_tables'
adir.mkdir(parents=True)

# 三件"服装" + 两个表情，全部登记成 lyj 这个 tag 的属性
rows = {}
for attr in ('swim_suit', 'uniform', 'casual', 'happy', 'calm'):
    rows[f'lyj_{attr}'] = {'ID': f'lyj_{attr}',
                           'Path': f'game/images/lyj/{attr}.png',
                           'Tag': 'lyj', 'Attributes': attr,
                           'Category': 'character', 'Notes': ''}
write_dict_to_excel(adir / 'images.xlsx', rows, _TABLE_FIELDS['image'])

reg = load_asset_registry(td)
symbols = make_symbols(reg)
print('资源表登记了 lyj 的属性：', sorted(
    str(r.get('Attributes')) for r in reg.images.values()))
print('\n=== 编辑器的候选（`attrs_of_tag`）')
print('  attrs_of_tag("lyj") =', symbols.attrs_of_tag('lyj'))

print('\n=== 写"服装"这一格时，词能不能落到属性上（attr_for）')
for word in ('swim_suit', '泳装', 'uniform', 'happy', '开心'):
    print(f'  attr_for("lyj", {word!r}) = {symbols.attr_for("lyj", word)!r}')

print('\n=== 渲染出来的立绘行')
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result       # noqa: E402
from storygraph.story.beat_render import render_beats_to_rpy          # noqa: E402

src = ('label ch01:\n'
       '    show lyj swim_suit\n'
       '    lyj "穿泳装说话"\n'
       '    show lyj uniform happy\n'
       '    return\n')
game = td / 'game'
(game / 'ch01.rpy').write_text(src, encoding='utf-8')
pr, _ = IncrementalRenPyAnalyzer().analyze(files=[game / 'ch01.rpy'], graph=None)
out = render_beats_to_rpy(beats_from_parse_result(pr), registry=reg)
print('  产物：')
for ln in out.splitlines():
    if ln.strip() and not ln.startswith('#'):
        print('   |', ln)
print('  逐字保留立绘行：',
      '    show lyj swim_suit' in out and '    show lyj uniform happy' in out)
