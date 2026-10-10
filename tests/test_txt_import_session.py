"""
纯文字导入 · 会话层判据（S3 接入）
====================================

`EditorSession.import_plain_text` 是"作者写的纯文字"进工具的那个口子。
这里钉住它在**会话层**的三件事：

  ① 走完整链路：`.txt` → `.rpy`（落 `game/`）→ 进剧情图（复用
     `import_chapters`，不另起一套解析）；
  ② **未确认角色 → 不写盘、不回图**（未定义变量会让游戏崩）；
  ③ 决定之后**回表**（`characters.xlsx`），因此第二次导入不用再问。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_registry import (                        # noqa: E402
    _TABLE_FIELDS, parse_asset_tables, write_dict_to_excel)
from storygraph.authoring.txt_import import CharacterMap              # noqa: E402

ROWS = {
    'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
    'mm': {'ID': 'mm', 'Name': '妈妈'},
}


@pytest.fixture
def project(tmp_path):
    """最小项目 + 一张真角色表；返回 (root, txt 文件路径)。"""
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    adir = root / 'game' / 'asset_tables'
    adir.mkdir(parents=True, exist_ok=True)
    write_dict_to_excel(adir / 'characters.xlsx', ROWS,
                        _TABLE_FIELDS['character'])
    fp = tmp_path / '1-0初入大学-定稿.txt'
    fp.write_text('1.0 chapter0-0 初入大学\n妈妈"跟妈客气啥"\n「蒋忱」"来了"\n',
                  encoding='utf-8')
    return root, fp


def _session(root):
    from storygraph.app.editor import EditorSession
    return EditorSession(project_root=root)


def test_the_whole_chain_works(project):
    """.txt → game/chapter0_0.rpy → 剧情图里有它的 label。"""
    root, fp = project
    s = _session(root)
    s.load_assets()
    res = s.import_plain_text([fp])
    assert res['ok'], res
    out = root / 'game' / 'chapter0_0.rpy'
    assert out in [Path(p) for p in res['written']]
    text = out.read_text(encoding='utf-8')
    assert '    mm "跟妈客气啥"' in text
    assert 'define' not in text, 'define 不该写进章节产物'

    graph = res['import_result']['graph']
    labels = {n.label_name for n in graph.nodes.values()}
    assert 'chapter0_0' in labels, labels


def test_the_graph_is_updated_so_it_can_be_looked_at(project):
    root, fp = project
    s = _session(root)
    s.load_assets()
    graph = s.import_chapters([])['graph'] if False else None   # noqa: F841
    from storygraph.graph.models import Graph
    graph = Graph()
    res = s.import_plain_text([fp], graph)
    assert res['ok'], res
    assert graph.nodes, '剧情图里什么都没有'


def test_unmapped_stops_the_whole_thing(project, tmp_path):
    """未映射写法 → 不写盘、不进图（且把"要你决定"的清单带回来）。"""
    root, fp = project
    fp.write_text('1.0 chapter0-0 初入大学\nMOM"跟妈客气啥"\n', encoding='utf-8')
    s = _session(root)
    s.load_assets()
    res = s.import_plain_text([fp])
    assert not res['ok']
    assert res['needs_decision']
    assert [p.token for p in res['proposals']] == ['MOM']
    assert res['written'] == []
    assert not list((root / 'game').glob('*.rpy')), '没确认就落盘了'
    assert '未定义变量' in ' '.join(res['messages'])


def test_decisions_come_back_through_the_ask_callback(project):
    """UI 的形态：`ask(proposals) -> 决定`；决定写回表后**重跑**并落盘。"""
    root, fp = project
    fp.write_text('1.0 chapter0-0 初入大学\nMOM"跟妈客气啥"\n', encoding='utf-8')
    s = _session(root)
    s.load_assets()

    seen = []

    def _ask(proposals):
        seen.append([p.token for p in proposals])
        return [{'token': 'MOM', 'action': 'existing', 'target': 'mm'}]

    res = s.import_plain_text([fp], ask=_ask)
    assert res['ok'], res
    assert seen == [['MOM']]
    text = (root / 'game' / 'chapter0_0.rpy').read_text(encoding='utf-8')
    assert '    mm "跟妈客气啥"' in text

    # 决定落进表里了 → 第二次导入**不该再问**
    rows = parse_asset_tables(root / 'game' / 'asset_tables').characters
    assert rows['mm']['Aliases'] == 'MOM'
    again_calls = []
    res2 = s.import_plain_text([fp], ask=lambda p: again_calls.append(p))
    assert res2['ok'] and again_calls == [], '第二次还在问同一个写法'


def test_the_session_reads_the_character_table(project):
    """`character_rows()` 就是那张表 —— txt 导入靠它判"这个写法是谁"。"""
    root, _fp = project
    s = _session(root)
    s.load_assets()
    rows = s.character_rows()
    assert 'mm' in rows and 'jc' in rows
    cm = CharacterMap(rows)
    assert cm.lookup('妈妈')[0] == 'mm'


def test_without_a_project_root_it_says_so():
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=None)
    res = s.import_plain_text(['whatever.txt'])
    assert not res['ok'] and '项目根' in res['error']


def test_dry_run_writes_nothing(project):
    root, fp = project
    s = _session(root)
    s.load_assets()
    res = s.import_plain_text([fp], dry_run=True)
    assert res['written'] == []
    assert not list((root / 'game').glob('*.rpy'))


# ==================== UI 接线（真 Qt，offscreen） ====================

def test_the_toolbar_has_the_entry(ui_window):
    """工具栏要有「导入纯文字章节」—— 不然这条路没人找得到。"""
    from PySide6.QtGui import QAction
    texts = [a.text() for a in ui_window.findChildren(QAction)]
    assert any('导入纯文字章节' in t for t in texts), texts


def test_the_ui_handler_runs_end_to_end(ui_window, monkeypatch):
    """
    走 UI 的那个方法（而不是直接调 session）：选文件 → 落盘 → 进图。
    `CharDecisionDialog` 用桩（真弹窗在 `test_dialog_chars.py` 里单独测）。
    """
    from PySide6.QtWidgets import QFileDialog
    proj = Path(ui_window._project_root)
    txt = proj / 'game' / '9-9纯文字试写.txt'
    # ⚠ 纯文字里**不要**拿引号标旁白：那对引号会被当正文（转义进产物）。
    txt.write_text('1.0 chapter9-9 纯文字试写\n这一句是旁白\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(txt)], '')))

    ui_window.import_plain_text()
    out = proj / 'game' / 'chapter9_9.rpy'
    assert out.is_file(), '没落盘'
    assert '"这一句是旁白"' in out.read_text(encoding='utf-8')
    # ⚠ 图可能是**这次新建**的（窗口初始 `self.graph` 为 None），断言要看
    # 窗口当前那一份，而不是别的引用
    labels = {n.label_name for n in (ui_window.graph.nodes or {}).values()}
    assert 'chapter9_9' in labels, labels
