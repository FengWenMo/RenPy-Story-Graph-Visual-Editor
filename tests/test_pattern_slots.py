"""
位置序列判据（2026-10-11 程序员撞墙记录 → 重新设计）
=========================================================

用户（程序员）用 `show 小明 泳装 开心` 填旧表单时**从第一步卡死**，诊断是：
"用**扁平的键值对表单**去描述**树状语法结构**"。

这份判据钉的就是他卡住的那四件事：
  ① 顺序（三张卡凭什么对应 `角色 服装 神态`）；
  ② 类型（工具怎么知道 `小明` 是变量、`泳装` 是枚举）；
  ③ 切行（这一行会被怎么切开 —— 界面必须能当场显示）；
  ④ 可选段（`at 位置` 这种可以不出现的段，不能被当成变量吃掉）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.authoring import pattern as P                          # noqa: E402

#: 用户那个真实需求：`show 小明 泳装 开心`（+ 可选的 `at 位置`）
SHOW_PATTERN = [
    {'kind': 'keyword', 'text': 'show'},
    {'kind': 'variable', 'column': '角色'},
    {'kind': 'choice', 'column': '服装', 'group': 'outfit'},
    {'kind': 'choice', 'column': '神态', 'group': 'expression'},
]
GROUPS = {
    'outfit': {'label': '服装', 'options': ['泳装', '校服']},
    'expression': {'label': '神态', 'options': ['开心', '害羞']},
}


def test_order_is_the_list_order_not_a_guess():
    """① **顺序就是列表顺序** —— 格子顺序变了，切行结果跟着变。"""
    got = P.match_line(SHOW_PATTERN, 'show 小明 泳装 开心', groups=GROUPS)
    assert got['ok'], got
    assert [c['text'] for c in got['cells']] == ['show', '小明', '泳装', '开心']
    assert [c['column'] for c in got['cells']] == ['', '角色', '服装', '神态']
    # 把"服装/神态"两格换个位置 → 原来那行就切不出来了（顺序是有意义的）
    swapped = [SHOW_PATTERN[0], SHOW_PATTERN[1], SHOW_PATTERN[3], SHOW_PATTERN[2]]
    assert P.match_line(swapped, 'show 小明 泳装 开心',
                        groups=GROUPS)['ok'] is False


def test_kind_tells_variable_from_enum():
    """② `小明` 是变量、`泳装` 是枚举 —— 靠**格子自己的类型**，不靠猜。"""
    kinds = [s['kind'] for s in P.parse_pattern(SHOW_PATTERN)]
    assert kinds == ['keyword', 'variable', 'choice', 'choice']
    # 枚举格**认候选值**：写个不在候选里的词 → 切不出来（并指出这是问题）
    bad = P.match_line(SHOW_PATTERN, 'show 小明 龙袍 开心', groups=GROUPS)
    assert bad['ok'] is False
    # 变量格什么都能吃
    assert P.match_line(SHOW_PATTERN, 'show 小红 泳装 开心',
                        groups=GROUPS)['ok'] is True


def test_the_line_is_cut_into_slots_for_live_preview():
    """③ **实时切行**：界面把这一行切成色块 —— 这是用户卡死的第一步的答案。"""
    got = P.match_line(SHOW_PATTERN, 'show 小明 泳装 开心', groups=GROUPS)
    assert len(got['cells']) == 4
    assert all(c['ok'] for c in got['cells'])
    # 多出来的词要**明确报出来**（不许默默吞掉）
    extra = P.match_line(SHOW_PATTERN, 'show 小明 泳装 开心 大笑',
                         groups=GROUPS)
    assert extra['ok'] is False
    assert extra['leftover'] == ['大笑'], extra
    assert '没人认领' in extra['reason'], extra


def test_optional_segment_may_be_absent_and_is_not_eaten_as_a_variable():
    """
    ④ ⚠ 可选段最容易写错：`show 小明 泳装 开心 at 左边` 里的 `at 左边`
    必须被**可选段**吃掉，而不是让变量格乱吃 —— 而且**不出现时也要能匹配**。
    """
    pat = SHOW_PATTERN + [{'kind': 'optional', 'text': 'at', 'column': '位置'}]
    with_at = P.match_line(pat, 'show 小明 泳装 开心 at 左边', groups=GROUPS)
    assert with_at['ok'], with_at
    assert with_at['cells'][-1]['kind'] == 'optional'
    assert 'at' in with_at['cells'][-1]['text']
    # 不出现 → 照样匹配
    without = P.match_line(pat, 'show 小明 泳装 开心', groups=GROUPS)
    assert without['ok'], without
    assert len(without['cells']) == 4


def test_chinese_without_spaces_is_best_effort_and_never_wrong_silently():
    """
    中文项目的现实：`show小明泳装开心` 没有空格。有候选值/关键字时**尽量切**；
    切不准就**明确失败并说清** —— 宁可不匹配，也不猜错边界。
    """
    got = P.match_line(SHOW_PATTERN, 'show小明泳装开心', groups=GROUPS)
    # 这句能切到什么程度取决于候选值覆盖得多全；**要钉的是"它说了实话"**
    if not got['ok']:
        assert got['reason'], got
    # 完全不给候选值/词组时 → 不许假装切开了
    bare = P.match_line(SHOW_PATTERN, 'show小明泳装开心')
    assert bare['ok'] is False, bare
    assert bare['reason'], bare


def test_problems_are_reported_in_plain_language():
    """坏格子要**用人话说清**（界面直接显示；不出现 schema 这类词）。"""
    bad = P.parse_pattern([
        {'kind': 'keyword'},                     # 没说固定哪个字
        {'kind': 'choice', 'column': '服装'},     # 枚举却没候选值/词组
        {'kind': 'nonsense', 'column': 'x'},
    ])
    probs = P.problems_of(bad)
    assert any('固定段' in p for p in probs), probs
    assert any('候选值' in p or '词组' in p for p in probs), probs
    blob = ' '.join(probs)
    assert 'schema' not in blob.lower() and 'JSON' not in blob


def test_columns_and_groups_are_derived_in_order():
    """表格列 / 用到的词组**按顺序**推出来（界面据此提示"还得建词组"）。"""
    assert P.columns_of(SHOW_PATTERN) == ['角色', '服装', '神态']
    assert P.groups_used(SHOW_PATTERN) == ['outfit', 'expression']


def test_old_flat_shape_is_still_accepted():
    """
    ⚠ 旧形状（键值对：`[{'name': '强度', 'label': '强度'}]`）**要继续认** ——
    判据 `test_custom_statement_entry.py` / `test_spec_groups.py` 还压着它，
    不许为了新设计把老数据读崩。
    """
    got = P.parse_pattern([{'name': '强度', 'label': '强度',
                            'choices': '1,2,3'}])
    assert got[0]['kind'] == 'keyword', got       # 认不出类型就按"固定段"处理
    assert got[0]['column'] == '强度'
    assert got[0]['options'] == ['1', '2', '3']
    # 老的字符串形状也认：`强度=` 猜成变量
    simple = P.parse_pattern(['强度='])
    assert simple[0]['kind'] == 'variable' and simple[0]['column'] == '强度'


def test_the_sequence_lands_in_the_declaration_in_order():
    """
    ⭐⭐ **顺序必须真的落进声明**（不能只用来做试切）：
    用户卡点"竖排的三张卡凭什么对应那个顺序" —— 现在顺序决定
    ① `columns` 的排列 ② 存下来的 `pattern`（后端据此知道代码长什么样）。
    ⚠ 而且**中文列名要保住**：那是我们自己表格的列，不是脚本参数，
    不该套"必须是合法标识符"那套校验（旧校验会把「角色/服装」全丢掉）。
    """
    from storygraph.authoring.learn_statement import build_declaration
    slots = [
        {'kind': 'keyword', 'text': 'show'},
        {'kind': 'variable', 'column': '角色'},
        {'kind': 'choice', 'column': '服装', 'options': ['泳装', '校服']},
        {'kind': 'optional', 'text': 'at', 'column': '位置'},
    ]
    built = build_declaration('show', label='出场', pattern=slots)
    assert built['ok'], built
    assert [c['name'] for c in built['columns']] == ['角色', '服装', '位置'], \
        built['columns']
    assert built['columns'][1]['choices'] == ['泳装', '校服']
    assert [s['kind'] for s in built['pattern']] == [
        'keyword', 'variable', 'choice', 'optional']
    # 把最后两格换个位置 → 列顺序跟着换（顺序是有意义的）
    swapped = [slots[0], slots[1], slots[3], slots[2]]
    assert [c['name'] for c in
            build_declaration('show', pattern=swapped)['columns']] == [
        '角色', '位置', '服装']


def test_without_a_pattern_the_product_is_unchanged():
    """⚠ **只增不改**：没给 `pattern` 时，产物与以前一模一样（老判据压在这上面）。"""
    from storygraph.authoring.learn_statement import build_declaration
    old = build_declaration('swim_suit', label='泳装',
                            params=[{'name': 'intensity', 'label': '强度',
                                     'choices': '1,2,3'}])
    assert 'pattern' not in old, '没给 pattern 却多出了一个键'
    assert [c['name'] for c in old['columns']] == ['intensity']


def test_the_sequence_is_persisted_into_the_manifest():
    """后端要把 `pattern` 存进清单（否则下次打开就丢了顺序与类型）。"""
    import json
    from storygraph.authoring.learn_statement import build_declaration
    from storygraph.story.statement_registry import declare_statement
    slots = [{'kind': 'keyword', 'text': 'show'},
             {'kind': 'variable', 'column': '角色'}]
    built = build_declaration('show', label='出场', pattern=slots)
    got = declare_statement(ROOT / '.pip-tmp' / 'pat_decl_tmp', 'show',
                            label='出场', columns=built['columns'],
                            pattern=built.get('pattern'))
    assert got['ok'], got
    data = json.loads((ROOT / '.pip-tmp' / 'pat_decl_tmp'
                       / 'storygraph.rules.json').read_text(encoding='utf-8'))
    entry = data['statements'][0]
    assert [s['kind'] for s in entry['pattern']] == ['keyword', 'variable']
    assert [c['name'] for c in entry['columns']] == ['角色']


def test_indices_and_kind_labels_are_for_the_ui():

    """界面要用的人话标签在（`固定关键字 / 任意变量 / 从这些里选 / 可选的固定段`）。"""
    assert P.KIND_LABELS['keyword'] == '固定关键字'
    assert P.KIND_LABELS['variable'] == '任意变量'
    assert P.KIND_LABELS['choice'] == '从这些里选'
    assert set(P.KIND_LABELS) == set(P.SLOT_KINDS)
