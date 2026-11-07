# -*- coding: utf-8 -*-
"""**存盘前先对表**：两个人（两扇窗）改同一份 `storygraph.ext.json`

探针 `.pip-tmp/probe_ext_conflict.py`（2026-11-07 第六轮，修之前）：

```text
① 甲窗改 alpha 的名字 → 存；乙窗（更早打开的）改 beta 的开关 → 存
   实到：alpha.label 变回 '甲'                    ← 甲的改动没了（ok: True）
② 甲窗新加 gamma → 存；乙窗跟着存一次
   实到：gamma 整条消失（count: 2）                ← 文件里也没有了
③ 文件里有别人加的顶层键（手写注释 / 另一个工具写的）
   实到：顶层键被悄悄删掉
④ 甲窗删掉 alpha → 存；乙窗只改 beta → 存
   实到：alpha 又回来了
```

根因：`save()` 是"拿我内存里的整份稿子覆盖文件"，从不看文件变没变 ——
两次都 `ok: True`，界面上什么都看不出来。

修法（`ext/registry.py::ExtensionRegistry.save`）：三方合并

    base   = 装载那一刻文件里的原话（`_baseline`）
    mine   = 我现在想写的（`records()`）
    theirs = 此刻磁盘上的

按 id 逐条判：**只改我改过的那几条**；同一条两边都改 → 默认一个字都不写，
把 id 列回去（`conflicts`），窗口当面问。`_parse_ext_records` 负责"别人的
顶层键 / 认不出的条目原样还回去"。

同一族的另外两个问题（也在这里钉住）：

· `payload()` 只收 `project` 来源的点 —— 插件声明了同名那条之后存一次盘，
  **项目自己那份声明被删掉**（`.pip-tmp/probe_ext_shadow.py`：`count: 0`，
  整份文件清空）；
· 同名被盖住这件事**不出声**（现在进 `problems`，左树上看得见）。
"""
from __future__ import annotations

import json
from pathlib import Path

from storygraph.ext import registry as ER
from storygraph.ext import schema as ES

ALPHA = {'id': 'alpha', 'kind': 'statement', 'label': '甲',
         'fields': [{'key': '方向', 'kind': 'keyword'}]}

BETA = {'id': 'beta', 'kind': 'statement', 'label': '乙',
        'fields': [{'key': '秒数', 'kind': 'number'}]}

WIPE = {'id': 'my_wipe', 'kind': 'call', 'label': '我写的擦除',
        'effect': 'Dissolve', 'seconds_arg': 0,
        'fields': [{'key': '方向', 'kind': 'keyword'},
                   {'key': '秒数', 'kind': 'number'}]}

PLUGIN_WIPE = {'id': 'my_wipe', 'kind': 'call', 'label': '插件版擦除',
               'fields': [{'key': '名字', 'kind': 'variable'}]}


def _project(tmp_path: Path, ext=None, plugin=None, extras=None) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    (root / 'game' / 'script.rpy').write_text(
        'label start:\n    return\n', encoding='utf-8')
    if plugin:
        (root / 'extensions').mkdir()
        (root / 'extensions' / '动画.json').write_text(
            json.dumps({'extensions': list(plugin)}, ensure_ascii=False),
            encoding='utf-8')
    if ext is not None:
        data = {'schema': ES.EXT_SCHEMA, 'extensions': list(ext)}
        data.update(extras or {})
        (root / ER.MANIFEST).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return root


def _rows(root) -> dict:
    """文件里现在有哪几条（`id → 记录`）。"""
    path = Path(root) / ER.MANIFEST
    data = json.loads(path.read_text(encoding='utf-8'))
    return {str(e.get('id')): e for e in data.get('extensions') or ()}


def _ids(root) -> list:
    path = Path(root) / ER.MANIFEST
    data = json.loads(path.read_text(encoding='utf-8'))
    return [str(e.get('id')) for e in data.get('extensions') or ()]


def _windows(root) -> tuple:
    """两扇窗：各自在**不同时刻**读同一份文件（这就是问题所在）。"""
    return (ER.ExtensionRegistry.for_project(root),
            ER.ExtensionRegistry.for_project(root))


# ---------------------------------------------------------------------------
#  一、后存的不会再盖掉先存的（探针 ① ② ④ 的回归）
# ---------------------------------------------------------------------------

def test_a_stale_window_no_longer_clobbers_the_other(tmp_path):
    root = _project(tmp_path, ext=[ALPHA, BETA])
    first, second = _windows(root)
    first.get('alpha').label = '甲（甲窗改过）'
    assert first.save(root)['ok']
    second.get('beta').enabled = False
    res = second.save(root)
    assert res['ok'], res
    rows = _rows(root)
    assert rows['alpha']['label'] == '甲（甲窗改过）'   # ← 以前这里变回 '甲'
    assert rows['beta']['enabled'] is False
    # 明说是"收进来的"，不是"我本来就有"
    assert 'alpha' in res['absorbed']


