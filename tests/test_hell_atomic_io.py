# -*- coding: utf-8 -*-
"""
写盘那条路的**地狱测试**（2026-11-01，第二轮审查 P0 收尾）
==========================================================

`test_atomic_io.py` 守的是四条承诺"在正常路径上成立"。这个文件换一副面孔问：

    磁盘坏了、文件被占着、时间戳撞了、八个线程一起写、临时文件是上一次
    崩溃留下的 —— 那些承诺**还成立吗**？

逐条对着实现里最容易出事的点：

  ① **备份名撞车**：Windows 的时钟精度可能粗到一个 tick（两次写入落在同一
     微秒）—— 备份名若只由时间戳决定，第二代就**盖掉**第一代，
    而第一代往往才是用户手写的原件；
  ② **写到一半断电**：`fsync` / `wb.save` / `os.replace` 三步任意一步抛，
     目标文件必须**一个字节没动**、临时文件必须清干净；
  ③ **只读文件**（Windows）：替换必须失败，且原文件还在；
  ④ **路径本身是坑**：父目录是个文件、名字带中文空格、8MB 大文件；
  ⑤ **后缀白名单**：`.xlsm` / `.csv` / `.rpy` / 没后缀 —— 一律拒绝且**不动**文件；
  ⑥ **并发**：多线程 / 多进程同时写同一个目标，最终文件必须是**某一次的完整
     内容**，不能是两次写混在一起；备份也必须份份完整。

这些不是"覆盖率"，是**事故形态**：每一条都对应一种"用户以为文件还在、其实没了"。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip('openpyxl')
import openpyxl                                        # noqa: E402

from storygraph.core import atomicio                    # noqa: E402


def _xlsx(path: Path, value: str = 'x') -> Path:
    wb = openpyxl.Workbook()
    wb.active['A1'] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def _read_a1(path: Path) -> str:
    """按字节读（`.bak` 后缀 openpyxl 不认，也不能为了读它落一个临时文件）。"""
    wb = openpyxl.load_workbook(io.BytesIO(Path(path).read_bytes()))
    try:
        return str(wb.active['A1'].value)
    finally:
        wb.close()


def _no_temp_files(folder: Path) -> list:
    """
    本模块自己的临时文件（`.<名>.tmp-…` / `.<名>.xlsx-…`）一个都不许剩。

    ⚠ 只认**隐藏名**：`a.rpy.bak-<戳>` 是正当备份，不是临时文件。
    """
    return [p.name for p in Path(folder).iterdir()
            if p.name.startswith('.')
            and ('.tmp-' in p.name or '.xlsx-' in p.name or '.bak-' in p.name)]


class _FrozenClock:
    """时间停在某一微秒 —— 模拟"两次备份落在同一个时间戳"（Windows 时钟很粗）。"""

    def __init__(self, moment):
        self._m = moment

    def now(self):
        return self._m


# ---------------------------------------------------------------------------
#  ① 备份名撞车：同一微秒的两次备份，两份都得在
# ---------------------------------------------------------------------------

class TestBackupNamesNeverCollide:
    def test_two_backups_in_the_same_tick_both_survive(self, tmp_path, monkeypatch):
        """
        固定时钟写三次 → 第二代、第三代的备份名**完全一样**。

        只按时间戳命名的实现会让第三次把第二次**盖掉**：`KEEP_BACKUPS=5` 看着
        留了 5 代，实际只剩 1 代。判据是"内容都在"，不是"名字不同"。
        """
        target = tmp_path / 'a.rpy'
        atomicio.write_text_atomic(target, 'v0\n')
        monkeypatch.setattr(
            atomicio, 'datetime',
            _FrozenClock(_dt.datetime(2026, 1, 1, 12, 0, 0, 123456)))

        atomicio.write_text_atomic(target, 'v1\n', backup=True)   # → 固定名 .bak
        atomicio.write_text_atomic(target, 'v2\n', backup=True)   # → 带戳 #1
        atomicio.write_text_atomic(target, 'v3\n', backup=True)   # → 带戳 #2（同一戳！）

        stamped = sorted(tmp_path.glob('a.rpy.bak-*'))
        assert len(stamped) == 2, (
            f'同一时间戳的两次备份只剩 {len(stamped)} 份：{[p.name for p in stamped]}')
        assert sorted(p.read_bytes() for p in stamped) == [b'v1\n', b'v2\n']
        assert (tmp_path / 'a.rpy.bak').read_bytes() == b'v0\n'

    def test_pruning_keeps_the_oldest_backup_and_the_newest_ones(self, tmp_path,
                                                                 monkeypatch):
        """撞名 + 清理一起上：固定名（原件）永远在，其余按"最近"留。"""
        target = tmp_path / 'a.rpy'
        atomicio.write_text_atomic(target, 'v0\n')
        monkeypatch.setattr(
            atomicio, 'datetime',
            _FrozenClock(_dt.datetime(2026, 1, 1, 12, 0, 0, 123456)))
        for i in range(1, atomicio.KEEP_BACKUPS + 5):
            atomicio.write_text_atomic(target, f'v{i}\n', backup=True)
        assert (tmp_path / 'a.rpy.bak').read_bytes() == b'v0\n'
        backups = atomicio._backup_candidates(target)
        assert len(backups) <= atomicio.KEEP_BACKUPS, [p.name for p in backups]
        # 最后一代（写之前那一刻的内容）必须还在 —— 清理不能把"最近的"删掉
        assert target.read_bytes() == f'v{atomicio.KEEP_BACKUPS + 4}\n'.encode()
        assert any(p.read_bytes() == f'v{atomicio.KEEP_BACKUPS + 3}\n'.encode()
                   for p in backups)

    def test_backup_is_byte_identical_even_with_bom_and_crlf(self, tmp_path):
        """备份走二进制复制：BOM、CRLF、末尾没有换行，一个字节都不许动。"""
        target = tmp_path / 'a.rpy'
        original = b'\xef\xbb\xbflabel a:\r\n    "x"\r\n    return'
        target.write_bytes(original)
        atomicio.write_text_atomic(target, 'new\n', backup=True)
        assert (tmp_path / 'a.rpy.bak').read_bytes() == original

    def test_missing_target_means_no_backup_at_all(self, tmp_path):
        assert atomicio.backup_file(tmp_path / 'nope.rpy') is None
        assert not list(tmp_path.iterdir())

    def test_backup_false_leaves_no_bak(self, tmp_path):
        target = tmp_path / 'a.rpy'
        target.write_bytes(b'old\n')
        atomicio.write_text_atomic(target, 'new\n', backup=False)
        assert target.read_bytes() == b'new\n'
        assert not list(tmp_path.glob('a.rpy.bak*'))


# ---------------------------------------------------------------------------
#  ② 三步里任意一步抛：目标一个字节没动、临时文件清干净
# ---------------------------------------------------------------------------

class TestInterruptedWriteLeavesTheOldFile:
    def test_text_fsync_failure(self, tmp_path, monkeypatch):
        target = tmp_path / 'a.rpy'
        target.write_bytes(b'old\n')

        def boom(fd):
            raise OSError(28, 'No space left on device')

        monkeypatch.setattr(atomicio.os, 'fsync', boom)
        with pytest.raises(OSError):
            atomicio.write_text_atomic(target, 'new\n')
        assert target.read_bytes() == b'old\n'
        assert _no_temp_files(tmp_path) == []

    def test_workbook_save_failure(self, tmp_path):
        """`wb.save` 自己抛（磁盘满 / 序列化失败）—— 旧表必须还在。"""
        target = _xlsx(tmp_path / 't.xlsx', 'old')
        before = target.read_bytes()

        class _BadWorkbook:
            def save(self, _path):
                raise OSError(28, 'No space left on device')

        with pytest.raises(OSError):
            atomicio.save_workbook_atomic(_BadWorkbook(), target)
        assert target.read_bytes() == before, '保存失败却动了旧表'
        assert _no_temp_files(tmp_path) == []
        assert not (tmp_path / 't.xlsx.bak').exists()

    def test_workbook_fsync_failure(self, tmp_path, monkeypatch):
        target = _xlsx(tmp_path / 't.xlsx', 'old')
        before = target.read_bytes()

        def boom(fd):
            raise OSError(5, 'I/O error')

        monkeypatch.setattr(atomicio.os, 'fsync', boom)
        wb = openpyxl.Workbook()
        wb.active['A1'] = 'new'
        with pytest.raises(OSError):
            atomicio.save_workbook_atomic(wb, target)
        assert target.read_bytes() == before
        assert _no_temp_files(tmp_path) == []

    def test_text_replace_failure_cleans_temp(self, tmp_path, monkeypatch):
        target = tmp_path / 'a.rpy'
        target.write_bytes(b'old\n')

        def boom(src, dst):
            raise PermissionError(13, 'in use')

        monkeypatch.setattr(atomicio.os, 'replace', boom)
        with pytest.raises(PermissionError):
            atomicio.write_text_atomic(target, 'new\n')
        assert target.read_bytes() == b'old\n'
        assert _no_temp_files(tmp_path) == []

    def test_base_exception_still_cleans_up(self, tmp_path, monkeypatch):
        """Ctrl-C / SystemExit 落在替换之前 —— 临时文件也不能留。"""
        target = tmp_path / 'a.rpy'
        target.write_bytes(b'old\n')

        def boom(src, dst):
            raise KeyboardInterrupt

        monkeypatch.setattr(atomicio.os, 'replace', boom)
        with pytest.raises(KeyboardInterrupt):
            atomicio.write_text_atomic(target, 'new\n')
        assert target.read_bytes() == b'old\n'
        assert _no_temp_files(tmp_path) == []

    def test_stale_temp_from_a_crashed_run_does_not_break_the_next_write(self,
                                                                        tmp_path):
        """上一次被杀掉留下的 `.<名>.tmp-<pid>-<hex>`：不认领、不误删、不串内容。"""
        target = tmp_path / 'a.rpy'
        stale = tmp_path / '.a.rpy.tmp-9999-deadbeef'
        stale.write_bytes(b'half-written garbage')
        atomicio.write_text_atomic(target, 'clean\n')
        assert target.read_bytes() == b'clean\n'
        # 别人的临时文件**不许**被我们当成自己的删掉（可能是另一个进程正在写）
        assert stale.read_bytes() == b'half-written garbage'


# ---------------------------------------------------------------------------
#  ③ 只读文件 / ④ 路径本身是坑
# ---------------------------------------------------------------------------

class TestHostilePaths:
    @pytest.mark.skipif(os.name != 'nt', reason='POSIX 允许替换只读文件（只看目录权限）')
    def test_readonly_target_is_not_replaced(self, tmp_path):
        target = tmp_path / 'a.rpy'
        target.write_bytes(b'old\n')
        os.chmod(target, stat.S_IREAD)
        try:
            with pytest.raises(OSError):
                atomicio.write_text_atomic(target, 'new\n')
            assert target.read_bytes() == b'old\n'
            assert _no_temp_files(tmp_path) == []
        finally:
            os.chmod(target, stat.S_IWRITE)     # 否则 tmp_path 清理会失败

    def test_parent_path_is_a_file(self, tmp_path):
        blocker = tmp_path / 'not-a-folder'
        blocker.write_bytes(b'x')
        with pytest.raises(OSError):
            atomicio.write_text_atomic(blocker / 'a.rpy', 'new\n')
        assert blocker.read_bytes() == b'x'

    def test_chinese_spaces_and_middle_dots_in_the_name(self, tmp_path):
        target = tmp_path / '剧 本 · 第一章（改）.xlsx'
        wb = openpyxl.Workbook()
        wb.active['A1'] = '中文 空格 · 括号'
        atomicio.save_workbook_atomic(wb, target)
        assert _read_a1(target) == '中文 空格 · 括号'

    def test_deep_nested_missing_folders_are_created(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'c' / 'd.rpy'
        atomicio.write_text_atomic(target, 'x\n')
        assert target.read_text(encoding='utf-8') == 'x\n'

    def test_eight_megabyte_payload_is_written_intact(self, tmp_path):
        blob = (b'0123456789abcdef' * 4096) * 128          # 8 MiB
        target = tmp_path / 'big.bin'
        atomicio.write_bytes_atomic(target, blob)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == \
            hashlib.sha256(blob).hexdigest()
        assert _no_temp_files(tmp_path) == []


# ---------------------------------------------------------------------------
#  ⑤ 后缀白名单：拒绝，而且**不动**文件
# ---------------------------------------------------------------------------

HOSTILE_SUFFIXES = ['a.xls', 'a.xlsm', 'a.csv', 'a.rpy', 'a.txt', 'a',
                    'a.xlsx.bak', 'a.xlsx.txt']


class TestSuffixWhitelistHell:
    @pytest.mark.parametrize('name', HOSTILE_SUFFIXES)
    def test_hostile_suffix_is_refused_and_file_untouched(self, tmp_path, name):
        target = tmp_path / name
        sentinel = b'user content that must survive'
        target.write_bytes(sentinel)
        wb = openpyxl.Workbook()
        with pytest.raises(atomicio.RefusedWrite) as exc:
            atomicio.save_workbook_atomic(wb, target)
        assert target.read_bytes() == sentinel, f'{name} 被 Excel 字节覆盖了'
        assert '.xlsx' in str(exc.value)
        assert not list(tmp_path.glob(name + '.bak*'))

    def test_uppercase_suffix_is_allowed(self, tmp_path):
        target = tmp_path / 'A.XLSX'
        wb = openpyxl.Workbook()
        wb.active['A1'] = 'ok'
        atomicio.save_workbook_atomic(wb, target)
        assert _read_a1(target) == 'ok'

    def test_refused_write_is_a_value_error_not_an_os_error(self):
        """调用方常写 `except OSError`（磁盘满/占用）—— 拒绝写入不能被它接住当"存储错误"。"""
        assert issubclass(atomicio.RefusedWrite, ValueError)
        assert not issubclass(atomicio.RefusedWrite, OSError)


# ---------------------------------------------------------------------------
#  ⑥ 并发：线程与进程同时写同一个目标
# ---------------------------------------------------------------------------

class TestConcurrentWriters:
    def test_threads_never_leave_a_torn_or_mixed_file(self, tmp_path):
        """八个线程各写 6 次：最终文件必须是**某一次写入的完整内容**。"""
        target = tmp_path / 'a.rpy'
        atomicio.write_text_atomic(target, 'init\n')
        payloads = {f'writer-{i}-' + ('x' * 65536) + '\n' for i in range(8)}
        errors = []

        def worker(text):
            try:
                for _ in range(6):
                    atomicio.write_text_atomic(target, text)
            except BaseException as exc:                 # noqa: BLE001 - 收集后统一断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, errors
        final = target.read_text(encoding='utf-8')
        assert final in payloads | {'init\n'}, '最终文件不是任何一次写入的完整内容'
        assert _no_temp_files(tmp_path) == []

    def test_threads_with_backup_never_leave_a_torn_backup(self, tmp_path):
        """
        开着多代备份再并发：Windows 上"替换"可能撞上别人正在读备份源
        （`PermissionError`）—— 那一次写入**不算数**，但**目标与已有备份
        都不许变成半截**。这是这一层能诚实承诺的边界。
        """
        target = tmp_path / 'a.rpy'
        atomicio.write_text_atomic(target, 'init\n')
        payloads = {f'w{i}-' + ('y' * 8192) + '\n' for i in range(6)}
        ok, transient = [], []

        def worker(text):
            for _ in range(4):
                try:
                    atomicio.write_text_atomic(target, text, backup=True)
                    ok.append(text)
                except PermissionError as exc:           # Windows 共享冲突：重试由调用方决定
                    transient.append(exc)
                except BaseException as exc:             # noqa: BLE001 - 别的错都不许有
                    ok.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        bad = [e for e in ok if isinstance(e, BaseException)]
        assert not bad, bad
        assert ok, '一次都没写成功（全是共享冲突？）'
        final = target.read_text(encoding='utf-8')
        assert final in payloads | {'init\n'}, '目标被写成了半截内容'
        for b in atomicio._backup_candidates(target):
            assert b.read_text(encoding='utf-8') in payloads | {'init\n'}, b.name
        assert _no_temp_files(tmp_path) == []

    def test_two_processes_writing_the_same_workbook(self, tmp_path):
        """
        两个进程（模拟"Excel 正开着 + 工具在存"的另一种形态）各自存 3 次，
        最后的表必须是**某一次完整保存**的结果，能被 openpyxl 正常打开。
        """
        target = tmp_path / 't.xlsx'
        _xlsx(target, 'init')
        code = (
            'import sys, openpyxl\n'
            'from storygraph.core import atomicio\n'
            'target, value = sys.argv[1], sys.argv[2]\n'
            'for _ in range(3):\n'
            '    wb = openpyxl.Workbook()\n'
            '    wb.active["A1"] = value\n'
            '    atomicio.save_workbook_atomic(wb, target, backup=False)\n'
        )
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT)
        env['PYTHONIOENCODING'] = 'utf-8'
        procs = [subprocess.Popen([sys.executable, '-c', code, str(target), value],
                                  cwd=str(tmp_path), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for value in ('proc-A', 'proc-B')]
        for p in procs:
            out, err = p.communicate(timeout=120)
            assert p.returncode == 0, err.decode('utf-8', 'replace')
        assert _read_a1(target) in {'proc-A', 'proc-B', 'init'}
        assert _no_temp_files(tmp_path) == []


# ---------------------------------------------------------------------------
#  人话：错误消息必须能让编剧照着做
# ---------------------------------------------------------------------------

class TestHumanIOErrorHell:
    def test_disk_full(self):
        msg = atomicio.human_io_error(OSError(28, 'No space left on device'),
                                      Path('表.xlsx'))
        assert '磁盘满' in msg and '一个字没动' in msg

    def test_readonly(self):
        msg = atomicio.human_io_error(OSError('Read-only file system'),
                                      Path('表.xlsx'))
        assert '只读' in msg

    def test_permission(self):
        msg = atomicio.human_io_error(PermissionError('denied'), Path('表.xlsx'))
        assert 'Excel' in msg and '关掉那个窗口' in msg

    def test_unknown_error_keeps_the_original_text(self):
        msg = atomicio.human_io_error(OSError('something odd'), Path('表.xlsx'))
        assert 'something odd' in msg and '表.xlsx' in msg
