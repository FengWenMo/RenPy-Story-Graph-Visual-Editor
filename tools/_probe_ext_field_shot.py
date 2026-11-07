"""截图：**只在项目扩展点里**改卡片一格的样子（端到端，不改一行 Python）。

    python tools/_probe_ext_field_shot.py

三张图，回答同一个问题的三个面：

  1. `docs/shots/card_fields_from_ext_2026-11-07.png`
     卡片右栏 —— `Stage` 那一格变成代码框、标签变成「舞台动作」，
     `Kind` 变成下拉（候选来自扩展点声明）。这些**全部**写在
     `<项目>/storygraph.ext.json` 里，`card_editor.py` 一行没动。

  2. `docs/shots/ext_kind_2026-11-07.png`
     扩展窗口 —— 左树里那条是"拍字段（卡片上的一格）"，右边「种类」下拉
     可以选四类（这个下拉本身也是本轮补的：之前新建永远是"写法"）。

  3. 控制台会打印"值仍然写回同一个字段"的证据（`collect_fields()`）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication                       # noqa: E402

OUT = ROOT / 'docs' / 'shots'

#: 项目扩展点：两条 `kind: "field"` —— 改的是卡片上已有的格（不是加新列）
EXT = {'schema': 'storygraph.ext/1', 'extensions': [
    {'id': '舞台动作', 'kind': 'field', 'label': '舞台动作',
     'docs': '把卡片上「Stage」那一格换成代码框（值仍写回 Beat.stage）',
     'enabled': True,
     'fields': [{'key': 'Stage', 'label': '舞台动作', 'kind': 'code',
                 'help': '一行一条舞台指令，左边是源文件里的真实行号'}]},
    {'id': '类型下拉', 'kind': 'field', 'label': '类型',
     'docs': '把「Kind」换成下拉（候选是本项目真在用的那几种）',
     'enabled': True,
     'fields': [{'key': 'Kind', 'label': '拍类型', 'kind': 'choice',
                 'choices': ['say', 'narration', 'show', 'scene', 'menu'],
                 'help': '这一拍的类型（下拉只是提示，手打新值也能过）'}]},
]}

SCRIPT = '''\
label ch:
    "开场"
    show eileen at left
    e "这一格是舞台动作"
    "收尾"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.main_window import MainWindowQt

    td = Path(tempfile.mkdtemp(prefix='shot-extfield-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'storygraph.ext.json').write_text(
        json.dumps(EXT, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'game' / 'ch.rpy').write_text(SCRIPT, encoding='utf-8')

    class _Engine:
        project_root = str(root)

        def get_registry(self):
            return None

    win = MainWindowQt(_Engine(), (root / 'game' / 'ch.rpy'))
    win.resize(1500, 980)
    win.show()
    app.processEvents()
    win._set_page(win.PAGE_CARD)
    app.processEvents()

    target = None
    for idx, beat in enumerate(win.beats):
        if str(getattr(beat, 'show', '') or '').strip():
            target = idx
            break
    if target is None:                       # 没有 show 拍就挑第一张有台词的
        for idx, beat in enumerate(win.beats):
            if str(getattr(beat, 'text', '') or '').strip():
                target = idx
                break
    print('target =', target, 'beats =', len(win.beats))
    if target is None:
        return 1
    win._show_index(target)
    app.processEvents()
    app.processEvents()

    panel = win._panel
    print('adv open   =', panel.is_advanced_open())
    print('Stage 控件 =', type(panel._widgets['Stage']).__name__)
    print('Kind  控件 =', type(panel._widgets['Kind']).__name__)
    print('Kind  候选 =', panel._widgets['Kind'].count())
    fields = panel.collect_fields()
    print('写回的值   =', {k: fields.get(k) for k in ('Stage', 'Kind')})

    OUT.mkdir(parents=True, exist_ok=True)
    # 卡片栏是滚动区：高级区在**下面**，不滚下去截不到（截图要能看见那两格）
    from PySide6.QtWidgets import QScrollArea
    for area in win.findChildren(QScrollArea):
        bar = area.verticalScrollBar()
        bar.setValue(bar.maximum())
    app.processEvents()
    app.processEvents()
    png = OUT / 'card_fields_from_ext_2026-11-07.png'
    win.grab().save(str(png))
    print('saved', png)

    # —— 扩展窗口：那条"拍字段"，以及「种类」下拉 ——
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog
    dlg = ExtensionDialog(None, project_root=root)
    dlg.resize(1120, 760)
    dlg.show()
    app.processEvents()
    # 选中左边树上那条「舞台动作」
    for i in range(dlg.tree.topLevelItemCount()):
        top = dlg.tree.topLevelItem(i)
        for j in range(top.childCount()):
            child = top.child(j)
            if child.text(0) == '舞台动作':
                dlg.tree.setCurrentItem(child)
                break
    app.processEvents()
    app.processEvents()
    png2 = OUT / 'ext_kind_2026-11-07.png'
    dlg.grab().save(str(png2))
    print('saved', png2)
    print('kind 下拉   =', [dlg.cb_kind.itemData(i)
                            for i in range(dlg.cb_kind.count())])
    print('kind 说明   =', dlg.kind_note.text())
    dlg.close()
    win.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
