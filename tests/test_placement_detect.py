# -*- coding: utf-8 -*-
"""
项目形状探测（G0 后半）：七个形状样本 + 几条容易写错的判据。

## 为什么是"集合相等"而不是"列表相等"

`shape_e_multifile/expected.json` 里 `declarations` 的顺序是
`names / chars / flags` —— 那是**人按语义排的**（名字先于角色），
不是任何算法能从文件里读出来的东西。文件系统的字母序是
`chars / flags / names`。两者都对，所以这里比**集合**。

其余三项（`control_flow` / `content_globs` / `declaration_style`）必须逐字相等 ——
它们是可以从源码推出来的，错一个字就是判据错。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storygraph.story.placement import (
    DECL_DEDICATED, DECL_MIXED, DECL_SINGLE, detect_placement, matches_globs,
)

SAMPLES = Path(__file__).parent / 'fixtures' / 'placement'
SAMPLE_DIRS = sorted(p for p in SAMPLES.iterdir()
                     if p.is_dir() and (p / 'expected.json').is_file())


def _expected(sample_dir: Path) -> dict:
    return json.loads((sample_dir / 'expected.json').read_text(encoding='utf-8'))['placement']


def _norm_decl(value):
    """单个文件 → 字符串；多个 → 排序后的元组（顺序无语义，见模块 docstring）。"""
    return tuple(sorted(value)) if isinstance(value, list) else value


@pytest.mark.parametrize('sample_dir', SAMPLE_DIRS, ids=lambda p: p.name)
def test_shape_matches_expected(sample_dir):
    exp = _expected(sample_dir)
    got = detect_placement(sample_dir).to_dict()

    assert _norm_decl(got['declarations']) == _norm_decl(exp['declarations'])
    assert got['control_flow'] == exp['control_flow']
    assert got['content_globs'] == exp['content_globs']
    assert got['declaration_style'] == exp['declaration_style']


@pytest.mark.parametrize('sample_dir', SAMPLE_DIRS, ids=lambda p: p.name)
def test_every_content_file_is_covered_by_a_glob(sample_dir):
    """glob 只是"大致范围"，但必须能覆盖全部内容文件。"""
    got = detect_placement(sample_dir).to_dict()
    assert got['content_files'], '每个样本至少有一个内容文件'
    for rel in got['content_files']:
        assert matches_globs(rel, got['content_globs']), \
            f'{rel} 没被 {got["content_globs"]} 覆盖'


# ---------------------------------------------------------------------------
#  容易写错的判据
# ---------------------------------------------------------------------------

def test_framework_define_is_not_a_project_declaration(tmp_path):
    """
    `define gui.accent_color` / `define config.has_sound` 是 Ren'Py 框架配置。
    认成声明的话，任何项目的 gui.rpy / options.rpy 都会变成"声明文件"。
    """
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'gui.rpy').write_text(
        "define gui.accent_color = '#99ccff'\n"
        "define config.has_sound = True\n", encoding='utf-8')
    (game / 'story.rpy').write_text(
        'default trust = 0\n\nlabel ch01:\n    "台词"\n    return\n',
        encoding='utf-8')

    p = detect_placement(tmp_path)
    assert p.declarations == 'game/story.rpy', \
        f'框架配置不该算声明；实际 {p.declarations!r}'


def test_control_flow_file_does_not_need_the_name_control(tmp_path):
    """`flow.rpy` / `剧情控制.rpy` 一样认 —— 判据是"全文件无演出"。"""
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'flow.rpy').write_text(
        'label a_flow:\n    call a\n    jump b_flow\n\n'
        'label b_flow:\n    call b\n    return\n', encoding='utf-8')
    (game / 'a.rpy').write_text(
        'label a:\n    "演出一句"\n    return\n', encoding='utf-8')
    (game / 'b.rpy').write_text(
        'label b:\n    "再演一句"\n    return\n', encoding='utf-8')

    p = detect_placement(tmp_path)
    assert p.control_flow == 'game/flow.rpy'


def test_single_file_without_top_level_flow_is_dedicated(tmp_path):
    """
    官方模板形状：声明 + 剧情同文件，跳转全在 menu 里（内容内分支）。
    "分散"至少要两个文件才谈得上，所以记 dedicated。
    """
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'script.rpy').write_text(
        'define e = Character("Eileen")\n'
        'default points = 0\n\n'
        'label start:\n'
        '    e "早上好。"\n'
        '    menu:\n'
        '        "去屋顶":\n'
        '            jump roof\n'
        '        "留下":\n'
        '            jump stay\n\n'
        'label roof:\n    e "风景真好。"\n    return\n', encoding='utf-8')

    p = detect_placement(tmp_path)
    assert p.control_flow == '', '只有 menu 内的 jump，不算顶层编排'
    assert p.declaration_style == DECL_DEDICATED


def test_single_file_doing_its_own_routing_is_single_file(tmp_path):
    """
    原型形状：一个文件里既有声明、又有剧情、又有顶层跳转（`jump start` 那种）。
    三件套落点重合 —— 工具不该假装存在一个独立编排层。
    """
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'all_in_one.rpy').write_text(
        'define n = Character("旁白")\n'
        'default hp = 100\n\n'
        'label start:\n    n "你醒来了。"\n    jump explore\n\n'
        'label explore:\n    n "两条路。"\n    jump start\n', encoding='utf-8')

    p = detect_placement(tmp_path)
    assert p.control_flow == 'game/all_in_one.rpy'
    assert p.declaration_style == DECL_SINGLE
    assert '单文件' in p.summary()


def test_scattered_declarations_in_multiple_chapters(tmp_path):
    game = tmp_path / 'game'
    game.mkdir()
    for i in (1, 2):
        (game / f'chapter{i}.rpy').write_text(
            f'default flag{i} = False\n\nlabel ch{i}:\n    "台词{i}"\n    return\n',
            encoding='utf-8')

    p = detect_placement(tmp_path)
    assert isinstance(p.declarations, list) and len(p.declarations) == 2
    assert p.declaration_style == DECL_MIXED


def test_glob_strips_numbers_anywhere_not_just_at_the_end(tmp_path):
    """中文编号夹在中间（`第一章`），只削尾巴是削不掉的。"""
    game = tmp_path / 'game'
    game.mkdir()
    for name in ('第一章', '第二章'):
        (game / f'{name}.rpy').write_text(
            f'label {name}:\n    "台词"\n    return\n', encoding='utf-8')

    p = detect_placement(tmp_path)
    assert p.content_globs == ['game/第*.rpy'], f'实际 {p.content_globs!r}'


def test_inline_flow_files_are_reported(tmp_path):
    """控制流混在内容里 = 图只能读，这条事实要能报出来。"""
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'ch1.rpy').write_text(
        'label ch1:\n    "台词"\n    jump ch2\n\n'
        'label ch2:\n    "台词"\n    return\n', encoding='utf-8')

    p = detect_placement(tmp_path)
    assert 'game/ch1.rpy' in p.inline_flow_files


def test_empty_project_says_so_instead_of_crashing(tmp_path):
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'only_text.rpy').write_text('# 什么都没有\n', encoding='utf-8')

    p = detect_placement(tmp_path)
    assert p.declarations == ''
    assert p.content_files == []
    assert p.warnings, '空项目要给出警告，而不是静默返回空形状'
