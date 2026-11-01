"""
tests/test_table_edits.py
=========================
`app.table_edits.FieldWriter` 的规则单测 —— **不造会话、不加载项目、不碰 Qt**。

这就是把 `apply` 那十二段搬出去的目的：从前要验"高级区收起 ≠ 清空"，
得先 `EditorSession(project_root=...)`、`load(一个 .rpy)`、再 `apply(idx, …)`，
一条规则被三件事包着。现在一拍、一个字典、一个断言。

每条用例对应**一条曾经出过事的语义**（注释里写着年份与现场），
不是"覆盖率"。刻意不在这里重测整章导出 —— 那是 `test_ui_qt` /
`test_writeback_fidelity` 的活。
"""
from __future__ import annotations

import pytest

from storygraph.app import beats as B
from storygraph.app.table_edits import (FieldWriter, uniform_position_of,
                                        write_statement_head)
from storygraph.story.beat_model import Beat, KIND_MENU, MENU_NO_CAPTION
from storygraph.story.beat_render import make_symbols


def _writer(peers=()) -> FieldWriter:
    return FieldWriter(make_symbols(), peers)


def _sprite_card(image: str, position: str = '') -> dict:
    return {'image': image, 'expression': '', 'position': position,
            'at': '', 'order_index': 0, 'extra_rows': []}


# ---------------------------------------------------------------------------
#  主区：**键不存在 = 不动这一格**
# ---------------------------------------------------------------------------

def test_absent_key_leaves_the_cell_alone():
    """
    2026-10-05 修：局部表单从前会**静默清空**其它列
    （只提交 Speaker + Text，背景就从 `bg classroom` 变成空串）。
    """
    b = Beat(scene='bg classroom', bgm='bgm_a.ogg')
    _writer().write(b, {'Speaker': 'e', 'Text': '只改台词'})
    assert b.speaker == 'e'
    assert b.text == '只改台词'
    assert b.scene == 'bg classroom'
    assert b.bgm == 'bgm_a.ogg'


def test_empty_string_really_clears_the_cell():
    """给了空串就是**要清空** —— 与"没给"必须分得开。"""
    b = Beat(bgm='bgm_a.ogg')
    _writer().write(b, {'BGM': ''})
    assert b.bgm == ''


def test_menu_placeholder_comes_back():
    """
    无标题菜单的占位符：表单里那一格显示成空串，但"空"的意思是
    "菜单没有标题"而不是"这一拍什么都没有"。补不回来 `menu:` 块会消失。
    """
    b = Beat(kind=KIND_MENU, text='')
    _writer().write(b, {'Text': ''})
    assert b.text == MENU_NO_CAPTION


# ---------------------------------------------------------------------------
#  高级区：收起 ≠ 清空
# ---------------------------------------------------------------------------

def test_collapsed_advanced_area_is_not_written():
    """
    收起的高级区**不写回**（不是"写空"）。
    前端收起时也照样把旧值塞在字典里，这条保证它不会覆盖。
    """
    b = Beat(sfx='click.ogg', notes='人写的备注', say_tail='with dissolve')
    _writer().write(b, {'SFX': 'boom.ogg', 'Notes': '新备注',
                        'SayTail': ''}, advanced_open=False)
    assert b.sfx == 'click.ogg'
    assert b.notes == '人写的备注'
    assert b.say_tail == 'with dissolve'


def test_expanded_advanced_area_is_written():
    b = Beat()
    _writer().write(b, {'SFX': 'boom.ogg', 'Notes': '新备注'},
                    advanced_open=True)
    assert b.sfx == 'boom.ogg'
    assert b.notes == '新备注'


# ---------------------------------------------------------------------------
#  《代码》那一格：没改就别动（2026-10-18）
# ---------------------------------------------------------------------------

def test_untouched_code_cell_keeps_raw_and_its_kinds():
    """
    代码格是 `raw` + `code` **拼出来给人看的**。无条件写回会把 `raw`
    清空、连"这行是不是原生语句"的分类一起丢掉 —— 于是
    `play music / play ambience / play sound` 的顺序被重排，
    而用户只是打开卡片、什么都没改。
    """
    b = Beat(raw='play music "a.ogg"')
    shown = B.compose_code(b)
    _writer().write(b, {'CodeBlock': shown}, advanced_open=True)
    assert b.raw == 'play music "a.ogg"'
    assert b.code == ''


def test_changed_code_cell_becomes_python_code():
    b = Beat(raw='play music "a.ogg"')
    _writer().write(b, {'CodeBlock': '$ x = 1'}, advanced_open=True)
    assert b.code == '$ x = 1'
    assert b.raw == ''


# ---------------------------------------------------------------------------
#  立绘卡：空网格不清空（1999 次误删的老坑）
# ---------------------------------------------------------------------------

