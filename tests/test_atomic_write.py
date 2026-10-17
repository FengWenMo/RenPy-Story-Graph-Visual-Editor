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

        import storygraph.story.paths as paths
        real_replace = paths.os.replace

        def boom(src, dst):
            raise OSError('模拟磁盘满')

        monkeypatch.setattr(paths.os, 'replace', boom)
        with pytest.raises(OSError):
            write_text_atomic(target, 'label a:\n    e "new"\n    return\n')
        monkeypatch.setattr(paths.os, 'replace', real_replace)
        assert target.read_bytes() == old, '失败时把目标写坏了'
        assert [p.name for p in tmp_path.iterdir() if '.tmp-' in p.name] == []


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
