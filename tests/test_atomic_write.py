"""
落盘：保留换行 / 原子替换 / 写前备份（调研 P1-2）
================================================

`write_generation()` 从前是：

    out_path.write_text(text, encoding='utf-8')

两个问题（都实测复现过）：

  ① **Windows 上 `newline=None` 会把 `\\n` 翻成 `\\r\\n`** —— 与项目反复
     承诺的"打开 → 导出逐字节不变"直接冲突；
  ② **不是原子写**：先截断再写，进程崩溃 / 磁盘满 / 杀毒软件占着目标时，
     游戏脚本会留下**半截**。

现在走 `story.paths.write_text_atomic`：同目录临时文件 → `newline=''`
写 → fsync → （可选）.bak → `os.replace`。

这个文件守：LF / CRLF 原样、备份是上一版字节、失败时目标不动且不留垃圾。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.paths import write_text_atomic      # noqa: E402


class TestWriteTextAtomic:

    def test_lf_is_not_translated(self, tmp_path):
        target = tmp_path / 'a.rpy'
        text = 'label a:\n    e "x"\n    return\n'
        write_text_atomic(target, text)
        assert target.read_bytes() == text.encode('utf-8'), \
            'LF 被翻成了 CRLF（Windows 上 write_text 的默认行为）'

    def test_crlf_is_not_translated(self, tmp_path):
        target = tmp_path / 'a.rpy'
        data = b'label a:\r\n    e "x"\r\n    return\r\n'
        write_text_atomic(target, data.decode('utf-8'))
        assert target.read_bytes() == data

    def test_backup_is_the_previous_bytes(self, tmp_path):
        target = tmp_path / 'a.rpy'
        old = b'label a:\n    e "old"\n    return\n'
        new = 'label a:\n    e "new"\n    return\n'
        write_text_atomic(target, old.decode())
        write_text_atomic(target, new, backup=True)
        assert target.read_bytes() == new.encode()
        assert (tmp_path / 'a.rpy.bak').read_bytes() == old

    def test_no_temp_files_left_behind(self, tmp_path):
        target = tmp_path / 'a.rpy'
        write_text_atomic(target, 'label a:\n    return\n')
        leftovers = [p.name for p in tmp_path.iterdir() if '.tmp-' in p.name]
        assert leftovers == []

    def test_failure_keeps_the_original_and_cleans_up(self, tmp_path,
                                                      monkeypatch):
        """
        替换那一步失败（磁盘满 / 权限 / 占用）→ 目标必须**还是旧的**，
        临时文件必须清掉。这正是"非原子写"最贵的形态：半截脚本。
        """
        target = tmp_path / 'a.rpy'
        old = b'label a:\n    e "old"\n    return\n'
        target.write_bytes(old)

        import storygraph.core.atomicio as atomicio
        real_replace = atomicio.os.replace

        def boom(src, dst):
            raise OSError('模拟磁盘满')

        monkeypatch.setattr(atomicio.os, 'replace', boom)
        with pytest.raises(OSError):
            write_text_atomic(target, 'label a:\n    e "new"\n    return\n')
        monkeypatch.setattr(atomicio.os, 'replace', real_replace)
        assert target.read_bytes() == old, '失败时把目标写坏了'
        assert [p.name for p in tmp_path.iterdir() if '.tmp-' in p.name] == []

    def test_unicode_encode_error_also_cleans_up(self, tmp_path):
        """
        `UnicodeEncodeError` 是 `ValueError` 子类，不会被 `except OSError`
        接住 —— 从前会留下 `.xxx.tmp-<pid>`（写进 game/ 就是垃圾文件）。
        """
        target = tmp_path / 'a.rpy'
        with pytest.raises(UnicodeEncodeError):
            write_text_atomic(target, 'label a:\n    e "\ud800"\n')
        assert not target.exists()
        assert [p.name for p in tmp_path.iterdir() if '.tmp-' in p.name] == []

    def test_temp_name_is_unique_within_one_process(self, tmp_path,
                                                     monkeypatch):
        """tmp 名只带 pid 时，同进程两个线程写同一目标会撞同一个 tmp 名。"""
        import builtins

        seen = []
        real_open = builtins.open

        def fake_open(name, *a, **kw):
            seen.append(str(name))
            return real_open(name, *a, **kw)

        monkeypatch.setattr(builtins, 'open', fake_open)
        target = tmp_path / 'a.rpy'
        write_text_atomic(target, 'x')
        write_text_atomic(target, 'y')
        tmps = [name for name in seen if '.tmp-' in name]
        assert len(tmps) == 2 and len(set(tmps)) == 2, tmps


class TestGenerationGuardUsesIt:

    def test_write_generation_keeps_lf_and_writes_backup(self, tmp_path):
        from storygraph.core.vocabulary import GENERATOR_MARKER
        from storygraph.story.generation_guard import write_generation

        target = tmp_path / 'out' / 'ch01.rpy'
        text = (f'{GENERATOR_MARKER}\nlabel a:\n    e "x"\n    return\n')
        art = write_generation(text, target)
        assert art.written and target.exists()
        assert target.read_bytes() == text.encode('utf-8')
        # 第二次写（工具自己的产物，允许覆盖）→ 留一份 .bak
        text2 = (f'{GENERATOR_MARKER}\nlabel a:\n    e "y"\n    return\n')
        art2 = write_generation(text2, target)
        assert art2.written
        assert target.read_bytes() == text2.encode('utf-8')
        assert (tmp_path / 'out' / 'ch01.rpy.bak').read_bytes() == \
            text.encode('utf-8')
