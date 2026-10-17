"""搭建一个可复现的审计项目，并打印卡片编辑器实际拿到的数据。

只读仓库源码；临时项目写在系统临时目录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from PySide6.QtWidgets import QApplication                          # noqa: E402

from _ui_project import build_project, PNG_1PX                      # noqa: E402

OUT = Path(__file__).resolve().parent
PROJ = OUT / 'proj_card_audit'
import shutil
if PROJ.exists():
    shutil.rmtree(PROJ)

root = build_project(PROJ)
game = root / 'game'

# 1) 角色 + layeredimage：变量 lan / 显示名 岚 / show tag lan_tag
(game / 'defs.rpy').write_text(
    'define lan = Character("岚", image="lan_tag")\n'
    '\n'
    'layeredimage lan_tag:\n'
    "    image_format 'images/lan_tag/{image}.png'\n"
    '    group outfit:\n'
    '        attribute swim_suit default:\n'
    "            'swim_suit'\n"
    '        attribute uniform:\n'
    "            'uniform'\n"
    '    group pose:\n'
    '        attribute stand default:\n'
    "            'stand'\n"
    '        attribute wave:\n'
    "            'wave'\n",
    encoding='utf-8')

# 2) 两条 show：一条用 show tag（对），一条用变量名 lan（作者以为的写法）
(game / 'script' / 'ch01.rpy').write_text(
    'label start:\n'
    '    show lan_tag swim_suit stand\n'
    '    lan "第一句：用 show tag 写的"\n'
    '    show lan swim_suit stand\n'
    '    lan "第二句：用变量名 lan 写的"\n'
    '    return\n',
    encoding='utf-8')

# 3) 立绘文件 + 资源表行（Tag=lan_tag，Attributes 是**服装与动作混在一起**的旧列）
attrs = ['swim_suit', 'uniform', 'stand', 'wave']
rows = []
for a in attrs:
    rel = f'images/lan_tag/{a}.png'
    p = game / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(PNG_1PX)
    rows.append({'ID': f'lan_tag_{a}', 'Path': f'game/{rel}',
                 'Tag': 'lan_tag', 'Attributes': a,
                 'Category': 'character', 'Notes': ''})
from storygraph.assets.asset_registry import (                       # noqa: E402
    _TABLE_FIELDS, append_rows_to_table, write_dict_to_excel)
append_rows_to_table(root / 'asset_tables', 'images', rows)
write_dict_to_excel(root / 'asset_tables' / 'characters.xlsx',
                    {'lan': {'ID': 'lan', 'Name': '岚', 'Color': '',
                             'ImageTag': 'lan_tag', 'Aliases': '',
                             'Notes': ''}},
                    _TABLE_FIELDS['character'])

# 4) rules.json：只定义 attr_groups（没有 statements）
(root / 'storygraph.rules.json').write_text('''{
  "attr_groups": [
    {"name": "outfit", "label": "服装", "options": ["swim_suit", "uniform"]},
    {"name": "pose", "label": "动作", "options": ["stand", "wave"]}
  ]
}
''', encoding='utf-8')

print('项目：', root)
from storygraph.pipeline.build import load_asset_registry             # noqa: E402
reg = load_asset_registry(root)
print('角色表：', {k: (v.get('Name'), v.get('ImageTag'))
                  for k, v in (reg.characters or {}).items()})
print('图片表 Tag=lan_tag 的 Attributes：',
      sorted({r.get('Attributes') for r in (reg.images or {}).values()
              if r.get('Tag') == 'lan_tag'}))
from storygraph.story import statement_registry as SR                 # noqa: E402
print('rules.json attr_groups：',
      [(g.name, g.label, g.options)
       for g in SR.StatementRegistry.for_project(root).attr_groups])

# 5) 打开主窗口，打印拍 / 卡片
class _Engine:
    def __init__(self, r):
        self.project_root = r
        self._registry = None
        try:
            self._registry = load_asset_registry(r)
        except Exception:
            pass
        if self._registry is None:
            return
        try:
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            from storygraph.story.beat_build import apply_script_definitions
            pr, _g = IncrementalRenPyAnalyzer().analyze(
                files=discover_scripts(r), graph=None)
            apply_script_definitions(self._registry, pr)
        except Exception:
            pass

    def get_registry(self):
        return self._registry

app = QApplication.instance() or QApplication([])
from storygraph.ui_qt.main_window import MainWindowQt                # noqa: E402
win = MainWindowQt(_Engine(root), game / 'script' / 'ch01.rpy')
win.resize(1500, 900)
win.show()
app.processEvents()
win._set_page(win.PAGE_CARD)
app.processEvents()
print('\n拍：')
for i, b in enumerate(win._session.beats or []):
    print(i, 'kind=', getattr(b, 'kind', ''), 'speaker=', repr(getattr(b, 'speaker', '')),
          'show=', repr(getattr(b, 'show', '')), 'text=', repr(getattr(b, 'text', '')))
print('\n列表行数：', win._list.count())
for i in range(win._list.count()):
    print(' ', i, repr(win._list.item(i).text()))
print('\n卡片（当前拍）：')
for c in win._panel._grid._cards:
    print(' item=', c.item)
    print('   slot_btns=', [b.text() for b in c._slot_btns],
          ' text=', c._expr_btn.text() if getattr(c, '_expr_btn', None) else None,
          ' provider=', c._choice_groups_for is not None)
win.close()
