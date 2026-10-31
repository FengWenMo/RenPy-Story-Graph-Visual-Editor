# -*- coding: utf-8 -*-
"""
tests/test_mapping_edit.py
==========================
**映射的"表格式"编辑模型**（用户 2026-10-30，⑧）

用户原话：

> json的自定义能不能用工具显式化……？本质上它就是键值对嘛！也就是类似于之前
> 探测未定义角色名称的样子，弄一个显化的UI，然后点击保存的话再存为json……？

这一片钉住的是"界面背后的那张表"——纯逻辑、不碰 Qt：

  · 六段（角色 / 图片 / 规则 / 服装代号 / 表情编号 / 属性名）都按**行**读得出来；
  · 改一格只改那一个 JSON 键；清空 = 删掉这个键（不留 `""`）；
  · `tag` / `attrs` 这些**旧写法**就地改，不另加一个键（否则一条里两个
    意思冲突，谁赢看读取顺序）；
  · 不认识的顶层键 / 条目字段**原样保留**（`to_data()` 的 diff 只该有
    人真改的那几行）；
  · 点「新增一行」的空壳不落盘，但人改过键的空行不丢；
  · 问题按行报（red = 这条不会生效，amber = 可能是笔误），**不拦保存**
    —— 与 `asset_mapping.validate_mapping` 同一个口径。
"""

from __future__ import annotations

import json
import re

import pytest

from storygraph.assets import mapping_edit as ME


def _mapping() -> dict:
    return {
        'schema': 'storygraph.mapping/1',
        'name': '测试项目 · 资源映射',
        'characters': {'ling': {'display': '凌跃捷', 'aliases': ['凌', 'lyj']}},
        'rules': [
            {'glob': 'game/art/full/<tag>/*.png', 'kind': 'sprite',
             'char': '<tag>', 'attr': '<stem>'},
            {'glob': 'game/images/bg/*.png', 'kind': 'bg'},
        ],
        'images': {'cg_01': {'kind': 'cg'}},
        'attributes': {'expressions': {'label': '表情'}},
    }


# ==================================================================
#  ① 六段都读得出来（界面靠这个摆表）
# ==================================================================

class TestRows:
    def test_every_section_has_its_own_columns(self):
        labels = {name: [f.label for f in ME.SPECS[name].fields]
                  for name in ME.SECTIONS}
        assert labels['characters'] == ['名字', '别的写法', '头像图']
        assert labels['images'] == ['它是哪种图', '属于谁', '表情／状态']
        assert labels['rules'] == ['文件在哪儿', '它是哪种图', '属于谁', '表情／状态']
        assert labels['attributes'] == ['界面上叫它']

    def test_no_jargon_on_screen(self):
        """
        屏幕上**不许出现工具自己的行话**（用户 2026-10-30：

        > tag 一类的……谁知道你是怎么叫的呢……？？？我需要一个编剧跟文案
        > 都能看明白的表！

        ）：表头 / 左栏 / 那句说明里出现 `tag` `ID` `glob` `kind` `attr`
        `键` `值` `通配` `维度` `schema` 这几个词就红。机器那边叫什么，只能
        出现在 `tech`（tooltip 那一行）里。
        """
        banned = ('tag', 'id', 'glob', 'kind', 'attr', 'schema',
                  '键', '值', '通配', '维度', '资产', '精确覆盖')
        seen = []
        for name in ME.SECTIONS:
            spec = ME.SPECS[name]
            for text in (spec.label, spec.key_label, spec.hint, spec.empty):
                seen.append((name, '段', text))
            for field in spec.fields:
                seen.extend([(name, field.label, field.label),
                             (name, field.label, field.hint)])

        def _plain(text):
            # `<tag>` / `<stem>` 这种尖括号是**要照抄的写法**（不是行话），
            # 而且只出现在「一类图片」这种进阶段里 —— 扫之前先摘掉。
            return re.sub(r'<[^>]*>', '', str(text)).lower()

        bad = [(name, where, text) for name, where, text in seen
               if any(word in _plain(text) for word in banned)]
        assert not bad, bad

    def test_every_section_explains_how_to_start_when_empty(self):
        """空表格是最容易劝退人的时候：每段都得有一句"怎么开始"。"""
        for name in ME.SECTIONS:
            assert ME.SPECS[name].empty.strip(), f'{name} 没有空表说明'

    def test_counts_and_rows_follow_the_file(self):
        edit = ME.MappingEdit(_mapping())
        assert edit.counts() == {'characters': 1, 'images': 1, 'rules': 2,
                                 'outfit_codes': 0, 'expression_codes': 0,
                                 'vocabulary': 0, 'attributes': 1}
        rows = edit.rows('rules')
        assert [r.values[0] for r in rows] == ['game/art/full/<tag>/*.png',
                                               'game/images/bg/*.png']
        # 规则没有"键"：第一列是空的，界面自己编号
        assert [r.key for r in rows] == ['', '']

    def test_an_empty_mapping_has_no_rows(self):
        edit = ME.MappingEdit({})
        assert edit.counts() == {name: 0 for name in ME.SECTIONS}
        assert edit.problems() == []
        assert edit.to_data() == {}

    def test_a_broken_entry_is_reported_not_guessed(self):
        edit = ME.MappingEdit({'images': {'x': 'cg'}})
        row = edit.rows('images')[0]
        assert row.broken is True
        assert row.key == 'x'
        problems = edit.problems()
        assert len(problems) == 1
        assert problems[0].level == 'bad'
        assert '不是对象' in problems[0].text
        # 原样留着，不替人改
        assert edit.to_data()['images'] == {'x': 'cg'}

    def test_a_wrong_section_type_disables_only_that_section(self):
        edit = ME.MappingEdit({'images': [], 'rules': [{'glob': 'a/*.png'}]})
        assert edit.editable('images') is False
        assert edit.editable('rules') is True
        assert edit.set_field('images', 0, 'kind', 'cg') != ''
        problems = [p for p in edit.problems() if p.section == 'images']
        assert problems and problems[0].level == 'bad'

    def test_unknown_fields_are_kept_and_listed(self):
        data = _mapping()
        data['images']['cg_01']['notes'] = '这张是结局'
        edit = ME.MappingEdit(data)
        assert edit.rows('images')[0].unknown == ('notes',)
        assert edit.to_data() == data


