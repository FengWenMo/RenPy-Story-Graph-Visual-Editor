# -*- coding: utf-8 -*-
"""**插件那条的开关，真的关得掉**（2026-11-07 第五轮收尾）

探针 `.pip-tmp/probe_plugin_switch.py`（在补这一轮的时候顺手跑的）：

```text
① 插件声明的调用 + ext.json 里记 {"id":"my_wipe","enabled":false,"override":true}
   → 注册表里的调用 = ['my_wipe']                     ← 关不掉
   → 扩展点树上看得到吗 = [('my_wipe', True)]          ← 窗口里也显示"在用"
② rules.json 声明的那条：注册表里的调用 = []            ← 这条一直是对的
③ 项目自己 ext.json 声明的那条：[]                     ← 这条也一直是对的
```

根因是**装载顺序**：`内置 → 旧清单 → ext.json → 插件`，插件在最后
`upsert(replace=True)`，把 ext.json 里那条"只带开关"的记录**整个换掉**。
于是"插件那条的『是否使用』"怎么写都没用 —— 和第一轮那三处断链同一类：
开关看着能关，关不掉。

修法（`ext/registry.py::_apply_switches`）：开关记录**留到插件之后再贴一遍**，
只盖 `enabled` / `batchable` / `batch` 三样，**形状仍归插件**。
窗口那边把插件点归进"只能改开关"那一类（`_switch_only`）：字段表锁上、
保存只写 `override` 记录（形状不抄），删除按钮说清"删了它也会回来"。
"""
from __future__ import annotations

import json
from pathlib import Path

from storygraph.ext import registry as ER
from storygraph.ext import schema as ES
from storygraph.story import statement_registry as SR

CALL = {'id': 'my_wipe', 'kind': 'call', 'label': '擦除过场',
        'fields': [{'key': '方向', 'position': 0}]}

STATEMENT = {'id': 'mycue', 'kind': 'statement', 'label': '闪电动画',
             'fields': [{'key': '文字'}]}

BATCH = {'when': {'show': 'sprite'},
         'insert': {'text': '$ my_wipe("left", 0.1)',
                    'placement': 'after_last_show'}}


def _project(tmp_path: Path, plugin=None, ext=None, script='label start:\n'
             '    return\n') -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(script, encoding='utf-8')
    if plugin:
        (root / 'extensions').mkdir()
        (root / 'extensions' / '动画.json').write_text(
            json.dumps({'extensions': list(plugin)}, ensure_ascii=False),
            encoding='utf-8')
    if ext is not None:
        (root / ER.MANIFEST).write_text(json.dumps(
            {'schema': ES.EXT_SCHEMA, 'extensions': list(ext)},
            ensure_ascii=False), encoding='utf-8')
    return root


def _switch(ident: str, kind: str, *, on: bool, batchable: bool = False,
            batch: dict = None) -> dict:
    rec = {'id': ident, 'kind': kind, 'enabled': bool(on), 'override': True}
    if batchable:
        rec['batchable'] = True
        rec['batch'] = dict(batch or BATCH)
    return rec


def _reg(root) -> SR.StatementRegistry:
    SR.clear_cache()
    return SR.StatementRegistry.for_project(root)


# ---------------------------------------------------------------------------
#  开关关得掉（探针 ① 的回归）
# ---------------------------------------------------------------------------

def test_a_plugin_declared_call_can_be_switched_off(tmp_path):
    root = _project(tmp_path, plugin=[CALL],
                    ext=[_switch('my_wipe', 'call', on=False)])
    assert [c['head'] for c in _reg(root).calls] == []


def test_a_plugin_declared_statement_can_be_switched_off(tmp_path):
    root = _project(tmp_path, plugin=[STATEMENT],
                    ext=[_switch('mycue', 'statement', on=False)])
    assert _reg(root).match('mycue') is None


def test_switching_it_back_on_brings_the_plugin_point_back(tmp_path):
    root = _project(tmp_path, plugin=[CALL],
                    ext=[_switch('my_wipe', 'call', on=True)])
    assert [c['head'] for c in _reg(root).calls] == ['my_wipe']