def test_empty_sprite_grid_does_not_clear_show():
    b = Beat(show='e happy', at='left')
    _writer().write(b, {}, cards=[])
    assert b.show == 'e happy'
    assert b.at == 'left'


def test_sprite_cards_rebuild_show_and_beat_level_position():
    b = Beat()
    _writer().write(b, {}, cards=[_sprite_card('e happy', 'left')])
    assert 'e' in b.show
    assert 'happy' in b.show
    assert b.at == 'left'


def test_unexpressible_items_survive_a_card_round_trip():
    """`with` / `behind` 这类卡片表达不了的项必须原样带回（P0，2026-10-24）。"""
    b = Beat(show='e happy; bg library with dissolve')
    _writer().write(b, {}, cards=[_sprite_card('e happy')])
    assert 'bg library with dissolve' in b.show


# ---------------------------------------------------------------------------
#  「表情 / 位置」两格：只在没有立绘卡时写
# ---------------------------------------------------------------------------

def test_expression_cell_applies_without_cards():
    b = Beat(show='e happy')
    _writer().write(b, {'Expression': 'sad'})
    assert 'sad' in b.show
    assert 'happy' not in b.show


def test_expression_cell_is_ignored_when_cards_are_present():
    """卡片才是真源：两处同时写会互相覆盖。"""
    b = Beat(show='e happy')
    _writer().write(b, {'Expression': 'sad'}, cards=[_sprite_card('e happy')])
    assert 'sad' not in b.show


def test_position_goes_to_beat_level_at_when_that_is_what_is_in_use():
    b = Beat(show='e happy', at='left')
    _writer().write(b, {'Position': 'right'})
    assert b.at == 'right'
    assert '@' not in b.show


def test_position_goes_to_the_item_when_that_is_what_is_in_use():
    """两种存法只能改**已经在用的那一个**，否则写出 `e happy@right at right`。"""
    b = Beat(show='e happy@left')
    _writer().write(b, {'Position': 'right'})
    assert '@right' in b.show
    assert b.at == ''


def test_empty_position_never_wipes_all_positions():
    """空串在那一格的约定是"多张立绘位置不一致、没有统一值"，拿它写会把位置全抹掉。"""
    b = Beat(show='e happy@left')
    _writer().write(b, {'Position': ''})
    assert '@left' in b.show


# ---------------------------------------------------------------------------
#  条件：链头一改，后面几支的守卫跟着重算（P1，2026-10-25）
# ---------------------------------------------------------------------------

def test_condition_change_rechains_the_siblings():
    head = Beat(nest_path='if:1', condition='a == 1')
    sib = Beat(nest_path='if:2', condition='(b == 2) and not ((a == 1))')
    _writer(peers=[head, sib]).write(head, {'condition': 'a == 9'})
    assert head.condition == 'a == 9'
    # 真实存储形式是**双括号**：`(b == 2) and not ((a == 1))`
    # （解析器从 `elif` 展开时就写成这样，`rechain_condition` 只认这个形状）。
    assert sib.condition == '(b == 2) and not ((a == 9))', sib.condition


def test_condition_change_on_a_branch_does_not_rechain():
    """非链头（`if:2`）改条件不该动别人：它们存的是累积形式，改一格是别的事。"""
    head = Beat(nest_path='if:1', condition='a == 1')
    sib = Beat(nest_path='if:2', condition='(b == 2) and not ((a == 1))')
    _writer(peers=[head, sib]).write(sib, {'condition': 'b == 3'})
    assert sib.condition == 'b == 3'
    assert head.condition == 'a == 1'


# ---------------------------------------------------------------------------
#  Label：改一格 = 给整章那个 label 改名（P0-8，2026-10-24）
# ---------------------------------------------------------------------------

def test_label_change_renames_the_whole_block():
    a = Beat(label='ch1')
    b = Beat(label='ch1')
    _writer(peers=[a, b]).write(a, {'Label': 'ch2'}, advanced_open=True)
    assert a.label == 'ch2'
    assert b.label == 'ch2'


def test_label_change_refuses_a_name_that_is_already_taken():
    a = Beat(label='ch1')
    b = Beat(label='ch2')
    _writer(peers=[a, b]).write(a, {'Label': 'ch2'}, advanced_open=True)
    assert a.label == 'ch1'
    assert b.label == 'ch2'


# ---------------------------------------------------------------------------
#  Stage：自己吃自己的正解（2026-10-05 / 2026-10-24）
# ---------------------------------------------------------------------------

def test_typed_stage_clears_structured_columns_then_rebuilds():
    """
    人**真的改了** Stage（typed ≠ 载入时的原文）→ 以人写的为准，
    而且必须先清结构化列再 hydrate，否则"旧投影 + 新文本"会叠成两份。
    """
    b = Beat(scene='bg classroom', show='e happy')
    _writer().write(b, {'Stage': 'scene bg new_library'}, advanced_open=True,
                    stage_loaded='scene bg classroom; show e happy')
    # Scene 列存的是**带 `bg ` 前缀**的图像名（与表里那一列同一个格式）。
    assert b.scene == 'bg new_library'
    assert b.show == ''          # 旧投影（立绘）被清掉，没有叠成两份


