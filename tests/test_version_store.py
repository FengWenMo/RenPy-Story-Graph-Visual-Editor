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
import os
import stat
import time
from pathlib import Path

import pytest

from storygraph.core import atomicio
from storygraph.core import versions as V
from storygraph.story import version_store as VS
from storygraph.story.paths import write_text_atomic
from storygraph.ui_qt.window_recover import RecoverMixin

CRLF = 'label ch01:\r\n    "第一句"\r\n    "第二句"\r\n    return\r\n'
LF = CRLF.replace('\r\n', '\n')


def _age(path, seconds):
    """把某一份备份的 mtime 往前拨 `seconds` 秒（伪造"这是多久以前写的"）。"""
    t = time.time() - float(seconds)
    os.utime(path, (t, t))


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
#  二半、留多久：最近的几次 + **时间台阶**（跨天的版本不许全被裁掉）
# ----------------------------------------------------------------------

class TestRetention:
    """
    纯按次数裁剪对**脚本**不够用：探针实测连改 30 次，5 代备份跨 1.1 秒；
    跨几天各改十次之后，"昨天那一版"一个都不剩。这里钉住补上的时间台阶。
    """

    def test_an_old_generation_survives_a_busy_day(self, tmp_path):
        root, script = _project(tmp_path)
        for i in range(3):                       # 上午改三次
            write_text_atomic(script, CRLF.replace('第二句', f'上午{i}'))
        for ver in VS.versions_of(script):       # 把这几代拨成"两小时前"
            _age(ver.path, 2 * 3600)
        old = {v.path for v in VS.versions_of(script)
               if time.time() - v.written > 3600}
        assert old, '样本没造出来（没有"两小时前"的世代）'
        for i in range(12):                      # 下午连改十二次
            write_text_atomic(script, CRLF.replace('第二句', f'下午{i}'))
        gens = VS.versions_of(script)
        assert len(gens) > atomicio.KEEP_BACKUPS, '台阶没起作用：还是只有最近那几代'
        assert old & {v.path for v in gens}, '两小时前那几代被裁光了'

    def test_the_original_does_not_eat_every_step(self, tmp_path):
        """
        固定名那份**永远留着、而且通常最老** —— 它不能参与挑台阶。

        第一版就是这么写错的：每个台阶都在"最新的、够老的"里命中它自己，
        于是台阶一个版本也没多留（跨天的那几代照样被裁掉）。
        """
        root, script = _project(tmp_path)
        for i in range(4):                       # 造几代"带时间戳的旧版本"
            write_text_atomic(script, CRLF.replace('第二句', f'中{i}'))
        for ver in VS.versions_of(script):
            if ver.kind == V.KIND_STAMPED:       # 只拨带时间戳的
                _age(ver.path, 6 * 3600)
        for i in range(12):                      # 再改一堆，把最近几代挤满
            write_text_atomic(script, CRLF.replace('第二句', f'新{i}'))
        gens = VS.versions_of(script)
        old_stamps = [v for v in gens
                      if v.kind == V.KIND_STAMPED
                      and time.time() - v.written > 3600]
        assert old_stamps, '台阶只命中了固定名那份 —— 跨天版本一个都没留下'

    def test_generations_are_still_bounded(self, tmp_path):
        """台阶有上限：不可能越攒越多（每份脚本最多多 6 个老世代）。"""
        root, script = _project(tmp_path)
        for i in range(30):
            write_text_atomic(script, CRLF.replace('第二句', f'第{i}'))
            if i % 5 == 4:
                for ver in VS.versions_of(script):
                    _age(ver.path, (i // 5 + 1) * 3600)
        gens = VS.versions_of(script)
        assert len(gens) <= atomicio.KEEP_BACKUPS + len(VS.KEEP_AGES), \
            [v.name for v in gens]

    def test_hint_says_how_long_we_keep(self):
        hint = VS.retention_hint()
        assert '最近 5 次' in hint and '1 小时前' in hint and '14 天前' in hint


# ----------------------------------------------------------------------
#  二又半、占用与清理（要能说清"占多大"，也要能一键清掉）
# ----------------------------------------------------------------------

class TestProjectHistory:
    def _make(self, tmp_path):
        root, script = _project(tmp_path)
        (root / 'game' / 'chapter' / 'ch02.rpy').write_text(LF, encoding='utf-8')
        for i in range(3):
            write_text_atomic(script, CRLF.replace('第二句', f'甲{i}'))
        other = root / 'game' / 'chapter' / 'ch02.rpy'
        write_text_atomic(other, LF.replace('第二句', '乙'))
        return root, script, other

    def test_counts_files_bytes_scripts_and_days(self, tmp_path):
        root, script, other = self._make(tmp_path)
        info = VS.project_history(root)
        assert info['files'] == 4            # 3 + 1
        assert info['scripts'] == 2          # 两个脚本
        assert info['bytes'] > 0
        assert info['days'] >= 1
        assert info['oldest'] and info['newest']
        # `.gitignore` 不算版本文件
        assert VS.history_root(root).is_dir()

    def test_nothing_to_count_on_a_fresh_project(self, tmp_path):
        root, script = _project(tmp_path)
        info = VS.project_history(root)
        assert info == {'dir': str(VS.history_root(root)), 'files': 0,
                        'bytes': 0, 'scripts': 0, 'days': 0,
                        'oldest': None, 'newest': None}

    def test_clear_removes_only_the_history(self, tmp_path):
        root, script, other = self._make(tmp_path)
        (root / '.storygraph' / 'layout.json').write_text('{}', encoding='utf-8')
        game_before = _files_under(root / 'game')
        res = VS.clear_history(root)
        assert res['removed'] == 4 and res['failed'] == []
        assert VS.project_history(root)['files'] == 0
        # 剧本一个字节没动、别的元数据也留着
        assert _files_under(root / 'game') == game_before
        assert (root / '.storygraph' / 'layout.json').is_file()
        # 清完还能继续用（下一次写盘会重新建目录并留一代）
        write_text_atomic(script, CRLF.replace('第二句', '清完之后'))
        assert VS.project_history(root)['files'] == 1

    def test_a_symlink_in_the_history_is_not_followed(self, tmp_path,
                                                      monkeypatch):
        """
        符号链接不跟：删它没事，但**不能**顺着它去删项目外面的东西。

        Windows 默认建不了符号链接（要开发者模式），所以建不出来时改成
        "假装它是链接"—— 这样这条判据在 Windows 上也真的跑（不许 skip：
        跳过一次，`is_symlink` 那道闸就永远没人验）。
        """
        root, script = _project(tmp_path)
        write_text_atomic(script, CRLF.replace('第二句', '甲'))
        outside = tmp_path / 'outside.txt'
        outside.write_text('别动我', encoding='utf-8')
        link = VS.history_root(root) / 'chapter' / 'looks-like-a-version.txt'
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            link.write_text('历史目录里的一个普通文件', encoding='utf-8')
            monkeypatch.setattr(type(link), 'is_symlink',
                                lambda self: self == link)
        assert VS.project_history(root)['files'] == 1     # 只数真的版本文件
        VS.clear_history(root)
        assert outside.read_text(encoding='utf-8') == '别动我'

    def test_human_size(self):
        assert V.human_size(0) == '0 B'
        assert V.human_size(999) == '999 B'
        assert V.human_size(2048) == '2 KB'
        assert V.human_size(3 * 1024 * 1024) == '3.0 MB'
        # 算不出来就说"?"，别假装 0（"0 B" 会让人以为历史被清空了）
        assert V.human_size(None) == '?'
        assert V.human_size('不是数字') == '?'


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

    def test_the_cleanup_action_is_wired_too(self, ui_window, monkeypatch):
        calls = []
        monkeypatch.setattr(ui_window, 'show_history_cleanup',
                            lambda: calls.append('cleanup'))
        menu = ui_window._more_tools_menu()
        act = next(a for a in menu.actions() if '历史版本（占多少' in a.text())
        act.trigger()
        assert calls == ['cleanup']

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


# ----------------------------------------------------------------------
#  六、清理入口（"占多少"要说得清，"清掉"要问过）
# ----------------------------------------------------------------------

class _FakeBox:
    """
    `QMessageBox` 的替身：记下文案，按 `click` 返回"用户点了哪个"。

    它得**同时**当类用（`QMessageBox.Information` / `DestructiveRole` 是类属性，
    生产代码在调 `addButton` 时要用到），所以这里直接做成"可调用的类"。
    """

    Information = 0
    DestructiveRole = 1
    RejectRole = 2

    def __init__(self, click=None):
        self._click = click
        self.buttons = {}
        self.text = ''
        self.info = ''
        self.title = ''

    def __call__(self, parent=None):
        return self

    def setWindowTitle(self, text):
        self.title = text

    def setIcon(self, icon):            # noqa: N802 - Qt 风格
        pass

    def setText(self, text):            # noqa: N802
        self.text = text

    def setInformativeText(self, text):  # noqa: N802
        self.info = text

    def addButton(self, text, role):    # noqa: N802
        self.buttons[text] = object()
        return self.buttons[text]

    def exec(self):                     # noqa: N802
        return 0

    def clickedButton(self):            # noqa: N802
        return self.buttons.get(self._click)


class TestHistoryCleanup:
    def _host_with_history(self, tmp_path):
        root, script = _project(tmp_path)
        for i in range(3):
            write_text_atomic(script, CRLF.replace('第二句', f'甲{i}'))
        host = _Host(script)
        host._project_root = root
        return host, root, script

    def test_summary_is_a_readable_account(self, tmp_path):
        from storygraph.ui_qt.window_recover import history_summary
        host, root, script = self._host_with_history(tmp_path)
        text = history_summary(VS.project_history(root))
        assert '3 个旧版本文件' in text
        assert '1 个脚本' in text
        assert ' B' in text and '最早到' in text
        # 空项目也是一句话，不是空白
        assert '还没有' in history_summary(VS.project_history(tmp_path / 'nope'))

    def test_empty_project_says_so_and_shows_no_box(self, tmp_path,
                                                    monkeypatch):
        host = _Host(_project(tmp_path)[1])
        host._project_root = tmp_path / 'proj'
        monkeypatch.setattr('storygraph.ui_qt.window_recover.QMessageBox',
                            lambda *a: pytest.fail('不该弹框'))
        assert host.show_history_cleanup() == 'empty'
        assert '还没有历史版本' in host.statuses[-1]

    def test_clicking_delete_really_clears(self, tmp_path, monkeypatch):
        host, root, script = self._host_with_history(tmp_path)
        box = _FakeBox(click='全部删掉')
        monkeypatch.setattr('storygraph.ui_qt.window_recover.QMessageBox', box)
        assert host.show_history_cleanup() == 'cleared'
        assert VS.project_history(root)['files'] == 0
        assert '3 个旧版本文件' in box.text        # 先摊开账，再动手
        assert '剧本本身不受影响' in box.info
        assert '已删掉 3 个' in host.statuses[-1]
        # 剧本一个字节没动
        assert script.read_bytes() == CRLF.replace('第二句', '甲2').encode('utf-8')

    def test_closing_the_box_keeps_everything(self, tmp_path, monkeypatch):
        host, root, script = self._host_with_history(tmp_path)
        monkeypatch.setattr('storygraph.ui_qt.window_recover.QMessageBox',
                            _FakeBox(click='关闭'))
        assert host.show_history_cleanup() == 'cancelled'
        assert VS.project_history(root)['files'] == 3

    def test_no_project_is_refused_politely(self, tmp_path):
        host = _Host(_project(tmp_path)[1])
        host._project_root = None
        assert host.show_history_cleanup() == 'no-project'
        assert '先打开一个项目' in host.statuses[-1]

    def test_script_hint_says_both_where_and_how_long(self, tmp_path):
        host = _Host(_project(tmp_path)[1])
        hint = host._script_version_hint()
        assert '.storygraph/versions' in hint
        assert '留多少' in hint and '1 小时前' in hint

    def test_dialog_status_shows_the_total_size(self, qapp, tmp_path):
        root, script = _project(tmp_path)
        for i in range(2):
            write_text_atomic(script, CRLF.replace('第二句', f'甲{i}'))
        from storygraph.ui_qt.dialog_versions import VersionsDialog
        dlg = VersionsDialog(None, script, summarize=VS.summarize_script,
                             store=VS.SCRIPT_STORE)
        assert '共 ' in dlg.status.text() and 'B' in dlg.status.text()
        dlg.close()
