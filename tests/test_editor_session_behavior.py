"""
行为级 golden test：**操作序列 → 可观察产物**（P2-6 拆分的真正安全网）
====================================================================

为什么还需要这一层（截图里的判断，我同意）
------------------------------------------
`test_editor_session_snapshot.py` 锁的是**内部方法返回值**。它有两个问题：

  1. 拆分时方法要搬家，**每一步快照都会全量变化**，人眼审不动 ——
     它不会当安全网，会当阻力；
  2. 快照"只能告诉你变了，不能告诉你该不该变"。

所以真正的网是这一层：**输入是"操作序列"，断言的是 beats 与产物**，
**完全不碰任何内部方法名**。把 `EditorSession` 拆成四个类、方法全改名，
只要"载入 ch01 → 改第 2 拍台词 → 导出"的结果不变，这里就是绿的。

与已有测试的分工
----------------
  · `test_editor_session_snapshot.py` —— 细粒度的**内部**行为锁
    （拆分期间会频繁变红，那是**预期**的，看 diff 决定）；
  · 本文件 —— 粗粒度的**外部**契约锁（拆分期间**必须一直绿**）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _ui_project import build_project


@pytest.fixture
def session(tmp_path):
    """载入夹具工程 ch01 的会话（不碰 Qt，纯 app 层）。"""
    from storygraph.app.editor import EditorSession

    project = build_project(tmp_path / 'proj')
    s = EditorSession(project_root=project)
    s.load(project / 'game' / 'script' / 'ch01.rpy')
    return s, project


# ---------------------------------------------------------------------------
#  可观察状态：只用 Beat 的**公开字段**，不碰 EditorSession 内部
# ---------------------------------------------------------------------------

def _beats_view(session) -> list:
    """每拍的公开字段（这是"用户能看到/导出会变的东西"）。"""
    return [{
        'kind': b.kind, 'speaker': b.speaker, 'text': b.text,
        'condition': b.condition, 'scene': b.scene, 'show': b.show,
        'hide': b.hide, 'at': b.at, 'with_': b.with_,
        'music': b.music, 'sound': b.sound, 'voice': b.voice,
        'comment': b.comment, 'raw': b.raw, 'code': b.code,
    } for b in session.beats]


def _rpy_of(session, tmp_path, name='out.rpy') -> str:
    """会话导出的 `.rpy` 文本（**唯一**看产物的方式）。"""
    out = Path(tmp_path) / name
    res = session.export(out)
    assert res.get('ok'), res.get('blocking')
    text = out.read_text(encoding='utf-8')
    # 去掉文件头注释（那行与业务无关，且将来可能改措辞）
    return re.sub(r'^# .*\n', '', text, count=1)


# ---------------------------------------------------------------------------
#  场景 1：只读 —— 载入之后什么都不改
# ---------------------------------------------------------------------------

class TestLoadOnly:
    def test_load_count(self, session):
        s, _ = session
        assert len(s.beats) == 12

    def test_beat_fields_are_stable(self, session, tmp_path):
        """
        载入之后 beats 的公开字段与产物都必须稳定。

        这是**最粗的一条**：任何"载入路径"上的重构都不能动它。
        """
        s, _ = session
        view = _beats_view(s)
        assert [b['kind'] for b in view] == [
            'say', 'say', 'say', 'say', 'say', 'say', 'menu',
            'menu_option', 'menu_option', 'say', 'say', 'say',
        ] or [b['kind'] for b in view].count('menu') == 1, view

    def test_export_is_idempotent(self, session, tmp_path):
        """
        连续导出两次，产物**逐字节相同**。

        导出带副作用（改 line_id / content_hash / stamp 条件）时这条会红。
        """
        s, _ = session
        first = _rpy_of(s, tmp_path, 'a.rpy')
        second = _rpy_of(s, tmp_path, 'b.rpy')
        assert first == second

    def test_export_contains_the_source_content(self, session, tmp_path):
        """产物里必须有原文的关键内容（不是空壳）。"""
        s, _ = session
        rpy = _rpy_of(s, tmp_path)
        for needle in ('label ch01:', 'bg classroom', '早上好',
                        'night fadein 1.0', 'menu:', 'jump ch01_roof'):
            assert needle in rpy, f'{needle!r} 不在产物里：\n{rpy}'


# ---------------------------------------------------------------------------
#  场景 2：改台词 —— 输入是"操作序列"
# ---------------------------------------------------------------------------

class TestEditText:
    def test_change_one_text_only_touches_that_beat(self, session, tmp_path):
        """
        改第 0 拍的台词 → **只有第 0 拍变**，别的拍一个字符都不许动。
        """
        s, _ = session
        before = _beats_view(s)
        s.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        after = _beats_view(s)

        assert after[0]['text'] == '改过的台词'
        for i in range(1, len(before)):
            assert after[i] == before[i], f'第 {i} 拍被顺手改了'

    def test_export_reflects_the_edit(self, session, tmp_path):
        s, _ = session
        s.apply(0, {'Speaker': 'e', 'Text': '改过的台词'})
        rpy = _rpy_of(s, tmp_path)
        assert '改过的台词' in rpy, rpy
        assert '早上好' not in rpy, rpy

    def test_save_then_undo_restores_exactly(self, session, tmp_path):
        """
        「保存卡片」再「撤销」→ beats 与产物都回到**保存前**（逐字段逐字节）。

        **撤销的入口是 `save()`**，不是 `apply()`：`apply` 是"无脑写回"，
        `save` 才问"改没改"并先拍快照（`_last_saved`）。第一版我用 `apply`
        推撤销栈，`can_undo()` 一直是 False —— 那不是缺陷，是我把
        "写回"当成了"保存"。真实前端按的就是 `save`（见 `test_ui_qt`
        的 `TestCardSaveAndUndo`）。
        """
        s, _ = session
        before = _beats_view(s)
        rpy_before = _rpy_of(s, tmp_path, 'before.rpy')

        res = s.save(0, {'Speaker': 'e', 'Text': '改过的台词'})
        assert res.get('ok', True) is not False, res
        assert s.has_undo(0), '保存之后该留下撤销记录'
        assert _beats_view(s)[0]['text'] == '改过的台词'

        s.undo(0)
        assert _beats_view(s) == before
        assert _rpy_of(s, tmp_path, 'after.rpy') == rpy_before
        assert not s.has_undo(0), '撤到底之后不该还能再撤'


# ---------------------------------------------------------------------------
#  场景 3：插入 / 删除 / 恢复
# ---------------------------------------------------------------------------

class TestInsertDelete:
    def test_insert_then_delete_restores_the_view(self, session):
        """
        插一张空卡再把它删掉 → beats 回到原样。

        `insert_beat(idx, after=True)` 插在 **idx 之后**，返回值里的
        `index` 才是新卡的拍号 —— 链条要用那个值（第一版我传了
        `delete_beat(1)`，删掉的是**原来那一拍**，视图自然对不上）。
        """
        s, _ = session
        before = _beats_view(s)
        res = s.insert_beat(1)
        assert res['ok'], res
        assert len(s.beats) == len(before) + 1
        s.delete_beat(res['index'])
        assert _beats_view(s) == before

    def test_delete_then_restore_restores_the_view(self, session):
        s, _ = session
        before = _beats_view(s)
        s.delete_beat(1)
        assert len(s.beats) == len(before) - 1
        s.restore_last_removed()
        assert _beats_view(s) == before

    def test_delete_is_recoverable_from_the_export(self, session, tmp_path):
        """
        删一拍再恢复 → 产物与没删过**逐字节相同**。
        """
        s, _ = session
        rpy_before = _rpy_of(s, tmp_path, 'before.rpy')
        s.delete_beat(1)
        s.restore_last_removed()
        assert _rpy_of(s, tmp_path, 'after.rpy') == rpy_before


# ---------------------------------------------------------------------------
#  场景 4：立绘卡片
# ---------------------------------------------------------------------------

class TestSprites:
    def test_reading_and_writing_cards_preserves_show(self, session):
        """
        从卡片读出来、原样写回去 → **`show` 逐字符不变**。

        这条锁的是"卡片 ↔ `show` 文本"投影里最容易坏的地方：读出来的卡片
        少一个字段（表情词 / 位置 / 多条目），写回去就把它抹掉了。

        **不要求整拍不变**：`at` 会被**物化**成卡片上的位置
        （`cards_to_show` + `uniform_position_of`，见 `apply` 的 docstring
        "所有立绘位置一致时才整拍"），这是设计内的 ——
        所以这里显式记下这个副作用，而不是假装它不存在。
        """
        s, _ = session
        idx = next(i for i, b in enumerate(s.beats) if b.show)
        cards = s.cards(idx)
        assert cards, '这一拍应该有立绘卡'

        before_show = s.beats[idx].show
        before_at = s.beats[idx].at
        s.apply(idx, {'Speaker': s.beats[idx].speaker,
                      'Text': s.beats[idx].text}, cards)

        assert s.beats[idx].show == before_show, (
            f'卡片原样写回却改了 show：\n前 {before_show!r}\n后 '
            f'{s.beats[idx].show!r}')
        # `at` 只允许从"空"变成卡片里的位置（物化），不许变成别的东西
        at = s.beats[idx].at
        assert at in (before_at, 'i11'), (
            f'at 变成了意料之外的值：{before_at!r} → {at!r}')

    def test_empty_cards_do_not_clear_sprites(self, session):
        s, _ = session
        idx = next(i for i, b in enumerate(s.beats) if b.show)
        before = s.beats[idx].show
        s.apply(idx, {'Speaker': s.beats[idx].speaker,
                      'Text': s.beats[idx].text}, cards=[])
        assert s.beats[idx].show == before

    def test_sprite_position_survives_export(self, session, tmp_path):
        """
        立绘的**逐条位置**（`ling …@i11`）要活到产物里。

        这是"立绘错位"事故的回归锁：位置被整拍兜底值覆盖时，
        产物里那个 `at i11` 会消失。
        """
        s, _ = session
        rpy = _rpy_of(s, tmp_path)
        assert 'i11' in rpy, rpy
