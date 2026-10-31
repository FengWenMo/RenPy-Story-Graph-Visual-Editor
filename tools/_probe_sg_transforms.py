"""临时探针：sg_transforms 的护栏（手改 / 追加 / 撞名 / 引用缺失）。跑完可删。"""
from __future__ import annotations

import pathlib
import tempfile

from storygraph.story import sg_transforms as SG
from storygraph.story.atl import scan_transforms

root = pathlib.Path(tempfile.mkdtemp()) / 'proj'
(root / 'game').mkdir(parents=True)

p1 = SG.plan_for_project(root, 'cover')
print('1', p1.action, p1.name)
p1.apply()
p2 = SG.plan_for_project(root, 'contain')
print('2', p2.action, p2.name)
print(p2.diff_text())
p2.apply()
f = root / 'game' / 'sg_transforms.rpy'
print(repr(f.read_text(encoding='utf-8')))

# 手改：加一行属性
f.write_text(f.read_text(encoding='utf-8').replace(
    'fit "cover"', 'fit "cover"\n    alpha 0.9'), encoding='utf-8')
sg = SG.inspect(root)
print('after hand edit:', sg.state, '|', sg.reason)
p3 = SG.plan_for_project(root, 'half')
print('3', p3.action, '|', p3.reason)
try:
    p3.apply()
except Exception as e:                       # noqa: BLE001 - 探针
    print('apply refused:', type(e).__name__, e)
f.unlink()
print('4', SG.plan_for_project(root, 'half').action)

# 名字被占：人自己写过同名但内容不同
(root / 'game' / 'mine.rpy').write_text('transform sg_fit_cover:\n    zoom 3\n',
                                        encoding='utf-8')
idx = scan_transforms(root)
p5 = SG.plan_for_project(root, 'cover', index=idx)
print('5', p5.action, p5.name, '| note:', p5.note)

# 引用缺失（台词里那句 "meet me at sg_home" 不算）
(root / 'game' / 'a.rpy').write_text(
    'label a:\n'
    '    scene bg room at sg_fit_cover\n'
    '    show ling happy at sg_zoom_075, sg_nope\n'
    '    add "x.png" at sg_gone behind foo\n'
    '    e "meet me at sg_home"\n',
    encoding='utf-8')
idx2 = scan_transforms(root)
print('refs', [(r['name'], r['line']) for r in SG.references(root)])
print('missing', [(m['name'], m['file'], m['line'])
                  for m in SG.missing_references(root, index=idx2)])