def test_the_plugin_point_still_shows_its_own_shape(tmp_path):
    """开关只盖三样：字段 / 标签仍是插件的（不是被空记录顶掉）。"""
    root = _project(tmp_path, plugin=[CALL],
                    ext=[_switch('my_wipe', 'call', on=False)])
    point = ER.ExtensionRegistry.for_project(root).get('my_wipe')
    assert [f.key for f in point.field_specs()] == ['方向']
    assert point.label == '擦除过场'
    assert point.source.startswith('plugin:')
    assert point.enabled is False


def test_the_batch_switch_also_applies_to_a_plugin_point(tmp_path):
    root = _project(tmp_path, plugin=[CALL],
                    ext=[_switch('my_wipe', 'call', on=True, batchable=True)])
    reg = ER.ExtensionRegistry.for_project(root)
    assert [r['id'] for r in reg.cue_rules()] == ['my_wipe']


def test_batch_off_for_a_plugin_point_means_no_rule(tmp_path):
    root = _project(tmp_path, plugin=[CALL],
                    ext=[_switch('my_wipe', 'call', on=True)])
    assert ER.ExtensionRegistry.for_project(root).cue_rules() == []


# ---------------------------------------------------------------------------
#  窗口：插件点只能改开关
# ---------------------------------------------------------------------------

def _dialog(qapp, root):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    return ExtensionDialog(None, project_root=root)


def test_the_window_locks_a_plugin_shape_but_shows_it(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, plugin=[CALL])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    assert not dlg.tbl.isEnabled()
    assert not dlg.cb_kind.isEnabled()
    assert not (dlg.tbl.item(0, DE._F_POS).flags() & Qt.ItemIsEditable)
    assert '插件' in dlg.source_note.text()
    assert '只能停用' in dlg.source_note.text()
    dlg.close()


def test_saving_from_the_window_writes_only_the_switch(qapp, tmp_path):
    root = _project(tmp_path, plugin=[CALL])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    dlg.ck_enabled.setChecked(False)
    dlg._save()
    dlg.close()
    saved = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    item = saved['extensions'][0]
    assert item['id'] == 'my_wipe'
    assert item['override'] is True
    assert item['enabled'] is False
    assert 'fields' not in item
    assert [c['head'] for c in _reg(root).calls] == []


def test_deleting_a_plugin_point_is_refused(qapp, tmp_path, monkeypatch):
    from storygraph.ui_qt import dialog_extensions as DE

    said = []
    monkeypatch.setattr(DE.QMessageBox, 'information',
                        staticmethod(lambda *a, **k: said.append(a[2])))
    root = _project(tmp_path, plugin=[CALL])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    dlg._delete_point()
    dlg.close()
    assert said and '插件' in said[0] and '装回来' in said[0]
    assert (root / ER.MANIFEST).is_file() is False
    assert [c['head'] for c in _reg(root).calls] == ['my_wipe']


# ---------------------------------------------------------------------------
#  老行为别退：旧清单的投影仍借用字段；项目自己的点仍能改
# ---------------------------------------------------------------------------

def test_a_rules_projection_still_borrows_fields_for_display(tmp_path):
    root = _project(tmp_path, ext=[_switch('my_wipe', 'call', on=False)])
    (root / 'storygraph.rules.json').write_text(json.dumps({'calls': [{
        'head': 'my_wipe', 'columns': [{'name': '方向', 'position': 0}]}]},
        ensure_ascii=False), encoding='utf-8')
    point = ER.ExtensionRegistry.for_project(root).get('my_wipe')
    assert [f.key for f in point.field_specs()] == ['方向']
    assert point.enabled is False
    assert [c['head'] for c in _reg(root).calls] == []


def test_a_project_point_is_still_editable(qapp, tmp_path):
    root = _project(tmp_path, ext=[{
        'id': 'my_wipe', 'kind': 'call',
        'fields': [{'key': '方向', 'position': 0}]}])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('my_wipe')
    assert dlg.tbl.isEnabled()
    assert dlg.cb_kind.isEnabled()
    assert '改完保存即生效' in dlg.source_note.text()
    dlg.close()
