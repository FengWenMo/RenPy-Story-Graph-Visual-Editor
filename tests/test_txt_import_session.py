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


def _patch_export_dir(ui_window, monkeypatch, proj: Path,
                      rel: str = 'game') -> Path:
    """
    导出文件夹选择器 → 桩（真选择器是 `QFileDialog` 目录模式，offscreen 下会卡住）。

    2026-10-26 起导入前会先问"`.rpy` 导出到哪个文件夹"——不猜子目录
    （用户："不要写入 chapter 文件夹！很多项目根本没有这个文件夹"）。
    """
    target = proj / rel
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ui_window, '_ask_plain_text_export_dir',
                        lambda: target)
    return target


def test_the_ui_handler_runs_end_to_end(ui_window, monkeypatch):
    """
    走 UI 的那个方法（而不是直接调 session）：选文件 → 选导出文件夹 →
    落盘 → 进图。`CharDecisionDialog` 用桩（真弹窗在 `test_dialog_chars.py`
    里单独测），目录选择器用桩（见 `_patch_export_dir`）。
    """
    from PySide6.QtWidgets import QFileDialog
    proj = Path(ui_window._project_root)
    txt = proj / 'game' / '9-9纯文字试写.txt'
    # ⚠ 纯文字里**不要**拿引号标旁白：那对引号会被当正文（转义进产物）。
    txt.write_text('1.0 chapter9-9 纯文字试写\n这一句是旁白\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(txt)], '')))
    _patch_export_dir(ui_window, monkeypatch, proj)

    ui_window.import_plain_text()
    out = proj / 'game' / 'chapter9_9.rpy'
    assert out.is_file(), '没落盘'
    assert '"这一句是旁白"' in out.read_text(encoding='utf-8')
    # ⚠ 图可能是**这次新建**的（窗口初始 `self.graph` 为 None），断言要看
    # 窗口当前那一份，而不是别的引用
    labels = {n.label_name for n in (ui_window.graph.nodes or {}).values()}
    assert 'chapter9_9' in labels, labels


def test_the_import_writes_into_the_chosen_export_folder(ui_window, monkeypatch):
    """
    ⭐ 用户 2026-10-26："不要写入 chapter 文件夹！……你可以给一个 rpy 的
    导出路径，问一问导出到哪个文件夹就行！"

    选了 `game/chapter/` 就落 `game/chapter/`；项目里**没有**这层目录时
    也照样能选别处 —— 工具不猜，用户说了算。
    """
    proj = Path(ui_window._project_root)
    txt = proj / 'game' / '9-9纯文字试写.txt'
    txt.write_text('1.0 chapter9-9 纯文字试写\n这一句是旁白\n', encoding='utf-8')
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(txt)], '')))
    out_dir = _patch_export_dir(ui_window, monkeypatch, proj,
                                rel='game/chapter')

    ui_window.import_plain_text()

    assert (out_dir / 'chapter9_9.rpy').is_file(), '没写进人选的那个文件夹'
    assert not (proj / 'game' / 'chapter9_9.rpy').exists(), \
        '默认目录里又多了一份（应该只按人选的目录写）'


def test_cancelling_the_export_folder_writes_nothing(ui_window, monkeypatch):
    """目录选择器点「取消」→ 这次不导入、零写盘。"""
    proj = Path(ui_window._project_root)
    txt = proj / 'game' / '9-9纯文字试写.txt'
    txt.write_text('1.0 chapter9-9 纯文字试写\n这一句是旁白\n', encoding='utf-8')
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(txt)], '')))
    monkeypatch.setattr(ui_window, '_ask_plain_text_export_dir', lambda: None)

    ui_window.import_plain_text()

    assert not list((proj / 'game').glob('chapter9_9.rpy'))
    assert '没有选择导出文件夹' in ui_window._status_bar.currentMessage()


# ==================== 冲突预检 / 清理残留的 UI 接线 ====================

def _write_handwritten_chapter9(proj: Path) -> Path:
    """手写的 `game/chapter9_9.rpy`（导入 9-9 的 txt 会撞上它）。"""
    hand = proj / 'game' / 'chapter9_9.rpy'
    hand.write_text('define hand = Character("手写")\n'
                    'label chapter9_9:\n    "原文"\n', encoding='utf-8')
    return hand


def _pick_txt(monkeypatch, proj: Path) -> Path:
    from PySide6.QtWidgets import QFileDialog
    txt = proj / 'game' / '9-9纯文字试写.txt'
    txt.write_text('1.0 chapter9-9 纯文字试写\n这一句是旁白\n', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(txt)], '')))
    return txt


def _patch_dialog(monkeypatch, *, accepted: bool, choose=None, seen=None):
    """把冲突预检弹窗换成桩（真弹窗在 `test_dialog_import_review.py` 里测）。"""
    from PySide6.QtWidgets import QDialog

    class _Stub:
        def __init__(self, parent, review):
            self.review = review
            if seen is not None:
                seen['review'] = review

        def exec(self):
            return QDialog.Accepted if accepted else QDialog.Rejected

        def choices(self):
            if choose is not None:
                return choose(self.review)
            return [{'source': str(i.source), 'action': 'skip'}
                    for i in self.review.items]

    monkeypatch.setattr(
        'storygraph.ui_qt.dialog_import_review.CollisionReviewDialog', _Stub)


