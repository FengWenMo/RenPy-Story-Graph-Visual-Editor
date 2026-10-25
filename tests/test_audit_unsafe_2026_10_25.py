# -*- coding: utf-8 -*-
"""
判据 · **落盘安全**（2026-10-25，`docs/P0审计_不安全因素_2026-10-25.md`）
=====================================================================

四条"会改坏别人项目代码（或 100% 走不通）"的问题：

  · P0-2 BOM 章节：卡片页原地导出必然被 `OPAQUE_LOST` 误拒
  · P0-1 选择点**追加**把整份文件换行从 CRLF 改写成 LF（还丢 BOM）
  · P1-2 非 UTF-8 文件载入静默 0 拍 / 追加时抛未捕获异常
  · P1-1 UI 表格导出不过程引擎闸门（同一操作，CLI 过）
  · P1-3 选择点新文件不带生成标记（日后会被判成"手写"拒写）

判据一律是**字节**：BOM 在不在、CRLF 有几条、原有行是否逐字节不变。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.app.editor import EditorSession
from storygraph.story.choice_point import apply_choice_point, plan_choice_point

SRC = 'label start:\n    e "台词"\n    return\n'
SRC_CRLF_BOM = ('\ufeff# 文件头注释\r\n'
                'label start:\r\n'
                '    e "台词"\r\n'
                '    return\r\n')


def _project(tmp_path, data: bytes, name='script.rpy'):
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / name
    path.write_bytes(data)
    return EditorSession(project_root=tmp_path), path


# ======================================================================
#  P0-2 · BOM 文件不许被自家门禁误拒
# ======================================================================
class TestBomChapter:
    def test_bom_crlf_inplace_export_works_and_keeps_the_bytes(self, tmp_path):
        """
        从前 `_source_text_for_guard` 用 `Path.read_text`（不剥 BOM）→
        门禁判"第 1 行顶层语句没出现在产物里" → `OPAQUE_LOST` → 拒写。
        现在：导出成功，且 BOM / CRLF / 其它行一个字节不变。
        """
        session, path = _project(tmp_path, SRC_CRLF_BOM.encode('utf-8'))
        assert session.load(path) >= 1
        session.apply(0, {'Text': '改过的'})
        res = session.export(path)
        assert res.get('ok'), res
        raw = path.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf'), 'BOM 被丢了'
        assert raw.count(b'\r\n') == raw.count(b'\n'), 'CRLF 被改写了'
        text = raw.decode('utf-8-sig')
        assert '改过的' in text
        # 产物首行会多一条生成标记（既有行为），但**作者那一行还在**、
        # 而且行尾仍是 CRLF
        assert '# 文件头注释\r\nlabel start:\r\n' in text, text[:120]
        assert 'e "改过的"\r\n' in text

    def test_zero_change_with_bom_is_byte_identical(self, tmp_path):
        session, path = _project(tmp_path, SRC_CRLF_BOM.encode('utf-8'))
        session.load(path)
        res = session.export(path)
        assert res.get('ok'), res
        assert path.read_bytes() == SRC_CRLF_BOM.encode('utf-8')


# ======================================================================
#  P0-1 · 选择点追加：载体风格必须还原
# ======================================================================
class TestChoicePointAppend:
    def _fixture(self, tmp_path, newline='\r\n', bom=True):
        data = (('' if not bom else '\ufeff')
                + '# 手写注释' + newline
                + 'label start_choice_1:' + newline
                + '    menu:' + newline
                + '        "一":' + newline
                + '            return' + newline)
        session, script = _project(
            tmp_path, SRC.replace('\n', newline).encode('utf-8'))
        cp = tmp_path / 'game' / 'script_choices.rpy'
        cp.write_bytes(data.encode('utf-8'))
        return session, script, cp

    def test_append_keeps_crlf_and_bom(self, tmp_path):
        session, script, cp = self._fixture(tmp_path)
        before = cp.read_bytes()
        plan = plan_choice_point(tmp_path, script, 'start',
                                 options=('选项A', '选项B'))
        assert plan.ok, plan.blocking
        assert not plan.creates_file
        res = apply_choice_point(plan, tmp_path)
        assert res.get('ok'), res
        raw = cp.read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf'), 'BOM 被丢了'
        assert raw.count(b'\n') == raw.count(b'\r\n'), (
            f'追加后出现裸 LF：{raw.count(b"\\n") - raw.count(b"\\r\\n")} 处')
        assert raw.startswith(before), '原有内容不是"逐字节保留"'
        assert '选项A' in raw.decode('utf-8-sig')

    def test_append_keeps_lf_file_lf(self, tmp_path):
        session, script, cp = self._fixture(tmp_path, newline='\n', bom=False)
        before = cp.read_bytes()
        plan = plan_choice_point(tmp_path, script, 'start', options=('A', 'B'))
        assert apply_choice_point(plan, tmp_path).get('ok')
        raw = cp.read_bytes()
        assert b'\r' not in raw, 'LF 文件被写成了 CRLF'
        assert raw.startswith(before)

    def test_new_file_carries_the_generator_marker(self, tmp_path):
        """P1-3：新文件不带生成标记 = 日后任何重写都会被判"手写"拒写。"""
        session, script = _project(tmp_path, SRC.encode('utf-8'))
        plan = plan_choice_point(tmp_path, script, 'start', options=('A', 'B'))
        assert plan.ok and plan.creates_file
        assert apply_choice_point(plan, tmp_path).get('ok')
        text = Path(plan.path).read_text(encoding='utf-8')
        from storygraph.story.generation_guard import GENERATOR_MARKER
        assert GENERATOR_MARKER in text, text[:200]


# ======================================================================
#  P1-2 · 非 UTF-8：静默变 0 拍 → 一句人话
# ======================================================================
class TestNonUtf8:
    def test_gbk_load_raises_with_an_actionable_message(self, tmp_path):
        from storygraph.app.editor import EditorSession as S
        game = tmp_path / 'game'
        game.mkdir()
        p = game / 'script.rpy'
        p.write_bytes('label start:\n    e "中文"\n'.encode('gbk'))
        session = S(project_root=tmp_path)
        with pytest.raises(ValueError) as ei:
            session.load(p)
        msg = str(ei.value)
        assert 'UTF-8' in msg and 'GBK' in msg or 'ANSI' in msg, msg

    def test_choice_point_append_on_non_utf8_reports_instead_of_raising(
            self, tmp_path):
        session, script = _project(tmp_path, SRC.encode('utf-8'))
        cp = tmp_path / 'game' / 'script_choices.rpy'
        cp.write_bytes('# 注释\nlabel start_choice_1:\n    menu:\n'.encode('gbk'))
        plan = plan_choice_point(tmp_path, script, 'start', options=('A', 'B'))
        assert plan.ok, plan.blocking
        res = apply_choice_point(plan, tmp_path)
        assert not res.get('ok'), res
        assert 'UTF-8' in str(res.get('error')), res
        assert cp.read_bytes().startswith('# 注释'.encode('gbk')), '坏文件被写了'


# ======================================================================
#  P1-1 · 表格导出也要过引擎闸门
# ======================================================================
class TestTableExportGate:
    def test_apply_export_passes_engine_lint(self, tmp_path, monkeypatch):
        from storygraph.app import tables

        seen = {}

        class _Art:
            written = True
            review_path = None
            out_path = tmp_path / 'game' / 'out.rpy'
            issues = []

        def _fake_write(text, target, **kw):
            seen.update(kw)
            return _Art()

        # `tables` 是 `from … import write_generation`（模块级名字）→ 打它
        monkeypatch.setattr(tables, 'write_generation', _fake_write)
        out = tables.apply_export(
            {'text': 'label a:\n    return\n',
             'out_path': str(tmp_path / 'game' / 'out.rpy'),
             'source_path': None, 'merged_from': None},
            project_root=tmp_path)
        assert out.get('ok'), out
        assert seen.get('engine_lint') is True, seen