# ==================================================================
#  ② 改一格
# ==================================================================

class TestEditing:
    def test_set_field_writes_the_value(self):
        edit = ME.MappingEdit(_mapping())
        assert edit.set_field('images', 0, 'char', 'ling') == ''
        assert edit.to_data()['images']['cg_01'] == {'kind': 'cg',
                                                     'char': 'ling'}

    def test_clearing_a_field_removes_the_key(self):
        edit = ME.MappingEdit(_mapping())
        edit.set_field('images', 0, 'kind', '   ')
        assert edit.to_data()['images']['cg_01'] == {}

    def test_a_list_field_is_written_as_an_array(self):
        edit = ME.MappingEdit(_mapping())
        edit.set_field('characters', 0, 'aliases', '凌 | LTJ, 小凌')
        assert edit.to_data()['characters']['ling']['aliases'] == [
            '凌', 'LTJ', '小凌']

    def test_list_separators_accept_chinese_punctuation(self):
        assert ME.split_list('凌、lyj；阿凌') == ['凌', 'lyj', '阿凌']
        assert ME.split_list('a,b，c；d') == ['a', 'b', 'c', 'd']

    def test_old_key_names_are_updated_in_place(self):
        data = {'rules': [{'glob': 'a/<tag>/*.png', 'tag': '<tag>',
                           'attrs': '<stem>'}]}
        edit = ME.MappingEdit(data)
        edit.set_field('rules', 0, 'char', 'ling')
        edit.set_field('rules', 0, 'attr', 'happy')
        entry = edit.to_data()['rules'][0]
        assert entry['tag'] == 'ling' and 'char' not in entry
        assert entry['attrs'] == 'happy' and 'attr' not in entry

    def test_renaming_a_key_keeps_the_order(self):
        data = {'characters': {'a': {'display': 'A'}, 'b': {'display': 'B'}}}
        edit = ME.MappingEdit(data)
        assert edit.set_key('characters', 0, 'alpha') == ''
        assert list(edit.to_data()['characters']) == ['alpha', 'b']

    def test_renaming_rejects_an_empty_or_duplicate_key(self):
        edit = ME.MappingEdit(_mapping())
        assert '不能为空' in edit.set_key('characters', 0, '  ')
        edit.add('characters', 'other')
        assert '已经有' in edit.set_key('characters', 0, 'other')
        # 失败不动数据
        assert list(edit.to_data()['characters'])[0] == 'ling'

    def test_rules_can_be_reordered(self):
        edit = ME.MappingEdit(_mapping())
        assert edit.move('rules', 1, -1) == 0
        assert [r.values[0] for r in edit.rows('rules')] == [
            'game/images/bg/*.png', 'game/art/full/<tag>/*.png']

    def test_a_new_row_gets_a_unique_default_key(self):
        edit = ME.MappingEdit({})
        first = edit.add('characters')
        second = edit.add('characters')
        keys = list(edit.to_data()['characters']) if edit.to_data() else []
        assert first == 0 and second == 1
        assert edit.rows('characters')[0].key == '新角色'
        assert edit.rows('characters')[1].key == '新角色2'

    def test_adding_an_existing_key_merges_instead_of_duplicating(self):
        edit = ME.MappingEdit(_mapping())
        index = edit.add('images', 'cg_01', {'char': 'ling'})
        assert index == 0
        assert len(edit.rows('images')) == 1
        assert edit.to_data()['images']['cg_01'] == {'kind': 'cg',
                                                     'char': 'ling'}

    def test_removing_rows_works_for_dicts_and_lists(self):
        edit = ME.MappingEdit(_mapping())
        assert edit.remove('rules', [0]) == 1
        assert [r.values[0] for r in edit.rows('rules')] == [
            'game/images/bg/*.png']
        assert edit.remove('images', [0]) == 1
        assert edit.rows('images') == []