def test_the_collision_dialog_is_asked_before_any_write(ui_window, monkeypatch):
    """
    撞上**手写**章节时先弹冲突预检：选"跳过" → 手写文件一个字不动，
    也不产生 `.blocked.rpy`（这正是用户要删的那堆东西）。
    """
    from storygraph.authoring import import_review as IR
    proj = Path(ui_window._project_root)
    hand = _write_handwritten_chapter9(proj)
    before = hand.read_text(encoding='utf-8')
    _pick_txt(monkeypatch, proj)
    _patch_export_dir(ui_window, monkeypatch, proj)
    seen = {}
    _patch_dialog(monkeypatch, accepted=True, seen=seen)

    ui_window.import_plain_text()

    review = seen.get('review')
    assert review is not None, '没有走冲突预检'
    assert len(review.items) == 1, review.items
    assert review.items[0].code in (IR.CODE_HANDWRITTEN, IR.CODE_LABEL_COLLISION)
    assert hand.read_text(encoding='utf-8') == before, '手写文件被动了！'
    assert not list((proj / 'build' / 'review').glob('*.blocked.rpy'))
    log = ui_window._console.toPlainText()
    assert '跳过' in log or '先不写' in log, log[-2000:]


def test_cancelling_the_collision_dialog_writes_nothing(ui_window, monkeypatch):
    """点「取消（什么都不写）」→ 一个字节都不写（会话层保证）。"""
    proj = Path(ui_window._project_root)
    hand = _write_handwritten_chapter9(proj)
    before = hand.read_text(encoding='utf-8')
    _pick_txt(monkeypatch, proj)
    _patch_export_dir(ui_window, monkeypatch, proj)
    _patch_dialog(monkeypatch, accepted=False)

    ui_window.import_plain_text()

    assert hand.read_text(encoding='utf-8') == before
    assert not list((proj / 'build' / 'review').glob('*.blocked.rpy'))
    assert '先不写' in ui_window._console.toPlainText()


def test_the_collision_dialog_can_replace_after_the_user_says_so(ui_window,
                                                                monkeypatch):
    """人明确选「覆盖旧文件」→ 手写的旧文件被覆盖，且**先备份 .bak**。"""
    proj = Path(ui_window._project_root)
    hand = _write_handwritten_chapter9(proj)
    before = hand.read_text(encoding='utf-8')
    _pick_txt(monkeypatch, proj)
    _patch_export_dir(ui_window, monkeypatch, proj)
    _patch_dialog(monkeypatch, accepted=True,
                  choose=lambda rv: [{'source': str(rv.items[0].source),
                                      'action': 'replace'}])

    ui_window.import_plain_text()

    assert before in Path(str(hand) + '.bak').read_text(encoding='utf-8'), \
        '覆盖手写文件前必须备份 .bak'
    assert '"这一句是旁白"' in hand.read_text(encoding='utf-8')
    assert '覆盖' in ui_window._console.toPlainText()


def test_the_toolbar_has_the_cleanup_entry(ui_window):
    """工具栏要有「清理导入残留」—— 用户要的就是"别再一个个删"。"""
    from PySide6.QtGui import QAction
    texts = [a.text() for a in ui_window.findChildren(QAction)]
    assert any('清理导入残留' in t for t in texts), texts


def test_cleanup_says_so_when_there_is_nothing_to_clean(ui_window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = []
    monkeypatch.setattr(QMessageBox, 'information',
                        staticmethod(lambda *a, **k: seen.append(a[2])))
    ui_window.cleanup_import_junk()
    assert seen and '没找到' in seen[0], seen


def test_cleanup_deletes_only_after_the_dialog_says_yes(ui_window, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from storygraph.authoring import import_cleanup as IC
    proj = Path(ui_window._project_root)
    junk = proj / 'game' / 'chapter1_1.rpy.bak'
    junk.write_text('旧的一版\n', encoding='utf-8')
    keep = proj / 'game' / 'asset_tables'
    keep.mkdir(parents=True, exist_ok=True)
    precious = keep / 'characters.xlsx.bak'
    precious.write_bytes(b'\x00xlsx')

    class _Stub:
        def __init__(self, parent, project_root, files=None):
            self.deleted = []
            self.failed = []
            self.files = list(files or [])

        def exec(self):
            self.deleted, self.failed = IC.delete_import_junk(self.files)
            return QDialog.Accepted

    monkeypatch.setattr('storygraph.ui_qt.dialog_cleanup.ImportJunkDialog',
                        _Stub)
    ui_window.cleanup_import_junk()
    assert not junk.exists(), '选了确认却没删'
    assert precious.is_file(), '资源表备份被误删了！'
    assert '清理' in ui_window._console.toPlainText()
