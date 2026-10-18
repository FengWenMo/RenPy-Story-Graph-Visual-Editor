"""
`menu` 头（名 / 参数）的建模回归 —— 审计 P1-3 收尾（2026-10-19）
================================================================

体检报告 P1-3 原来写的是"菜单参数被读完即丢"，2026-10-18 的修法是
**整块交给未建模通道**（`opaque`）：一个字节不丢，但代价是
**选项不在拍里** —— 卡片上整段菜单消失，策划连"选项 A/B"都看不见。

这一批用例钉住"现在真的建模了"这件事，判据四层：

  1. 结构：菜单拍 1 张 + 选项拍 N 张 + **没有**原样块；
  2. 保真：头行逐字节（`menu` / `menu 名` / `menu (args)` / `menu 名 (args)`）；
  3. 过表：`to_row` / `from_row` 一趟之后两格还在（Excel 是主战场）；
  4. 过卡片：`beat_to_fields` / `fields_to_beat` 一趟之后两格还在。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result      # noqa: E402
from storygraph.story.beat_model import (Beat, BEAT_FIELDS,          # noqa: E402
                                        KIND_MENU, KIND_MENU_OPTION,
                                        menu_head_text)
from storygraph.story.beat_render import render_beats_to_rpy         # noqa: E402


def _beats(tmp_path, src: str, name: str = 'a.rpy') -> list:
    f = tmp_path / name
    f.write_text(src, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _render(tmp_path, src: str) -> str:
    return render_beats_to_rpy(_beats(tmp_path, src))


def _src(head: str) -> str:
    return (
        'label start:\n'
        '    "before"\n'
        f'    {head}\n'
        '        "选项 A":\n'
        '            jump done\n'
        '        "选项 B":\n'
        '            "chose B"\n'
        '    return\n'
        '\n'
        'label done:\n'
        '    return\n'
    )


# ---------------------------------------------------------------------------
#  1. 结构：选项真的成了拍，不再整块落进「原样块」
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('head,name,args', [
    ('menu:', '', ''),
    ('menu (nvl=True):', '', '(nvl=True)'),
    ('menu choice_menu:', 'choice_menu', ''),
    ('menu named_menu (screen="choice"):', 'named_menu', '(screen="choice")'),
])
def test_menu_head_is_modeled_not_opaque(tmp_path, head, name, args):
    beats = _beats(tmp_path, _src(head))
    heads = [b for b in beats if b.kind == KIND_MENU]
    opts = [b for b in beats if b.kind == KIND_MENU_OPTION]
    assert len(heads) == 1, f'{head} 应当有且只有一张菜单拍'
    assert len(opts) == 2, f'{head} 的选项必须是真拍（否则卡片上看不见）'
    assert not [b for b in beats if b.opaque], (
        '菜单不该再掉进「原样块」—— 那等于整段菜单在卡片里消失')
    assert heads[0].menu_name == name
    assert heads[0].menu_args == args


def test_menu_name_is_a_real_label_target(tmp_path):
    """`menu 名:` 的「名」是 Ren'Py 的**真 label**（jump/call 跳得进来）。"""
    src = (
        'label start:\n'
        '    menu choice_menu:\n'
        '        "选项":\n'
        '            jump done\n'
        '    jump choice_menu\n'
        'label done:\n'
        '    return\n'
    )
    out = _render(tmp_path, src)
    assert 'menu choice_menu:' in out
    assert 'jump choice_menu' in out


# ---------------------------------------------------------------------------
#  2. 保真：头行逐字节
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('head', [
    'menu:',
    'menu (nvl=True):',
    'menu choice_menu:',
    'menu named_menu (screen="choice"):',
])
def test_menu_head_is_rendered_byte_exactly(tmp_path, head):
    out = _render(tmp_path, _src(head))
    assert f'    {head}' in out, f'头行没有逐字节写回：{head}'


def test_tight_form_gets_one_space_and_still_is_valid(tmp_path):
    """
    `menu 名(x):`（名字与括号紧贴）会规范化成一个空格。

    这是**唯一**一处不逐字节的菜单头写法：引擎两种都收
    （`parse_arguments` 直接匹配 `(`），而我们从不生成紧贴形态。
    """
    out = _render(tmp_path, _src('menu named_menu(screen="choice"):'))
    assert 'menu named_menu (screen="choice"):' in out


