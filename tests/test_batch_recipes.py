# -*- coding: utf-8 -*-
"""
「可视化配规则」的**纯函数层**（`app.batch_recipes`）。

用户 2026-11-07：

> 我不要手写 json！我这个工具是给编剧设计的！我需要自定义模块中的可视化界面！

所以钉这几条会要命的事：

1. **人只填"什么时候加 / 加什么"，代码是拼出来的** —— 拼出来的规则必须过
   `batch_edit.parse_rule`（同一份校验），不能"界面说能存、引擎说不能用"；
2. **反解要往返一致**：`build_rule(form)` → `read_form()` → `build_rule()`
   必须给出同一段文本（否则"打开文件什么都没改，一保存就变样"）；
3. **认不出就不改**：`replace`/`delete`/带章名条件的规则，`read_form` 给
   `None` + 原因，`dump_rules` **原样带回去**（绝不因为界面没显示就删掉）；
4. **反复保存逐字相同**（`dump_rules` 稳定）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.batch_edit import BatchRuleError, parse_rule
from storygraph.app.batch_recipes import (DEFAULT_FORM, build_rule, build_text,
                                          describe, dump_rules, load_rules,
                                          next_id, read_form, save_rules)

CUE = '$ renpy.transition(Dissolve(0.1), layer="master")'


def _form(**kw) -> dict:
    f = dict(DEFAULT_FORM)
    f.update(kw)
    return f


# ----------------------------------------------------------------------
#  拼代码：四种「加什么」
# ----------------------------------------------------------------------

def test_master_layer_is_the_default_and_quotes_the_layer():
    assert build_text(_form()) == CUE


def test_full_screen_has_no_layer_argument():
    assert build_text(_form(cue='full_transition')) == \
        '$ renpy.transition(Dissolve(0.1))'


def test_named_transition_writes_the_bare_name():
    assert build_text(_form(cue='named', named='dis_master')) == \
        '$ renpy.transition(dis_master, layer="master")'


def test_kwargs_order_and_always():
    got = build_text(_form(always=True))
    assert got == '$ renpy.transition(Dissolve(0.1), layer="master", always=True)'


def test_seconds_are_normalised_but_not_rounded():
    assert build_text(_form(seconds='0.10')) == CUE        # 0.10 → 0.1
    assert build_text(_form(seconds='0.15')) == \
        '$ renpy.transition(Dissolve(0.15), layer="master")'


@pytest.mark.parametrize('bad', ['', 'abc', '0', '-1', '1,5'])
def test_bad_seconds_are_refused(bad):
    with pytest.raises(BatchRuleError):
        build_text(_form(seconds=bad))


def test_raw_must_start_with_a_dollar_sign():
    assert build_text(_form(cue='raw', raw=CUE)) == CUE
    with pytest.raises(BatchRuleError) as got:
        build_text(_form(cue='raw', raw='renpy.transition(Dissolve(0.1))'))
    assert '$' in str(got.value)


def test_unknown_effect_is_refused_not_silently_written():
    with pytest.raises(BatchRuleError):
        build_text(_form(effect='my_wipe'))     # 名字走「项目里定义好的转场」


# ----------------------------------------------------------------------
#  拼规则：`when` / `placement` 的映射
# ----------------------------------------------------------------------

def test_sprite_change_covers_hide_and_adds_after_every_change():
    """「每次…」= 一拍里每一条 `show`/`hide` 后面都补（2026-11-08 第十九轮）。

    用户拿真机 2_2 指着一个拍里并排的三条 `show`：

    > 本来是两条分别的 show！你应该加两个那一句话……三个 show 并列在一个
    > 卡片内……你只给了一个……
    """
    rule = build_rule(_form(mode='sprite_change'), 'r1')
    assert rule['insert']['placement'] == 'after_each_change'
    assert rule['when'] == {'change': 'sprite', 'transition': 'none',
                            'code_not_contains': 'renpy.transition'}
    parse_rule(rule)                              # 唯一校验必须放行


def test_the_last_only_modes_still_reach_the_engine():
    """「每拍只在最后一次…」也要留着：`$ renpy.transition` 这种设一次就够。"""
    change = build_rule(_form(mode='sprite_change_last'), 'r1')
    assert change['insert']['placement'] == 'after_last_change'
    assert change['when'] == {'change': 'sprite', 'transition': 'none',
                              'code_not_contains': 'renpy.transition'}
    show = build_rule(_form(mode='sprite_show_last'), 'r2')
    assert show['insert']['placement'] == 'after_last_show'


def test_sprite_show_mode_is_per_show_and_keeps_the_shipped_example_readable():
    """`examples/batch/master-dissolve.json`（`after_last_show`）打开要能原样存回。"""
    rule = build_rule(_form(mode='sprite_show'), 'r1')
    assert rule['insert'] == {'placement': 'after_each_show', 'text': CUE,
                              'skip_if_present': True}
    assert rule['when'] == {'show': 'sprite', 'transition': 'none',
                            'code_not_contains': 'renpy.transition'}
    shipped = {'id': 'master-dissolve',
               'when': {'show': 'sprite', 'transition': 'none',
                        'code_not_contains': 'renpy.transition'},
               'insert': {'placement': 'after_last_show', 'text': CUE}}
    form, why = read_form(shipped)
    assert why == '' and form['mode'] == 'sprite_show_last'
    again = build_rule(form, 'master-dissolve')
    # 语义一致：只在**最后一条**立绘后面补（多写的那个 `skip_if_present`
    # 是界面自己的默认，不是把规则改味了）
    assert again['insert']['placement'] == 'after_last_show'
    assert again['insert']['text'] == CUE
    assert again['when'] == shipped['when']


def test_say_and_beat_modes():
    say = build_rule(_form(mode='say_line'), 'r1')
    assert say['when']['say'] is True
    assert say['insert']['placement'] == 'after_beat'
    beat = build_rule(_form(mode='every_beat', skip_existing_transition=False),
                      'r2')
    assert beat['when'] == {}
    assert beat['insert']['placement'] == 'after_beat'


def test_turning_off_the_two_guards_removes_them_from_when():
    rule = build_rule(_form(skip_existing_transition=False), 'r1')
    assert rule['when'] == {'change': 'sprite'}
    assert rule['insert']['skip_if_present'] is True


def test_built_rule_is_accepted_by_the_engine_parser():
    for mode, _text in (('sprite_change', ''), ('sprite_show', ''),
                        ('say_line', ''), ('every_beat', '')):
        for cue in ('master_transition', 'full_transition', 'named', 'raw'):
            form = _form(mode=mode, cue=cue, named='dis_master', raw=CUE)
            parse_rule(build_rule(form, 'x'))      # 全组合都得过


# ----------------------------------------------------------------------
#  反解：打开文件不能"一保存就变样"
# ----------------------------------------------------------------------

@pytest.mark.parametrize('kw', [
    {},
    {'mode': 'sprite_show'},
    {'mode': 'say_line'},
    {'mode': 'every_beat'},
    {'cue': 'full_transition'},
    {'cue': 'full_transition', 'mode': 'say_line'},
    {'cue': 'named', 'named': 'dis_master'},
    {'cue': 'raw', 'raw': CUE},
    {'always': True},
    {'effect': 'Fade', 'seconds': '0.5'},
    {'layer': 'screens'},
    {'skip_existing_transition': False},
    {'skip_if_present': False},
])
def test_roundtrip_build_read_build_is_identical(kw):
    first = build_rule(_form(**kw), 'r1')
    form, why = read_form(first)
    assert why == ''
    assert form is not None
    # 名字由调用方给（表单里的 id），其余字段必须一模一样
    assert build_rule(form, 'r1') == first


def test_read_form_refuses_replace_and_delete_instead_of_guessing():
    rep = {'id': 'r', 'when': {}, 'replace': {'scope': 'beat',
                                              'match': {'mode': 'exact',
                                                        'pattern': 'x'},
                                              'text': 'y'}}
    form, why = read_form(rep)
    assert form is None and '替换' in why
    dele = {'id': 'r', 'when': {}, 'delete': {'scope': 'beat',
                                              'match': {'mode': 'exact',
                                                        'pattern': 'x'}}}
    form, why = read_form(dele)
    assert form is None and '删除' in why


def test_read_form_refuses_rules_with_chapter_or_speaker_filters():
    rule = {'id': 'r', 'when': {'label': 'ch01*'},
            'insert': {'placement': 'after_last_show', 'text': CUE}}
    form, why = read_form(rule)
    assert form is None and 'label' in why


def test_read_form_keeps_an_unrecognised_line_verbatim():
    text = '$ my_wipe("left", 0.6)'
    rule = {'id': 'r', 'when': {'change': 'sprite'},
            'insert': {'placement': 'after_last_change', 'text': text}}
    form, why = read_form(rule)
    assert form is not None and why == ''
    assert form['cue'] == 'raw'
    assert build_rule(form, 'r')['insert']['text'] == text


# ----------------------------------------------------------------------
#  读 / 写文件
# ----------------------------------------------------------------------

def _file(tmp_path, rules, name='storygraph.batch.json'):
    path = tmp_path / name
    path.write_text(json.dumps({'schema': 'storygraph.batch/1', 'rules': rules},
                               ensure_ascii=False), encoding='utf-8')
    return path


HANDWRITTEN = {'id': 'old', 'when': {'code_contains': 'debug'},
               'delete': {'scope': 'beat',
                          'match': {'mode': 'contains', 'pattern': 'debug'}}}


def test_handwritten_rules_survive_a_save(tmp_path):
    rule = build_rule(_form(), 'r1')
    path = _file(tmp_path, [HANDWRITTEN, rule])
    items = load_rules(path)
    assert [i['form'] is not None for i in items] == [False, True]
    saved = json.loads(dump_rules(items))
    assert saved['rules'][0] == HANDWRITTEN          # 一个字都没动
    assert saved['rules'][1]['id'] == 'r1'


def test_editing_the_visible_rule_does_not_touch_the_handwritten_one(tmp_path):
    path = _file(tmp_path, [HANDWRITTEN, build_rule(_form(), 'r1')])
    items = load_rules(path)
    items[1]['form']['seconds'] = '0.4'
    saved = json.loads(dump_rules(items))
    assert saved['rules'][1]['insert']['text'] == \
        '$ renpy.transition(Dissolve(0.4), layer="master")'
    assert saved['rules'][0] == HANDWRITTEN


def test_save_is_stable(tmp_path):
    path = _file(tmp_path, [build_rule(_form(), 'r1')])
    items = load_rules(path)
    a = dump_rules(items)
    save_rules(path, items)
    again = load_rules(path)
    assert dump_rules(again) == a
    save_rules(path, again)
    assert path.read_text(encoding='utf-8') == a


def test_save_is_atomic_and_keeps_a_backup(tmp_path):
    path = _file(tmp_path, [build_rule(_form(), 'r1')])
    items = load_rules(path)
    items[0]['form']['seconds'] = '0.3'
    save_rules(path, items)
    assert '0.3' in path.read_text(encoding='utf-8')
    assert list(tmp_path.glob('storygraph.batch.json.bak*')), '没有留备份'


def test_duplicate_ids_are_refused_on_save(tmp_path):
    path = _file(tmp_path, [build_rule(_form(), 'same'), build_rule(_form(), 'same')])
    with pytest.raises(BatchRuleError):
        load_rules(path)


def test_next_id_skips_taken_names(tmp_path):
    items = [{'raw': {'id': '规则1'}, 'form': _form(id='规则1')}]
    assert next_id(items) == '规则2'


def test_describe_speaks_human():
    text = describe(build_rule(_form(), 'r1'))
    assert '立绘变化' in text and '每一条立绘变化后面' in text
    assert 'renpy.transition' in text
    last = describe(build_rule(_form(mode='sprite_change_last'), 'r2'))
    assert '最后一个立绘变化后面' in last
