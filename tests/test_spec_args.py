"""
「每参数一列」的第一层：**头行参数的唯一真源**
=============================================

`story/spec_args.py` 是这次改动的支点，它要同时满足三件事，缺一件整块就塌：

  ① **不改就不动字节**：表里每一格都会把值写回来，所以"值没变"必须
     逐字节原样返回 —— 否则 `target="ling"` 被改写成 `target=ling`，
     用户看到的是"我只是打开表又导出，引号就没了"；
  ② **改了只动那一个值**：头行里的缩进、参数顺序、别的参数的写法、
     行尾的块标记 `:` 都不许动（改一个参数顺手重排整行是**改别人的脚本**）；
  ③ **引号里的 `a=b` 不是参数**：那是台词/值的内容。老实现会把
     `mycue "a=b" intensity=3` 里的 `a=b` 也收成参数，于是"每参数一列"
    会多出一列，而改那一列等于改台词。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story import spec_args as sa  # noqa: E402


# ---------------------------------------------------------------------------
#  ① 不改就不动字节
# ---------------------------------------------------------------------------

def test_setting_the_same_value_changes_nothing():
    for head in ('mycue "闪电" intensity=3 target=ling:',
                 "mycue \"闪电\" target='ling'",
                 'mycue "闪电"  intensity = 3:',
                 'mycue "闪电" intensity="3":'):
        for name, value in sa.spec_arg_values(head).items():
            assert sa.set_spec_arg(head, name, value) == head, (
                f'{name} 的值没变，头行却被改写了：{head!r}')


def test_quotes_are_kept_when_value_is_unchanged():
    """`target="ling"` 不变时**引号不许被吃掉**（这是最容易踩的一条）。"""
    head = 'mycue "闪电" target="ling":'
    assert sa.set_spec_arg(head, 'target', 'ling') == head


# ---------------------------------------------------------------------------
#  ② 改了只动那一个值
# ---------------------------------------------------------------------------

def test_changing_one_value_touches_only_that_token():
    head = 'mycue "闪电" intensity=3 target=ling:'
    out = sa.set_spec_arg(head, 'intensity', '7')
    assert out == 'mycue "闪电" intensity=7 target=ling:', out


def test_other_whitespace_and_order_are_preserved():
    """参数顺序、多余空格、行尾块标记都逐字保留。"""
    head = 'mycue   "闪电"   target=ling   intensity = 3   :'
    out = sa.set_spec_arg(head, 'intensity', '5')
    assert out == 'mycue   "闪电"   target=ling   intensity = 5   :', out


def test_value_needing_quotes_gets_quoted():
    head = 'mycue "x" target=ling:'
    out = sa.set_spec_arg(head, 'target', 'ling close_happy')
    assert out == 'mycue "x" target="ling close_happy":', out
    assert sa.spec_arg_values(out)['target'] == 'ling close_happy'


def test_clearing_a_value_removes_the_parameter():
    head = 'mycue "x" intensity=3 target=ling:'
    out = sa.set_spec_arg(head, 'intensity', '')
    assert out == 'mycue "x" target=ling:', out


def test_appending_a_new_parameter_goes_before_the_block_colon():
    head = 'mycue "x" intensity=3:'
    out = sa.set_spec_arg(head, 'target', 'ling')
    assert out == 'mycue "x" intensity=3 target=ling:', out
    assert sa.spec_arg_values(out) == {'intensity': '3', 'target': 'ling'}


def test_clearing_a_missing_parameter_is_not_an_edit():
    head = 'mycue "x" intensity=3:'
    assert sa.set_spec_arg(head, 'target', '') == head


def test_fold_applies_every_parameter():
    head = 'mycue "x" intensity=3 target=ling:'
    out = sa.fold_spec_args(head, {'intensity': '3', 'target': 'lan'})
    assert out == 'mycue "x" intensity=3 target=lan:', out


# ---------------------------------------------------------------------------
#  ③ 引号里的 `a=b` 不是参数
# ---------------------------------------------------------------------------

def test_kv_inside_a_string_is_not_a_parameter():
    vals = sa.spec_arg_values('mycue "a=b" intensity=3')
    assert vals == {'intensity': '3'}, vals


def test_setting_a_value_never_touches_a_string_body():
    head = 'mycue "a=b" intensity=3:'
    out = sa.set_spec_arg(head, 'a', 'zzz')
    assert out == 'mycue "a=b" intensity=3 a=zzz:', out


def test_escaped_quote_inside_a_string_does_not_break_scanning():
    vals = sa.spec_arg_values(r'mycue "他说\"a=b\"" intensity=3')
    assert vals == {'intensity': '3'}, vals


# ---------------------------------------------------------------------------
#  动态列名
# ---------------------------------------------------------------------------

def test_dynamic_column_name_round_trips():
    name = sa.dynamic_column_name('mycue', 'intensity')
    assert name == 'mycue·intensity', name
    assert sa.parse_dynamic_column(name) == ('mycue', 'intensity')
    assert sa.is_dynamic_column(name)


def test_non_dynamic_headers_are_not_mistaken():
    for header in ('', '台词', 'Speaker', 'a·b·c', 'a·', '·b',
                   '1bad·x', 'a·1bad'):
        assert not sa.is_dynamic_column(header), header


def test_fold_dynamic_cells_ignores_unknown_columns():
    got = sa.fold_dynamic_cells(None, {'mycue·target': 'lan',
                                       '含义·': 'x',
                                       'Speaker': 'ling'})
    assert got == {'target': 'lan'}, got


def test_dynamic_headers_are_ordered_and_deduped():
    rows = [{'mycue·intensity': 1, 'mycue·target': 'a'},
            {'Speaker': 'x', 'mycue·intensity': 2}]
    assert sa.dynamic_headers(rows) == ['mycue·intensity', 'mycue·target']


# ---------------------------------------------------------------------------
#  位置参数：**只按声明认，不猜**
# ---------------------------------------------------------------------------

def test_positional_tokens_skip_head_named_args_and_block_colon():
    assert [t for _s, _e, t in
            sa.positional_tokens('camera background:')] == ['background']
    assert sa.positional_values('camera background:') == ['background']
    assert sa.positional_values('camera:') == []
    # 有名字的参数不算位置参数；说话人头（第一个词）也不算
    assert sa.positional_values('mycue "闪电" intensity=3:') == ['闪电']
    assert sa.positional_values('pause 1.0') == ['1.0']
    # 引号串整串算**一个** token（不是两个词）
    assert sa.positional_values('mycue "一句话 带空格" x=1') == ['一句话 带空格']


def test_positional_values_ignore_kv_inside_strings():
    assert sa.positional_values('mycue "a=b" intensity=3') == ['a=b']


def test_setting_the_same_positional_value_changes_nothing():
    for head, idx, value in (('camera background:', 0, 'background'),
                             ("camera 'background':", 0, 'background'),
                             ('camera   background  :', 0, 'background')):
        assert sa.set_positional_arg(head, idx, value) == head, head


def test_changing_a_positional_value_replaces_only_that_token():
    assert sa.set_positional_arg('camera background:', 0, 'screens') == \
        'camera screens:'
    assert sa.set_positional_arg('camera   background  :', 0, 'screens') == \
        'camera   screens  :'


def test_positional_value_with_spaces_gets_quoted():
    out = sa.set_positional_arg('camera background:', 0, 'my layer')
    assert out == 'camera "my layer":', out
    assert sa.positional_values(out) == ['my layer']


def test_clearing_a_positional_value_removes_the_token():
    assert sa.set_positional_arg('camera background:', 0, '') == 'camera:'


def test_appending_a_missing_positional_value_goes_before_the_colon():
    assert sa.set_positional_arg('camera:', 0, 'background') == \
        'camera background:'
    assert sa.set_positional_arg('camera:', 0, '') == 'camera:'
    assert sa.set_positional_arg('camera:', 5, 'x') == 'camera x:'


def test_out_of_range_index_does_not_touch_existing_tokens():
    """下标超出范围 → 追加到**尾冒号之前**，绝不回头去改第 0 个（那是改别人的参数）。"""
    assert sa.set_positional_arg('camera background:', 3, 'zzz') == \
        'camera background zzz:'


# ---------------------------------------------------------------------------
#  位置号进列名（读表那一侧没有 schema，只能从列名判）
# ---------------------------------------------------------------------------

def test_positional_column_name_carries_the_index():
    name = sa.dynamic_column_name('camera', 'layer', positional=0)
    assert name == 'camera·layer@0', name
    assert sa.parse_dynamic_full(name) == ('camera', 'layer', 0)
    assert sa.parse_dynamic_column(name) == ('camera', 'layer')
    assert sa.is_dynamic_column(name)


def test_named_column_has_no_index():
    assert sa.parse_dynamic_full('mycue·intensity') == \
        ('mycue', 'intensity', None)


def test_fold_dynamic_columns_uses_the_index_from_the_name():
    # 位置参数：替换第 0 个 token（**不是**追加 layer=…）
    assert sa.fold_dynamic_columns('camera background:',
                                   {'camera·layer@0': 'screens'}) == \
        'camera screens:'
    # 有名字的参数：照旧写 key=value
    assert sa.fold_dynamic_columns('mycue "x" intensity=3:',
                                   {'mycue·intensity': '7'}) == \
        'mycue "x" intensity=7:'
