"""
「自定义语句」弹窗 —— **第六版（信息架构重做）** 的界面判据（2026-10-18）
================================================================================

这一版不是换皮：整件事被重新排成 **列表页 → 编辑页** 两页（`PAGE_LIST` /
`PAGE_FORM`），外加两个"项目级"页（候选分栏 / 项目清单）。用户原话：

> 运用UI设计和用户体验的角度重构一下自定义模块的UI界面，去除令人疑惑的表述，
> 保证策划/程序逻辑清晰明了，操作方便，层次合理……可直接进行前端重构实现，
> 不再旧有基础上改

这个文件守三件事：

  ① **一处写盘**：保存由弹窗自己做（`_save_statement`），而且**必须**把
     `pattern`（每格的类型/顺序/多选/可不填）一起写下去 —— 旧版调用方漏传
     了它，格子的类型、顺序、多选**全丢**，只有 `columns` 活下来；
  ② **层次**：列表回答"哪些写法还不认识 / 已经认识哪些"，编辑页只回答
     "这一条怎么描述"；同一屏不会同时出现两件事，也不会出现两条滚动条；
  ③ **措辞**：同一个东西只有一个名字（语句 / 开头词 / 格 / 列名 / 候选分栏 /
     项目清单）。旧稿里的"动作名""词组""两把锁""写进 storygraph.rules.json"
     这类让人看不懂的字，界面上一个都不许再有。
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

from PySide6.QtWidgets import (QCheckBox, QComboBox, QLabel,   # noqa: E402
                               QPushButton, QScrollArea)

from storygraph.story import statement_registry as SR          # noqa: E402
from storygraph.ui_qt.dialog_learn import (CustomStatementDialog,  # noqa: E402
                                            PAGE_FORM, PAGE_GROUPS,
                                            PAGE_LIST, PAGE_MANIFEST)

MANIFEST = 'storygraph.rules.json'


def _project(tmp_path, scripts: str = ''):
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    if scripts:
        (root / 'game' / 'ch01.rpy').write_text(scripts, encoding='utf-8')
    return root


def _write_declared(root, payload):
    (root / MANIFEST).write_text(json.dumps(payload, ensure_ascii=False,
                                            indent=2), encoding='utf-8')
    SR.clear_cache()
    return root


def _manifest(root) -> dict:
    path = root / MANIFEST
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def _dlg(root, **kw):
    SR.clear_cache()
    if 'discovered' not in kw:
        kw['discovered'] = SR.discover_undeclared(root)
    return CustomStatementDialog(None, project_root=root, **kw)


# ======================================================================
#  ① 一处写盘：保存必须带着格子的类型 / 顺序 / 多选
# ======================================================================

def test_保存这条语句会把格子的类型和顺序一起写下去(qapp, tmp_path):
    """
    ⚠⚠ 这条钉的是一个**真实的丢数据 bug**（2026-10-18 第六版重做时抓到）：

    旧链路是 弹窗产出 `built['pattern']` → 关窗 → 主窗口 `declare_statement(...)`
    —— 主窗口那次调用**没有传 `pattern`**。于是：
      · 编剧选「固定几个字」→ 清单里没有 `text`，这个格子变成死格；
      · 勾了「可以连着写多个」→ 清单里是"一格一个词"；
      · 拖过的顺序 → 清单里按列的顺序重排（而列顺序不等于格子顺序）。
    界面上一切正常，写下去的东西却是另一回事。

    现在写盘只在 `_save_statement` 一处，这条判据直接读**磁盘上的清单**。
    """
    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg.name.setText('swim_suit')
    dlg.label.setText('泳装切换')
    dlg.params.clear()
    # ① 固定字（keyword）  ② 任意文字  ③ 从候选里选（+ 多选 + 限死）
    # ⚠ 第一个参数是**内部键**（新格子留空即可），第二个才是"表格里这一列叫什么"
    dlg.params.add_param('', '动作', '', kind='keyword')
    dlg.params._cards[0]._literal.setText('swim_suit')
    dlg.params.add_param('', '角色', '', kind='variable')
    card = dlg.params.add_param('', '服装', 'clothes2、base_swimsuit',
                                kind='choice')
    card._multi.setChecked(True)
    card._limit.setChecked(True)
    assert dlg._save_statement() is True, dlg._status.text()

    entry = _manifest(root)['statements'][0]
    assert entry['name'] == 'swim_suit'
    assert [c['name'] for c in entry['columns']] == ['动作', '角色', '服装']
    slots = entry['pattern']
    assert [s['kind'] for s in slots] == ['keyword', 'variable', 'choice'], slots
    assert slots[0]['text'] == 'swim_suit', '固定字丢了 —— 这一格会变成死格'
    assert slots[2]['multi'] is True, '「可以连着写多个」丢了'
    assert slots[2]['limit'] is True, '「只能从候选里选」丢了'
    assert slots[2]['options'] == ['clothes2', 'base_swimsuit'], slots[2]
    # 顺序也要按格子来（不是按列名的字典序/插入序）
    assert [s.get('column') for s in slots] == ['动作', '角色', '服装']


def test_保存会覆盖同名声明而不是写第二条(qapp, tmp_path):
    root = _write_declared(_project(tmp_path), {
        'statements': [{'name': 'weather', 'label': '天气',
                        'columns': [{'name': '强度', 'label': '强度'}],
                        'pattern': [{'kind': 'variable', 'column': '强度'}]}],
    })
    dlg = _dlg(root)
    dlg._load_statement(SR.StatementRegistry.for_project(root).declared[0])
    dlg.label.setText('天气切换')
    dlg.params._cards[0]._name.setText('强度值')
    assert dlg._save_statement() is True, dlg._status.text()
    stmts = _manifest(root)['statements']
    assert len(stmts) == 1, '同类声明写成了两条'
    assert stmts[0]['label'] == '天气切换'
    # 存量声明的内部键（脚本里那个 `强度=`）保留；界面改的是**列名/显示名**
    assert stmts[0]['columns'][0]['name'] == '强度'
    assert stmts[0]['columns'][0]['label'] == '强度值'


def test_保存失败不写盘也不关窗(qapp, tmp_path):
    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg.name.setText('9 bad name')
    assert dlg._save_statement() is False
    assert not (root / MANIFEST).exists(), '非法名字也写下去了'
    assert dlg._status.text().startswith('这'), dlg._status.text()


def test_保存之后列表当场更新_不用重开弹窗(qapp, tmp_path):
    root = _project(tmp_path,
                    'label ch01:\n    swim_suit 小明 泳装\n'
                    '    mycue "x" intensity=2:\n        renpy.pause(0.2)\n')
    dlg = _dlg(root)
    assert sorted(r['head'] for r in dlg._pending) == ['mycue', 'swim_suit']
    row = [i for i, r in enumerate(dlg._pending) if r['head'] == 'swim_suit'][0]
    dlg.found.setCurrentCell(row, 0)
    assert dlg.name.text() == 'swim_suit'
    dlg._go(PAGE_FORM)
    dlg.params.clear()
    dlg.params.add_param('', '角色', '', kind='variable')
    assert dlg._save_statement() is True, dlg._status.text()
    # 列表页：待认领少一个、已认识多一个，都**不用重开**
    assert dlg.pages.currentIndex() == PAGE_LIST
    assert [r['head'] for r in dlg._pending] == ['mycue']
    assert dlg.known.rowCount() == 1
    assert dlg.known.item(0, 0).text() == 'swim_suit'
    assert dlg.changed is True
    assert dlg.saved and dlg.saved[-1]['kind'] == 'statement'


# ======================================================================
#  ② 层次：列表 → 编辑，一屏只干一件事
# ======================================================================

def test_列表页就是入口_编辑页才动手(qapp, tmp_path):
    root = _project(tmp_path, 'label ch01:\n    swim_suit 小明 泳装\n')
    dlg = _dlg(root)
    # 打开时停在列表页
    assert dlg.pages.currentIndex() == PAGE_LIST
    # 选中一行**只带词、不跳页**（人还在看原文样例）
    dlg.found.setCurrentCell(0, 0)
    assert dlg.name.text() == 'swim_suit'
    assert dlg.pages.currentIndex() == PAGE_LIST, '点一下就跳页，人会丢失上下文'
    assert dlg._teach_btn.isEnabled()
    # 按按钮才进编辑页
    dlg._teach_btn.click()
    assert dlg.pages.currentIndex() == PAGE_FORM
    assert dlg._form_title.text() == '语句「swim_suit」'
    assert '待认领' in dlg._form_sub.text(), dlg._form_sub.text()
    # 返回列表
    dlg._back_btn.click()
    assert dlg.pages.currentIndex() == PAGE_LIST


def test_同一屏只有一个滚动区(qapp, tmp_path):
    """
    用户报过两次"这两个滚动条是何意味" —— 结构上只许有一个可见的 QScrollArea。
    """
    root = _project(tmp_path, 'label ch01:\n    swim_suit 小明 泳装\n')
    dlg = _dlg(root)
    dlg.resize(960, 640)
    dlg.show()
    qapp.processEvents()
    for page in (PAGE_LIST, PAGE_FORM, PAGE_GROUPS, PAGE_MANIFEST):
        dlg.pages.setCurrentIndex(page)
        qapp.processEvents()
        visible = [a for a in dlg.findChildren(QScrollArea) if a.isVisible()]
        assert len(visible) <= 1, f'第 {page} 页同屏有 {len(visible)} 个滚动区'
    dlg._baseline = dlg._signature()
    dlg.close()


def test_类型下拉必须真的在界面上(qapp, tmp_path):
    """
    ⚠⚠ **2026-10-18 重做时抓到的 P0**：`FieldCard._kind`（"这一格是"）以前
    从来没有被加进任何布局 —— 它是个没有父控件的 `QComboBox`。

    也就是说：`固定几个字` / `从候选里选` / `可选段` 这三种类型**界面上选不了**，
    人只能拿到"任意文字"。判据一直用 `add_param(kind=...)` 直接塞，所以从没红过。

    这条从**控件树**上守：它必须真的是某张卡的子孙控件，而且能改到 `data()`。
    """
    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg._go(PAGE_FORM)
    card = dlg.params._cards[0]
    combos = [c for c in card.findChildren(QComboBox)]
    assert card._kind in combos, '类型下拉不在卡片里（又变成了孤儿控件）'
    idx = card._kind.findData('choice')
    assert idx >= 0, [card._kind.itemText(i) for i in range(card._kind.count())]
    card._kind.setCurrentIndex(idx)
    assert card.data()['kind'] == 'choice'
    assert card._values.isVisibleTo(card), '选了枚举却不露出候选值输入'


def test_格子的序号和试切报的第几格对得上(qapp, tmp_path):
    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg._go(PAGE_FORM)
    dlg.name.setText('show')
    dlg.params.clear()
    dlg.params.add_param('小明', '角色', '', kind='variable')
    dlg.params.add_param('泳装', '服装', 'clothes2', kind='choice')
    titles = [c._title.text() for c in dlg.params._cards]
    assert titles[0].startswith('第 1 格') and titles[1].startswith('第 2 格'), titles
    assert '角色' in titles[0] and '服装' in titles[1], titles
    # 换位之后序号跟着变
    dlg.params.move(dlg.params._cards[1], -1)
    titles = [c._title.text() for c in dlg.params._cards]
    assert '服装' in titles[0] and '角色' in titles[1], titles


# ======================================================================
#  ③ 措辞：同一个东西只有一个名字
# ======================================================================

#: 旧稿里让人看不懂 / 一个东西好几个名字的字（界面上一个都不许再有）
_BANNED = ('动作名', '词组', '两把锁', '写进 storygraph.rules.json',
           '可出现可不出现', '这一列叫什么（编剧看到的）')


def _visible_texts(dlg) -> list:
    out = []
    for cls in (QLabel, QPushButton, QCheckBox):
        for w in dlg.findChildren(cls):
            for t in (w.text(), w.toolTip()):
                if t:
                    out.append(t)
    for table in (dlg.found, dlg.known):
        for i in range(table.rowCount()):
            pass
    return out


def test_界面上不再出现旧稿那些让人看不懂的字(qapp, tmp_path):
    root = _project(tmp_path, 'label ch01:\n    swim_suit 小明 泳装\n')
    dlg = _dlg(root)
    # 把每一页都点一遍 —— 有些字只在某一页上（"可出现可不出现"就在卡片上）
    for page in (PAGE_LIST, PAGE_GROUPS, PAGE_MANIFEST):
        dlg.pages.setCurrentIndex(page)
        qapp.processEvents()
    dlg.found.setCurrentCell(0, 0)
    dlg._go(PAGE_FORM)
    texts = _visible_texts(dlg)
    hit = [f'{t[:40]}（命中 {k}）' for t in texts for k in _BANNED if k in t]
    assert not hit, '界面上还留着旧名字：\n' + '\n'.join(hit)


def test_一个东西只有一个名字(qapp, tmp_path):
    """
    术语表（界面上一律用右列）：
      语句 / 开头词 / 格 / 列名 / 候选分栏 / 项目清单
    """
    root = _project(tmp_path, 'label ch01:\n    swim_suit 小明 泳装\n')
    dlg = _dlg(root)
    dlg.found.setCurrentCell(0, 0)
    dlg._go(PAGE_FORM)
    texts = ' '.join(_visible_texts(dlg))
    # 开头词（不再是"动作名 / 语句名"混用）
    assert '开头词' in texts
    # 格（一格的序号写在卡头上）
    assert dlg.params._cards[0]._title.text().startswith('第 1 格')
    # 列名（只有这一处提"列"，因为它真的会变成表格的一列）
    assert '表格里这一列叫什么' in texts
    # 分栏 / 清单：旧名"词组"不再出现（由上面那条兜着），"清单"只在页面上出现


def test_项目清单页不重复念同一个路径(qapp, tmp_path):
    """截图里那行路径曾经在标题、说明、只读框里各出现一次。"""
    root = _write_declared(_project(tmp_path), {'statements': [{'name': 'x'}]})
    dlg = _dlg(root)
    dlg._go(PAGE_MANIFEST)
    texts = [w.text() for w in dlg.findChildren(QLabel) if w.isVisibleTo(dlg)]
    assert not dlg._manage_path.isVisibleTo(dlg), '路径标签又露出来了'
    assert sum(str(root) in t for t in texts) <= 1, texts


def test_脚本里注册的语句不显示删除键(qapp, tmp_path):
    """
    脚本里 `renpy.register_statement` 声明的那几条**不在项目清单里** ——
    给它们一个"删除这条语句"的按钮，点下去只能得到一句"没删成"。
    能点就必须有反应，所以这种时候**干脆不显示**。
    """
    class _Host:
        name = 'battle'
        label = ''
        docs = ''
        block = 'possible'
        columns = []
        pattern = []
        source = 'host-registry'

    root = _project(tmp_path)
    dlg = _dlg(root)
    dlg._load_statement(_Host())
    dlg._go(PAGE_FORM)
    assert not dlg._delete_btn.isVisibleTo(dlg), '脚本里的语句不该有删除键'
    # 清单里自己声明的就**该**有
    root2 = _write_declared(_project(tmp_path / 'two'),
                            {'statements': [{'name': 'weather'}]})
    dlg2 = _dlg(root2)
    decl = SR.StatementRegistry.for_project(root2).declared[0]
    dlg2._load_statement(decl)
    dlg2._go(PAGE_FORM)
    assert dlg2._delete_btn.isVisibleTo(dlg2)


def test_保存候选分栏只动分栏_并且有自己的按钮(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    root = _write_declared(_project(tmp_path), {
        'statements': [{'name': 'weather'}],
        'attr_groups': [{'name': 'outfit', 'label': '服装', 'options': ['a']}],
    })
    dlg = _dlg(root)
    dlg._go(PAGE_GROUPS)
    assert dlg._groups_save.isVisibleTo(dlg), '分栏页没有自己的保存按钮'
    assert not dlg._ok_btn.isVisibleTo(dlg), '语句的保存键跑到分栏页上了'
    dlg.columns_editor.clear()
    dlg.columns_editor.add_column('pose', '动作', '站立')
    monkeypatch.setattr(QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg._save_columns_now()
    data = _manifest(root)
    assert [g['name'] for g in data['attr_groups']] == ['pose']
    assert data['statements'][0]['name'] == 'weather', '保存分栏把语句删了'