def test_a_point_added_elsewhere_is_not_lost(tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    first, second = _windows(root)
    first.upsert(ES.parse_extension({'id': 'gamma', 'kind': 'call',
                                     'label': '丙'}))
    assert first.save(root)['ok']
    second.get('alpha').enabled = False
    res = second.save(root)
    assert 'gamma' in _ids(root)          # ← 以前整条消失
    assert 'gamma' in res['absorbed']
    assert _rows(root)['gamma']['label'] == '丙'


def test_deleting_one_point_does_not_resurrect_it(tmp_path):
    root = _project(tmp_path, ext=[ALPHA, BETA])
    first, second = _windows(root)
    first.remove('alpha')
    assert first.save(root)['ok']
    second.get('beta').label = '乙（乙窗改过）'
    assert second.save(root)['ok']
    assert _ids(root) == ['beta']         # ← 以前 alpha 会回来
    assert _rows(root)['beta']['label'] == '乙（乙窗改过）'


def test_two_windows_can_edit_different_points_at_the_same_time(tmp_path):
    """同一条不撞的时候，两边各自保存都能落下（不是"先存的人赢"）。"""
    root = _project(tmp_path, ext=[ALPHA, BETA])
    first, second = _windows(root)
    first.get('alpha').label = '甲-A'
    second.get('beta').label = '乙-B'
    assert first.save(root)['ok']
    assert second.save(root)['ok']
    rows = _rows(root)
    assert (rows['alpha']['label'], rows['beta']['label']) == ('甲-A', '乙-B')


# ---------------------------------------------------------------------------
#  二、文件里别人的东西不许顺手删（探针 ③）
# ---------------------------------------------------------------------------

def test_top_level_keys_written_by_someone_else_are_kept(tmp_path):
    root = _project(tmp_path, ext=[ALPHA],
                    extras={'$comment': '手写的注释', 'owner': 'story-team'})
    reg = ER.ExtensionRegistry.for_project(root)
    reg.get('alpha').label = '甲（改过）'
    assert reg.save(root)['ok']
    data = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    assert data['$comment'] == '手写的注释'
    assert data['owner'] == 'story-team'
    assert data['schema'] == ES.EXT_SCHEMA
    assert data['extensions'][0]['label'] == '甲（改过）'


def test_a_record_it_cannot_read_is_written_back_verbatim(tmp_path):
    """认得出 id、读不懂形状的那条：**原样留着**，不替人删。"""
    odd = {'id': 'weird', 'kind': 'nope', 'who_knows': [1, 2, 3]}
    root = _project(tmp_path, ext=[ALPHA, odd])
    reg = ER.ExtensionRegistry.for_project(root)
    reg.get('alpha').label = '甲（改过）'
    assert reg.save(root)['ok']
    rows = json.loads((root / ER.MANIFEST).read_text(encoding='utf-8'))
    kept = [e for e in rows['extensions'] if e.get('id') == 'weird']
    assert kept and kept[0] == odd


def test_saving_twice_from_the_same_registry_is_stable(tmp_path):
    """连存两次：第二次不该把自己刚写的当成"别人改的"。"""
    root = _project(tmp_path, ext=[ALPHA, BETA])
    reg = ER.ExtensionRegistry.for_project(root)
    reg.get('alpha').label = '甲（改过）'
    assert reg.save(root)['ok']
    again = reg.save(root)
    assert again['ok'] and not again['conflicts'] and not again['absorbed']
    assert _ids(root) == ['alpha', 'beta']
    assert reg.payload()['extensions'] == list(_rows(root).values())


def test_broken_json_still_refuses_to_write(tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    (root / ER.MANIFEST).write_text('{ 不是 json', encoding='utf-8')
    reg = ER.ExtensionRegistry.for_project(root)
    res = reg.save(root)
    assert res['ok'] is False
    assert (root / ER.MANIFEST).read_text(encoding='utf-8') == '{ 不是 json'


# ---------------------------------------------------------------------------
#  三、同一条两边都改：拦下来，说清是哪几条（探针 ⑤）
# ---------------------------------------------------------------------------

def test_both_sides_changing_one_point_is_refused_not_silently_overwritten(
        tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    first, second = _windows(root)
    first.get('alpha').label = '甲-A'
    assert first.save(root)['ok']
    second.get('alpha').label = '甲-B'
    res = second.save(root)
    assert res['ok'] is False and res['conflict'] is True
    assert res['conflicts'] == ['alpha']
    assert _rows(root)['alpha']['label'] == '甲-A'      # 文件一个字没动


def test_on_conflict_mine_wins(tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    first, second = _windows(root)
    first.get('alpha').label = '甲-A'
    assert first.save(root)['ok']
    second.get('alpha').label = '甲-B'
    assert second.save(root)['ok'] is False
    res = second.save(root, on_conflict='mine')
    assert res['ok'], res
    assert _rows(root)['alpha']['label'] == '甲-B'


def test_on_conflict_theirs_keeps_the_file(tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    first, second = _windows(root)
    first.get('alpha').label = '甲-A'
    assert first.save(root)['ok']
    second.get('alpha').label = '甲-B'
    res = second.save(root, on_conflict='theirs')
    assert res['ok'], res
    assert _rows(root)['alpha']['label'] == '甲-A'


def test_a_delete_and_an_edit_on_the_same_point_is_a_conflict(tmp_path):
    root = _project(tmp_path, ext=[ALPHA, BETA])
    first, second = _windows(root)
    first.remove('alpha')
    assert first.save(root)['ok']
    second.get('alpha').label = '甲-B'          # 它不知道 alpha 已经被删了
    res = second.save(root)
    assert res['conflicts'] == ['alpha']
    assert 'alpha' not in _ids(root)            # 没写 = 保持"已删除"


def test_an_unknown_argument_is_refused_instead_of_guessed(tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    reg = ER.ExtensionRegistry.for_project(root)
    res = reg.save(root, on_conflict='什么')
    assert res['ok'] is False and 'abort' in res['reason']


# ---------------------------------------------------------------------------
#  四、同名两条声明：形状归插件，项目那条仍在文件里（探针 probe_ext_shadow）
# ---------------------------------------------------------------------------

def test_a_shadowed_declaration_survives_a_save(tmp_path):
    root = _project(tmp_path, ext=[WIPE], plugin=[PLUGIN_WIPE])
    reg = ER.ExtensionRegistry.for_project(root)
    assert reg.get('my_wipe').source.startswith('plugin:')
    res = reg.save(root)
    assert res['ok'], res
    rec = _rows(root)['my_wipe']
    assert rec['label'] == '我写的擦除'          # ← 以前整条被删（count: 0）
    assert [f['key'] for f in rec['fields']] == ['方向', '秒数']


def test_a_shadowed_declaration_is_named_in_problems(tmp_path):
    root = _project(tmp_path, ext=[WIPE], plugin=[PLUGIN_WIPE])
    reg = ER.ExtensionRegistry.for_project(root)
    said = ' '.join(reg.problems)
    assert 'my_wipe' in said and '两次' in said and '插件' in said


def test_switching_a_shadowed_point_keeps_the_project_shape(tmp_path):
    """窗口里把被插件盖住的那条关掉：形状还在，开关也记住了。"""
    root = _project(tmp_path, ext=[WIPE], plugin=[PLUGIN_WIPE])
    reg = ER.ExtensionRegistry.for_project(root)
    point = reg.get('my_wipe')
    point.enabled = False
    point.override = True                       # `_apply_to` 会这么置
    assert reg.save(root)['ok']
    rec = _rows(root)['my_wipe']
    assert rec['enabled'] is False
    assert [f['key'] for f in rec['fields']] == ['方向', '秒数']
    assert 'override' not in rec                # 不是"只带开关"，是真声明
    again = ER.ExtensionRegistry.for_project(root)
    assert again.get('my_wipe').enabled is False    # 重装后仍然关着
    assert again.get('my_wipe').source.startswith('plugin:')


def test_a_plugin_point_that_never_was_in_the_file_still_saves_as_a_switch(
        tmp_path):
    """插件点（文件里没有它）关掉 → 写的还是 `override` 记录（第五轮那条规矩）。"""
    root = _project(tmp_path, plugin=[PLUGIN_WIPE])
    reg = ER.ExtensionRegistry.for_project(root)
    point = reg.get('my_wipe')
    point.enabled = False
    point.override = True
    assert reg.save(root)['ok']
    rec = _rows(root)['my_wipe']
    assert rec['override'] is True and rec['enabled'] is False
    assert 'fields' not in rec


# ---------------------------------------------------------------------------
#  五、程序化改开关也要记得住（探针 ⑥）
# ---------------------------------------------------------------------------

def test_switching_off_a_builtin_via_the_api_survives_a_reload(tmp_path):
    root = _project(tmp_path, ext=[ALPHA])
    reg = ER.ExtensionRegistry.for_project(root)
    assert reg.set_enabled('renpy.transition', False)
    assert reg.save(root)['ok']
    rec = _rows(root)['renpy.transition']
    assert rec['override'] is True and rec['enabled'] is False
    again = ER.ExtensionRegistry.for_project(root)
    assert again.get('renpy.transition').enabled is False


def test_switching_off_a_point_that_is_in_the_file_does_not_add_override(tmp_path):
    """项目自己的那条：关掉就是那条的 `enabled: false`，别变成开关副本。"""
    root = _project(tmp_path, ext=[ALPHA])
    reg = ER.ExtensionRegistry.for_project(root)
    reg.set_enabled('alpha', False)
    assert reg.save(root)['ok']
    rec = _rows(root)['alpha']
    assert rec['enabled'] is False and 'override' not in rec
    assert rec['label'] == '甲' and rec['fields']


# ---------------------------------------------------------------------------
#  六、窗口：存之前问，存之后重读
# ---------------------------------------------------------------------------

def _dialog(qapp, root):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    return ExtensionDialog(None, project_root=root)


def _answer(monkeypatch, value):
    """把"要不要覆盖"的弹窗钉成 `value`，并留下问过的话。"""
    from storygraph.ui_qt import dialog_extensions as DE

    asked = []
    monkeypatch.setattr(
        DE.QMessageBox, 'question',
        staticmethod(lambda *a, **k: (asked.append(a[2]), value)[1]))
    return asked


def test_the_window_asks_before_overwriting_someone_elses_edit(
        qapp, tmp_path, monkeypatch):
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, ext=[ALPHA, BETA])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('alpha')
    other = ER.ExtensionRegistry.for_project(root)
    other.get('alpha').label = '别人改的'
    assert other.save(root)['ok']
    dlg.ed_label.setText('我改的')
    asked = _answer(monkeypatch, DE.QMessageBox.No)
    assert dlg._save() is False
    dlg.close()
    assert _rows(root)['alpha']['label'] == '别人改的'   # 否 = 一个字没写
    assert asked and 'alpha' in asked[0]
    assert '没存' in dlg.status.text()


def test_saying_yes_to_the_question_writes_mine(qapp, tmp_path, monkeypatch):
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, ext=[ALPHA, BETA])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('alpha')
    other = ER.ExtensionRegistry.for_project(root)
    other.get('alpha').label = '别人改的'
    assert other.save(root)['ok']
    dlg.ed_label.setText('我改的')
    _answer(monkeypatch, DE.QMessageBox.Yes)
    assert dlg._save() is True
    dlg.close()
    assert _rows(root)['alpha']['label'] == '我改的'
    assert '覆盖' in dlg.status.text()


def test_a_merged_save_re_reads_the_file_into_the_tree(qapp, tmp_path):
    """存完之后树上得是**文件里真正的样子**（含别人加的那条）。"""
    root = _project(tmp_path, ext=[ALPHA])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('alpha')
    other = ER.ExtensionRegistry.for_project(root)
    other.upsert(ES.parse_extension({'id': 'gamma', 'kind': 'call',
                                     'label': '丙'}))
    assert other.save(root)['ok']
    dlg.ed_label.setText('甲（我改的）')
    assert dlg._save() is True
    assert 'gamma' in [p.id for p in dlg._reg.all()]
    assert '收进' in dlg.status.text()
    assert _rows(root)['alpha']['label'] == '甲（我改的）'
    dlg.close()


def test_clicking_save_writes_the_file(qapp, tmp_path):
    """按钮**真的**连到 `_save` 上（不是被 Qt 的 `checked` 参数喂进 `on_conflict`）。

    这条是 `tools/audit_qt_signal_slots.py` 抓到的真事（2026-11-07 第六轮）：
    `_save` 多了个参数之后，`b_save.clicked.connect(self._save)` 会让
    Qt 把 `checked=False` 当第一个实参 → 点"保存到项目"变成"不认识，没存"。
    """
    from PySide6.QtWidgets import QPushButton

    root = _project(tmp_path, ext=[ALPHA])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('alpha')
    dlg.ed_label.setText('甲（点按钮存的）')
    button = next(b for b in dlg.findChildren(QPushButton)
                  if b.text() == '保存到项目')
    button.click()
    dlg.close()
    assert _rows(root)['alpha']['label'] == '甲（点按钮存的）'


def test_deleting_does_not_lie_when_a_conflict_blocks_it(
        qapp, tmp_path, monkeypatch):
    from storygraph.ui_qt import dialog_extensions as DE

    root = _project(tmp_path, ext=[ALPHA, BETA])
    dlg = _dialog(qapp, root)
    assert dlg.focus_on('alpha')
    other = ER.ExtensionRegistry.for_project(root)
    other.get('alpha').label = '别人改的'
    assert other.save(root)['ok']
    monkeypatch.setattr(DE.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: DE.QMessageBox.Yes))
    dlg._delete_point()
    assert 'alpha' in _ids(root)                 # 没删掉
    assert '没落盘' in dlg.status.text()
    assert dlg._reg.get('alpha') is not None     # 树上也还在（不骗人）
    dlg.close()
