"""
写盘只有一条路：原子、逐字节、多代备份、失败即中止（2026-11-01，外部审查 P0）
============================================================================

审查报告把"保护不对称"列为当前最大的一笔债：

    `.rpy` 的写盘又原子又备份又过门禁；Excel 的写盘是 `wb.save(path)` ——
    直接覆盖、写一半崩了就是半截文件、没有备份、不校验后缀。

而且两份备份都**只留一代**（固定名 `.bak`），连导两次第一代就被盖掉 ——
第一代往往才是用户手写的原件。

这个文件守的是 `core/atomicio.py` 那四条承诺：

  1. **后缀白名单**：Excel 只认 `.xlsx`，传 `.rpy` 直接拒绝（一个参数毁掉剧本的路）；
  2. **多代备份**：第二次备份带时间戳，第一代**永远不被覆盖**；
  3. **备份失败即中止**：备份拷贝失败时目标文件必须一个字节都没动
     （从前是"吞掉异常、照常覆盖"—— 用户以为有 `.bak`，其实没有）；
  4. **原子替换**：替换那一步失败 → 目标还是旧的、临时文件清干净、不留半截。
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core import atomicio                    # noqa: E402


def _xlsx(path: Path, value: str = 'x') -> Path:
    wb = openpyxl.Workbook()
    wb.active['A1'] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def _read_a1(path: Path) -> str:
    """读 A1。`.bak` 后缀 openpyxl 不认 —— 直接按字节读进内存（不落临时文件）。"""
    import io

    wb = openpyxl.load_workbook(io.BytesIO(Path(path).read_bytes()))
    try:
        return str(wb.active['A1'].value)
    finally:
        wb.close()


class TestWorkbookSuffixWhitelist:
    """P0-2：Excel 保存只认 `.xlsx` —— 这条闸直接挡住 import-ir 那种覆盖。"""

    def test_rpy_target_is_refused_and_left_untouched(self, tmp_path):
        """实测事故形态：1488 字节的 .rpy 被 15481 字节的 xlsx 覆盖。"""
        script = tmp_path / 'ch01.rpy'
        original = b'label ch01:\n    "hello"\n    return\n'
        script.write_bytes(original)

        wb = openpyxl.Workbook()
        with pytest.raises(atomicio.RefusedWrite) as exc:
            atomicio.save_workbook_atomic(wb, script)
        assert 'ch01.rpy' in str(exc.value)
        assert script.read_bytes() == original, '剧本被 Excel 字节覆盖了'
        assert not list(tmp_path.glob('*.bak*')), '拒绝时不该留下备份'

    def test_error_message_says_what_is_allowed(self, tmp_path):
        wb = openpyxl.Workbook()
        with pytest.raises(atomicio.RefusedWrite) as exc:
            atomicio.save_workbook_atomic(wb, tmp_path / 'a.csv')
        assert '.xlsx' in str(exc.value)

    def test_xlsx_is_saved_and_readable(self, tmp_path):
        target = tmp_path / 'ok.xlsx'
        wb = openpyxl.Workbook()
        wb.active['A1'] = '你好'
        atomicio.save_workbook_atomic(wb, target)
        assert _read_a1(target) == '你好'

    def test_allowed_suffixes_can_be_disabled_for_temp_files(self, tmp_path):
        """调用方自己已经确认过（例如写临时文件）时可以不校验。"""
        target = tmp_path / 'x.part'
        wb = openpyxl.Workbook()
        atomicio.save_workbook_atomic(wb, target, backup=False,
                                      allowed_suffixes=None)
        assert target.is_file()


class TestMultiGenerationBackup:
    """P0-3：多代备份 —— 第一代是原件，绝不能被第二代盖掉。"""

    def test_second_backup_gets_a_timestamp_and_keeps_the_first(self, tmp_path):
        target = tmp_path / 'a.rpy'
        v1, v2, v3 = b'one\n', b'two\n', b'three\n'
        atomicio.write_text_atomic(target, v1.decode())
        atomicio.write_text_atomic(target, v2.decode(), backup=True)
        atomicio.write_text_atomic(target, v3.decode(), backup=True)
        assert target.read_bytes() == v3
        # 固定名那份**永远是最早的一代**（= 用户手写的原件），不再被后来的覆盖。
        assert (tmp_path / 'a.rpy.bak').read_bytes() == v1
        stamped = sorted(tmp_path.glob('a.rpy.bak-*'))
        assert len(stamped) == 1
        assert stamped[0].read_bytes() == v2, '第二代没留下来'

    def test_backups_are_pruned_but_never_below_the_limit(self, tmp_path):
        target = tmp_path / 'a.rpy'
        for i in range(atomicio.KEEP_BACKUPS + 4):
            atomicio.write_text_atomic(target, f'v{i}\n', backup=True)
        backups = atomicio._backup_candidates(target)
        assert len(backups) <= atomicio.KEEP_BACKUPS, backups

    def test_workbook_backup_is_the_previous_bytes(self, tmp_path):
        target = _xlsx(tmp_path / 't.xlsx', 'old')
        wb = openpyxl.Workbook()
        wb.active['A1'] = 'new'
        atomicio.save_workbook_atomic(wb, target)
        assert _read_a1(target) == 'new'
        assert _read_a1(tmp_path / 't.xlsx.bak') == 'old'


class TestBackupFailureAbortsTheWrite:
    """P0-3 的另一半：备份失败**必须中止**，不能"吞掉异常照常覆盖"。"""

    def test_text_write_aborts_when_backup_raises(self, tmp_path, monkeypatch):
        target = tmp_path / 'a.rpy'
        original = b'label a:\n    "old"\n    return\n'
        target.write_bytes(original)

        def boom(path, **_kw):
            raise OSError('模拟磁盘满（备份写不进去）')

        monkeypatch.setattr(atomicio, 'backup_file', boom)
        with pytest.raises(OSError):
            atomicio.write_text_atomic(target, 'label a:\n    "new"\n    return\n',
                                       backup=True)
        assert target.read_bytes() == original, '备份失败却照样覆盖了原文件'
        assert not [p for p in tmp_path.iterdir() if '.tmp-' in p.name]

    def test_workbook_write_aborts_when_backup_raises(self, tmp_path, monkeypatch):
        target = _xlsx(tmp_path / 't.xlsx', 'old')
        before = target.read_bytes()

        def boom(path, **_kw):
            raise OSError('模拟磁盘满（备份写不进去）')

        monkeypatch.setattr(atomicio, 'backup_file', boom)
        wb = openpyxl.Workbook()
        wb.active['A1'] = 'new'
        with pytest.raises(OSError):
            atomicio.save_workbook_atomic(wb, target)
        assert target.read_bytes() == before
        assert not [p for p in tmp_path.iterdir() if '.xlsx-' in p.name]


class TestAtomicReplaceFailure:
    """替换那一步失败 → 目标还是旧的、临时文件清干净（半截文件比没有更坏）。"""

    def test_text_replace_failure_keeps_the_old_file(self, tmp_path, monkeypatch):
        target = tmp_path / 'a.rpy'
        target.write_bytes(b'old\n')

        def boom(src, dst):
            raise OSError('模拟占用')

        monkeypatch.setattr(atomicio.os, 'replace', boom)
        with pytest.raises(OSError):
            atomicio.write_text_atomic(target, 'new\n')
        assert target.read_bytes() == b'old\n'
        assert not [p for p in tmp_path.iterdir() if '.tmp-' in p.name]

    def test_workbook_replace_failure_keeps_the_old_file(self, tmp_path,
                                                         monkeypatch):
        target = _xlsx(tmp_path / 't.xlsx', 'old')
        before = target.read_bytes()

        def boom(src, dst):
            raise OSError('模拟占用')

        monkeypatch.setattr(atomicio.os, 'replace', boom)
        wb = openpyxl.Workbook()
        wb.active['A1'] = 'new'
        with pytest.raises(OSError):
            atomicio.save_workbook_atomic(wb, target)
        assert target.read_bytes() == before
        assert not [p for p in tmp_path.iterdir() if '.xlsx-' in p.name]


class TestHumanIOError:

    def test_permission_error_becomes_a_sentence_a_writer_can_act_on(self):
        msg = atomicio.human_io_error(PermissionError('denied'),
                                      Path('剧本表.xlsx'))
        assert 'Excel' in msg and '一个字没动' in msg

    def test_unknown_error_still_mentions_the_file(self):
        msg = atomicio.human_io_error(OSError('weird'), Path('a.xlsx'))
        assert 'a.xlsx' in msg and 'weird' in msg
