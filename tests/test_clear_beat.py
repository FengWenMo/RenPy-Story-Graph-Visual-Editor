# -*- coding: utf-8 -*-
"""
分级删除 · 清空（用户 2026-10-25）

    > 当我删掉卡片的时候……我会顺带把一些代码块什么的也删掉……
    > 能不能说：只删掉句子等……而不删掉代码块呢……？

三档：`text`（只清文字）/ `text_stage`（连舞台音频）/ `whole`（整拍删，走
`delete_beat`，不在这里）。核心判据只有一条：

    **清空之后，剩下的行必须逐字节等于"原来的非文字行"** —— 这条成立，
    就证明了"除了那几个通道，产物一个字没动"（不靠枚举字段自觉）。

配套：什么卡不许清空（控制流/块头/清完什么都不剩）、撤销能放回来、
列表里认得出"空拍·只剩代码"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app import beat_clear as BC                # noqa: E402
from storygraph.story.beat_model import Beat, KIND_MENU    # noqa: E402

pytest.importorskip('PySide6')


def _mixed_beat(**kw):
    """一句话 + 舞台 + 音乐 + 代码 —— 就是用户截图里那种拍。"""
    base = dict(label='ch1', kind='say', speaker='jc', text='第一句',
                stage='show ling happy at left', bgm='theme',
                code='msg.reset()\nmsg.add("SP")')
    base.update(kw)
    return Beat(**base)


# ==================================================================
#  ① 计划：清掉什么、留下什么
# ==================================================================

class TestPlanClear:
    def test_text_only_keeps_code_stage_and_audio(self):
        plan = BC.plan_clear(_mixed_beat(), BC.MODE_TEXT)
        assert plan.ok, plan.blocking
        assert plan.drop_lines == ['jc "第一句"']
        assert any('msg.add' in x for x in plan.keep_lines)
        assert any('show ling' in x for x in plan.keep_lines)
        assert any('play music' in x for x in plan.keep_lines)

    def test_text_stage_drops_stage_and_audio_but_keeps_code(self):
        plan = BC.plan_clear(_mixed_beat(), BC.MODE_TEXT_STAGE)
        assert plan.ok, plan.blocking
        assert plan.keep_lines == ['$ msg.reset()', '$ msg.add("SP")'], plan.keep_lines
        assert any('show ling' in x for x in plan.drop_lines)
        assert any('play music' in x for x in plan.drop_lines)

    def test_plan_does_not_touch_the_beat(self):
        beat = _mixed_beat()
        BC.plan_clear(beat, BC.MODE_TEXT_STAGE)
        assert beat.text == '第一句'
        assert beat.code and beat.stage and beat.bgm

    def test_apply_clears_only_what_it_said(self):
        beat = _mixed_beat()
        res = BC.apply_clear(beat, BC.MODE_TEXT)
        assert res['ok']
        assert beat.text == '' and beat.say_tail == ''
        assert beat.code.startswith('msg.reset()')      # 代码一个字节没动
        assert beat.stage == 'show ling happy at left'


# ==================================================================
#  ② 不许清空的那些卡（要整拍删）
# ==================================================================

class TestRefusals:
    def test_a_flow_card_is_refused(self):
        beat = Beat(label='ch1', kind='say', raw='call ch02')
        plan = BC.plan_clear(beat, BC.MODE_TEXT)
        assert not plan.ok and '控制流' in plan.blocking

    def test_a_return_card_is_refused(self):
        beat = Beat(label='ch1', raw='return')
        assert not BC.plan_clear(beat, BC.MODE_TEXT).ok

    def test_a_menu_header_is_refused(self):
        beat = Beat(label='ch1', kind=KIND_MENU, text='选一个')
        plan = BC.plan_clear(beat, BC.MODE_TEXT)
        assert not plan.ok and '菜单头' in plan.blocking

    def test_an_opaque_block_head_is_refused(self):
        beat = Beat(label='ch1', opaque='mycue x:\n    pass', spec_name='mycue',
                    spec_head='mycue x:')
        plan = BC.plan_clear(beat, BC.MODE_TEXT)
        assert not plan.ok and '块' in plan.blocking

    def test_a_text_only_beat_is_refused(self):
        """只有一句台词 → 清空等于删掉它，别造空卡片。"""
        beat = Beat(label='ch1', kind='say', speaker='jc', text='就一句')
        plan = BC.plan_clear(beat, BC.MODE_TEXT)
        assert not plan.ok and '整个删掉' in plan.blocking

    def test_whole_delete_is_not_done_here(self):
        plan = BC.plan_clear(_mixed_beat(), BC.MODE_WHOLE)
        assert not plan.ok


# ==================================================================
#  ③ 自校验：字段枚举漏了就要拒绝，不许产出半个结果
# ==================================================================

class TestSelfCheck:
    def test_a_missed_field_makes_the_plan_refuse(self, monkeypatch):
        """
        把"要清的字段"故意清空 → 副本上什么都没清 → `keep_after` 里
        还留着那句台词 → 校验必须红、必须拒绝。

        这条测的是**安全网本身**：将来有人加了一个新字段（比如
        `stage_v2`）却忘了登记，红的是它，不是用户的文件。
        """
        monkeypatch.setitem(BC._FIELDS, BC.MODE_TEXT, ())
        plan = BC.plan_clear(_mixed_beat(), BC.MODE_TEXT)
        assert not plan.ok
        assert '其余行变了' in plan.blocking


# ==================================================================
#  ④ 会话：改内存 + 撤销
# ==================================================================

class TestSession:
    def _session(self, ui_project):
        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=ui_project)
        s.load(str(ui_project / 'game' / 'script' / 'ch01.rpy'))
        return s

    def test_clear_keeps_the_beat_and_the_code(self, ui_project):
        s = self._session(ui_project)
        beat = s.beats[1]
        beat.code = 'msg.add("SP")'          # 给它一段代码（真实项目里就有）
        beat.stage = 'show ling happy'
        n = len(s)
        res = s.clear_beat(1, BC.MODE_TEXT_STAGE)
        assert res['ok'], res
        assert len(s) == n, '清空不该删卡片'
        assert s.beats[1].text == ''
        assert s.beats[1].code == 'msg.add("SP")'
        assert res['kept_lines'] == ['$ msg.add("SP")']

    def test_undo_structural_puts_the_text_back(self, ui_project):
        s = self._session(ui_project)
        beat = s.beats[1]
        beat.code = 'msg.add("SP")'
        old_text, old_stage = beat.text, beat.stage
        assert s.clear_beat(1, BC.MODE_TEXT_STAGE)['ok']
        assert s.beats[1].text == ''
        assert s.undo_structural()['ok']
        assert s.beats[1].text == old_text
        assert s.beats[1].stage == old_stage

    def test_preview_has_no_side_effect(self, ui_project):
        s = self._session(ui_project)
        before = (s.beats[1].text, s.beats[1].stage, len(s))
        s.clear_preview(1, BC.MODE_TEXT)
        assert (s.beats[1].text, s.beats[1].stage, len(s)) == before


# ==================================================================
#  ⑤ 界面：三档、禁用原因、空拍标记
# ==================================================================

class TestUi:
    def test_three_options_with_counts(self, ui_window):
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._show_index(1)
        opts = w.delete_options(1)
        assert [o['mode'] for o in opts] == ['text', 'text_stage', 'whole']
        for opt in opts:
            assert 'label' in opt and 'desc' in opt
        # 每一档都要有行数（0 也行，但字段必须在）
        assert all(o['lines'] >= 0 for o in opts)

    def test_flow_card_disables_the_two_clear_modes(self, ui_window):
        w = ui_window
        w._set_page(w.PAGE_CARD)
        from storygraph.story.beat_model import Beat
        w._session.beats[1] = Beat(label='ch1', raw='jump ch02')
        opts = w.delete_options(1)
        by_mode = {o['mode']: o for o in opts}
        assert not by_mode['text']['enabled']
        assert '控制流' in by_mode['text']['why']
        body = w._delete_choice_text(1, '控制流', 'jump ch02', opts)
        assert '用不了' in body and '控制流' in body

    def test_empty_beat_shows_up_in_the_list(self, ui_window):
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._show_index(1)
        beat = w._session.beats[1]
        beat.code = 'msg.add("SP")'
        assert w._session.clear_beat(1, BC.MODE_TEXT_STAGE)['ok']
        w._reload_list()
        entry = w._session.list_entry(1)
        assert entry['empty_note'] == '空拍·只剩代码', entry
        assert '空拍·只剩代码' in w._item_text(1)


class TestEmptyNote:
    def test_normal_beat_has_no_note(self):
        assert BC.empty_note(Beat(label='ch1', text='有话', code='x')) == ''

    def test_only_code_left(self):
        assert BC.empty_note(Beat(label='ch1', code='msg.add(1)')) \
            == '空拍·只剩代码'

    def test_nothing_left_has_no_note(self):
        # 什么都没剩 = 该整拍删，不该在列表上挂"空拍"
        assert BC.empty_note(Beat(label='ch1')) == ''
