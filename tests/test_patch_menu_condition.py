"""
tests/test_patch_menu_condition.py
==================================
**菜单里的条件**在增量补丁下必须逐字节原样回去。

2026-10-23 之前这里有个洞（`docs/审计_保真证据链_2026-10-23.md` §五-A）：

    补丁是**逐行配对**的，而单拍渲染（`render_beat_to_rpy`）不知道菜单结构，
    于是同一件事长成两种形状：

        整份渲染（用户文件里的样子）        单拍渲染（补丁用来配对的）
        "台词D" if flag:                   if flag:
            e "台词E。"                        "台词D":
                                               if flag:      ← 又包一层
                                                   e "台词E。"

    配不上 → 判"新增内容没能落进产物" → **整章退回全量重生成**。
    真机 55 个文件里恰好一条条件选项都没有，所以 55/55 全绿也盖不住。

修法在 `patcher._menu_condition_view` / `_menu_shaped_lines`：把单拍渲染的行
按菜单结构摆回"源文件那种形状"（选项行内联条件、选项体剥掉累积条件）。

这个文件逐条钉住六种形状 —— 包括**不该被剥**的那种（选项里真的嵌了一层 `if`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.compile import compile_table       # noqa: E402
from storygraph.story.script_table import open_rpy_as_table  # noqa: E402

CASES = {
    'plain_conditional_option': '''define e = Character("A", image="e")

label ch:
    e "一。"
    menu:
        "去":
            jump roof
        "留" if persistent.flag:
            e "留。"
    return

label roof:
    e "屋顶。"
    return
''',
    # 菜单在 if 下、选项**没有**自己的条件：整块 if 由源文那一行保留，选项行原样
    'menu_under_if': '''define e = Character("A", image="e")

label ch:
    if persistent.act2:
        menu:
            "去":
                e "去。"
            "留":
                e "留。"
    e "后。"
    return
''',
    # 嵌套菜单 + 两层条件：外层选项体不该重复包 if
    'nested_menu_in_cond_option': '''define e = Character("A", image="e")

label ch:
    menu:
        "去" if persistent.a:
            menu:
                "左":
                    e "左。"
                "右" if persistent.b:
                    e "右。"
        "留":
            e "留。"
    return
''',
    # ⚠ 这条是"**不该被剥**"的：选项体里真的多嵌了一层 if，必须原样保留
    'extra_if_inside_option': '''define e = Character("A", image="e")

label ch:
    menu:
        "去" if persistent.a:
            if persistent.c:
                e "只在 c 时。"
            e "总是。"
    return
''',
    # 空选项体（`pass` / 什么都不写）最容易在配对时错位
    'empty_conditional_option': '''define e = Character("A", image="e")

label ch:
    menu:
        "去" if persistent.a:
            pass
        "留":
            e "留。"
    return
''',
    # 菜单挂在 `if` 下、**选项自己还有条件**：两层条件都要保住
    # （补丁只保留源文那一行 `if …:`，选项行原样不动）。
    'menu_under_if_with_option_cond': '''define e = Character("A", image="e")

label ch:
    if persistent.act2:
        menu:
            "去" if persistent.route:
                e "去。"
            "留":
                e "留。"
    return
''',
}


def _roundtrip(tmp_path: Path, source: str, tag: str = 'r'):
    game = tmp_path / tag / 'game'
    game.mkdir(parents=True)
    src = game / 'x.rpy'
    src.write_text(source, encoding='utf-8')
    xlsx = tmp_path / tag / 't.xlsx'
    out = tmp_path / tag / 'out.rpy'
    open_rpy_as_table(str(src), str(xlsx))
    _p, _s, res = compile_table(str(xlsx), rpy_path=out, write_sheet=False,
                                force=True, source_path=str(src),
                                skip_empty=True, engine_lint=False)
    return res, (out.read_text(encoding='utf-8') if out.exists() else '')


@pytest.mark.parametrize('name', list(CASES))
def test_menu_conditions_survive_the_patch(name, tmp_path):
    res, out = _roundtrip(tmp_path, CASES[name])
    assert res.used_patch, f'{name} 退回了全量：{res.patch_note}'
    assert out == CASES[name], f'{name} 往返后不是逐字节相同：\n{out}'


@pytest.mark.parametrize('name', list(CASES))
def test_it_is_stable_on_a_second_pass(name, tmp_path):
    """再导出一次仍然相同（配对一旦"学歪"了，第二轮就会现形）。"""
    src = CASES[name]
    _res, first = _roundtrip(tmp_path, src, tag='p1')
    path = tmp_path / 'p1' / 'game' / 'x.rpy'
    path.write_text(first, encoding='utf-8')
    xlsx = tmp_path / 'p2' / 't.xlsx'
    xlsx.parent.mkdir()
    out2 = tmp_path / 'p2' / 'out.rpy'
    open_rpy_as_table(str(path), str(xlsx))
    _p, _s, res2 = compile_table(str(xlsx), rpy_path=out2, write_sheet=False,
                                 force=True, source_path=str(path),
                                 skip_empty=True, engine_lint=False)
    assert res2.used_patch
    assert out2.read_text(encoding='utf-8') == first, f'{name} 第二轮变形了'


#: 菜单挂在 `if` 下、**选项自己还有条件**：两层条件都要保住
#: （补丁只保留源文那一行 `if …:`，选项行原样不动）。
_MENU_UNDER_IF_WITH_OPTION_COND = '''define e = Character("A", image="e")

label ch:
    if persistent.act2:
        menu:
            "去" if persistent.route:
                e "去。"
            "留":
                e "留。"
    return
'''

CASES['menu_under_if_with_option_cond'] = _MENU_UNDER_IF_WITH_OPTION_COND


#: **已知限制**（2026-10-23 记，不在本轮修）：改一句**条件选项的文案**。
#:
#: `_line_key` 对条件选项算的是 `('other', 整行)`（`_OPTION_ROW_RE` 只认 `"文本":`），
#: 文案一改键就变 → 生成行配不上 → 判"新增内容" → 整章退回全量。
#: 修它要动配对键（让条件选项与普通选项同键），而那会把"嵌套菜单里条件不同的选项行"
#: 也配到一起（实测 `nested_menu_in_cond_option` 当场被重复并条件打回）——
#: 属于"配对键 + 条件校验"一起改的一轮，先别在这里打补丁。
@pytest.mark.xfail(strict=True, reason=(
    '已知限制：条件选项改文案时配对键会变，补丁退回全量（见上面注释）。'))
def test_editing_a_conditional_option_touches_only_that_line(tmp_path):
    import openpyxl

    src = CASES['plain_conditional_option']
    game = tmp_path / 'game'
    game.mkdir()
    path = game / 'x.rpy'
    path.write_text(src, encoding='utf-8')
    xlsx = tmp_path / 't.xlsx'
    open_rpy_as_table(str(path), str(xlsx))

    # 在表里把选项文案改掉（Excel 侧唯一的真源就是这张表）
    wb = openpyxl.load_workbook(xlsx)
    touched = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.strip() == '留':
                    cell.value = '留下'
                    touched += 1
    assert touched, '表里没找到那个选项'
    wb.save(xlsx)

    out = tmp_path / 'out.rpy'
    _p, _s, res = compile_table(
        str(xlsx), rpy_path=out, write_sheet=False, force=True,
        source_path=str(path), skip_empty=True, engine_lint=False)
    got = out.read_text(encoding='utf-8')
    assert res.used_patch, res.patch_note
    assert '"留" if persistent.flag:' not in got
    assert '"留下" if persistent.flag:' in got, got
    # 只有那一行变了
    before = src.splitlines()
    after = got.splitlines()
    diff = [(a, b) for a, b in zip(before, after) if a != b]
    assert diff == [('        "留" if persistent.flag:',
                     '        "留下" if persistent.flag:')], diff
