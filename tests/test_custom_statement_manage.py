"""
「④ 已有声明」页：**看得见、删得掉、重置得了**（用户 2026-10-18）
==================================================================

用户原话：

> 你给了个rule.json建立，却又不给删除重置，并且用户根本看不到自定义模块
> 究竟定义了个什么。

这个文件从界面层守：
  · 打开弹窗就能看到清单里**实际写着什么**（语句 / 分栏 / 其它键）；
  · 选中一条语句能删；删完文件与界面同步；
  · "清空候选分栏"与"重置所有声明"真的写盘，且 `host_hooks` 等其它键不丢；
  · 分栏页的"只保存候选分栏"能整段替换（删掉的栏真的消失），
    不需要先编一条假语句。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
# **先确认有 PySide6 再 import 任何 ui_qt 模块**（2026-10-21，修 CI 全红）：
# 这个文件直接 `from storygraph.ui_qt.…` / `from PySide6…`，没有 PySide6
# 的环境（CI）会在**收集阶段**整批 ERROR —— 收集阶段没有 skip 的机会，
# 一条测试都不会跑。`importorskip` 必须站在第一次 import 它们之前。
pytest.importorskip('PySide6')

from PySide6.QtWidgets import QDialog, QMessageBox                  # noqa: E402

from storygraph.ui_qt.dialog_learn import CustomStatementDialog    # noqa: E402

MANIFEST = 'storygraph.rules.json'


def _project(tmp_path, data=None):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    payload = data if data is not None else {
        'statements': [{'name': 'weather', 'label': '天气',
                        'columns': [{'header': '强度', 'param': 'strength'}]}],
        'attr_groups': [{'name': 'outfit', 'label': '服装',
                         'options': ['泳装', '校服']}],
        'host_hooks': {'trust': True},
        'my_note': '我的键',
    }
    (root / MANIFEST).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding='utf-8')
    return root


def _dlg(root):
    return CustomStatementDialog(None, project_root=root, discovered=[])


def _data(root):
    return json.loads((root / MANIFEST).read_text(encoding='utf-8'))


def _yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))


def test_manage_page_shows_what_is_actually_defined(qapp, tmp_path):
    root = _project(tmp_path)
    dlg = _dlg(root)
    text = dlg.manage_view.toPlainText()
    assert str(root / MANIFEST) in text
    assert 'weather' in text and '天气' in text and '1 格' in text
    assert 'outfit' in text and '服装' in text and '2 个词' in text
    assert 'host_hooks' in text and '其它键' in text
    assert dlg.manage_list.count() == 1, '删语句的列表里没有那一条'
    assert dlg.manage_list.item(0).text().startswith('weather')
    assert dlg._manage_reset.isEnabled()


def test_no_manifest_means_no_dangerous_buttons(qapp, tmp_path):
    root = tmp_path / 'empty'
    (root / 'game').mkdir(parents=True)
    dlg = _dlg(root)
    assert '不存在' in dlg.manage_view.toPlainText()
    assert dlg.manage_list.count() == 0
    assert not dlg._manage_del.isEnabled()
    assert not dlg._manage_clear_groups.isEnabled()
    assert not dlg._manage_reset.isEnabled()


def test_delete_selected_statement_updates_file_and_page(qapp, tmp_path, monkeypatch):
    root = _project(tmp_path)
    dlg = _dlg(root)
    _yes(monkeypatch)
    dlg.manage_list.setCurrentRow(0)
    dlg._delete_selected_statement()
    data = _data(root)
    assert data.get('statements') == [], data
    assert data['attr_groups'][0]['name'] == 'outfit', '删语句时把分栏也删了'
    assert data['host_hooks'] == {'trust': True}
    assert dlg.changed is True
    assert 'weather' not in dlg.manage_view.toPlainText()
    assert dlg.manage_list.count() == 0
    assert list((root / 'storygraph.rules.backup').glob('*.json')), '删之前没备份'


def test_clear_groups_keeps_statements(qapp, tmp_path, monkeypatch):
    root = _project(tmp_path)
    dlg = _dlg(root)
    _yes(monkeypatch)
    dlg._clear_groups()
    data = _data(root)
    assert 'attr_groups' not in data
    assert data['statements'][0]['name'] == 'weather'
    assert data['my_note'] == '我的键'
    assert dlg.changed is True
    assert dlg.columns_editor.count() == 0, '清空之后分栏页还留着旧卡'
    assert '候选分栏（0 栏）' in dlg.manage_view.toPlainText()


def test_reset_keeps_other_keys_and_deletes_when_empty(qapp, tmp_path, monkeypatch):
    root = _project(tmp_path)
    dlg = _dlg(root)
    _yes(monkeypatch)
    dlg._reset_all()
    data = _data(root)
    assert 'statements' not in data and 'attr_groups' not in data
    assert data == {'host_hooks': {'trust': True}, 'my_note': '我的键'}
    assert dlg.changed is True

    # 只剩声明时，重置 = 删掉整个文件（回到"项目没声明"）
    root2 = _project(tmp_path / 'second', {
        'statements': [{'name': 'weather'}],
        'attr_groups': [{'name': 'outfit', 'label': '服装'}],
    })
    dlg2 = _dlg(root2)
    dlg2._reset_all()
    assert not (root2 / MANIFEST).exists()
    assert list((root2 / 'storygraph.rules.backup').glob('*.json'))


def test_save_columns_only_replaces_the_section(qapp, tmp_path, monkeypatch):
    """分栏页的"只保存候选分栏"：不需要填语句名，删掉的栏会真的消失。"""
    root = _project(tmp_path)
    dlg = _dlg(root)
    _yes(monkeypatch)
    dlg.columns_editor.clear()
    dlg.columns_editor.add_column('pose', '动作', '站立,挥手')
    dlg._save_columns_now()
    data = _data(root)
    assert [g['name'] for g in data['attr_groups']] == ['pose'], data
    assert data['attr_groups'][0]['label'] == '动作'
    assert data['attr_groups'][0]['options'] == ['站立', '挥手']
    assert data['statements'][0]['name'] == 'weather', '保存分栏时把语句删了'
    assert data['host_hooks'] == {'trust': True}
    assert dlg.changed is True


def _write_layeredimage(root):
    (root / 'game' / 'defs.rpy').write_text(
        'layeredimage lan:\n'
        "    image_format 'images/lan/{image}.png'\n"
        '    group outfit:\n'
        '        attribute suit default:\n'
        "            'suit'\n"
        '    group pose:\n'
        '        attribute stand default:\n'
        "            'stand'\n",
        encoding='utf-8')


def test_column_key_picker_offers_the_project_group_names(qapp, tmp_path):
    """「分栏标识」不该让人盲填：项目里真有的组名直接给出来。"""
    root = _project(tmp_path)
    _write_layeredimage(root)
    dlg = _dlg(root)
    card = dlg.columns_editor.add_column()
    assert card.known_keys() == ['outfit', 'pose'], card.known_keys()
    assert card._pick_key.isVisibleTo(card), '有组名却没露出选择入口'
    card.apply_known_key('pose')
    assert card._key.text() == 'pose'


def test_保存分栏会挡住非法键名(qapp, tmp_path, monkeypatch):
    """
        用户把「服装」填进"键名" → 保存**不许静默丢**：当场说清，且一个字节都不写
        （2026-10-18 审计 P0-4）。
    """
    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg.columns_editor.clear()
    dlg.columns_editor.add_column('服装', '服装', '泳装,校服')
    warned = []
    monkeypatch.setattr(
        QMessageBox, 'warning',
        staticmethod(lambda *a, **k: warned.append(a[2] if len(a) > 2 else '')))
    before = (root / MANIFEST).read_text(encoding='utf-8')
    dlg._save_columns_now()
    assert warned, '非法分栏被静默接受'
    assert (root / MANIFEST).read_text(encoding='utf-8') == before
    assert dlg.changed is False


def test_保存一条语句不会碰候选分栏(qapp, tmp_path):
    """
        旧版主 OK 背后有个"把分栏一起写进清单"的勾选框 —— 空列表要不要当成
        "清空"是 2026-10-18 审出来的事故点。新版把两件事彻底分开：
        语句的保存**只写 statements**，分栏只由分栏页那个按钮写。
    """
    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg.name.setText('swim_suit')
    dlg.params.clear()
    dlg.params.add_param('lan', '角色', '', kind='variable')
    assert dlg._save_statement() is True, dlg._status.text()
    data = _data(root)
    assert [s['name'] for s in data['statements']] == ['weather', 'swim_suit'], data
    assert data['attr_groups'][0]['name'] == 'outfit', '保存语句时把分栏动了'
    assert data['attr_groups'][0]['options'] == ['泳装', '校服']
    assert data['host_hooks'] == {'trust': True}
    assert dlg.changed is True
    # 保存之后列表里就该看见它（而不是等重开弹窗）
    assert 'swim_suit' in dlg.manage_view.toPlainText()