def test_empty_stage_is_reprojected_never_trusted():
    """清空格子是很常见的误操作，它的后果是抹掉 scene/show/hide → 一律重新投影。"""
    b = Beat(scene='bg classroom', show='e happy')
    _writer().write(b, {'Stage': ''}, advanced_open=True,
                    stage_loaded='bg classroom; show e happy')
    assert b.scene == 'bg classroom'
    assert b.show == 'e happy'


def test_unknown_baseline_never_counts_as_a_hand_edit():
    """
    2026-10-05 实测修：`stage_loaded=None` 时旧代码拿 `b.stage` 当基线，
    而界面显示的那一格往往是**投影** → 被误判成"人改过" → 整段清掉
    刚加的立绘。没告诉过我们基线，就没资格说这是人改的。
    """
    b = Beat(scene='bg classroom', show='e happy', stage='')
    _writer().write(b, {'Stage': 'bg classroom; show e happy'},
                    advanced_open=True)
    assert b.show == 'e happy'
    assert b.scene == 'bg classroom'


def test_extra_stage_ops_survive_reprojection():
    """结构化列表达不了的（`[停顿] 1.0`）要原样留下，否则点一下下一句就没了。"""
    b = Beat(scene='bg classroom')
    _writer().write(b, {}, advanced_open=True,
                    stage_loaded='scene bg classroom; [停顿] 1.0')
    assert b.scene == 'bg classroom'
    # 中文速记在 `stage_only_ops` 里归一成 Ren'Py 写法（`pause 1.0`），
    # 关键是它**还在** —— 重新投影不许把作者手写的这一条吞掉。
    assert 'pause 1.0' in b.stage, b.stage


# ---------------------------------------------------------------------------
#  顺序是契约（"动态列最后折"，2026-10-13）
# ---------------------------------------------------------------------------

def test_write_runs_the_stages_in_the_contract_order(monkeypatch):
    """
    **顺序不能改**：动态列必须在立绘卡与 hydrate **之后**折，
    否则卡片重建 `show`、hydrate 反推结构化字段都会把刚折进去的参数盖回去
    （现场：`show·人物@0` 改成 `ling`，保存后又变回 `lan`）。
    这里把每个阶段记下来 —— 有人重排会当场红，并指到这条注释。
    """
    calls = []
    stages = ['_apply_flow_fields', '_apply_main_columns',
              '_apply_advanced_fields', '_apply_sprite_cards',
              '_apply_word_cells', '_apply_condition', '_apply_stage',
              '_fold_dynamic_columns']
    for name in stages:
        # 注意 `_fold_dynamic_columns` 是 `staticmethod`：打桩要保持静态绑定，
        # 否则实例访问会多塞一个 self 进去（`TypeError: takes 2 but 3 given`）。
        is_static = isinstance(FieldWriter.__dict__[name], staticmethod)
        original = getattr(FieldWriter, name)

        def wrapper(self, *a, __n=name, __o=original, __s=is_static, **kw):
            calls.append(__n)
            # 打桩之后静态方法变回了普通函数，实例访问会把 self 塞进来
            # → 静态那条要把它去掉再调原函数。
            return __o(*a, **kw) if __s else __o(self, *a, **kw)

        monkeypatch.setattr(FieldWriter, name, wrapper)
    FieldWriter(make_symbols()).write(Beat(), {})
    assert calls == stages


# ---------------------------------------------------------------------------
#  语句头 / 位置投影（搬过来的自由函数）
# ---------------------------------------------------------------------------

def test_write_statement_head_rewrites_a_raw_line():
    b = Beat(raw='mycue intensity=5')
    assert write_statement_head(b, 'mycue intensity=9') is True
    assert b.raw == 'mycue intensity=9'


def test_write_statement_head_moves_the_declared_spec_head_too():
    b = Beat(spec_name='mycue', spec_head='mycue intensity=5')
    assert write_statement_head(b, 'mycue intensity=9') is True
    assert b.spec_head == 'mycue intensity=9'


def test_write_statement_head_refuses_multiline_and_unknown_verbs():
    b = Beat(raw='play music "a.ogg"')
    assert write_statement_head(b, '$ x = 1\n$ y = 2') is False
    assert write_statement_head(b, 'something_else') is False
    assert b.raw == 'play music "a.ogg"'


@pytest.mark.parametrize('cards, expected', [
    ([{'position': 'left'}, {'position': 'left'}], 'left'),
    ([{'position': 'left'}, {'position': 'right'}], ''),
    ([{'at': 'i11'}, {'at': 'i11'}], 'i11'),
    ([], ''),
])
def test_uniform_position_of(cards, expected):
    assert uniform_position_of(cards) == expected
