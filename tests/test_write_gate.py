"""
tests/test_write_gate.py
========================
**写入前的三态判定**：采纳 / rebase / 真冲突（2026-10-31，P0 那条 ❌）。

设计文档 §6.3b 的原话（Cherry Studio 的 `write_if_unchanged`）：

| 磁盘当前内容 | 判定 | 处理 |
|---|---|---|
| 等于本次要写的草稿 | 不算冲突 | 直接采纳 |
| 等于基线（只是被 touch） | 不算冲突 | rebase 重试 |
| 与基线、草稿都不同 | 真分叉 | 才算冲突，交给人 |

这里钉四件事：

  ① 三态各自成立，且 `REBASE` 不会被当成冲突（touch 不是冲突）；
  ② 没有基线（`base_sha1=''`）时只有"采纳 / 冲突"两态，不猜；
  ③ `base_text` 是**预留参数**：给了才有自动合并候选，但**合不合并由人定**
     （状态仍是 `CONFLICT`）；
  ④ `EditorSession.source_unchanged()` 改成**只看内容** —— 只被 touch 的
     文件不该丢掉可信行号（这才是 rebase 那一态的真实收益）。
"""

from __future__ import annotations

import os

import pytest

from storygraph.story.write_gate import (ADOPT, CONFLICT, REBASE, STATE_CN,
                                         Verdict, classify, merge_text,
                                         sha1_16)

BASE_TEXT = 'label start:\n    scene bg room\n    "你好"\n    return\n'
DRAFT_TEXT = 'label start:\n    scene bg room\n    "你好呀"\n    return\n'


class TestThreeStates:
    def test_disk_equals_draft_is_adopted(self):
        got = classify(DRAFT_TEXT.encode('utf-8'), sha1_16(DRAFT_TEXT),
                       DRAFT_TEXT)
        assert got.state == ADOPT
        assert got.ok and not got.is_conflict
        assert '盘上已经是' in got.sentence()

    def test_disk_equals_base_is_rebase_not_conflict(self):
        """只被 touch / 元数据变了：内容 == 基线 → rebase，不算冲突。"""
        got = classify(BASE_TEXT.encode('utf-8'), sha1_16(BASE_TEXT),
                       DRAFT_TEXT)
        assert got.state == REBASE
        assert got.ok
        assert 'touch' in got.sentence() or '元数据' in got.sentence()

    def test_real_divergence_is_conflict(self):
        disk = 'label start:\n    scene bg room\n    "他说"\n    return\n'
        got = classify(disk.encode('utf-8'), sha1_16(BASE_TEXT), DRAFT_TEXT)
        assert got.state == CONFLICT
        assert not got.ok and got.is_conflict

    def test_without_a_base_only_two_states_exist(self):
        got = classify(BASE_TEXT.encode('utf-8'), '', DRAFT_TEXT)
        assert got.state == CONFLICT          # 没有基线 → 不猜"没变过"
        got2 = classify(DRAFT_TEXT.encode('utf-8'), '', DRAFT_TEXT)
        assert got2.state == ADOPT

    def test_hashes_in_the_verdict_are_the_sixteen_char_kind(self):
        got = classify(BASE_TEXT.encode('utf-8'), sha1_16(BASE_TEXT),
                       DRAFT_TEXT)
        for value in (got.disk_sha1, got.base_sha1, got.draft_sha1):
            assert len(value) == 16

    def test_verdict_is_plain_data(self):
        got = classify(b'x', 'aaaa', 'y')
        assert got.as_dict()['state'] == CONFLICT
        assert set(STATE_CN) == {ADOPT, REBASE, CONFLICT}
        assert Verdict().state == CONFLICT

    def test_bytes_and_str_are_not_confused(self):
        """`sha1_16` 对 str 按 utf-8 编码（中文不能因为编码变成另一个哈希）。"""
        assert sha1_16('中文') == sha1_16('中文'.encode('utf-8'))
        assert sha1_16('中文') != sha1_16('中文'.encode('gbk', 'replace'))


class TestMergeCandidate:
    def test_disjoint_edits_merge(self):
        base = 'a\nb\nc\nd\n'
        disk = 'A\nb\nc\nd\n'          # 改了第 1 行
        draft = 'a\nb\nc\nD\n'         # 改了第 4 行
        assert merge_text(base, disk, draft) == 'A\nb\nc\nD\n'

    def test_same_line_conflicts(self):
        base = 'a\nb\nc\n'
        assert merge_text(base, 'a\nX\nc\n', 'a\nY\nc\n') == ''

    def test_two_insertions_at_the_same_point_conflict(self):
        base = 'a\nc\n'
        assert merge_text(base, 'a\nb\nc\n', 'a\nB\nc\n') == ''

    def test_trailing_newline_is_preserved(self):
        assert merge_text('a\n', 'A\n', 'a\n') == 'A\n'
        assert merge_text('a', 'A', 'a') == 'A'

    def test_classify_offers_the_merge_but_still_says_conflict(self):
        base = 'a\nb\nc\n'
        disk = 'A\nb\nc\n'
        draft = 'a\nb\nC\n'
        got = classify(disk.encode('utf-8'), sha1_16(base), draft,
                       base_text=base)
        assert got.state == CONFLICT          # 合并是"选项"，不是结论
        assert got.as_dict()['merged'] is True

    def test_classify_without_base_text_has_no_candidate(self):
        got = classify(b'a\n', sha1_16('zzz'), 'b\n')
        assert got.merged_text == '' and got.as_dict()['merged'] is False


class TestSessionUsesContentNotMtime:
    """`EditorSession.source_unchanged()`：touch 不该让它变 False。"""

    def _session(self, tmp_path, text='label start:\n    return\n'):
        from storygraph.app.session_table import TableSessionMixin

        path = tmp_path / 'ch.rpy'
        # 用 bytes 写：Windows 的文本模式会把 `\n` 翻成 `\r\n`，
        # 而三态比的是**字节**（`SourceStyle` 管的也是这个），测试要写准。
        path.write_bytes(text.encode('utf-8'))
        session = object.__new__(TableSessionMixin)
        session.script_path = str(path)
        session._source_fingerprint = TableSessionMixin._fingerprint_of(
            str(path))
        return session, path

    def test_a_touched_file_is_still_trustworthy(self, tmp_path):
        session, path = self._session(tmp_path)
        assert session.source_state() == REBASE
        assert session.source_unchanged()

        st = path.stat()
        os.utime(path, (st.st_atime + 60, st.st_mtime + 60))
        assert session.source_state() == REBASE
        assert session.source_unchanged(), '只改了 mtime，内容一个字没变'

    def test_a_real_edit_is_a_conflict(self, tmp_path):
        session, path = self._session(tmp_path)
        path.write_text('label start:\n    "改了"\n    return\n',
                        encoding='utf-8')
        assert session.source_state() == CONFLICT
        assert not session.source_unchanged()

    def test_a_draft_that_matches_the_disk_is_adopted(self, tmp_path):
        session, path = self._session(tmp_path)
        assert session.source_state(
            draft_text='label start:\n    return\n') == ADOPT
        assert session.source_state(draft_text='别的\n') == REBASE

    def test_a_deleted_file_is_a_conflict(self, tmp_path):
        session, path = self._session(tmp_path)
        path.unlink()
        assert session.source_state() == CONFLICT
        assert not session.source_unchanged()