# ==================================================================
#  ③ 写出去的东西（空壳 / 未知键 / schema）
# ==================================================================

class TestWhatGetsWritten:
    def test_untouched_mapping_round_trips_exactly(self):
        data = _mapping()
        assert ME.MappingEdit(data).to_data() == data

    def test_blank_auto_rows_are_not_written(self):
        edit = ME.MappingEdit(_mapping())
        edit.add('images')
        assert edit.blank_rows() == 1
        assert '新图片' not in json.dumps(edit.to_data(), ensure_ascii=False)
        assert edit.to_data() == _mapping()

    def test_a_typed_key_without_fields_is_kept(self):
        edit = ME.MappingEdit(_mapping())
        index = edit.add('images')
        edit.set_key('images', index, 'ling_happy')
        assert edit.blank_rows() == 0
        assert edit.to_data()['images']['ling_happy'] == {}

    def test_unknown_top_level_keys_survive(self):
        data = _mapping()
        data['notes'] = {'作者': '某人'}
        data['未来版本'] = [1, 2]
        edit = ME.MappingEdit(data)
        edit.set_field('images', 0, 'char', 'ling')
        out = edit.to_data()
        assert out['notes'] == {'作者': '某人'}
        assert out['未来版本'] == [1, 2]

    def test_deleting_every_rule_drops_the_array(self):
        edit = ME.MappingEdit(_mapping())
        edit.remove('rules', [0, 1])
        assert 'rules' not in edit.to_data()

    def test_an_originally_empty_section_is_kept(self):
        data = _mapping()
        data['images'] = {}
        edit = ME.MappingEdit(data)
        assert edit.to_data()['images'] == {}

    def test_schema_is_added_only_when_there_is_content(self):
        assert ME.MappingEdit({}).to_data() == {}
        out = ME.MappingEdit({'images': {'a': {'kind': 'cg'}}}).to_data()
        assert list(out)[0] == 'schema'
        assert out['schema'] == ME.MAPPING_SCHEMA

    def test_json_text_of_the_result_is_readable(self):
        text = json.dumps(ME.MappingEdit(_mapping()).to_data(),
                          ensure_ascii=False, indent=2)
        assert '凌跃捷' in text and '\\u' not in text


# ==================================================================
#  ④ 问题（红字 / 黄字）—— 提示但不拦保存
# ==================================================================

