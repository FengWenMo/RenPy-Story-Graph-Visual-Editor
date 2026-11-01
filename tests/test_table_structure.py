"""
`app.table_structure` —— 表格结构编辑的撤销栈与四条逆操作（服务化第四刀）。

以前这四条判据长在 `session_table.undo_structural` 里，要验一条得先
**建会话 + 加载真项目 + 插删几次卡**；红了也分不清是"逆操作算错"还是
"会话状态不对"。现在它们只跟 `host` 打交道，这里就用一个假 host 钉住：

  · 插卡撤销 = 删掉落点那一拍；
  · 删卡撤销 = 原位插回（撞上单级 `_last_removed` 时顺手清掉）；
  · 清空撤销 = 把存下来的字段放回去；
  · 移动撤销 = 移回去 + 还原 `nest_path`/`condition` + 重建树；
  · 四条都会在末尾统一下发顶层块、重排拍号、作废内容撤销、重算脏标记。

外加两条"归属"守卫：栈是**构造时就在**的对象（不再靠 `getattr` 懒创建），
以及仓库里不许再冒出第二个平行的 `_struct_undo` 列表。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from storygraph.app import table_structure as TS
from storygraph.app.editor import EditorSession
from storygraph.app.table_structure import (ClearOp, DeleteOp, InsertOp,
                                            MoveOp, StructureUndoStack)
from storygraph.story.chapter_tree import ChapterTreeError
from storygraph.story.beat_model import Beat


class _FakeDoc:
    """只做逆操作真正用到的那四件事；`beats` 与 host 共用同一个列表语义。"""

    def __init__(self, beats=None):
        self.beats = list(beats or [])
        self.calls = []

    def remove_beat_at(self, at):
        self.calls.append(('remove', at))
        if at >= len(self.beats):
            raise ChapterTreeError('has_children', '那一拍不在了')
        self.beats = self.beats[:at] + self.beats[at + 1:]
        return [self.beats[at]] if at < len(self.beats) else []

    def adopt(self, beats):
        self.calls.append(('adopt', list(beats)))
        self.beats = beats

    def move_beat(self, src, dst):
        self.calls.append(('move', src, dst))
        beat = self.beats.pop(src)
        self.beats.insert(dst, beat)
        return dst

    def _rebuild_tree(self):
        self.calls.append(('rebuild',))


class _FakeHost:
    """撤销需要的会话表面（真实实现见 `SessionTableMixin`）。"""

    def __init__(self, beats=None):
        self.beats = list(beats or [])
        self._last_removed = None
        self.doc = _FakeDoc(self.beats)
        self.after = []

    def _chapter_doc(self):
        return self.doc

    def _sync_chapter(self):
        self.after.append('sync')

    def _renumber(self):
        self.after.append('renumber')

    def forget_undo(self):
        self.after.append('forget')

    def refresh_edits_flag(self):
        self.after.append('refresh')


def _beats(*texts):
    return [Beat(seq=i, label='a', kind='say', text=t)
            for i, t in enumerate(texts)]


class TestInsertUndo:
    def test_it_removes_the_beat_that_was_inserted(self):
        host = _FakeHost(_beats('一', '二', '三'))
        res = TS.undo(InsertOp(1), host)
        assert res['ok'] is True
        assert res['note'] == '撤销插卡'
        assert [b.text for b in host.beats] == ['一', '三']

    def test_the_reported_index_never_points_past_the_end(self):
        """撤掉最后一拍时，返回的拍号要落在新的末拍上（不是那个已不存在的号）。"""
        host = _FakeHost(_beats('一', '二'))
        assert TS.undo(InsertOp(1), host)['index'] == 0

    def test_a_gone_landing_point_is_refused_not_guessed(self):
        host = _FakeHost(_beats('一'))
        res = TS.undo(InsertOp(7), host)
        assert res['ok'] is False and '落点' in res['error']
        assert host.after == [], '被拒的撤销不该动会话状态'

    def test_the_document_refusal_is_surfaced(self):
        host = _FakeHost(_beats('一', '二'))
        host.doc.remove_beat_at = _boom_tree
        res = TS.undo(InsertOp(0), host)
        assert res['ok'] is False and '不在了' in res['error']


class TestDeleteUndo:
    def test_it_puts_the_beats_back_where_they_were(self):
        host = _FakeHost(_beats('一', '三'))
        res = TS.undo(DeleteOp(1, _beats('二')), host)
        assert res['ok'] is True and res['note'] == '恢复删除'
        assert [b.text for b in host.beats] == ['一', '二', '三']

    def test_a_stale_index_is_clamped_to_the_end(self):
        host = _FakeHost(_beats('一'))
        res = TS.undo(DeleteOp(99, _beats('二')), host)
        assert res['ok'] is True
        assert [b.text for b in host.beats] == ['一', '二']

    def test_it_clears_the_single_level_restore_when_they_agree(self):
        host = _FakeHost(_beats('一', '三'))
        host._last_removed = (1, _beats('二'))
        TS.undo(DeleteOp(1, _beats('二')), host)
        assert host._last_removed is None

    def test_it_leaves_the_single_level_restore_alone_when_they_differ(self):
        host = _FakeHost(_beats('一', '三'))
        other = (0, _beats('零'))
        host._last_removed = other
        TS.undo(DeleteOp(1, _beats('二')), host)
        assert host._last_removed is other

    def test_it_asks_the_document_to_rebuild_from_the_restored_beats(self):
        host = _FakeHost(_beats('一'))
        TS.undo(DeleteOp(0, _beats('零')), host)
        assert ('adopt', ) == host.doc.calls[0][:1]


class TestClearUndo:
    def test_it_restores_the_saved_fields(self):
        host = _FakeHost(_beats(''))
        saved = {'text': '原来的台词', 'speaker': 'e'}
        res = TS.undo(ClearOp(0, saved, 'text'), host)
        assert res['ok'] is True and res['note'] == '撤销清空'
        assert host.beats[0].text == '原来的台词'
        assert host.beats[0].speaker == 'e'

    def test_a_gone_beat_is_refused(self):
        host = _FakeHost(_beats('一'))
        res = TS.undo(ClearOp(3, {'text': 'x'}), host)
        assert res['ok'] is False and '不在了' in res['error']


class TestMoveUndo:
    def test_it_moves_back_and_restores_the_structure_slot(self):
        host = _FakeHost(_beats('一', '二'))
        beat = host.beats[1]
        beat.nest_path = 'menu:1>B'
        res = TS.undo(MoveOp(1, 0, beat, 'if:0', 'a > 1'), host)
        assert res['ok'] is True and res['note'] == '撤销移动'
        assert host.doc.calls[-1] == ('rebuild',)
        assert [b.text for b in host.beats] == ['二', '一']
        assert (beat.nest_path, beat.condition) == ('if:0', 'a > 1')

    def test_the_document_refusal_is_surfaced(self):
        host = _FakeHost(_beats('一', '二'))
        host.doc.move_beat = _boom_tree
        res = TS.undo(MoveOp(1, 0, host.beats[1], '', ''), host)
        assert res['ok'] is False and '不在了' in res['error']


class TestCommonTail:
    def test_every_successful_undo_runs_the_same_four_steps(self):
        host = _FakeHost(_beats('一', '二'))
        TS.undo(InsertOp(1), host)
        assert host.after == ['sync', 'renumber', 'forget', 'refresh']

    def test_an_unknown_record_is_named_not_swallowed(self):
        host = _FakeHost(_beats('一'))
        res = TS.undo(('insert', 0), host)
        assert res['ok'] is False and '不认识的结构操作' in res['error']
        assert host.after == []


class TestTheStack:
    def test_pop_on_empty_returns_none_instead_of_raising(self):
        stack = StructureUndoStack()
        assert stack.pop() is None
        assert not stack and len(stack) == 0

    def test_it_only_keeps_the_newest(self):
        stack = StructureUndoStack(limit=3)
        for i in range(5):
            stack.push(InsertOp(i))
        assert len(stack) == 3
        assert [stack.pop().at, stack.pop().at, stack.pop().at] == [4, 3, 2]

    def test_clear_empties_it(self):
        stack = StructureUndoStack()
        stack.push(InsertOp(0))
        stack.clear()
        assert not stack and stack.pop() is None

    def test_the_session_owns_one_from_construction(self):
        """不再靠 `getattr(self, '_struct_undo', None)` 懒创建。"""
        s = EditorSession()
        assert isinstance(s._struct_edits, StructureUndoStack)
        assert s.struct_undo_depth() == 0
        assert s.can_undo_structural() is False

    def test_the_session_limit_is_the_service_limit(self):
        assert EditorSession.STRUCT_UNDO_LIMIT == TS.STRUCT_UNDO_LIMIT


def _boom_tree(*_a, **_k):
    raise ChapterTreeError('has_children', '那一拍不在了')


#: 仓库根（找 `storygraph/` 用）
ROOT = Path(__file__).resolve().parent.parent


class TestOwnershipGuards:
    def test_no_module_keeps_a_second_structural_stack(self):
        """`_struct_undo` 这个旧名字**不许**再出现 —— 平行栈 = 两条真相。

        （`regex` 用"前面不能是标识符字符"这条更严的判据，这样
        `_push_struct_undo` 那种"名字里带了它"的写法不算命中。
        `table_structure.py` 自己跳过：它的模块 docstring 要写清"从前叫什么"，
        那是迁移记录，不是栈。）
        """
        pattern = re.compile(r'(?<![\w])_struct_undo\b')
        hits = []
        for path in (ROOT / 'storygraph').rglob('*.py'):
            if path.name == 'table_structure.py':
                continue
            for n, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), start=1):
                if pattern.search(line):
                    hits.append(f'{path.relative_to(ROOT).as_posix()}:{n}')
        assert hits == [], hits

    def test_the_inverse_operations_live_in_one_place(self):
        """四条逆操作的**提示语**只许在那一个模块里写（别处只能调 `TS.undo`）。"""
        notes = ("'撤销插卡'", "'恢复删除'", "'撤销清空'", "'撤销移动'")
        hits = []
        for path in (ROOT / 'storygraph').rglob('*.py'):
            if path.name == 'table_structure.py':
                continue
            text = path.read_text(encoding='utf-8')
            for note in notes:
                if note in text:
                    hits.append(f'{path.relative_to(ROOT).as_posix()}：{note}')
        assert hits == [], hits

    def test_the_data_model_survives_a_round_trip(self):
        """四个记录都是不可变的：没人能偷偷改历史。"""
        with pytest.raises(Exception):
            InsertOp(0).at = 5
