"""截图：新的「🛠 扩展」窗口（四种扩展点 + 动态字段 + 两个开关 + 批处理预演）。

    python tools/_probe_ext_dialog_shot.py

临时项目，不碰真工程。产物 `docs/shots/ext_*.png`。

2026-11-07 第二轮加了三张：
  · `ext_statement_2026-11-07.png` —— 只在 `storygraph.ext.json` 里声明的写法
    （**没有** rules.json 那一条）：类型下拉是中文、`with` 是灰字固定字、
    预览里代码格就是代码框；
  · `ext_batch_2026-11-07.png` —— 批处理开关**真的跑了一遍引擎**（预演）；
  · `ext_problems_2026-11-07.png` —— 读不进来的条目在树上有个「问题」节点
    （以前只在状态栏一行小字，用户看到的是"我加的写法不见了"）。
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

RULES = {'statements': [
    {'name': 'mycue', 'label': '预约镜头',
     'docs': '把镜头推到指定位置并过一次场',
     'pattern': [{'kind': 'keyword', 'text': 'mycue', 'column': '动作名'},
                 {'kind': 'variable', 'column': '文字'}],
     'clauses': [{'keyword': 'at', 'name': '位置', 'kind': 'words'},
                 {'keyword': 'with', 'name': '转场', 'kind': 'transition'}]},
], 'calls': [
    {'head': 'my_wipe', 'label': '擦除过场',
     'columns': [{'name': '方向', 'label': '方向', 'position': 0},
                 {'name': '秒数', 'label': '秒数', 'position': 1}]},
]}

#: **只在扩展清单里**声明的一条写法：解析器也要认它（`ext.bridge` 那条链）。
FLASH = {'id': 'flash', 'kind': 'statement', 'label': '闪电动画（扩展里新建的）',
         'docs': '文字 + 一段代码 + 固定字 with + 转场表达式',
         'enabled': True,
         'fields': [
             {'key': '文字', 'kind': 'variable'},
             {'key': '片段', 'kind': 'code'},
             {'key': 'with', 'kind': 'keyword'},
             {'key': '转场', 'kind': 'transition'},
         ]}

EXT = {'schema': 'storygraph.ext/1', 'extensions': [
    FLASH,
    {'id': 'master-dissolve', 'kind': 'batch', 'label': '主层过场（每处立绘后补一行）',
     'docs': '整章批处理：每次立绘变化后补 $ renpy.transition(...)',
     'enabled': True, 'batchable': True,
     'fields': [{'key': '时长', 'label': '时长（秒）', 'kind': 'number',
                 'default': '0.1'}],
     'batch': {'when': {'show': 'sprite'},
               'insert': {'text': '$ renpy.transition(Dissolve(0.1), '
                                  'layer="master")',
                          'placement': 'after_last_show',
                          'skip_if_present': True}}},
]}

#: 批处理预演要有真章节可扫（否则"0 个文件"看起来像功能没做完）
SCRIPT = '''\
label start:
    show lan happy
    "第一句"
    flash 闪电 Dissolve(0.1, 0.2) with Fade(0.2, 0, 0.2)
    "第二句"
'''


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    td = Path(tempfile.mkdtemp(prefix='shot-ext-'))
    root = td / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(SCRIPT, encoding='utf-8')
    (root / 'storygraph.rules.json').write_text(
        json.dumps(RULES, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'storygraph.ext.json').write_text(
        json.dumps(EXT, ensure_ascii=False, indent=2), encoding='utf-8')
    plug = root / 'extensions'
    plug.mkdir()
    (plug / '项目擦除.json').write_text(json.dumps({
        'extensions': [{'id': 'film_transition', 'kind': 'call',
                        'label': '胶片过场（插件加的）',
                        'fields': [{'key': '效果', 'kind': 'variable'}]}]},
        ensure_ascii=False, indent=2), encoding='utf-8')
    # 故意坏一条：它必须在树上看得见，而不是"悄悄不见"
    (plug / '坏插件.json').write_text(json.dumps({
        'extensions': [{'id': 'broken', 'kind': 'statement',
                        'fields': [{'key': 'a', 'kind': '说不上来的类型'}]}]},
        ensure_ascii=False, indent=2), encoding='utf-8')

    OUT.mkdir(parents=True, exist_ok=True)
    dlg = ExtensionDialog(None, project_root=str(root))
    dlg.resize(1120, 780)
    dlg.show()
    app.processEvents()

    def pick(ident):
        from PySide6.QtCore import Qt as _Qt
        tree = dlg.tree
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                if str(child.data(0, _Qt.UserRole) or '') == ident:
                    tree.setCurrentItem(child)
                    app.processEvents()
                    app.processEvents()
                    return True
        return False

    def shot(name):
        path = OUT / name
        dlg.grab().save(str(path))
        print('saved', path)

    if pick('flash'):
        dlg.form.set_values({'文字': '闪电', '片段': 'Dissolve(0.1, 0.2)',
                             '转场': 'Fade(0.2, 0, 0.2)'})
        app.processEvents()
        shot('ext_statement_2026-11-07.png')

    if pick('master-dissolve'):
        plan = dlg._preview_batch()
        print('plan =', None if plan is None else
              (len(plan.files), plan.pending, plan.errors))
        app.processEvents()
        shot('ext_batch_2026-11-07.png')

    if pick('renpy.transition'):
        shot('ext_call_2026-11-07.png')

    # 问题节点（选不中，只是让树滚到它）
    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(
        dlg.tree.topLevelItemCount() - 1))
    app.processEvents()
    shot('ext_problems_2026-11-07.png')

    print('problems =', dlg._reg.problems)
    dlg.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
