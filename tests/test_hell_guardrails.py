# -*- coding: utf-8 -*-
"""
护栏三件套的**地狱测试**（2026-11-01，第二轮审查收尾）
=====================================================

这一轮自己造了三个"看不见但必须靠得住"的小模块，它们正常路径上都不出声，
所以**只有地狱问法能验**：

  · `core/safeguard.py` 的 `configure()` / `summary_line()`：
    "被兜住的错误"要能进标准库日志、能落盘、**重复调用不叠 handler**；
    八个线程同时 `swallow()` 时计数不许丢；环形缓冲不许无界长大。
  · `core/preflight.py`：`icuuc.dll` 缺失时**必须**说人话（而且不能真弹窗，
    否则 CI 会卡在一个没人点的对话框上）。
  · `story/script_table.py` 的 `last_open_stats()`：解析错误/可疑台词要能
    交出去；**上一次的统计不许残留到这一次**（否则"这次没问题"永远查不出来）。

判据是"性质"：幂等、有界、线程安全、失败时也不崩。
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import preflight, safeguard           # noqa: E402


@pytest.fixture(autouse=True)
def _reset_safeguard():
    """每个用例前后都把全局状态清干净（这模块是进程级的）。"""
    safeguard.clear()
    safeguard.configure(debug=False)
    yield
    safeguard.configure(debug=False)
    safeguard.clear()
    for h in list(safeguard.logger.handlers):
        if getattr(h, '_storygraph', False):
            safeguard.logger.removeHandler(h)


# ---------------------------------------------------------------------------
#  一、safeguard：日志接线
# ---------------------------------------------------------------------------

class TestConfigureLogging:
    def test_configure_is_idempotent(self):
        """连调五次 `configure()`：handler 不能越挂越多。"""
        for _ in range(5):
            safeguard.configure(debug=True, log_file=None)
        mine = [h for h in safeguard.logger.handlers
                if getattr(h, '_storygraph', False)]
        assert len(mine) == 1, [type(h).__name__ for h in mine]

    def test_configure_log_file_and_debug_together(self, tmp_path):
        log = tmp_path / 'storygraph.log'
        safeguard.configure(debug=True, log_file=log)
        safeguard.swallow('core.test 撞了', ValueError('boom-123'))
        for h in safeguard.logger.handlers:
            h.flush()
        text = log.read_text(encoding='utf-8')
        assert 'boom-123' in text and 'core.test 撞了' in text, text

    def test_log_file_is_appended_not_truncated(self, tmp_path):
        log = tmp_path / 'a.log'
        for i in range(3):
            safeguard.configure(log_file=log)
            safeguard.swallow(f'ctx{i}', ValueError(f'err{i}'))
            for h in safeguard.logger.handlers:
                h.flush()
        text = log.read_text(encoding='utf-8')
        for i in range(3):
            assert f'err{i}' in text, text

    def test_unwritable_log_path_does_not_crash(self, tmp_path):
        """日志写不了（这里传一个目录）不该拖垮主流程 —— 但也不能把写入搞崩。"""
        safeguard.configure(log_file=tmp_path)      # 目录当文件
        safeguard.swallow('x', ValueError('y'))     # 不抛就算过
        assert safeguard.total() == 1

    def test_debug_off_closes_the_stderr_handler(self):
        safeguard.configure(debug=True)
        assert [h for h in safeguard.logger.handlers if getattr(h, '_storygraph', False)]
        safeguard.configure(debug=False)
        assert not [h for h in safeguard.logger.handlers
                    if getattr(h, '_storygraph', False)]

    def test_swallow_reaches_standard_logging(self, caplog):
        safeguard.configure(debug=True)
        with caplog.at_level(logging.DEBUG, logger=safeguard.LOGGER_NAME):
            safeguard.swallow('excel.cells 剜字符', ValueError('U+0005'))
        assert any('U+0005' in r.getMessage() for r in caplog.records), \
            [r.getMessage() for r in caplog.records]

    def test_summary_line_speaks_only_when_something_was_swallowed(self):
        assert safeguard.summary_line() == ''
        safeguard.swallow('a', ValueError('x'))
        line = safeguard.summary_line()
        assert '1' in line and '--debug' in line

    def test_report_shape(self):
        safeguard.swallow('a', ValueError('x'))
        rep = safeguard.report()
        assert isinstance(rep, str) and 'a' in rep


# ---------------------------------------------------------------------------
#  二、safeguard：多线程 / 有界
# ---------------------------------------------------------------------------

class TestThreadSafetyAndBounds:
    def test_eight_threads_do_not_lose_count(self):
        """
        8 线程 × 300 次：`total()` 必须**恰好** 2400。

        `swallow()` 是全项目最热的一条路（594 个调用点），GUI 里又从多个线程
        进来（缩略图池 / AI 线程 / 引擎轮询）。少记几次不致命，但"计数不准"
        会让后面所有基于它的判断（`summary_line` / 体检）跟着偏。
        """
        errors = []

        def worker():
            try:
                for _ in range(300):
                    safeguard.swallow('thread.same', ValueError('x'))
            except BaseException as exc:           # noqa: BLE001 - 收集
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, errors
        assert safeguard.total() == 8 * 300, safeguard.total()

    def test_context_table_is_bounded(self):
        for i in range(safeguard.MAX_CONTEXTS + 50):
            safeguard.swallow(f'ctx.{i}', ValueError('x'))
        assert len(safeguard.contexts()) <= safeguard.MAX_CONTEXTS
        assert safeguard.total() == safeguard.MAX_CONTEXTS + 50

    def test_samples_per_context_are_bounded(self):
        for i in range(safeguard.SAMPLES_PER_CONTEXT + 20):
            safeguard.swallow('one', ValueError(f'e{i}'))
        rec = [r for r in safeguard.records() if r.get('context') == 'one']
        assert rec and len(rec[0].get('samples') or []) <= safeguard.SAMPLES_PER_CONTEXT


# ---------------------------------------------------------------------------
#  三、preflight：icuuc.dll 缺了要说话，而且不许真弹窗
# ---------------------------------------------------------------------------

class _FakeWindll:
    """记录 `MessageBoxW` 被调用了几次（真弹窗会把 CI 卡死）。"""

    def __init__(self):
        self.calls = []
        outer = self

        class _User32:
            @staticmethod
            def MessageBoxW(hwnd, text, title, flags):
                outer.calls.append((text, title, flags))
                return 1

        self.user32 = _User32()


class TestPreflight:
    def test_shape_and_platform(self):
        res = preflight.check_gui_system_dlls()
        assert set(res) >= {'ok', 'missing', 'platform', 'detail'}
        assert isinstance(res['missing'], list)

    def test_missing_dll_is_reported_with_a_hint(self, monkeypatch):
        def boom(name, *a, **kw):
            raise OSError(f'找不到 {name}')

        monkeypatch.setattr(ctypes, 'WinDLL', boom)
        res = preflight.check_gui_system_dlls()
        if sys.platform.startswith('win'):
            assert res['ok'] is False
            assert 'icuuc.dll' in res['missing']
            hint = preflight.hint_for(res)
            assert 'icuuc.dll' in hint
            # 必须告诉人"命令行不受影响"—— 否则用户只会看到"打不开"
            assert '命令行' in hint
        else:
            assert res['ok'] is True

    def test_ok_result_produces_no_hint(self):
        assert preflight.hint_for({'ok': True, 'missing': []}) == ''

    def test_popup_is_not_shown_when_everything_is_fine(self, monkeypatch):
        fake = _FakeWindll()
        monkeypatch.setattr(ctypes, 'windll', fake, raising=False)
        preflight.warn_gui_startup_failure({'ok': True, 'missing': []})
        assert fake.calls == []

    def test_popup_text_is_the_hint_and_uses_no_qt(self, monkeypatch):
        fake = _FakeWindll()
        monkeypatch.setattr(ctypes, 'windll', fake, raising=False)
        res = {'ok': False, 'missing': ['icuuc.dll'], 'detail': 'icuuc.dll: 拒绝访问'}
        preflight.warn_gui_startup_failure(res)
        if sys.platform.startswith('win'):
            assert len(fake.calls) == 1
            text, title, flags = fake.calls[0]
            assert 'icuuc.dll' in text and '启动失败' in title
            assert flags & 0x10, '不是错误图标'
        else:
            assert fake.calls == []


# ---------------------------------------------------------------------------
#  四、script_table.last_open_stats：坏输入要留痕，且不串上一次
# ---------------------------------------------------------------------------

class TestOpenStats:
    def _write(self, tmp_path: Path, text: str, name: str = 'ch.rpy') -> Path:
        p = tmp_path / name
        p.write_bytes(text.encode('utf-8'))
        return p

    def test_good_chapter_reports_counts_and_no_errors(self, tmp_path):
        from storygraph.story.script_table import (last_open_stats,
                                                   open_rpy_as_table)
        ch = self._write(tmp_path, 'label ch:\n    e "一句话"\n    return\n')
        open_rpy_as_table(ch, ch.with_suffix('.xlsx'))
        stats = last_open_stats()
        assert stats['source'].endswith('ch.rpy')
        assert stats['labels'] >= 1 and stats['statements'] >= 1
        assert stats['errors'] == []
        assert stats['suspect_say'] == []

    def test_unclosed_quote_is_caught(self, tmp_path):
        from storygraph.story.script_table import (last_open_stats,
                                                   open_rpy_as_table)
        ch = self._write(tmp_path, 'label ch:\n    e "没闭合\n    return\n')
        open_rpy_as_table(ch, ch.with_suffix('.xlsx'))
        suspects = last_open_stats()['suspect_say']
        assert suspects, '引号没闭合却一句都没报'
        assert any('没闭合' in text for _line, text in suspects), suspects

    def test_stats_do_not_leak_from_the_previous_call(self, tmp_path):
        """上一次有错、这一次没病 —— 必须清零。"""
        from storygraph.story.script_table import (last_open_stats,
                                                   open_rpy_as_table)
        bad = self._write(tmp_path, 'label ch:\n    e "没闭合\n', 'bad.rpy')
        open_rpy_as_table(bad, bad.with_suffix('.xlsx'))
        assert last_open_stats()['suspect_say']
        good = self._write(tmp_path, 'label ok:\n    e "好"\n    return\n',
                           'good.rpy')
        open_rpy_as_table(good, good.with_suffix('.xlsx'))
        assert last_open_stats()['suspect_say'] == []
        assert last_open_stats()['source'].endswith('good.rpy')

    def test_empty_file_is_zero_statements_not_a_crash(self, tmp_path):
        from storygraph.story.script_table import (last_open_stats,
                                                   open_rpy_as_table)
        empty = self._write(tmp_path, '')
        open_rpy_as_table(empty, empty.with_suffix('.xlsx'))
        assert last_open_stats()['statements'] == 0

    def test_missing_file_raises_instead_of_silently_making_an_empty_table(
            self, tmp_path):
        from storygraph.story.script_table import open_rpy_as_table
        with pytest.raises(OSError):
            open_rpy_as_table(tmp_path / 'nope.rpy', tmp_path / 'nope.xlsx')

    def test_bom_crlf_chapter_still_reads(self, tmp_path):
        from storygraph.story.script_table import (last_open_stats,
                                                   open_rpy_as_table)
        ch = tmp_path / 'bom.rpy'
        ch.write_bytes(b'\xef\xbb\xbflabel ch:\r\n    e "\xe5\xa5\xbd"\r\n')
        open_rpy_as_table(ch, ch.with_suffix('.xlsx'))
        stats = last_open_stats()
        assert stats['statements'] >= 1, stats
        assert stats['errors'] == [], stats['errors']


# ---------------------------------------------------------------------------
#  五、体检与门禁同一套判据（报告 5.9 / H7）
# ---------------------------------------------------------------------------

def _project_with(tmp_path: Path, files: dict) -> Path:
    root = tmp_path / 'proj'
    game = root / 'game'
    game.mkdir(parents=True)
    for name, text in files.items():
        (game / name).write_text(text, encoding='utf-8')
    return root


class TestValidateJudge:
    def test_two_files_sharing_a_label_is_an_error(self, tmp_path):
        """
        `label start` 在两个文件里各有一份 —— 门禁判 block（LABEL_COLLISION），
        体检从前一条都不报。这条判据把两边钉在一起。
        """
        from storygraph.pipeline.validate import validate_label_collisions

        root = _project_with(tmp_path, {
            'a.rpy': 'label start:\n    return\n',
            'b.rpy': 'label start:\n    return\n',
        })
        issues = validate_label_collisions(root)
        assert issues, '跨文件重名没被报出来'
        codes = {i.code for i in issues}
        assert 'E306' in codes, codes
        assert all(i.level == 'L3' for i in issues), [i.level for i in issues]
        assert 'label `start`' in issues[0].message

    def test_single_definition_is_silent(self, tmp_path):
        from storygraph.pipeline.validate import validate_label_collisions
        root = _project_with(tmp_path, {
            'a.rpy': 'label start:\n    return\n',
            'b.rpy': 'label other:\n    return\n',
        })
        assert validate_label_collisions(root) == []

    def test_project_without_game_dir_is_not_guessed(self, tmp_path):
        from storygraph.pipeline.validate import validate_label_collisions
        bare = tmp_path / 'bare'
        bare.mkdir()
        (bare / 'a.rpy').write_text('label start:\n    return\n', encoding='utf-8')
        assert validate_label_collisions(bare) == []

    def test_validate_all_carries_it_and_says_unclean(self, tmp_path):
        from storygraph.pipeline.validate import validate_all
        root = _project_with(tmp_path, {
            'a.rpy': 'label start:\n    return\n',
            'b.rpy': 'label start:\n    return\n',
        })
        report = validate_all([], project_root=root)
        assert not report.ok, '体检还是绿的 —— 与门禁不一致'
        assert 'E306' in {i.code for i in report.issues}
        data = report.to_dict()
        assert data['ok'] is False and data['clean'] is False

    def test_warning_only_report_is_ok_but_not_clean(self):
        """`ok` 的口径只是"没有 error"—— JSON 里必须同时给出 `clean`。"""
        from storygraph.pipeline.validate import (L2, WARNING, Issue,
                                                  ValidationReport)

        rep = ValidationReport([Issue(L2, 'W999', WARNING, '只是一条警告')])
        assert rep.ok is True
        assert rep.clean is False
        data = rep.to_dict()
        assert data['ok'] is True and data['clean'] is False
        assert data['has_warnings'] is True

    def test_clean_report_is_clean(self):
        from storygraph.pipeline.validate import ValidationReport
        rep = ValidationReport([])
        assert rep.ok and rep.clean
        assert rep.to_dict()['has_warnings'] is False
