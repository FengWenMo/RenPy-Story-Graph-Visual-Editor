# -*- coding: utf-8 -*-
"""
tests/test_version_store.py
===========================
**脚本的磁盘旧版本**（第十一轮）—— 原地小改也要留退路，而且不往 `game/` 里堆。

这一轮的出发点是一条量出来的事实（`.pip-tmp/probe_r11_rpy_backup.py`）：
工具改 `.rpy` 有两条写路，退路不对称 ——

  · 整份导出（`generation_guard.write_generation`，`backup=True`）→
    文件旁边留 `<名>.rpy.bak`；
  · 原地小改（改 `@at`、改源边、删一行源、换编排层…）→ **一个备份都不留**。

于是"关窗之后也能退"只对 `storygraph.ext.json` 成立，对**真正值钱的剧本**
不成立。这里钉住补上的那半边，以及三条不许破的底线：

  ① 旧版本**不进 `game/`**（Ren'Py 不扫它，`git status` 也不该看见它）；
  ② 放回**逐字节**（CRLF 文件放回去还是 CRLF）；
  ③ 备份没成就**不许覆盖**（宁可这次改不成）。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from storygraph.core import atomicio
from storygraph.core import versions as V
from storygraph.story import version_store as VS
from storygraph.story.paths import write_text_atomic
from storygraph.ui_qt.window_recover import RecoverMixin

CRLF = 'label ch01:\r\n    "第一句"\r\n    "第二句"\r\n    return\r\n'
LF = CRLF.replace('\r\n', '\n')


def _project(tmp_path, *, body=CRLF, rel='chapter/ch01.rpy'):
    """一个最小工程：`game/<rel>` + 一份 `game/script.rpy`。"""
    root = tmp_path / 'proj'
    game = root / 'game'
    (game / Path(rel).parent).mkdir(parents=True, exist_ok=True)
    script = game / rel
    script.write_bytes(body.encode('utf-8'))
    (game / 'script.rpy').write_text('label start:\n    return\n',
                                     encoding='utf-8')
    return root, script


def _files_under(folder) -> set:
    """目录下所有文件（相对路径）—— 用来钉"这里一个文件都不许多"。"""
    base = Path(folder)
    return {str(p.relative_to(base)) for p in base.rglob('*') if p.is_file()}


# ----------------------------------------------------------------------
#  一、放哪儿（判据是路径关系，不是文件名猜测）
# ----------------------------------------------------------------------

class TestWhere:
    """`history_dir` 只认"项目 `game/` 下的 `.rpy`"。"""

    def test_chapter_keeps_its_relative_folder(self, tmp_path):
        root, script = _project(tmp_path)
        assert VS.history_dir(script) == (root / '.storygraph' / 'versions'
                                          / 'chapter')

    def test_top_level_script_lands_at_the_versions_root(self, tmp_path):
        root, script = _project(tmp_path, rel='ch02.rpy')
        assert VS.history_dir(script) == root / '.storygraph' / 'versions'

    def test_non_script_and_non_project_files_have_no_history(self, tmp_path):
        root, script = _project(tmp_path)
        # 不是 .rpy
        assert VS.history_dir(root / 'game' / 'x.json') is None
        # 在项目里、但不在 game/ 下（build/ 里的产物不该留历史）
        (root / 'build').mkdir()
        assert VS.history_dir(root / 'build' / 'x.rpy') is None
        # 根本不在项目里
        loose = tmp_path / 'loose'
        loose.mkdir()
        assert VS.history_dir(loose / 'x.rpy') is None

    def test_explicit_project_root_is_trusted(self, tmp_path):
        root, script = _project(tmp_path)
        other = tmp_path / 'elsewhere'
        other.mkdir()
        (other / 'game').mkdir()
        got = VS.history_dir(script, project_root=other)
        # 传了别的根就按那个根算 —— 但脚本不在它的 game/ 下 → 不留历史
        assert got is None


# ----------------------------------------------------------------------
#  二、写之前存一份（这是本轮真正补的那半边）
# ----------------------------------------------------------------------

class TestSnapshot:
    def test_in_place_write_leaves_a_generation_and_nothing_in_game(self,
                                                                    tmp_path):
        root, script = _project(tmp_path)
        before = _files_under(root / 'game')
        write_text_atomic(script, LF.replace('第二句', '改了'))
        # `game/` 里一个文件都不许多
        assert _files_under(root / 'game') == before
        gens = VS.versions_of(script)
        assert len(gens) == 1
        # 那一代 = **写之前**的原话，逐字节（CRLF 还是 CRLF）
        assert gens[0].path.read_bytes() == CRLF.encode('utf-8')

    def test_second_write_adds_one_and_identical_write_adds_none(self, tmp_path):
        root, script = _project(tmp_path)
        write_text_atomic(script, LF.replace('第二句', '甲'))
        write_text_atomic(script, LF.replace('第二句', '乙'))
        assert len(VS.versions_of(script)) == 2
        # 再写一模一样的内容：这次写盘本身就是空转，不该白烧一代
        write_text_atomic(script, LF.replace('第二句', '乙'))
        assert len(VS.versions_of(script)) == 2

    def test_reverting_to_an_old_state_does_not_store_it_twice(self, tmp_path):
        """
        O → X → O → X：第三次要把 O 换掉，可 O **已经存过**（固定名那份原稿），
        不该再存一遍。这就是 `_already_stored` 比"只看最新一代"多做的那点事
        —— 只比最新一代会漏掉"更早的那几代里就有它"。
        """
        root, script = _project(tmp_path)
        x = CRLF.replace('第二句', '甲')
        write_text_atomic(script, x)                     # 存 O（固定名）
        assert len(VS.versions_of(script)) == 1
        write_text_atomic(script, CRLF)                  # 存 X（带时间戳）
        assert len(VS.versions_of(script)) == 2
        write_text_atomic(script, x)                     # O 已存过 → 不再存
        assert script.read_bytes() == x.encode('utf-8')
        assert len(VS.versions_of(script)) == 2, '把已经存过的内容又存了一代'

    def test_the_oldest_generation_is_never_pruned(self, tmp_path):
        root, script = _project(tmp_path)
        for i in range(8):
            write_text_atomic(script, LF.replace('第二句', f'第{i}版'))
        gens = VS.versions_of(script)
        assert len(gens) <= atomicio.KEEP_BACKUPS
        # 固定名那一份永远是**第一次动刀之前**的原稿
        fixed = [g for g in gens if g.kind == V.KIND_FIXED]
        assert len(fixed) == 1
        assert fixed[0].path.read_bytes() == CRLF.encode('utf-8')

    def test_history_folder_ignores_itself_in_git(self, tmp_path):
        root, script = _project(tmp_path)
        (root / '.gitignore').write_text('*.log\n', encoding='utf-8')
        write_text_atomic(script, LF)
        where = VS.history_dir(script)
        assert (where / '.gitignore').read_text(encoding='utf-8') == '*\n'
        # 用户自己的 .gitignore 一个字节都不许动
        assert (root / '.gitignore').read_text(encoding='utf-8') == '*.log\n'

    def test_whole_file_export_keeps_its_old_home(self, tmp_path):
        """`backup=True` 那条路照旧：`.rpy.bak` 在旁边，**不**再进历史目录。"""
        from storygraph.core.vocabulary import GENERATOR_MARKER
        from storygraph.story.generation_guard import write_generation
        root, script = _project(tmp_path)
        write_generation(GENERATOR_MARKER + '\n' + LF, script, force=True,
                         project_root=root)
        assert (script.parent / 'ch01.rpy.bak').is_file()
        assert VS.history_dir(script).exists() is False

    def test_nothing_to_snapshot_for_a_brand_new_file(self, tmp_path):
        root, script = _project(tmp_path)
        fresh = root / 'game' / 'chapter' / 'ch02.rpy'
        write_text_atomic(fresh, LF)
        assert VS.versions_of(fresh) == []
        assert not VS.history_dir(fresh).exists()

    def test_failed_backup_refuses_the_write(self, tmp_path):
        """
        **备份没成 → 文件一个字节不动**（与 `atomicio` 同一条规矩）。

        做法：让 `.storygraph` 是个**文件**，历史目录建不出来。
        """
        root, script = _project(tmp_path)
        (root / '.storygraph').write_text('我不是目录\n', encoding='utf-8')
        with pytest.raises(OSError):
            write_text_atomic(script, LF.replace('第二句', '改了'))
        assert script.read_bytes() == CRLF.encode('utf-8')


# ----------------------------------------------------------------------
#  三、列出来 / 放回去
# ----------------------------------------------------------------------

class TestListAndRestore:
    def test_listing_merges_both_homes_newest_first(self, tmp_path):
        root, script = _project(tmp_path)
        # 老机制留下的那一代（文件旁边）
        legacy = script.with_name(script.name + '.bak')
        legacy.write_bytes(b'oldest\n')
        os.utime(legacy, (1_600_000_000, 1_600_000_000))
        write_text_atomic(script, LF.replace('第二句', '甲'))
        gens = VS.versions_of(script)
        assert len(gens) == 2
        assert gens[0].path.parent == VS.history_dir(script)
        assert gens[-1].path == legacy

    def test_restore_is_byte_exact_and_lands_in_the_history_folder(self,
                                                                   tmp_path):
        root, script = _project(tmp_path)
        write_text_atomic(script, LF.replace('第二句', '甲'))
        before = _files_under(root / 'game')
        res = VS.restore_version(script, VS.versions_of(script)[0].path)
        assert res['ok'] and res['written']
        assert script.read_bytes() == CRLF.encode('utf-8')   # 逐字节，CRLF 还在
        assert _files_under(root / 'game') == before          # `game/` 没长东西
        # 现在这份进了新一代 → 放错了还能再放回去
        assert len(VS.versions_of(script)) == 2

    def test_restore_from_legacy_bak_also_writes_into_the_history_folder(
            self, tmp_path):
        root, script = _project(tmp_path)
        legacy = script.with_name(script.name + '.bak')
        legacy.write_bytes(b'label old:\r\n    return\r\n')
        res = VS.restore_version(script, legacy)
        assert res['ok'] and res['written']
        assert script.read_bytes() == b'label old:\r\n    return\r\n'
        assert [p for p in script.parent.glob('ch01.rpy.bak-*')] == []
        assert VS.history_dir(script).is_dir()

    def test_restore_refuses_when_the_file_changed_under_us(self, tmp_path):
        root, script = _project(tmp_path)
        write_text_atomic(script, LF.replace('第二句', '甲'))
        seen = atomicio.read_text_exact(script)
        script.write_bytes(b'label somebody_else:\n    return\n')
        res = VS.restore_version(script, VS.versions_of(script)[0].path,
                                 expect_current=seen)
        assert res['ok'] is False and res['changed'] is True
        assert script.read_bytes() == b'label somebody_else:\n    return\n'

    def test_a_non_utf8_generation_is_refused_not_corrupted(self, tmp_path):
        """GBK / 二进制那一代：**列出**（人看得见"有这么一代"），但**不放回**。"""
        root, script = _project(tmp_path)
        bad = VS.history_dir(script)
        bad.mkdir(parents=True, exist_ok=True)
        (bad / 'ch01.rpy.bak').write_bytes(b'\xd6\xd0\xce\xc4\n')   # GBK
        gens = VS.versions_of(script)
        assert len(gens) == 1
        res = VS.restore_version(script, gens[0].path)
        assert res['ok'] is False
        assert '不是 UTF-8' in res['reason']
        assert script.read_bytes() == CRLF.encode('utf-8')

    def test_summary_counts_lines(self):
        assert VS.summarize_script('a\nb\n')['note'] == '2 行 · 4 字'
        assert VS.summarize_script('a\r\nb')['note'] == '2 行 · 4 字'
        assert VS.summarize_script(None)['missing'] is True


# ----------------------------------------------------------------------
#  四、**真**调用点：不是拿 `write_text_atomic` 试管道，而是走一条真命令
# ----------------------------------------------------------------------

class TestRealInPlacePath:
    """
    "删掉源文件那一行"是原地小改家族里的一条真命令
    （`app/source_line_delete.py`）。这里走它的**真入口**，确认历史版本
    真的在用户按下去的时候出现 —— 只测 `write_text_atomic` 等于只测了管道，
    万一哪天某个命令改回 `atomicio` 直写，管道测试照样全绿。
    """

    MENU_TWO = '''label ch01:
    e "开始"
    menu:
        "去屋顶":
            jump roof
        "留在教室":
            e "留下"
    e "继续"
    return

label roof:
    e "屋顶"
    return
'''

    def test_deleting_a_line_keeps_the_previous_script(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _ui_project import build_project
        from storygraph.app.editor import EditorSession
        from storygraph.app.source_line_delete import (
            apply_source_line_delete, plan_source_line_delete)

        root = build_project(tmp_path / 'proj')
        script = root / 'game' / 'script' / 'ch01.rpy'
        # 逐字节写：`Path.write_text` 在 Windows 上会把 `\n` 翻成 `\r\n`，
        # 拿它造样本，测出来的"逐字节"就不是我写的那些字节了。
        script.write_bytes(self.MENU_TWO.replace('\n', '\r\n').encode('utf-8'))
        original = script.read_bytes()
        before = _files_under(root / 'game')
        session = EditorSession(project_root=root)
        session.load(script)
        beat = next(b for b in session.beats
                    if str(getattr(b, 'raw', '') or '').strip() == 'jump roof')
        plan = plan_source_line_delete(root, script, beat.line_id)
        assert plan.ok, plan.reason
        assert apply_source_line_delete(plan)['ok']

        gens = VS.versions_of(script)
        assert gens, '删掉一行之后磁盘上一个旧版本都没有'
        assert gens[0].path.read_bytes() == original
        assert _files_under(root / 'game') == before


# ----------------------------------------------------------------------
#  五、窗口（真 Qt：列表、预览、**真点按钮**）
# ----------------------------------------------------------------------

class TestDialog:
    def _dialog(self, qapp, script, **kw):
        from storygraph.ui_qt.dialog_versions import VersionsDialog
        kw.setdefault('summarize', VS.summarize_script)
        kw.setdefault('store', VS.SCRIPT_STORE)
        dlg = VersionsDialog(None, script, **kw)
        return dlg

    def test_rows_preview_and_restore_by_real_click(self, qapp, tmp_path):
        root, script = _project(tmp_path)
        write_text_atomic(script, LF.replace('第二句', '甲'))
        dlg = self._dialog(qapp, script)
        assert dlg.table.rowCount() == 1
        assert '行' in dlg.table.item(0, 3 - 1).text()
        dlg.table.selectRow(0)
        assert '第二句' in dlg.preview.toPlainText()
        assert dlg.btn_restore.isEnabled()
        dlg.btn_restore.click()                     # 真点：槽是死的就抓不到
        assert script.read_bytes() == CRLF.encode('utf-8')
        assert '还能再放回' in dlg.status.text()
        dlg.close()

    def test_guard_blocks_the_restore_before_touching_the_file(self, qapp,
                                                               tmp_path):
        root, script = _project(tmp_path)
        write_text_atomic(script, LF.replace('第二句', '甲'))
        why = '内存里还有没导出的改动'
        dlg = self._dialog(qapp, script, before_restore=lambda: why)
        dlg.table.selectRow(0)
        res = dlg.restore_selected()
        assert res['ok'] is False and why in res['reason']
        assert script.read_bytes() == LF.replace('第二句', '甲').encode('utf-8')
        assert '没放回' in dlg.status.text()
        dlg.close()

    def test_non_utf8_row_does_not_blow_up_the_window(self, qapp, tmp_path):
        """`UnicodeDecodeError` 从前会从槽里抛出去 = 界面毫无反应。"""
        root, script = _project(tmp_path)
        bad = VS.history_dir(script)
        bad.mkdir(parents=True, exist_ok=True)
        (bad / 'ch01.rpy.bak').write_bytes(b'\xd6\xd0\xce\xc4\n')
        dlg = self._dialog(qapp, script)
        assert dlg.table.rowCount() == 1
        assert 'UTF-8' in dlg.table.item(0, 2).text()
        dlg.table.selectRow(0)
        assert 'UTF-8' in dlg.preview.toPlainText()
        res = dlg.restore_selected()
        assert res['ok'] is False
        dlg.close()

    def test_hint_says_where_the_generations_live(self, qapp, tmp_path):
        from storygraph.ui_qt.window_recover import RecoverMixin
        root, script = _project(tmp_path)
        dlg = self._dialog(qapp, script,
                           hint=RecoverMixin.SCRIPT_VERSION_HINT)
        assert '.storygraph/versions' in dlg.findChildren(type(dlg.status))[0].text()
        dlg.close()


# ----------------------------------------------------------------------
#  五、窗口接线（菜单真的有入口、入口真的带着这三样东西）
# ----------------------------------------------------------------------

class _Host(RecoverMixin):
    """
    只提供 `RecoverMixin` 用到的那几个宿主成员（不打桩整个窗口）。

    真继承那个 mixin：方法里的 `self` 必须真是宿主 —— 用一个"转发对象"
    包一层，`self._status` 就会落到转发对象上（第一次写成那样，当场红）。
    """

    def __init__(self, script, dirty=False):
        class _Session:
            def __init__(self):
                self.script_path = script
                self.beats = [1, 2, 3]
                self._dirty = dirty

            def has_unsaved_edits(self):
                return self._dirty
        self._session = _Session()
        self._project_root = Path(script).parents[2]
        self.statuses = []
        self.logs = []
        self.loaded = []

    def _status(self, text):
        self.statuses.append(text)

    def _log(self, text):
        self.logs.append(text)

    def load_script(self, path, goto_card=True, label=None):
        self.loaded.append((str(path), goto_card))


class TestWindowWiring:
    def test_more_tools_menu_has_the_entry(self, ui_window):
        texts = [a.text() for a in ui_window._more_tools_menu().actions()]
        assert any('这个脚本的旧版本' in t for t in texts), texts

    def test_the_menu_action_is_wired_to_the_right_method(self, ui_window,
                                                          monkeypatch):
        """
        **菜单项真的连到 `show_script_versions`**。

        只断言"菜单里有这行字"是不够的（第九轮两个死按钮就是这么全绿的）：
        文字在、槽接到了别的方法上，用户看到的仍然是"点了没反应/点出别的东西"。
        所以这里把两个候选槽都换成记号的 lambda，**真 trigger 一下**。
        """
        calls = []
        monkeypatch.setattr(ui_window, 'show_script_versions',
                            lambda: calls.append('script_versions'))
        monkeypatch.setattr(ui_window, 'show_recovery',
                            lambda: calls.append('recovery'))
        menu = ui_window._more_tools_menu()
        act = next(a for a in menu.actions()
                   if '这个脚本的旧版本' in a.text())
        act.trigger()
        assert calls == ['script_versions']

    def test_the_entry_passes_the_three_injections(self, tmp_path, monkeypatch):
        root, script = _project(tmp_path)
        host = _Host(script)
        seen = {}

        class _FakeDialog:
            def __init__(self, parent=None, path=None, **kw):
                seen['path'] = path
                seen.update(kw)

            def exec(self):
                return 0

            class restored:
                @staticmethod
                def connect(fn):
                    seen['slot'] = fn
        monkeypatch.setattr('storygraph.ui_qt.dialog_versions.VersionsDialog',
                            _FakeDialog)
        host.show_script_versions()
        assert seen['path'] == script
        assert seen['store'] is VS.SCRIPT_STORE
        assert seen['before_restore'] == host._script_restore_guard
        assert '.storygraph/versions' in seen['hint']

    def test_guard_speaks_up_only_when_dirty(self, tmp_path):
        root, script = _project(tmp_path)
        assert _Host(script)._script_restore_guard() is None
        why = _Host(script, dirty=True)._script_restore_guard()
        assert '没导出的改动' in why

    def test_after_restore_reloads_and_says_so(self, tmp_path):
        root, script = _project(tmp_path)
        host = _Host(script)
        host._after_script_restored({'ok': True, 'written': True,
                                    'path': str(script),
                                    'version': str(script) + '.bak'})
        assert host.loaded and host.loaded[0][0] == str(script)
        assert '已放回' in host.statuses[-1] and '3 拍' in host.statuses[-1]

    def test_a_refused_restore_does_not_reload(self, tmp_path):
        root, script = _project(tmp_path)
        host = _Host(script)
        host._after_script_restored({'ok': False, 'written': False})
        assert host.loaded == []