class TestProblems:
    def test_an_unknown_kind_is_bad(self):
        data = _mapping()
        data['images']['cg_01']['kind'] = 'cgg'
        problems = ME.MappingEdit(data).problems()
        assert any(p.level == 'bad' and '不是认识的类别' in p.text
                   for p in problems)

    def test_a_rule_without_a_path_is_bad(self):
        data = {'rules': [{'kind': 'sprite'}]}
        problems = ME.MappingEdit(data).problems()
        assert any(p.level == 'bad' and '没有路径' in p.text for p in problems)

    def test_two_rules_with_the_same_path_are_flagged(self):
        data = {'rules': [{'glob': 'a/*.png', 'kind': 'bg'},
                          {'glob': 'a/*.png', 'kind': 'cg'}]}
        problems = ME.MappingEdit(data).problems()
        assert any('永远不会生效' in p.text for p in problems)
        assert problems[0].level == 'warn'

    def test_an_absolute_glob_is_flagged(self):
        data = {'rules': [{'glob': 'D:/game/a/*.png', 'kind': 'bg'}]}
        problems = ME.MappingEdit(data).problems()
        assert any('绝对路径' in p.text for p in problems)

    def test_a_backslash_key_is_flagged(self):
        data = {'images': {'game\\art\\a.png': {'kind': 'cg'}}}
        problems = ME.MappingEdit(data).problems()
        assert any('反斜杠' in p.text for p in problems)

    def test_a_sprite_without_a_character_is_flagged(self):
        data = {'images': {'a': {'kind': 'sprite'}}}
        problems = ME.MappingEdit(data).problems()
        assert any('立绘没写' in p.text for p in problems)

    def test_the_default_key_is_flagged_until_it_is_renamed(self):
        edit = ME.MappingEdit({})
        edit.add('characters')
        edit.set_field('characters', 0, 'display', '某人')
        assert any('还是默认的' in p.text for p in edit.problems())
        edit.set_key('characters', 0, 'someone')
        assert not any('还是默认的' in p.text for p in edit.problems())

    def test_blank_rows_are_not_problems(self):
        edit = ME.MappingEdit(_mapping())
        edit.add('images')
        assert edit.problems() == []

    def test_a_problem_points_at_its_row(self):
        data = _mapping()
        data['images']['cg_01']['kind'] = 'nope'
        problem = ME.MappingEdit(data).problems()[0]
        assert problem.section == 'images' and problem.index == 0


# ==================================================================
#  ⑤ 改角色键之后：只报引用，不替人改
# ==================================================================

class TestReferences:
    def test_count_references_looks_at_images_and_rules(self):
        data = _mapping()
        data['images']['ling_happy'] = {'kind': 'sprite', 'char': 'ling'}
        data['rules'].append({'glob': 'game/x/*.png', 'kind': 'avatar',
                              'char': 'ling'})
        edit = ME.MappingEdit(data)
        # 新图片 + 新规则 = 2（原来那条规则的 char 是占位符 `<tag>`，不算）
        assert edit.count_references('characters', 'ling') == 2
        assert edit.count_references('characters', 'nobody') == 0
        assert edit.count_references('images', 'ling') == 0

    def test_renaming_a_character_does_not_rewrite_references(self):
        data = _mapping()
        edit = ME.MappingEdit(data)
        edit.set_key('characters', 0, 'ling2')
        out = edit.to_data()
        assert out['rules'][0]['char'] == '<tag>'      # 没被替人改
        assert 'ling2' in out['characters']


# ==================================================================
#  ⑥ 和读取方对得上（写出去的还能被 asset_mapping 读）
# ==================================================================

class TestReadBack:
    def test_the_edited_mapping_still_resolves(self, tmp_path):
        from storygraph.assets import asset_mapping as AM

        data = _mapping()
        data['images']['game/art/full/ling/happy.png'] = {
            'kind': 'sprite', 'char': 'ling', 'attr': 'happy'}
        edit = ME.MappingEdit(data)
        edit.set_field('images', 1, 'char', 'ling')
        path = tmp_path / 'storygraph.mapping.json'
        AM.save_mapping(str(path), edit.to_data())
        loaded, warnings = AM.load_mapping(str(path))
        assert warnings == []
        ident = AM.resolve_with_mapping(
            loaded, relpath='game/art/full/ling/happy.png')
        assert ident is not None
        assert (ident.kind, ident.tag, ident.attrs) == ('sprite', 'ling',
                                                        ('happy',))

    def test_problems_only_warn_on_a_file_that_loads(self, tmp_path):
        from storygraph.assets import asset_mapping as AM

        data = _mapping()
        data['images']['cg_01']['kind'] = 'nope'
        path = tmp_path / 'storygraph.mapping.json'
        AM.save_mapping(str(path), ME.MappingEdit(data).to_data())
        _, warnings = AM.load_mapping(str(path))
        assert any('kind 不认识' in w for w in warnings)


@pytest.mark.parametrize('raw,expected', [
    ('凌|lyj', ['凌', 'lyj']),
    ('凌、lyj', ['凌', 'lyj']),
    ('凌，lyj', ['凌', 'lyj']),
    (['凌', 'lyj'], ['凌', 'lyj']),
    ('', []),
])
def test_split_list_accepts_every_separator_the_project_uses(raw, expected):
    assert ME.split_list(raw) == expected
