"""
结构编辑的**多级撤销** —— 见不变量 79。

现状（改之前）：`delete_beat` 只留 `_last_removed`（**单级**），而任何一次结构
编辑都会 `forget_undo()`（内容撤销栈作废）—— 连插两张卡再想退回第一步没办法。

现在：会话里有一个**有界**的结构撤销栈，记"做了什么"而不是快照：
  · 插卡 → `('insert', 落点)`；撤销 = 删掉那一拍；
  · 删卡 → `('delete', 原拍号, Beat)`；撤销 = 原位插回；
  · 结构编辑**不**清这个栈（拍号没变，索引依然有效）；
  · `load()`（换文件 / 原地导出后重新对齐）清空 —— 索引全变了。

UI：卡片页那颗按钮与 Ctrl+Z 都接到它上面（内容撤销优先，没有内容可撤时退到
结构撤销）。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.app.editor import EditorSession
from storygraph.story.beat_model import Beat

SRC = '''label a:
    e "第一句"
    e "第二句"
    e "第三句"
'''


def _session(tmp: Path) -> EditorSession:
    f = tmp / 'ch.rpy'
    f.write_text(SRC, encoding='utf-8')
    s = EditorSession(project_root=tmp)
    s.load(f)
    return s


def _texts(s: EditorSession) -> list:
    return [str(b.text or '') for b in s.beats]


class TestMultiLevelUndo:
    def test_two_inserts_undo_one_by_one(self, tmp_path):
        s = _session(tmp_path)
        before = _texts(s)
        i1 = s.insert_beat(0, after=True)['index']
        s.beats[i1].text = '新一'
        i2 = s.insert_beat(1, after=True)['index']
        s.beats[i2].text = '新二'
        assert _texts(s)[:4] == [before[0], '新一', '新二', before[1]], _texts(s)

        assert s.undo_structural()['ok'] is True
        assert _texts(s)[:3] == [before[0], '新一', before[1]], _texts(s)
        assert s.undo_structural()['ok'] is True
        assert _texts(s) == before, _texts(s)
        assert s.undo_structural()['ok'] is False, '栈空了还报成功'
        assert s.can_undo_structural() is False

    def test_two_deletes_undo_in_reverse_order(self, tmp_path):
        s = _session(tmp_path)
        before = _texts(s)
        s.delete_beat(0)
        s.delete_beat(0)                  # 现在剩最后一句
        assert _texts(s) == [before[2]], _texts(s)

        assert s.undo_structural()['note'] == '恢复删除'
        assert _texts(s) == before[1:], _texts(s)
        assert s.undo_structural()['ok'] is True
        assert _texts(s) == before, _texts(s)

    def test_mixed_insert_and_delete(self, tmp_path):
        s = _session(tmp_path)
        before = _texts(s)
        s.insert_beat(0, after=True)       # 在第 1 张后面插一张空卡（新拍号 1）
        s.delete_beat(0)                   # 删掉原来的第 1 张 → 空卡落到 0 位
        assert _texts(s)[0] == '', _texts(s)
        assert _texts(s)[1:] == before[1:], _texts(s)
        s.undo_structural()                # 撤销那次删除 → 原第 1 句回到 0 位
        assert _texts(s)[0] == before[0], _texts(s)
        s.undo_structural()                # 撤销那次插入
        assert _texts(s) == before, _texts(s)

    def test_the_stack_is_bounded(self, tmp_path):
        s = _session(tmp_path)
        for _ in range(EditorSession.STRUCT_UNDO_LIMIT + 10):
            s.insert_beat(0, after=True)
            if s.can_undo_structural():
                s.undo_structural()        # 又插又撤，栈不会无限涨
        assert s.struct_undo_depth() <= EditorSession.STRUCT_UNDO_LIMIT

    def test_reload_clears_the_stack(self, tmp_path):
        s = _session(tmp_path)
        s.insert_beat(0, after=True)
        assert s.can_undo_structural() is True
        s.load(tmp_path / 'ch.rpy')        # 换文件 / 原地导出后重新对齐
        assert s.can_undo_structural() is False, '重载之后索引全变了，不许再撤'

    def test_content_edits_do_not_clear_it(self, tmp_path):
        """内容编辑（保存这一拍）**不许**动结构栈 —— 两者互不干扰。"""
        s = _session(tmp_path)
        s.insert_beat(0, after=True)
        fields = s.fields(1)
        fields['Text'] = '改过的'          # 字段键是 Excel 口径的大写名
        s.save(1, fields, s.cards(1))
        assert s.beats[1].text == '改过的'
        assert s.can_undo_structural() is True

    def test_the_old_single_level_api_still_works(self, tmp_path):
        """`restore_last_removed` 保留（老调用方/老按钮的兜底行为）。"""
        s = _session(tmp_path)
        before = _texts(s)
        s.delete_beat(1)
        assert s.has_removed() is True
        assert s.restore_last_removed()['ok'] is True
        assert _texts(s) == before, _texts(s)

    def test_undo_after_restore_is_the_inverse(self, tmp_path):
        """「恢复删除」本身也是一次结构操作，可以再被撤销。"""
        s = _session(tmp_path)
        before = _texts(s)
        s.delete_beat(1)
        s.restore_last_removed()
        assert _texts(s) == before
        assert s.can_undo_structural() is True
        assert s.undo_structural()['note'] == '撤销插卡'
        assert _texts(s) == [before[0], before[2]], _texts(s)


class TestUiWiring:
    def test_the_button_prefers_the_structural_stack(self, ui_window):
        """卡片页那颗按钮先走结构栈（插卡也能被它撤掉）。"""
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._reload_list()
        n0 = len(w.beats)
        w._show_index(0)
        w._insert_btn.click()
        assert len(w.beats) == n0 + 1
        w.restore_card()
        assert len(w.beats) == n0, '插卡没被那颗按钮撤回来'

    def test_ctrl_z_does_not_touch_the_structure(self, ui_window):
        """
        Ctrl+Z **只管内容**：没有内容可撤时它什么也不做，绝不顺手删卡/放回卡。

        （第一版让它退到结构撤销，`test_main_window_snapshot` 第 56 步当场抓到：
        连按撤销会凭空多出一张卡 —— 一个键盘动作改结构太意外了。）
        """
        w = ui_window
        w._set_page(w.PAGE_CARD)
        w._reload_list()
        n0 = len(w.beats)
        w._show_index(0)
        w._insert_btn.click()
        assert w._session.can_undo_structural() is True, '前提：栈里有插卡'
        w.undo_current_beat()
        assert len(w.beats) == n0 + 1, 'Ctrl+Z 不该动结构'
        w.restore_card()                    # 结构撤销走那颗按钮
        assert len(w.beats) == n0
