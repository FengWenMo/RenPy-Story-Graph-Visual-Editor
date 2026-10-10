"""
角色决策层判据（用户 2026-10-10 的方案）
=========================================

用户原话：

> A（保留+告警），但是先这样：在转换时搜索已定义的相似字符，可以搜索相关的 define，
> 比如 `Ljk` 和 `ljk` 最像 `L_J_K` 也有可能，`mom` 在游戏内显示为妈妈等……，
> 然后写一个弹窗，问是不是已定义的这个角色？如果是，自动标上别名，
> 如果不是，自动生成新角色 define 并输出。

这个文件钉住的是**决策层**（`storygraph/authoring/char_decisions.py`）的纯逻辑：

  ① **候选要带证据**（大小写/分隔符/编辑距离），且**噪音不许进候选**；
  ② **"自动标别名"必须过四道闸门** —— 尤其是"token 等于某个变量名"那种
     （`LJK` vs 变量 `ljk`）：加别名只会让这个键变歧义，正确处置是**改名**；
  ③ **建新角色要报重名**（ID / 显示名）；
  ④ 落盘只改「别名」列 / 只追加行（走既有的列白名单实现）。

口径（用户拍板）：新角色默认 **变量名=token、显示名=token**，可改；
新角色写进 `characters.xlsx`，**不**写进章节产物。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring.char_decisions import (                    # noqa: E402
    CASE, FUZZY, SEP, apply_alias_plans, apply_new_character_plans,
    plan_alias, plan_new_character, suggestions)
from storygraph.authoring.txt_import import CharacterMap, convert_text  # noqa: E402

#: 真机角色表的一小片（`ID`/`Name` 都是真机里的）
ROWS = {
    'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
    'yhj': {'ID': 'yhj', 'Name': '「叶怀瑾」'},
    'l': {'ID': 'l', 'Name': '「蓝」'},
    'ld': {'ID': 'ld', 'Name': '校领导'},
    'mm': {'ID': 'mm', 'Name': '妈妈'},
    'aaa': {'ID': 'aaa', 'Name': '舍友A'},
    'bbb': {'ID': 'bbb', 'Name': '舍友B'},
    'txf': {'ID': 'txf', 'Name': '汤X凤'},
    'jg': {'ID': 'jg', 'Name': '教官'},
}


def _conv(text: str, rows=None):
    rows = ROWS if rows is None else rows
    return convert_text(text, characters=CharacterMap(rows))


# ==================== ① 候选与证据 ====================

def test_writing_the_display_name_directly_just_works():
    """
    用户 2026-10-10：
    > 通常来说都会定义显示出来的名称，如果显示出来的名称跟写在 txt 的字符串
    > 完美对应，那也是同一个人物啊？比如 `mm` 对应为妈妈，我在剧本里就写"妈妈"。

    ✅ 这条**一直就是这样**（`CharacterMap` 的 L2 层）：txt 里直接写显示名，
    自动映射到变量，**不进弹窗**。真正机表实测 16 个显示名全部命中
    （`tools/probe_display_name_match.py`）。
    """
    rows = {'jc': {'ID': 'jc', 'Name': '「蒋忱」'},
            'mm': {'ID': 'mm', 'Name': '妈妈'},
            'ld': {'ID': 'ld', 'Name': '校领导'},
            'txf': {'ID': 'txf', 'Name': '汤X凤'}}
    conv = _conv('妈妈"跟妈客气啥"\n「蒋忱」"带角括号"\n蒋忱"不带也行"\n'
                 '校领导"金秋九月"\n汤X凤"你怎么睡得着哒"', rows)
    assert conv.unmapped == {} and conv.ambiguous == {}
    body = [ln.strip() for ln in conv.rpy_text.splitlines()
            if ln.strip() and not ln.startswith(('label', '#'))]
    assert body[:5] == ['mm "跟妈客气啥"', 'jc "带角括号"', 'jc "不带也行"',
                        'ld "金秋九月"', 'txf "你怎么睡得着哒"'], body
    # 都能自动命中 → 弹窗里一个都不该出现
    assert suggestions(conv, CharacterMap(rows), rows) == []


def test_a_display_name_that_is_also_someone_elses_alias_is_reported():
    """显示名与别人的别名撞车 → 强层赢（显示名）+ 报出来（既有契约，不动）。"""
    rows = {'a': {'ID': 'a', 'Name': '甲', 'Aliases': '乙'},
            'b': {'ID': 'b', 'Name': '乙'}}
    cm = CharacterMap(rows)
    assert cm.lookup('乙') == ('b', 'display')
    assert cm.conflicts


def test_candidates_are_offered_with_evidence():
    """`MOM` 在真机表里与 `mm`（妈妈）写法相近 —— 弹窗要给出它并说清凭什么。"""
    sug = suggestions(_conv('MOM"吃饭了"'), CharacterMap(ROWS), ROWS)
    assert [s.token for s in sug] == ['MOM']
    cands = sug[0].candidates
    assert cands, sug[0]
    assert cands[0].var == 'mm'
    assert cands[0].reason, '候选必须带证据（用户凭它点"是/不是"）'
    assert sug[0].samples, '弹窗必须能看到原文上下文'
    assert sug[0].samples[0][1].startswith('MOM')


def test_the_token_order_is_deterministic():
    text = 'MOM"一"\nTJF"二"\nBAOAN"三"'
    a = [s.token for s in suggestions(_conv(text), CharacterMap(ROWS), ROWS)]
    b = [s.token for s in suggestions(_conv(text), CharacterMap(ROWS), ROWS)]
    assert a == b == sorted(a)


def test_case_only_difference_is_detectable_evidence():
    """
    ⚠ 注意这条**不是**"弹窗里会出现 `MOM`"：表里一旦有别名 `Mom`，
    `CharacterMap` 的归一化就已经把 `MOM` 映射到 `mm` 了 —— 压根不进弹窗
    （那是最理想的结果）。这里钉的是**证据函数本身**会把它判成 `case`
    （最强的可验证证据），弹窗只在"归一化没命中"时才需要人。
    """
    from storygraph.authoring.char_decisions import _evidence_for
    ev = _evidence_for('MOM', 'mm', {'ID': 'mm', 'Name': '妈妈', 'Aliases': 'Mom'})
    assert ev.evidence == CASE and ev.var == 'mm', ev


def test_a_case_alias_makes_the_token_auto_resolve_without_a_dialog():
    """`Mom` 已在别名列 → `MOM` 自动命中，**不该再问用户**（问题越少越好）。"""
    rows = dict(ROWS, mm={'ID': 'mm', 'Name': '妈妈', 'Aliases': 'Mom'})
    conv = _conv('MOM"吃饭了"', rows)
    assert conv.unmapped == {} and conv.ambiguous == {}
    assert '    mm "吃饭了"' in conv.rpy_text
    assert suggestions(conv, CharacterMap(rows), rows) == []


def test_separator_only_difference_is_strong_evidence():
    """`L_J_K` 与变量 `ljk` 只差分隔符 —— 这是 `sep` 证据（可验证的事实）。"""
    from storygraph.authoring.char_decisions import _evidence_for
    ev = _evidence_for('L_J_K', 'ljk', {'ID': 'ljk', 'Name': '「李佳凯」'})
    assert ev.evidence == SEP and ev.var == 'ljk', ev


def test_an_underscore_alias_already_resolves_without_a_dialog():
    rows = dict(ROWS, ljk={'ID': 'ljk', 'Name': '「李佳凯」',
                           'Aliases': 'L_J_K'})
    conv = _conv('L_J_K"我"', rows)
    assert conv.unmapped == {} and conv.ambiguous == {}
    assert '    ljk "我"' in conv.rpy_text


def test_filler_names_do_not_pollute_candidates():
    """
    ⚠ 探针实测：`A"卷王——"` 的候选第一位是 `aaa`（舍友A），相似度 **100%**
    —— 因为 `a` 与 `aaa` 在编辑距离上完全包含。那不是"像"，只是"短"。
    """
    sug = suggestions(_conv('A"卷王——"'), CharacterMap(ROWS), ROWS)
    assert not sug[0].candidates, sug[0].candidates
    assert '手工指一个' in sug[0].note


def test_short_token_does_not_get_a_looong_candidate_list():
    """单字母 token 不许把半张表拉进候选（前缀命中 = 全表命中）。"""
    rows = dict(ROWS)
    for i in range(20):
        rows[f'x{i}'] = {'ID': f'x{i}', 'Name': f'路人{i}'}
    sug = suggestions(_conv('A"卷王——"', rows), CharacterMap(rows), rows)
    assert len(sug[0].candidates) <= 5, sug[0].candidates


def test_no_similar_candidate_says_so_instead_of_guessing():
    """`BAOAN` 与真机表里任何角色都不像 —— 弹窗要说"没有像的"，而不是硬塞一个。"""
    sug = suggestions(_conv('BAOAN"你好"'), CharacterMap(ROWS), ROWS)
    assert not sug[0].candidates
    assert '没有看着像' in sug[0].note


def test_ambiguous_tokens_are_surfaced_as_a_decision():
    """归一化撞车（`ljk`/`Ljk` 两个人）也要进弹窗，且说清"别名解决不了"。"""
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
            'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
    sug = suggestions(_conv('LJK"我是谁"', rows), CharacterMap(rows), rows)
    assert [s.token for s in sug] == ['LJK']
    assert '不能靠别名解决' in sug[0].note
    assert {c.var for c in sug[0].candidates} == {'ljk', 'Ljk'}


# ==================== ② 别名计划：四道闸门 ====================

def test_alias_plan_merges_into_the_existing_cell():
    """别名是**多值**列：加一个不许冲掉已有的（`MOM|母亲` → `MOM|母亲|老妈`）。"""
    rows = dict(ROWS, mm={'ID': 'mm', 'Name': '妈妈', 'Aliases': 'MOM|母亲'})
    plan = plan_alias(rows, '老妈', 'mm')
    assert plan.ok, plan.reason
    assert plan.after == 'MOM|母亲|老妈'


def test_alias_plan_rejects_a_token_that_is_already_known():
    plan = plan_alias(dict(ROWS, mm={'ID': 'mm', 'Name': '妈妈',
                                     'Aliases': 'MOM'}), 'MOM', 'mm')
    assert not plan.ok and '已经是' in plan.reason


def test_alias_plan_refuses_when_the_token_equals_another_variable():
    """
    ⚠ 这是最要命的一格：`LJK` 与**变量名** `ljk` 只差大小写。
    加别名的结果是 `Aliases` 里出现一个等于变量名的写法 → 该键变歧义
    （`CharacterMap` 会主动拒绝映射）→ 别名白加、更糟。
    正确处置：**改脚本写法或改变量名**。
    """
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'}}
    plan = plan_alias(rows, 'LJK', 'ljk')
    assert not plan.ok
    assert '变量名' in plan.reason and '大小写' in plan.reason


def test_alias_plan_reports_a_collision_instead_of_writing():
    """
    别名撞别人显示名 → 不写、报出来（复用 `CharacterMap` 的撞车检测）。
    这里 `乙` 本来就是 `b` 的显示名，所以这条会走"已经是 b 的写法"那一格
    —— **两种拒绝都算对**，关键是不许写。
    """
    rows = {'a': {'ID': 'a', 'Name': '甲', 'Aliases': ''},
            'b': {'ID': 'b', 'Name': '乙', 'Aliases': ''}}
    plan = plan_alias(rows, '乙', 'a')
    assert not plan.ok
    assert plan.reason


def test_alias_plan_reports_a_real_collision():
    """`丙` 谁都没写过，但 `a` 的显示名是「乙」、`b` 的别名要加 `丙`… —— 用一个
    真会撞的例子：给 `a` 加别名 `乙`（`b` 的显示名）时若 `乙` 不已在表里。"""
    rows = {'a': {'ID': 'a', 'Name': '甲', 'Aliases': ''},
            'b': {'ID': 'b', 'Name': '乙', 'Aliases': '丙'}}
    # `丙` 已是 b 的别名 → 再给 a 加同一个别名 = 同层撞车，必须拦下
    plan = plan_alias(rows, '丙', 'a')
    assert not plan.ok
    assert '已经是' in plan.reason or '撞车' in plan.reason


def test_alias_plan_rejects_unknown_character():
    plan = plan_alias(ROWS, 'MOM', 'nobody')
    assert not plan.ok and '没有' in plan.reason


# ==================== ③ 新角色计划 ====================

def test_new_character_defaults_to_the_token_itself():
    """口径：变量名/显示名默认就是 token 原样（**不编人名**），弹窗里可改。"""
    plan = plan_new_character(ROWS, 'TJF')
    assert plan.ok, plan.reason
    assert plan.row['ID'] == 'TJF' and plan.row['Name'] == 'TJF'
    assert 'TJF' in plan.row['Notes']


def test_new_character_accepts_a_hand_edited_name():
    plan = plan_new_character(ROWS, 'MOM', var='mom_new', name='我妈')
    assert plan.ok, plan.reason
    assert plan.row['ID'] == 'mom_new' and plan.row['Name'] == '我妈'


def test_new_character_reports_duplicate_variable():
    plan = plan_new_character(ROWS, 'MOM', var='mm')
    assert not plan.ok and '已经存在' in plan.reason


def test_new_character_reports_duplicate_display_name():
    """显示名已被别人用 → 应该当作已有角色加别名，而不是建第二个人。"""
    plan = plan_new_character(ROWS, 'MOM', var='mom2', name='妈妈')
    assert not plan.ok and '显示名' in plan.reason


def test_new_character_row_only_carries_columns_with_content():
    """
    ⚠ 空字符串列**不能放进行里**：`append_rows_to_table` 判"值变没变"用的是
    `(cell.value or '') != (row.get(key) or '')`，而 openpyxl 把空单元格读成 `None`
    —— 空串会被判成"没变化"（表里也不会出现空列，效果一样，但会让"写了几行"
    的返回值失真）。判据钉住：行里不许有空的列。
    """
    plan = plan_new_character(ROWS, 'TJF')
    assert plan.ok, plan.reason
    assert all(str(v).strip() for v in plan.row.values()), plan.row


def test_new_character_may_reuse_a_prefix_of_an_existing_name():
    """
    ⚠ 判据必须是"**归一后是同一个写法**"，而不是"看着像"：
    `MOM` 与 `mm` 前缀相似，但 `MOM` 建出来**不会**抢 `mm` 的任何写法
    （它归一后是 `mom`，两者不同）→ 允许建。
    探针实测：早先用"新建就查一遍全表 conflicts"的写法会把这种**合法**新建也判死。
    """
    plan = plan_new_character(ROWS, 'MOM')
    assert plan.ok, plan.reason
    assert plan.row['ID'] == 'MOM'


def test_new_character_reports_a_collision():
    """新建的变量名与别人只差大小写 → 不许建（那正是"两个人"的坑）。"""
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'}}
    plan = plan_new_character(rows, 'LJK', var='Ljk')
    assert not plan.ok
    assert '归一后是同一个写法' in plan.reason, plan.reason


def test_illegal_token_still_gets_a_usable_variable_name():
    plan = plan_new_character(ROWS, '123')
    assert plan.ok, plan.reason
    assert plan.row['ID'].isidentifier() or not plan.row['ID'][0].isdigit()


# ==================== ④ 落盘（只动该动的那一列 / 只追加） ====================

def test_apply_alias_plans_only_touches_the_alias_column(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(['ID', 'Name', 'Color', 'ImageTag', 'Aliases', 'Notes'])
    ws.append(['mm', '妈妈', '', '', '', '手写的备注'])
    ws.append(['jc', '「蒋忱」', '', '', '', ''])
    wb.save(tmp_path / 'characters.xlsx')

    rows = {'mm': {'ID': 'mm', 'Name': '妈妈', 'Notes': '手写的备注'},
            'jc': {'ID': 'jc', 'Name': '「蒋忱」'}}
    plan = plan_alias(rows, 'MM2', 'mm')
    assert plan.ok, plan.reason
    res = apply_alias_plans(tmp_path, [plan])
    assert res['ok'] and res['written'] >= 1, res

    from openpyxl import load_workbook
    ws2 = load_workbook(tmp_path / 'characters.xlsx').active
    got = {r[0]: list(r) for r in ws2.iter_rows(values_only=True)}
    assert got['mm'][4] == 'MM2', got['mm']            # 别名写进去了
    assert got['mm'][5] == '手写的备注', '人写的备注被改了！'   # 别的列一个不碰


def test_apply_new_character_plans_appends_and_keeps_existing_rows(tmp_path):
    from openpyxl import Workbook, load_workbook
    wb = Workbook()
    ws = wb.active
    ws.append(['ID', 'Name', 'Color', 'ImageTag', 'Aliases', 'Notes'])
    ws.append(['mm', '妈妈', '', '', 'MOM', ''])
    wb.save(tmp_path / 'characters.xlsx')

    plan = plan_new_character({'mm': {'ID': 'mm', 'Name': '妈妈'}},
                              'TJF', notes='由纯文字导入创建')
    assert plan.ok, plan.reason
    res = apply_new_character_plans(tmp_path, [plan])
    assert res['ok'] and res['written'] >= 1, res

    ws2 = load_workbook(tmp_path / 'characters.xlsx').active
    got = {r[0]: list(r) for r in ws2.iter_rows(values_only=True)}
    assert 'TJF' in got, got
    assert got['mm'][4] == 'MOM', '已有行的别名被动了'


def test_a_legacy_table_without_the_alias_column_gets_it_added(tmp_path):
    """
    ⚠ 2026-10-10 实机抓到的**静默失败**：真机 `characters.xlsx` 是加「别名」列
    **之前**建的（只有 5 列：ID/Name/Color/ImageTag/Notes）。而
    `_update_row_cells` 遇到"表头里没有的键"是**静默跳过**的 ——

        给真人项目加别名 → 返回成功、实际什么都没发生（用户以为配好了）

    现在：缺的列**当场补上**（按 `_TABLE_FIELDS` 的顺序，别跑到 `Notes` 后面）。
    """
    from openpyxl import Workbook, load_workbook
    wb = Workbook()
    ws = wb.active
    ws.append(['ID', 'Name', 'Color', 'ImageTag', 'Notes'])      # 老表：没有别名列
    ws.append(['mm', '妈妈', None, None, '该行是人写的备注'])
    wb.save(tmp_path / 'characters.xlsx')

    rows = {'mm': {'ID': 'mm', 'Name': '妈妈', 'Notes': '该行是人写的备注'}}
    plan = plan_alias(rows, 'MOM', 'mm')
    assert plan.ok, plan.reason
    res = apply_alias_plans(tmp_path, [plan])
    assert res['written'] >= 1, res

    ws2 = load_workbook(tmp_path / 'characters.xlsx').active
    headers = [c.value for c in ws2[1]]
    got = {r[0]: dict(zip(headers, r)) for r in ws2.iter_rows(values_only=True)
           if r and r[0]}
    assert 'Aliases' in headers, headers
    assert got['mm']['Aliases'] == 'MOM', got['mm']
    # ⚠ 补列只许**追加在末尾**，不许挪动已有列（挪了就把用户写好的列位置改了，
    # 而别的代码/人是按列位置读表的）—— 所以这里刻意**不**断言
    # "别名在备注之前"。
    assert headers.index('Notes') == 4, headers
    assert got['mm']['Notes'] == '该行是人写的备注', '人写的备注被改了'


# ==================== ⑤ 提议层（建议 → 可执行动作） ====================

def test_proposal_prefers_an_alias_over_creating_a_character():
    """能靠别名解决就别改角色表结构 —— 顺序是刻意的。"""
    from storygraph.authoring.char_decisions import (ACT_EXISTING, propose_one,
                                                     suggestions)
    sug = suggestions(_conv('MOM"吃饭了"'), CharacterMap(ROWS), ROWS)[0]
    prop = propose_one(ROWS, sug)
    assert prop.action == ACT_EXISTING and prop.executable
    assert prop.alias.var == 'mm' and prop.alias.after == 'MOM'
    assert not prop.new_ok


def test_proposal_creates_a_character_when_nothing_is_similar():
    from storygraph.authoring.char_decisions import (ACT_NEW, propose_one,
                                                     suggestions)
    sug = suggestions(_conv('BAOAN"你要去哪个地方"'), CharacterMap(ROWS), ROWS)[0]
    prop = propose_one(ROWS, sug)
    assert prop.action == ACT_NEW and prop.executable
    assert prop.new.row['ID'] == 'BAOAN'


def test_proposal_respects_a_hand_picked_target():
    """人在下拉里指定了角色 → 只试别名，不许自作主张建新角色。"""
    from storygraph.authoring.char_decisions import propose_alias
    prop = propose_alias(ROWS, 'TJF', 'txf')
    assert prop.alias_ok and prop.alias.var == 'txf'


def test_proposal_does_not_offer_a_colliding_new_character():
    """
    ⚠ 别名走不通时（`LJK` vs 变量 `ljk`）给的"第二条路"**也必须是安全的**：
    新建 `LJK`（或显示名写成 `LJK`）会与 `ljk` 归一撞车，所以这里**不能**
    假装能建。真正可行的处置是**改变量名**（`ljk2` + 别的显示名）。
    """
    from storygraph.authoring.char_decisions import propose_alias
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'}}
    prop = propose_alias(rows, 'LJK', 'ljk')
    assert not prop.alias_ok
    assert '只差大小写' in prop.note
    assert not prop.new_ok, '默认新建（ID=LJK/显示名=LJK）会撞车，不该说能建'
    assert '归一后是同一个写法' in prop.new.reason, prop.new.reason


def test_a_renamed_new_character_is_allowed_when_it_really_is_a_second_person():
    """真的是第二个人 → 变量名与显示名都要**不靠大小写区分**，这才建得成。"""
    from storygraph.authoring.char_decisions import plan_new_character
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'}}
    plan = plan_new_character(rows, 'LJK', var='ljk2', name='「陆嘉珂」')
    assert plan.ok, plan.reason
    assert plan.row['ID'] == 'ljk2' and plan.row['Name'] == '「陆嘉珂」'


def test_a_case_only_variant_auto_resolves_when_it_is_the_only_spelling():
    """表里只有一个 `ljk` 时，`LJK` **自动**归到它 —— 不进弹窗（最省事的结局）。"""
    from storygraph.authoring.char_decisions import proposals
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'}}
    conv = _conv('LJK"我是谁"', rows)
    assert conv.unmapped == {} and conv.ambiguous == {}
    assert '    ljk "我是谁"' in conv.rpy_text
    assert proposals(conv, CharacterMap(rows), rows) == []


def test_two_people_who_differ_only_in_case_need_a_human_decision():
    """用户在意的那个场景：`ljk` 与 `Ljk` 都在表里 → 归一写法必须人来定。"""
    from storygraph.authoring.char_decisions import ACT_SKIP, propose_one
    rows = {'ljk': {'ID': 'ljk', 'Name': '「李佳凯」'},
            'Ljk': {'ID': 'Ljk', 'Name': '「陆嘉珂」'}}
    conv = _conv('LJK"我是谁"', rows)
    assert 'LJK' in conv.ambiguous
    from storygraph.authoring.char_decisions import suggestions
    sug = suggestions(conv, CharacterMap(rows), rows)[0]
    assert '不能靠别名解决' in sug.note
    prop = propose_one(rows, sug)
    assert prop.action == ACT_SKIP and not prop.executable
    assert '只差大小写' in prop.note or '撞车' in prop.note


def test_proposals_lists_every_unmapped_token():
    from storygraph.authoring.char_decisions import proposals
    text = 'MOM"一"\nTJF"二"\nBAOAN"三"'
    got = proposals(_conv(text), CharacterMap(ROWS), ROWS)
    assert [p.token for p in got] == ['BAOAN', 'MOM', 'TJF']
    assert all(p.executable for p in got), [p.token for p in got if not p.executable]


def test_apply_with_nothing_to_do_is_not_an_error():
    assert apply_alias_plans('.', [])['ok'] is False
    assert apply_new_character_plans('.', [])['ok'] is False


def test_a_typo_in_the_table_key_is_not_silent(tmp_path):
    """
    ⚠ 2026-10-10 实测踩到的坑：资源表键是**复数**（`'characters'`），
    而 `_TABLE_FIELDS` 是**单数**（`'character'`）。老实现遇到拼错的键
    **静默返回 0** —— 调用方以为写成功、用户以为别名加上了，其实什么都没发生。
    现在必须**出声**（否则这个功能最贵的那种失败恰恰是无声的）。
    """
    import pytest
    from storygraph.assets.asset_registry import append_rows_to_table
    with pytest.raises(KeyError) as ei:
        append_rows_to_table(tmp_path, 'character', [{'ID': 'x', 'Name': 'x'}])
    assert 'characters' in str(ei.value), ei.value
    # 复数（真源键）照常工作
    (tmp_path / 'characters.xlsx').unlink(missing_ok=True)
    assert append_rows_to_table(tmp_path, 'characters',
                                [{'ID': 'x', 'Name': 'x'}]) >= 1


@pytest.mark.skipif(
    not (Path(r'D:\千连万理\RenpyTest\sr_general')).is_dir(),
    reason='真机项目不在本机')
def test_the_real_nine_unmapped_tokens_all_get_a_usable_decision():
    """
    真机 5 章里那 8 个未映射写法 + `A`/`B`：每一个都必须能落到**某个可执行的决定**上
    （加别名 / 建新角色 / 手工指定）—— 不许出现"既没候选、又不能建"的死角。
    """
    from storygraph.pipeline.build import load_asset_registry
    rows = load_asset_registry(Path(r'D:\千连万理\RenpyTest\sr_general')).characters
    text = ('MOM"跟妈客气啥"\nA"卷王——"\nB"大学生楷模——"\nLINGDAO"金秋九月"\n'
            'JIAOGUAN"预备——"\nXIAOYI"吃早餐了吗"\nBAOAN"你要去哪个地方"\n'
            'TJF"你怎么睡得着哒"\n')
    for sug in suggestions(_conv(text, rows), CharacterMap(rows), rows):
        alias_ok = any(plan_alias(rows, sug.token, r).ok for r in rows)
        new_ok = plan_new_character(rows, sug.token).ok
        assert alias_ok or new_ok, f'{sug.token} 没有任何可执行的决定'
