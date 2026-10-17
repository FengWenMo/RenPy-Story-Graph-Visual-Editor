"""
整章树形真源（`story.chapter_tree`，2026-10-18）
================================================

扁平拍列表回答不了"这一拍属于哪个块"，于是删菜单头 = 选项变裸旁白、
删循环头 = 循环体掉到块外。这个文件守：

  · 建树 → `flatten()` **逐拍同一对象、顺序不变**（真实解析的形状）；
  · 删叶子：菜单/选项/循环结构不动，渲染仍是一个块；
  · 删块头且还有块体：**拒绝**（`blocked='has_children'`），不许静默拆块；
  · 插在选项体/条件体里：新拍继承结构位与条件，渲染落在同一个块里；
  · 移动叶子：同块内换序、跨块重写结构位，撤销能还原；
  · 老数据（`nest_path is None`）留在场景层，不被瞎猜。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.story.beat_model import Beat            # noqa: E402
from storygraph.story.chapter import ChapterDocument    # noqa: E402
from storygraph.story.chapter_tree import ChapterTree   # noqa: E402

SRC = '''init python:
    persistent.x = 1

label ch:
    scene bg room
    "one"
    if flag:
        "two"
        menu:
            "A":
                e "a1"
            "B":
                e "b1"
    "three"
    while n < 2:
        e "loop"
    return
'''


def _session(tmp_path) -> EditorSession:
    p = tmp_path / 'ch.rpy'
    p.write_text(SRC, encoding='utf-8')
    s = EditorSession()
    s.load(p)
    return s


def _idx(s, text: str) -> int:
    return next(i for i, b in enumerate(s.beats)
                if str(getattr(b, 'text', '') or '') == text)


def _line_with(text: str, src: str) -> str:
    return next(ln for ln in src.splitlines() if text in ln)


class TestBuild:
    def test_tree_round_trips_the_parsed_shape(self, tmp_path):
        s = _session(tmp_path)
        tree = s.chapter.tree
        assert tree.matches(s.beats), 'flatten() 没还原成同一批拍'
        assert tree.validate() == []
        kinds = {n.kind for n in tree.root.walk()}
        assert {'scene', 'if', 'menu', 'option', 'loop'} <= kinds, kinds
        # 菜单在 if 里面：路径逐层对得上
        menu = next(n for n in tree.blocks() if n.kind == 'menu')
        assert menu.path == 'if:1>menu:1', menu.path
        assert menu.parent.kind == 'if'

    def test_file_blocks_live_on_the_document_not_on_a_beat(self, tmp_path):
        s = _session(tmp_path)
        assert 'persistent.x' in s.chapter.file_blocks
        # 顶层块不是树上的节点，也不参与拍数
        assert len(s.beats) == s.chapter.tree.flatten().__len__()

    def test_legacy_none_path_stays_at_scene_level(self):
        beats = [Beat(seq=1, label='a', text='x', nest_path=None),
                 Beat(seq=2, label='a', text='y', nest_path=None)]
        doc = ChapterDocument.from_beats(beats)
        tree = doc.tree
        nodes = [tree.node_of(b) for b in beats]
        assert all(n is not None and n.parent.kind == 'scene' for n in nodes)
        assert tree.flatten() == beats

    def test_outline_names_the_blocks(self, tmp_path):
        s = _session(tmp_path)
        text = '\n'.join(s.chapter.outline())
        assert '菜单' in text and '循环' in text and 'if 块' in text


class TestDelete:
    def test_deleting_a_body_line_keeps_the_menu_renderable(self, tmp_path):
        s = _session(tmp_path)
        a1 = _idx(s, 'a1')
        res = s.delete_beat(a1)
        assert res['ok'], res
        text = s.compile().rpy_text
        assert 'menu:' in text and '"A":' in text and '"B":' in text
        assert 'e "a1"' not in text
        assert s.chapter.tree.matches(s.beats)

    def test_undo_puts_the_line_back_into_its_option_body(self, tmp_path):
        s = _session(tmp_path)
        a1 = _idx(s, 'a1')
        old_path = s.beats[a1].nest_path
        s.delete_beat(a1)
        assert s.undo_structural()['ok']
        back = s.beats[a1]
        assert back.text == 'a1'
        assert back.nest_path == old_path == 'if:1>menu:1>A'
        line = _line_with('e "a1"', s.compile().rpy_text)
        assert line.startswith(' ' * 12), line

    def test_deleting_a_menu_header_with_body_is_refused(self, tmp_path):
        s = _session(tmp_path)
        menu_i = next(i for i, b in enumerate(s.beats) if b.kind == 'menu')
        res = s.delete_beat(menu_i)
        assert res['ok'] is False
        assert res['blocked'] == 'has_children', res
        assert res['blocked_count'] == 4, res   # 2 个选项 + 2 句选项体
        assert s.beats[menu_i].kind == 'menu', '被拒绝了却还是删掉了'
        assert 'menu:' in s.compile().rpy_text

    def test_deleting_an_option_header_with_body_is_refused(self, tmp_path):
        s = _session(tmp_path)
        opt = next(i for i, b in enumerate(s.beats) if b.kind == 'menu_option')
        res = s.delete_beat(opt)
        assert res['ok'] is False and res['blocked'] == 'has_children'
        assert res['blocked_count'] == 1

    def test_cascade_delete_removes_the_whole_block_and_undo_restores_it(self, tmp_path):
        """
        用户明确要"删整块"时，块头 + 选项 + 选项体一起走；撤销要能原样放回
        （`nest_path` 跟着回来，菜单结构不被压平）。
        """
        s = _session(tmp_path)
        menu_i = next(i for i, b in enumerate(s.beats) if b.kind == 'menu')
        before = [(b.kind, b.text, b.nest_path) for b in s.beats]
        res = s.delete_beat(menu_i, cascade=True)
        assert res['ok'], res
        assert len(s.beats) == len(before) - 5, (len(before), len(s.beats))
        assert not any(b.kind == 'menu' for b in s.beats)
        assert s.chapter.tree.matches(s.beats)

        undo = s.undo_structural()
        assert undo['ok'], undo
        assert [(b.kind, b.text, b.nest_path) for b in s.beats] == before
        assert s.chapter.tree.matches(s.beats)

    def test_restore_last_removed_handles_a_cascade(self, tmp_path):
        s = _session(tmp_path)
        menu_i = next(i for i, b in enumerate(s.beats) if b.kind == 'menu')
        before = [b.text for b in s.beats]
        assert s.delete_beat(menu_i, cascade=True)['ok']
        assert s.restore_last_removed()['ok']
        assert [b.text for b in s.beats] == before
        assert s.chapter.tree.matches(s.beats)

    def test_deleting_an_empty_option_header_is_allowed(self, tmp_path):
        """先把选项体那一句删掉，空选项头就可以删（不是"永远不许删块头"）。"""
        s = _session(tmp_path)
        a1 = _idx(s, 'a1')
        opt = next(i for i, b in enumerate(s.beats)
                   if b.kind == 'menu_option' and b.text == 'A')
        assert s.delete_beat(a1)['ok']
        # 删除后选项头位置没变（它在前）
        res = s.delete_beat(opt)
        assert res['ok'], res
        assert '"A":' not in s.compile().rpy_text


class TestInsert:
    def test_insert_after_option_body_stays_in_that_option(self, tmp_path):
        s = _session(tmp_path)
        res = s.insert_beat(_idx(s, 'a1'))
        assert res['ok'], res
        new = s.beats[res['index']]
        assert new.nest_path == 'if:1>menu:1>A', new.nest_path
        new.kind, new.speaker, new.text = 'say', 'e', '补充一句'
        line = _line_with('补充一句', s.compile().rpy_text)
        assert line.startswith(' ' * 12), line
        # 选项 B 里不许出现它
        text = s.compile().rpy_text
        b_block = text.split('"B":', 1)[1]
        assert '补充一句' not in b_block

    def test_insert_after_conditional_line_inherits_condition(self, tmp_path):
        s = _session(tmp_path)
        res = s.insert_beat(_idx(s, 'two'))
        new = s.beats[res['index']]
        assert new.nest_path == 'if:1', new.nest_path
        assert new.condition == 'flag', new.condition
        new.text = '还在 if 里'
        text = s.compile().rpy_text
        line = _line_with('还在 if 里', text)
        assert line.startswith(' ' * 8), line

    def test_insert_before_first_beat_keeps_top_level_blocks(self, tmp_path):
        s = _session(tmp_path)
        res = s.insert_beat(0, after=False)
        assert res['index'] == 0
        assert s.beats[0].nest_path == ''
        text = s.compile().rpy_text
        assert text.count('persistent.x = 1') == 1

    def test_insert_after_a_menu_header_does_not_eat_the_menu(self, tmp_path):
        """在菜单头后面插卡：结果要么进菜单体、要么被拒绝 —— 不许把菜单拆散。"""
        s = _session(tmp_path)
        menu_i = next(i for i, b in enumerate(s.beats) if b.kind == 'menu')
        res = s.insert_beat(menu_i, after=True)
        assert res['ok'], res
        text = s.compile().rpy_text
        assert 'menu:' in text and '"A":' in text and '"B":' in text


class TestMove:
    def test_moving_a_line_to_another_option_rewrites_its_path(self, tmp_path):
        s = _session(tmp_path)
        a1 = _idx(s, 'a1')
        b1 = _idx(s, 'b1')
        # `dst` 是**移动后的扁平下标**：删掉 a1 后 b1 前移一位，
        # 插在 b1 原来的位置上 = 落在 b1 后面。
        res = s.move_beat(a1, b1)
        assert res['ok'], res
        moved = s.beats[res['index']]
        assert moved.text == 'a1'
        assert moved.nest_path == 'if:1>menu:1>B', moved.nest_path
        text = s.compile().rpy_text
        b_block = text.split('"B":', 1)[1]
        assert 'e "a1"' in b_block.split('return', 1)[0]
        assert s.chapter.tree.matches(s.beats)

    def test_undo_move_restores_the_original_option(self, tmp_path):
        s = _session(tmp_path)
        a1 = _idx(s, 'a1')
        b1 = _idx(s, 'b1')
        s.move_beat(a1, b1)
        res = s.undo_structural()
        assert res['ok'], res
        assert s.beats[a1].text == 'a1'
        assert s.beats[a1].nest_path == 'if:1>menu:1>A', s.beats[a1].nest_path
        line = _line_with('e "a1"', s.compile().rpy_text)
        assert line.startswith(' ' * 12)

    def test_moving_a_block_header_is_refused(self, tmp_path):
        s = _session(tmp_path)
        opt = next(i for i, b in enumerate(s.beats) if b.kind == 'menu_option')
        res = s.move_beat(opt, 0)
        assert res['ok'] is False and res['blocked'] == 'header', res
        assert s.chapter.tree.matches(s.beats)


class TestExternalReplacement:
    def test_reassigned_beats_rebuild_the_tree(self, tmp_path):
        s = _session(tmp_path)
        s.beats = list(s.beats)
        s._sync_chapter()
        assert s.chapter.tree.matches(s.beats)
        assert s.chapter.tree.validate() == []

    def test_tree_is_not_rebuilt_when_projection_matches(self, tmp_path):
        s = _session(tmp_path)
        s._sync_chapter()               # 先让会话与文档接管同一份列表
        tree = s.chapter.tree
        s._sync_chapter()
        assert s.chapter.tree is tree, '没换拍却重建了树（结构编辑会被冲掉）'
