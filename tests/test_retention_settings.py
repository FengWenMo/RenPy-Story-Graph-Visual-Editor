# -*- coding: utf-8 -*-
"""
tests/test_retention_settings.py
================================
**留多久可以改**（第十三轮）—— 脚本旧版本的档位：设置文件、三档的账、生效时机。

探针 `.pip-tmp/probe_r13_settings.py` 量到四件事，这里逐条钉住：

  ① 上一轮的代数 / 台阶是**模块常量**：默认档在真项目（95 个 `.rpy`、1.26 MB）
     里最多约 15 MB，而"改小 / 改大"在源码层面只能改代码；
  ② 读一次设置 0.2 ms 量级 → **不值得加缓存**，于是每一代都读，改完立刻生效；
  ③ 改小**下一次写盘**才裁（不会当场删东西），改大**不补回**已经删掉的；
  ④ 设置坏了 / 缺了都**不许**影响写盘 —— 偏好最坏只能让"这次用默认"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from storygraph.core import atomicio
from storygraph.core import user_prefs
from storygraph.story import version_store as VS
from storygraph.story.paths import write_text_atomic
from storygraph.ui_qt.window_recover import RecoverMixin

ROOT = Path(__file__).resolve().parents[1]

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


def _set(**patch):
    """改一节设置（`None` = 删掉那个键）。"""
    return user_prefs.update(VS.SETTINGS_SECTION, patch)


def _gens(script):
    return VS.versions_of(script)


# ======================================================================
#  一、设置文件本身（读容错、写合并、锁里读改写）
# ======================================================================

class TestPrefsFile:
    def test_a_missing_file_is_not_an_error(self):
        data, state = user_prefs.read_state()
        assert data == {} and state == 'missing'

    def test_broken_json_is_reported_separately(self):
        """坏文件 ≠ 没设置过：前者是别人（用户）写的东西，不能当白纸。"""
        user_prefs.path().write_text('{"backups": ', encoding='utf-8')
        data, state = user_prefs.read_state()
        assert state == 'corrupt' and data == {}
        # `read()` 只给数据：读路径上坏文件 = 没有设置
        assert user_prefs.read() == {}

    def test_a_json_list_is_corrupt_too(self):
        user_prefs.path().write_text('[1, 2]', encoding='utf-8')
        assert user_prefs.read_state()[1] == 'corrupt'

    def test_update_writes_and_reads_back(self):
        user_prefs.update('backups', {'preset': 'lean'})
        assert user_prefs.read()['backups']['preset'] == 'lean'
        # 末尾换行 + 中文不转义（人能直接读）
        text = user_prefs.path().read_text(encoding='utf-8')
        assert text.endswith('\n') and '\\u' not in text

    def test_update_keeps_other_sections_and_unknown_keys(self):
        """只改你给的那一节、那几个键 —— 别人写的原样留着。"""
        user_prefs.path().write_text(json.dumps({
            'backups': {'preset': 'more', 'note_by_hand': '我写的'},
            'someone_else': {'x': 1},
        }, ensure_ascii=False), encoding='utf-8')
        user_prefs.update('backups', {'preset': 'lean'})
        data = user_prefs.read()
        assert data['backups']['preset'] == 'lean'
        assert data['backups']['note_by_hand'] == '我写的'
        assert data['someone_else'] == {'x': 1}

    def test_none_in_patch_removes_the_key(self):
        user_prefs.update('backups', {'keep': 9})
        user_prefs.update('backups', {'keep': None})
        assert 'keep' not in user_prefs.read()['backups']

    def test_a_corrupt_file_is_never_overwritten(self):
        raw = '{"backups": 这不是 JSON'
        user_prefs.path().write_text(raw, encoding='utf-8')
        with pytest.raises(user_prefs.CorruptSettings) as exc:
            user_prefs.update('backups', {'preset': 'lean'})
        # 报错要带路径（用户得知道去删哪个文件）
        assert str(user_prefs.path()) in str(exc.value)
        assert user_prefs.path().read_text(encoding='utf-8') == raw

    def test_no_temp_files_are_left_behind(self):
        user_prefs.update('backups', {'preset': 'lean'})
        leftovers = [p.name for p in user_prefs.path().parent.iterdir()
                     if 'tmp' in p.name]
        assert leftovers == []

    def test_two_processes_writing_different_sections_keep_both(self, tmp_path):
        """
        **真并发**：两个进程同时写不同的节 —— 后写的那个不许把先写的抹掉。

        没有"锁里读→合并→写"时，两个进程各自读到空文件、各写一节，
        结果是**只剩一个**（另一个的设置在用户看来"没生效"）。
        """
        target = user_prefs.path()
        child = tmp_path / 'child.py'
        child.write_text(textwrap.dedent('''
            import sys
            sys.path.insert(0, {root!r})
            from storygraph.core import user_prefs
            user_prefs.update(sys.argv[1], {'who': sys.argv[2]})
        ''').replace('{root!r}', repr(str(ROOT))), encoding='utf-8')
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT),
                   **{user_prefs.ENV_CONFIG: str(target)})
        procs = [subprocess.Popen([sys.executable, str(child), name, name],
                                  env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding='utf-8')
                 for name in ('alpha', 'beta')]
        outs = [p.communicate(timeout=120)[0] for p in procs]
        assert all(p.returncode == 0 for p in procs), outs
        data = json.loads(target.read_text(encoding='utf-8'))
        assert data['alpha'] == {'who': 'alpha'}, data
        assert data['beta'] == {'who': 'beta'}, data


# ======================================================================
#  二、规矩怎么算（档位、手改值、看不懂的东西）
# ======================================================================

class TestRule:
    def test_default_is_five_generations_plus_six_steps(self):
        rule = VS.retention()
        assert rule['preset'] == 'default'
        assert rule['keep'] == atomicio.KEEP_BACKUPS
        assert tuple(rule['ages']) == VS.KEEP_AGES
        assert rule['problems'] == [] and rule['custom'] is False

    def test_lean_is_three_and_no_steps(self):
        _set(preset='lean')
        rule = VS.retention()
        assert (rule['keep'], rule['ages']) == (3, ())
        assert VS.max_files(rule['keep'], rule['ages']) == 3

    def test_more_keeps_more_than_default(self):
        _set(preset='more')
        rule = VS.retention()
        assert rule['keep'] > atomicio.KEEP_BACKUPS
        assert len(rule['ages']) > len(VS.KEEP_AGES)
        assert VS.max_files(rule['keep'], rule['ages']) > \
            VS.max_files(atomicio.KEEP_BACKUPS, VS.KEEP_AGES)

    def test_unknown_preset_falls_back_and_says_so(self):
        _set(preset='巨大')
        rule = VS.retention()
        assert rule['preset'] == 'default'
        assert any('档位' in p for p in rule['problems'])

    def test_a_word_instead_of_a_number_is_reported(self):
        _set(keep='很多')
        rule = VS.retention()
        assert rule['keep'] == atomicio.KEEP_BACKUPS
        assert any('留几代' in p for p in rule['problems'])

    def test_a_huge_number_is_clamped_not_obeyed(self):
        _set(keep=999)
        rule = VS.retention()
        assert rule['keep'] == VS.KEEP_MAX
        assert rule['custom'] is True
        assert any(str(VS.KEEP_MAX) in p for p in rule['problems'])

    def test_true_is_not_a_number(self):
        """`True == 1` 在 Python 里成立 —— 写 `true` 的人想的是"开着"，不是"留 1 代"。"""
        _set(keep=True)
        rule = VS.retention()
        assert rule['keep'] == atomicio.KEEP_BACKUPS

    def test_bad_step_entries_are_skipped_one_by_one(self):
        _set(preset='lean', ages=[3600, 'x', -5, 0, 86400])
        rule = VS.retention()
        assert tuple(rule['ages']) == (3600.0, 86400.0)
        assert len(rule['problems']) == 3

    def test_too_many_steps_are_cut_to_the_limit(self):
        many = [60 * (i + 1) for i in range(VS.AGE_STEPS_MAX + 5)]
        _set(preset='lean', ages=many)
        rule = VS.retention()
        assert len(rule['ages']) == VS.AGE_STEPS_MAX
        # 留下的是**最密的**那几档（离现在最近），不是随便砍
        assert tuple(rule['ages']) == tuple(float(s) for s in
                                            many[:VS.AGE_STEPS_MAX])

    def test_hand_written_keep_zero_means_original_only(self):
        """界面不提供"一个都不留"，但文件里写 `keep: 0` 是"只留原件"。"""
        _set(preset='lean', keep=0)
        rule = VS.retention()
        assert rule['keep'] == 0 and rule['custom'] is True
        assert VS.max_files(0, ()) == 1

    def test_save_clears_hand_written_values(self):
        _set(preset='lean', keep=7, ages=[7200])
        assert VS.retention()['custom'] is True
        rule = VS.save_retention('default')
        assert rule['custom'] is False
        assert rule['keep'] == atomicio.KEEP_BACKUPS
        assert 'keep' not in user_prefs.read()[VS.SETTINGS_SECTION]

    def test_save_refuses_a_name_it_does_not_know(self):
        with pytest.raises(KeyError):
            VS.save_retention('huge')

    def test_hint_follows_the_rule(self):
        assert '1 小时前' in VS.retention_hint() and '14 天前' in VS.retention_hint()
        _set(preset='lean')
        hint = VS.retention_hint()
        assert '最近 3 次' in hint and '小时前' not in hint
        _set(preset='lean', keep=0)
        assert '只留最初那份原件' in VS.retention_hint()

    def test_max_files_matches_what_is_really_kept(self, tmp_path):
        """算出来的"最多几个"必须是**真会发生**的数目（否则界面上那笔账是假的）。"""
        _set(preset='lean')
        root, script = _project(tmp_path)
        for i in range(6):
            write_text_atomic(script, LF.replace('第二句', f'第{i}版'))
        assert len(_gens(script)) == VS.max_files(3, ()) == 3


# ======================================================================
#  三、生效时机（改小不立刻删、改大不补回、坏设置不拦写盘）
# ======================================================================

class TestTakesEffect:
    def test_shrinking_does_not_delete_before_the_next_write(self, tmp_path):
        """改档位**不当场删东西** —— 那是用户"下一次改脚本"时才发生的事。"""
        root, script = _project(tmp_path)
        for i in range(6):
            write_text_atomic(script, LF.replace('第二句', f'第{i}版'))
        before = len(_gens(script))
        assert before > 3
        _set(preset='lean')                     # 只是改设置
        assert len(_gens(script)) == before, '改设置当场删了东西'

    def test_shrinking_applies_at_the_next_write(self, tmp_path):
        root, script = _project(tmp_path)
        for i in range(6):
            write_text_atomic(script, LF.replace('第二句', f'第{i}版'))
        _set(preset='lean')
        write_text_atomic(script, LF.replace('第二句', '改完这一下'))
        assert len(_gens(script)) == 3

    def test_growing_does_not_bring_back_what_was_deleted(self, tmp_path):
        root, script = _project(tmp_path)
        for i in range(6):
            write_text_atomic(script, LF.replace('第二句', f'第{i}版'))
        _set(preset='lean')
        write_text_atomic(script, LF.replace('第二句', '挤掉旧的'))
        shrunk = len(_gens(script))
        _set(preset='default')
        write_text_atomic(script, LF.replace('第二句', '再写一次'))
        # 只多这一代（4 个），不是"变回 11 个"
        assert len(_gens(script)) == shrunk + 1

    def test_a_corrupt_file_does_not_block_the_write(self, tmp_path):
        root, script = _project(tmp_path)
        user_prefs.path().write_text('{坏掉了', encoding='utf-8')
        write_text_atomic(script, LF.replace('第二句', '照写'))
        assert script.read_bytes() == LF.replace('第二句', '照写').encode('utf-8')
        assert len(_gens(script)) == 1           # 按默认规矩照样留了一代
        rule = VS.retention()
        assert any('读不懂' in p for p in rule['problems'])

    def test_missing_settings_means_defaults(self, tmp_path):
        root, script = _project(tmp_path)
        assert user_prefs.read_state()[1] == 'missing'
        for i in range(8):
            write_text_atomic(script, LF.replace('第二句', f'第{i}版'))
        # 默认 5 代（这里没有够老的世代，台阶不额外留）
        assert len(_gens(script)) == atomicio.KEEP_BACKUPS

    def test_explicit_keep_overrides_the_settings(self, tmp_path):
        """判据里要造"只留 2 代"的场景时，`keep=` 直接覆盖（台阶一起关掉）。"""
        _set(preset='default')
        root, script = _project(tmp_path)
        for i in range(5):
            VS.snapshot(script, keep=2)
            atomicio.write_text_atomic(
                script, LF.replace('第二句', f'第{i}版'))
        assert len(_gens(script)) <= 2


# ======================================================================
#  四、项目账（"这个项目最多占多少"要算得出来）
# ======================================================================

class TestProjectScripts:
    def test_counts_only_rpy_under_game(self, tmp_path):
        """只数 `game/` 下的 `.rpy`：`.bak`（后缀不同）、别的后缀、项目根外都不算。"""
        root, script = _project(tmp_path)
        (root / 'game' / 'chapter' / 'ch02.rpy').write_text(LF, encoding='utf-8')
        (root / 'game' / 'chapter' / 'ch01.rpy.bak').write_text('x',
                                                                encoding='utf-8')
        (root / 'game' / 'note.txt').write_text('x', encoding='utf-8')
        (root / 'outside.rpy').write_text('x', encoding='utf-8')
        got = VS.project_scripts(root)
        assert got['count'] == 3                 # ch01 + ch02 + script.rpy
        assert got['bytes'] >= len(CRLF) * 2

    def test_an_empty_project_is_zero(self, tmp_path):
        assert VS.project_scripts(tmp_path / 'nope') == {'count': 0, 'bytes': 0}


# ======================================================================
#  五、窗口（三档都在、账算得对、真的存进去）
# ======================================================================

def _dialog(**kw):
    from storygraph.ui_qt.dialog_retention import RetentionDialog
    return RetentionDialog(None, **kw)


def _radios(dlg):
    from PySide6.QtWidgets import QRadioButton
    return {r.text(): r for r in dlg.findChildren(QRadioButton)}


def _button(dlg, text):
    from PySide6.QtWidgets import QPushButton
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    raise AssertionError(f'窗口上没有「{text}」按钮')


class TestDialog:
    def test_three_presets_and_the_current_one_is_checked(self, qapp):
        dlg = _dialog()
        radios = _radios(dlg)
        labels = [str(VS.PRESETS[p]['label']) for p in VS.PRESETS]
        assert [r for r in radios] == labels, radios
        assert radios['默认'].isChecked()
        assert dlg.chosen() == 'default'
        dlg.close()

    def test_each_preset_shows_what_it_costs(self, qapp):
        from storygraph.ui_qt.dialog_retention import preset_detail
        text = preset_detail('default', {'bytes': 1324466})
        assert '每份脚本最多 11 个版本' in text and '13.9 MB' in text
        lean = preset_detail('lean', {'bytes': 1324466})
        assert '每份脚本最多 3 个版本' in lean and '3.8 MB' in lean
        # 没有项目：不许硬编一个数出来，明说等打开项目再算
        assert '打开一个项目' in preset_detail('lean', None)

    def test_clicking_ok_really_saves(self, qapp):
        """**真点按钮**（不是调 `_on_ok`）：死按钮的教训在第九轮。"""
        dlg = _dialog()
        _radios(dlg)['省地方'].click()
        _button(dlg, '确定').click()
        assert user_prefs.read()[VS.SETTINGS_SECTION]['preset'] == 'lean'
        assert dlg.chosen() == 'lean'

    def test_a_hand_written_rule_is_shown_but_not_selectable(self, qapp):
        _set(preset='lean', keep=2)
        dlg = _dialog()
        radios = _radios(dlg)
        assert '自定义（设置文件里手改的）' in radios
        assert radios['自定义（设置文件里手改的）'].isEnabled() is False
        assert dlg.chosen() is None              # 选它 = 什么都不改
        raw = user_prefs.path().read_bytes()
        _button(dlg, '确定').click()
        assert user_prefs.path().read_bytes() == raw

    def test_a_corrupt_file_keeps_the_dialog_open(self, qapp, monkeypatch):
        """存不进去**不许**静默关窗（用户会以为改好了）。"""
        from PySide6.QtWidgets import QDialog
        raw = '{坏掉了'
        user_prefs.path().write_text(raw, encoding='utf-8')
        said = []

        class _FakeBox:
            @staticmethod
            def warning(parent, title, text):        # noqa: N802 - Qt 风格
                said.append(text)

        monkeypatch.setattr('storygraph.ui_qt.dialog_retention.QMessageBox',
                            _FakeBox)
        dlg = _dialog()
        _button(dlg, '确定').click()
        assert dlg.result() != QDialog.Accepted
        assert said and str(user_prefs.path()) in said[0]
        assert user_prefs.path().read_text(encoding='utf-8') == raw

    def test_no_markdown_leaks_into_the_labels(self, qapp):
        """
        `QLabel` **不渲染 markdown**：`**` 与反引号会原样显示。

        第十轮在同一个控件上踩过，第十三轮又踩了一次（"这里定的是**留多少**"
        真的显示成了带星号的样子）—— 所以留一条判据，而不是靠记性。
        """
        from PySide6.QtWidgets import QLabel
        dlg = _dialog()
        bad = [lab.text() for lab in dlg.findChildren(QLabel)
               if '**' in lab.text() or '`' in lab.text()]
        assert bad == [], bad
        dlg.close()

    def test_the_problems_are_spelled_out(self, qapp):
        _set(preset='lean', keep='很多')
        dlg = _dialog()
        from PySide6.QtWidgets import QLabel
        texts = [lab.text() for lab in dlg.findChildren(QLabel)]
        assert any('看不懂' in t and '留几代' in t for t in texts), texts
        dlg.close()


# ======================================================================
#  六、接线（菜单有入口、入口把账带给窗口、存完说一句）
# ======================================================================

class _Host(RecoverMixin):
    """`RecoverMixin` 那几个"怎么说话"的宿主成员（不打桩整个窗口）。"""

    def __init__(self, root):
        self._project_root = root
        self.statuses, self.logs = [], []

    def _status(self, text):
        self.statuses.append(text)

    def _log(self, text):
        self.logs.append(text)


class TestWiring:
    def test_the_more_tools_menu_has_the_entry(self, ui_window):
        texts = [a.text() for a in ui_window._more_tools_menu().actions()]
        assert any('留多久' in t for t in texts), texts

    def test_the_menu_action_is_wired(self, ui_window, monkeypatch):
        calls = []
        monkeypatch.setattr(ui_window, 'show_retention',
                            lambda: calls.append('retention'))
        menu = ui_window._more_tools_menu()
        act = next(a for a in menu.actions() if '留多久' in a.text())
        act.trigger()
        assert calls == ['retention']

    def test_the_entry_passes_the_project_numbers(self, tmp_path, monkeypatch):
        root, script = _project(tmp_path)
        write_text_atomic(script, LF)
        seen = {}

        class _FakeDialog:
            def __init__(self, parent=None, **kw):
                seen.update(kw)

            def exec(self):
                return 0

            class saved:
                @staticmethod
                def connect(fn):
                    seen['slot'] = fn

        monkeypatch.setattr('storygraph.ui_qt.dialog_retention.RetentionDialog',
                            _FakeDialog)
        host = _Host(root)
        host.show_retention()
        assert seen['rule']['preset'] == 'default'
        assert seen['scripts']['count'] == 2
        assert seen['stats']['files'] == 1       # 刚才那次写留了一代
        assert seen['slot'] == host._after_retention_changed

    def test_saving_speaks_up_about_when_it_applies(self):
        host = _Host(None)
        host._after_retention_changed({'preset': 'lean'})
        assert '省地方' in host.statuses[-1]
        assert '下一次' in host.statuses[-1]
        assert '找不回来' in host.statuses[-1]
        assert '留档改成' in host.logs[-1]