def test_menu_head_helper_is_the_single_source():
    """两条渲染路径共用 `menu_head_text` —— 各写一份正是 P0-2 的来源。"""
    assert menu_head_text(Beat(kind=KIND_MENU)) == 'menu'
    assert menu_head_text(Beat(kind=KIND_MENU, menu_name='m')) == 'menu m'
    assert menu_head_text(Beat(kind=KIND_MENU, menu_args='(x)')) == 'menu (x)'
    assert (menu_head_text(Beat(kind=KIND_MENU, menu_name='m',
                                menu_args='(x)'))
            == 'menu m (x)')


def test_options_conditions_survive_with_a_head(tmp_path):
    src = (
        'label start:\n'
        '    menu (nvl=True):\n'
        '        "A" if flag:\n'
        '            jump done\n'
        '        "B":\n'
        '            return\n'
        'label done:\n'
        '    return\n'
    )
    out = _render(tmp_path, src)
    assert 'menu (nvl=True):' in out
    assert '"A" if flag:' in out


# ---------------------------------------------------------------------------
#  3. 过表：Excel 那一趟不许丢
# ---------------------------------------------------------------------------

def test_menu_head_survives_excel_roundtrip(tmp_path):
    beats = _beats(tmp_path, _src('menu named_menu (screen="choice"):'))
    head = next(b for b in beats if b.kind == KIND_MENU)
    row = dict(zip(BEAT_FIELDS, head.to_row()))
    assert row['MenuName'] == 'named_menu'
    assert row['MenuArgs'] == '(screen="choice")'
    back = Beat.from_row(row)
    assert back.menu_name == 'named_menu'
    assert back.menu_args == '(screen="choice")'
    assert menu_head_text(back) == 'menu named_menu (screen="choice")'


def test_menu_head_enters_the_fingerprint():
    """改了名/参数必须算"这一拍变过"，否则协作/撤销会静默丢改动。"""
    from storygraph.story.beat_model import _beat_content_hash

    a = Beat(kind=KIND_MENU, text='\x00menu')
    b = Beat(kind=KIND_MENU, text='\x00menu', menu_args='(nvl=True)')
    c = Beat(kind=KIND_MENU, text='\x00menu', menu_name='m')
    assert _beat_content_hash(a) != _beat_content_hash(b)
    assert _beat_content_hash(a) != _beat_content_hash(c)


# ---------------------------------------------------------------------------
#  4. 过卡片：表单字段那一趟不许丢
# ---------------------------------------------------------------------------

def test_menu_head_survives_card_fields(tmp_path):
    from storygraph.app.beats import beat_to_fields, fields_to_beat

    beats = _beats(tmp_path, _src('menu named_menu (screen="choice"):'))
    head = next(b for b in beats if b.kind == KIND_MENU)
    fields = beat_to_fields(head)
    assert fields['MenuName'] == 'named_menu'
    assert fields['MenuArgs'] == '(screen="choice")'
    back = fields_to_beat(fields, base=head)
    assert (back.menu_name, back.menu_args) == ('named_menu', '(screen="choice")')


def test_card_fields_do_not_clear_when_key_missing(tmp_path):
    """键不存在 ≠ 清空（只交回改过那几格的调用方不该抹掉菜单参数）。"""
    from storygraph.app.beats import fields_to_beat

    b = Beat(kind=KIND_MENU, text='\x00menu', menu_name='m', menu_args='(x)')
    assert fields_to_beat({'Text': '只改标题'}, base=b).menu_args == '(x)'


def test_advanced_fields_expose_the_menu_head():
    from storygraph.app.editor import ADVANCED_FIELDS

    assert 'MenuName' in ADVANCED_FIELDS
    assert 'MenuArgs' in ADVANCED_FIELDS


def test_menu_beats_do_not_warn_about_a_missing_speaker():
    """
    菜单拍 / 选项拍的「台词」格是**标题 / 选项文案**，本来就没有说话人。

    从前卡片底部照样弹"这一拍有台词但没有说话人 —— 导出后会变成旁白，
    把「类型」改成 narration" —— 那是教人去点一个不存在的开关
    （2026-10-19，做菜单头建模时的截图里一眼看到）。
    """
    from storygraph.app.beats import speaker_missing_warning

    assert speaker_missing_warning(
        Beat(kind=KIND_MENU, text='要选哪一条？')) == ''
    assert speaker_missing_warning(
        Beat(kind=KIND_MENU_OPTION, text='选项 A')) == ''
    # 普通台词照样要吵（否则真正的幽灵旁白就没人管了）
    assert speaker_missing_warning(Beat(kind='say', text='没人说的话'))
