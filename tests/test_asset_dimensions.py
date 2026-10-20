"""
判据 · **资产维度结构化**（2026-10-19，用户排期第 1 项）
=======================================================

用户原话：

> UI 已经"一维一格"了，但表里还是 Attributes 装整串文件名，靠拆词。
> 做到结构化，立绘卡、下拉、校验、多引擎全都能受益。

这里钉住七件事：

  ① `Attributes` 不再被无脑拆词（`base_handdown` 是一个属性，不是三个）；
  ② 引擎事实 / `layeredimage` 能把属性归到维度（`outfit` / `expressions`）；
  ③ 扫描时只剥**能确认**的 tag 前缀（`eileen_happy` → `happy`，
     `lan_angry` 整体已知就保留）；
  ④ `Group`/`Value` 回填只填空白，老列与人的值一个字节不动；
  ⑤ `Symbols` 能直接给出维度，立绘卡的第四档结构来源可用；
  ⑥ 校验能报"同一维度选了两个"（W115）—— 引擎 lint 不管这类；
  ⑦ 多引擎 IR 的资产清单带 `{维度: 值}`，不是一串裸属性。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl                                            # noqa: E402

from storygraph.assets.asset_dimensions import (            # noqa: E402
    DIMENSION_CONFIDENCE, attributes_conflict, dimensions_of_row,
    enrich_image_rows, explain_dimensions, format_dimensions, group_metadata,
    row_values, split_values)
from storygraph.assets.asset_registry import (              # noqa: E402
    AssetRegistry, backfill_dimension_columns, read_excel_rows, scan_assets,
    write_asset_excel)
from storygraph.core import engine_facts as EF              # noqa: E402


def _facts(tag='ling'):
    attrs = {
        tag: [
            {'group': 'outfit', 'attribute': 'base_swimsuit'},
            {'group': 'outfit', 'attribute': 'base_arms_akimbo'},
            {'group': 'expressions', 'attribute': 'happy'},
            {'group': 'expressions', 'attribute': 'sad'},
            {'group': 'expressions', 'attribute': 'close_happy'},
            # 内部件：引擎认它，但它不属于任何展示维度
            {'group': '', 'attribute': 'base_swimsuit_mask'},
        ],
    }
    cands = {tag: ['base_swimsuit', 'base_arms_akimbo', 'happy', 'sad',
                   'close_happy', 'base_swimsuit_mask']}
    return EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, cands)],
    }, collection={'parse_clean': True, 'probe_complete': True})


class TestSplitValues:
    def test_underscore_is_part_of_the_name(self):
        assert split_values('base_handdown happy') == ['base_handdown', 'happy']
        assert split_values('base_arms_akimbo_close_happy') == \
            ['base_arms_akimbo_close_happy']

    def test_commas_and_pipes_are_separators(self):
        assert split_values('happy, sad|angry、shy；calm') == \
            ['happy', 'sad', 'angry', 'shy', 'calm']


class TestDimensionsOfRow:
    def test_facts_group_the_values(self):
        dims = dimensions_of_row(
            {'Tag': 'ling', 'Attributes': 'base_swimsuit happy'},
            facts=_facts())
        assert dims == [{'group': 'outfit', 'value': 'base_swimsuit'},
                        {'group': 'expressions', 'value': 'happy'}], dims

    def test_unknown_value_keeps_empty_group_not_guessed(self):
        dims = dimensions_of_row(
            {'Tag': 'ling', 'Attributes': 'base_swimsuit_mask'},
            facts=_facts())
        assert dims == [{'group': '', 'value': 'base_swimsuit_mask'}]

    def test_explicit_value_is_never_rewritten(self):
        """人写进 `Value` 的就是最终值：不再去猜 tag 前缀。"""
        dims = dimensions_of_row(
            {'Tag': 'ling', 'Value': 'ling_happy', 'Group': 'expressions'},
            facts=_facts())
        assert dims == [{'group': 'expressions', 'value': 'ling_happy'}]

    def test_known_full_name_wins_over_prefix_stripping(self):
        """
        `lan_angry` 在引擎里就是一个属性（内部件）；tag 叫 `lan` 也不能
        把它拆成 `angry` —— 那是另一个表情。
        """
        facts = EF.build_facts({
            'attributes': [(EF.SRC_PROBE, {'lan': [
                {'group': 'expressions', 'attribute': 'angry'},
                {'group': 'expressions', 'attribute': 'lan_angry'},
            ]})],
            'candidates': [(EF.SRC_PROBE, {'lan': ['angry', 'lan_angry']})],
        }, collection={'parse_clean': True, 'probe_complete': True})
        dims = dimensions_of_row({'Tag': 'lan', 'Attributes': 'lan_angry'},
                                 facts=facts)
        assert dims == [{'group': 'expressions', 'value': 'lan_angry'}], dims

    def test_nested_layout_never_strips_the_tag_prefix(self):
        """
        嵌套布局 `.../lan/dynamic_images/lan_angry.png`：文件名就是属性名，
        **不能**剥成 `angry`。平铺布局才剥（见上一条 / 扫描用例）。
        """
        facts = EF.build_facts({
            'attributes': [(EF.SRC_PROBE, {'lan': [
                {'group': 'expressions', 'attribute': 'angry'},
            ]})],
            'candidates': [(EF.SRC_PROBE, {'lan': ['angry']})],
        }, collection={'parse_clean': True, 'probe_complete': True})
        dims = dimensions_of_row({
            'Tag': 'lan', 'Attributes': 'lan_angry',
            'Path': 'game/images/characters/lan/dynamic_images/lan_angry.png',
        }, facts=facts)
        assert dims == [{'group': '', 'value': 'lan_angry'}], dims


class TestEnrichRows:
    def test_fills_blank_group_and_value_only(self):
        rows = [
            {'ID': 'ling_base_swimsuit', 'Tag': 'ling',
             'Attributes': 'base_swimsuit'},
            {'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
             'Group': '手写的维度', 'Value': 'happy2'},
        ]
        assert enrich_image_rows(rows, facts=_facts(),
                                 load_project_context=False)
        assert rows[0]['Group'] == 'outfit'
        assert rows[0]['Value'] == 'base_swimsuit'
        # 人写过的值不动
        assert rows[1]['Group'] == '手写的维度'
        assert rows[1]['Value'] == 'happy2'
        assert rows[0]['_dims'] == [{'group': 'outfit',
                                     'value': 'base_swimsuit'}]

    def test_multi_value_row_does_not_get_a_single_value(self):
        row = {'ID': 'ling', 'Tag': 'ling', 'Attributes': 'happy sad',
               'Path': ''}
        enrich_image_rows([row], facts=_facts(), load_project_context=False)
        assert not row.get('Value'), row
        assert row['_dims'] == [{'group': 'expressions', 'value': 'happy'},
                                {'group': 'expressions', 'value': 'sad'}]


class TestScanUsesDimensions:
    def test_flat_file_name_loses_the_tag_prefix(self, tmp_path):
        images = tmp_path / 'game' / 'images'
        images.mkdir(parents=True)
        (images / 'eileen_happy.png').write_bytes(b'png')
        facts = EF.build_facts({
            'attributes': [(EF.SRC_PROBE, {'eileen': [
                {'group': 'expressions', 'attribute': 'happy'},
            ]})],
            'candidates': [(EF.SRC_PROBE, {'eileen': ['happy']})],
        }, collection={'parse_clean': True, 'probe_complete': True})
        assets = scan_assets(tmp_path, include_ui=False, facts=facts)
        row = next(a for a in assets['images'] if a['ID'] == 'eileen_happy')
        assert row['Tag'] == 'eileen'
        assert row['Attributes'] == 'happy', row
        assert row['Value'] == 'happy'
        assert row['Group'] == 'expressions'

    def test_without_facts_the_stem_is_kept_intact(self, tmp_path):
        images = tmp_path / 'game' / 'images'
        images.mkdir(parents=True)
        (images / 'ling_base_handdown.png').write_bytes(b'png')
        assets = scan_assets(tmp_path, include_ui=False)
        row = next(a for a in assets['images']
                   if a['ID'] == 'ling_base_handdown')
        assert row['Attributes'] == 'ling_base_handdown'
        assert row['Value'] == 'ling_base_handdown'
        assert row['Group'] == ''

    def test_nested_layout_keeps_the_full_stem(self, tmp_path):
        images = tmp_path / 'game' / 'images' / 'characters' / 'lan' / \
            'dynamic_images'
        images.mkdir(parents=True)
        (images / 'lan_angry.png').write_bytes(b'png')
        facts = EF.build_facts({
            'attributes': [(EF.SRC_PROBE, {'lan': [
                {'group': 'expressions', 'attribute': 'angry'},
            ]})],
            'candidates': [(EF.SRC_PROBE, {'lan': ['angry']})],
        }, collection={'parse_clean': True, 'probe_complete': True})
        assets = scan_assets(tmp_path, include_ui=False, facts=facts)
        row = next(a for a in assets['images']
                   if str(a.get('Path') or '').endswith('lan_angry.png'))
        assert row['Tag'] == 'lan'
        assert row['Attributes'] == 'lan_angry', row
        assert row['Value'] == 'lan_angry'
        assert row['Group'] == ''


class TestBackfill:
    def test_adds_columns_and_fills_blanks(self, tmp_path):
        d = tmp_path / 'asset_tables'
        d.mkdir(parents=True)
        p = d / 'images.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])
        ws.append(['ling_happy', 'p.png', 'ling', 'happy', 'character',
                   '人工备注'])
        wb.save(p)
        rows = [{'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
                 'Notes': '人工备注'}]
        enrich_image_rows(rows, facts=_facts(), load_project_context=False)
        res = backfill_dimension_columns(d, rows)
        assert res['ok'] and res['saved'], res
        assert set(res['columns_added']) == {'Group', 'Value'}
        back = {r['ID']: r for r in read_excel_rows(p)}
        assert back['ling_happy']['Group'] == 'expressions'
        assert back['ling_happy']['Value'] == 'happy'
        assert back['ling_happy']['Notes'] == '人工备注'
        assert list(back['ling_happy'])[:6] == [
            'ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'], \
            '老列位置不许动'
        assert list(d.glob('images.bak*.xlsx')), '写前必须备份'

    def test_does_not_overwrite_hand_written_values(self, tmp_path):
        d = tmp_path / 'asset_tables'
        d.mkdir(parents=True)
        p = d / 'images.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes',
                   'Group', 'Value'])
        ws.append(['ling_happy', 'p.png', 'ling', 'happy', 'character', '',
                   '我写的', 'my_value'])
        wb.save(p)
        rows = [{'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy'}]
        enrich_image_rows(rows, facts=_facts(), load_project_context=False)
        backfill_dimension_columns(d, rows)
        back = {r['ID']: r for r in read_excel_rows(p)}
        assert back['ling_happy']['Group'] == '我写的'
        assert back['ling_happy']['Value'] == 'my_value'


class TestSymbolsAndCards:
    def _registry(self):
        reg = AssetRegistry()
        reg.images = {
            'ling_base_swimsuit': {
                'ID': 'ling_base_swimsuit', 'Tag': 'ling',
                'Attributes': 'base_swimsuit', 'Group': 'outfit',
                'Value': 'base_swimsuit', 'Category': 'character',
                'Path': ''},
            'ling_happy': {
                'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy',
                'Group': 'expressions', 'Value': 'happy',
                'Category': 'character', 'Path': ''},
            'ling_sad': {
                'ID': 'ling_sad', 'Tag': 'ling', 'Attributes': 'sad',
                'Group': 'expressions', 'Value': 'sad',
                'Category': 'character', 'Path': ''},
        }
        enrich_image_rows(reg.images, facts=_facts(),
                          load_project_context=False)
        return reg

    def test_symbols_expose_dimensions(self):
        from storygraph.story.beat_render import make_symbols
        sym = make_symbols(self._registry())
        assert sym.dimension_groups_of_tag('ling') == {
            'outfit': ['base_swimsuit'],
            'expressions': ['happy', 'sad'],
        }
        assert sym.same_group_conflicts('ling', ['happy', 'sad']) == [
            {'group': 'expressions', 'attrs': ['happy', 'sad']}]

    def test_card_slots_use_table_dimensions_without_engine_facts(self):
        from storygraph.app.beats import attr_slot_rows
        from storygraph.story.beat_render import make_symbols
        sym = make_symbols(self._registry())
        rows = attr_slot_rows('ling base_swimsuit happy', sym)
        assert len(rows) == 2, rows
        values = [r['value'] for r in rows]
        assert 'base_swimsuit' in values and 'happy' in values, values


    def test_dropdown_groups_use_table_dimensions_without_declaration(self):
        from storygraph.app.beats import expression_groups_for
        from storygraph.story.beat_render import make_symbols
        sym = make_symbols(self._registry())
        groups = expression_groups_for('ling', sym, groups=[], facts={})
        names = {g['group'] for g in groups}
        assert names == {'outfit', 'expressions'}, groups
        assert {v for g in groups for v in g['values']} == {
            'base_swimsuit', 'happy', 'sad'}


class TestTableColumns:
    def test_new_tables_write_group_and_value_last(self, tmp_path):
        from storygraph.assets.asset_registry import write_asset_excel
        write_asset_excel({'images': [{
            'ID': 'ling_happy', 'Path': 'p.png', 'Tag': 'ling',
            'Attributes': 'happy', 'Category': 'character', 'Notes': '',
            'Group': 'expressions', 'Value': 'happy'}],
            'audio': {'music': [], 'sound': []}, 'voices': []}, tmp_path)
        rows = read_excel_rows(tmp_path / 'images.xlsx')
        assert rows[0]['Group'] == 'expressions'
        assert rows[0]['Value'] == 'happy'
        keys = list(rows[0])
        assert keys[-2:] == ['Group', 'Value'], keys

    def test_append_never_turns_internal_keys_into_columns(self, tmp_path):
        """
        扫描行带 `_dims` / `_attrs`（运行期字段）。补登时若把它们当新列写，
        单元格会拿到 `[{'group':…}]` → openpyxl 抛
        "Cannot convert ... to Excel"，整次补登静默失败（2026-10-19 实测）。
        """
        from storygraph.assets.asset_registry import (
            append_rows_to_table, write_asset_excel)
        write_asset_excel({'images': [{
            'ID': 'old', 'Path': 'old.png', 'Tag': 'ling',
            'Attributes': 'old', 'Category': 'character', 'Notes': ''}],
            'audio': {'music': [], 'sound': []}, 'voices': []}, tmp_path)
        row = {'ID': 'ling_new', 'Path': 'new.png', 'Tag': 'ling',
               'Attributes': 'new', 'Category': 'character', 'Notes': '',
               'Group': 'expressions', 'Value': 'new',
               '_dims': [{'group': 'expressions', 'value': 'new'}],
               '_attrs': ['new'], '_dim_groups': ['expressions']}
        assert append_rows_to_table(tmp_path, 'images', [row]) == 1
        rows = {r['ID']: r for r in read_excel_rows(tmp_path / 'images.xlsx')}
        assert 'ling_new' in rows, rows.keys()
        assert 'Group' in rows['ling_new'] and 'Value' in rows['ling_new']
        assert all(not str(k).startswith('_') for k in rows['ling_new']), \
            list(rows['ling_new'])


class TestValidationAndExport:
    def _registry(self):
        reg = AssetRegistry()
        reg.images = {'ling_happy': {
            'ID': 'ling_happy', 'Tag': 'ling', 'Attributes': 'happy sad',
            'Group': 'expressions', 'Value': 'happy',
            'Category': 'character', 'Path': ''}}
        enrich_image_rows(reg.images, facts=_facts(),
                          load_project_context=False)
        return reg

    def test_same_group_conflict_is_reported_as_w115(self):
        from storygraph.pipeline.validate import validate_level1
        from storygraph.story.beat_model import Beat
        b = Beat(seq=1, label='a', kind='say', speaker='e', text='x',
                 show='ling happy sad')
        issues = validate_level1([b], registry=self._registry())
        codes = [i.code for i in issues]
        assert 'W115' in codes, [(i.code, i.message) for i in issues]

    def test_manifest_carries_structured_attributes(self):
        from storygraph.pipeline.engine_exporters import _asset_manifest
        reg = self._registry()
        manifest = _asset_manifest(reg)
        entry = next(e for e in manifest['images'] if e['id'] == 'ling_happy')
        # 同组两个值时：第一个进结构化字段，第二个进 `attribute_values`
        # —— 不能静默丢词（W115 会把这条点名）。
        assert entry['attributes'] == {'expressions': 'happy'}, entry
        assert entry['attribute_values'] == ['sad'], entry


def test_format_dimensions_is_readable():
    assert format_dimensions([
        {'group': 'outfit', 'value': 'base_swimsuit'},
        {'group': 'expressions', 'value': 'happy'},
    ]) == 'outfit=base_swimsuit；expressions=happy'


class TestConfidence:
    """
    置信度（2026-10-20，"结构化再细一层"）。

    用户目标里点了"置信度"：**猜出来的维度和表里写死的维度不能长得一样**。
    每一维现在带 `source`（explicit/value/fact/layeredimage/unknown）和
    `confidence`，剥过 tag 前缀的再打折。
    """

    def _meta(self, row, **kw):
        return dimensions_of_row(row, facts=kw.pop('facts', _facts()),
                                 meta=True, **kw)

    def test_explicit_group_is_full_confidence(self):
        row = {'Tag': 'ling', 'Group': 'expressions', 'Value': 'happy',
               'Path': 'images/ling_happy.png'}
        dims = self._meta(row)
        assert dims[0]['group'] == 'expressions'
        assert dims[0]['source'] == 'explicit'
        assert dims[0]['confidence'] == 1.0

    def test_fact_group_is_high_but_not_certain(self):
        dims = self._meta({'Tag': 'ling', 'Attributes': 'happy'})
        assert dims[0]['source'] == 'fact'
        assert dims[0]['confidence'] == DIMENSION_CONFIDENCE['fact'] == 0.9

    def test_layeredimage_group_scores_lower_than_engine_facts(self):
        class _Group:
            name = 'outfit'
            attrs = {'base_swimsuit': 'x'}

        class _Img:
            groups = [_Group()]

        class _Model:
            images = {'ling': _Img()}

        dims = dimensions_of_row({'Tag': 'ling', 'Attributes': 'base_swimsuit'},
                                 facts={}, layer_model=_Model(), meta=True)
        assert dims[0]['group'] == 'outfit'
        assert dims[0]['source'] == 'layeredimage'
        assert dims[0]['confidence'] == 0.8

    def test_value_column_is_more_trusted_than_attributes(self):
        dims = self._meta({'Tag': 'ling', 'Value': 'happy'})
        assert dims[0]['source'] == 'value'
        assert dims[0]['confidence'] == 0.95

    def test_stripping_the_tag_prefix_lowers_confidence(self):
        dims = self._meta({'Tag': 'ling', 'Attributes': 'ling_happy'})
        assert dims[0]['value'] == 'happy' and dims[0]['stripped'] is True
        assert dims[0]['confidence'] == 0.7, '剥前缀是判断，要打折'

    def test_unknown_group_is_low_confidence_and_empty(self):
        dims = self._meta({'Tag': 'ling', 'Attributes': 'mystery_word'})
        assert dims[0]['group'] == ''
        assert dims[0]['source'] == 'unknown'
        assert dims[0]['confidence'] == 0.15

    def test_plain_dimensions_keep_the_old_shape(self):
        """`meta=False` 时返回的还是 `{group, value}` —— 老调用方不受影响。"""
        row = {'Tag': 'ling', 'Attributes': 'happy'}
        assert dimensions_of_row(row, facts=_facts()) == [
            {'group': 'expressions', 'value': 'happy'}]

    def test_enrich_keeps_the_meta_for_downstream(self):
        rows = [{'Tag': 'ling', 'Attributes': 'happy'}]
        enrich_image_rows(rows, facts=_facts(), load_project_context=False)
        assert rows[0]['_dim_meta'][0]['confidence'] == 0.9

    def test_ir_asset_manifest_carries_attribute_meta(self):
        from types import SimpleNamespace

        from storygraph.pipeline.engine_exporters import build_neutral_ir
        from storygraph.story.beat_model import Beat, assign_identity

        reg = SimpleNamespace(
            images={'ling happy': {
                'Path': 'images/ling_happy.png', 'Tag': 'ling',
                'Group': 'expressions', 'Value': 'happy',
                '_dims': [{'group': 'expressions', 'value': 'happy'}],
                '_dim_meta': [{'group': 'expressions', 'value': 'happy',
                               'source': 'explicit', 'confidence': 1.0,
                               'stripped': False}],
            }},
            music={}, sounds={}, voices={}, transforms={}, characters={})
        beats = [Beat(seq=1, label='a', text='hi')]
        assign_identity(beats)
        ir = build_neutral_ir(beats, registry=reg, project='demo')
        asset = ir['assets']['images'][0]
        assert asset['attributes'] == {'expressions': 'happy'}
        meta = asset['attribute_meta'][0]
        assert meta['source'] == 'explicit' and meta['confidence'] == 1.0


class TestGroupMetadata:
    """"分组元数据"：这一组值是怎么来的、能不能信。"""

    def test_explicit_groups_are_certain(self):
        rows = [{'Tag': 'ling', 'Group': 'expressions', 'Value': 'happy',
                 'Path': 'images/ling_happy.png'}]
        meta = group_metadata(rows, facts=_facts())['ling']['expressions']
        assert meta['values'] == ['happy']
        assert meta['sources'] == ['explicit']
        assert meta['confidence'] == 1.0 and meta['guessed'] is False

    def test_stripped_prefix_marks_the_group_as_guessed(self):
        rows = [{'Tag': 'ling', 'Attributes': 'ling_happy'}]
        meta = group_metadata(rows, facts=_facts())['ling']['expressions']
        assert meta['guessed'] is True, '剥过前缀的维度不能当"确定"'
        assert meta['confidence'] == 0.7

    def test_group_confidence_is_the_weakest_member(self):
        rows = [
            {'Tag': 'ling', 'Attributes': 'happy'},           # fact 0.9
            {'Tag': 'ling', 'Attributes': 'ling_sad'},        # 剥前缀 0.7
        ]
        meta = group_metadata(rows, facts=_facts())['ling']['expressions']
        assert meta['values'] == ['happy', 'sad']
        assert meta['confidence'] == 0.7
        assert meta['guessed'] is True
        assert set(meta['sources']) == {'fact'}

    def test_unknown_groups_are_not_reported(self):
        rows = [{'Tag': 'ling', 'Attributes': 'mystery_word'}]
        assert group_metadata(rows, facts=_facts()) == {}


class TestColumnConflictAndExplicitGroup:
    """
    两列打架 + 多值行上的显式 `Group`（2026-10-20 修，P2）。

    ① 从前"`Attributes` 只有一个词"被当成 `Value` 的老兼容**镜像整格跳过**：
       `{Value:'happy', Attributes:'angry'}` 里 `angry` 一个字不提。
       镜像和真值不一致时它就不是镜像了 —— 用户只改老列（或只改新列）时，
       产物会**安静地用另一列**，而模块开头写着"一个都不丢"。

    ② `explain_dimensions` 只在"这一行只有一个值"时才认显式 `Group`：
       `{Group:'outfit', Value:'base_swimsuit', _attrs:['happy']}` 里人写的
       `outfit=base_swimsuit` 整列失效、置信度掉到 0.15（unknown）——
       人写的和猜的混成同一档，恰好抵消了这次加置信度的目的。
    """

    def test_differing_attributes_word_is_kept(self):
        row = {'Tag': 'ling', 'Value': 'happy', 'Attributes': 'angry'}
        assert row_values(row) == ['happy', 'angry'], '老列的那个词被静默丢了'

    def test_same_word_is_still_a_mirror(self):
        row = {'Tag': 'ling', 'Value': 'happy', 'Attributes': 'happy'}
        assert row_values(row) == ['happy']

    def test_conflict_is_detected_only_when_both_columns_disagree(self):
        assert attributes_conflict(
            {'Tag': 'ling', 'Value': 'happy', 'Attributes': 'angry'}) == {
                'value': 'happy', 'attributes': 'angry'}
        assert attributes_conflict({'Tag': 'ling', 'Value': 'happy'}) == {}
        assert attributes_conflict(
            {'Tag': 'ling', 'Value': 'happy', 'Attributes': 'happy sad'}) == {}

    def test_conflict_word_is_labelled_and_priced(self):
        dims = explain_dimensions(
            {'Tag': 'ling', 'Value': 'happy', 'Attributes': 'angry'},
            facts=_facts())
        by_value = {d['value']: d for d in dims}
        assert by_value['happy']['source'] == 'value'
        assert by_value['angry']['source'] == 'conflict'
        assert by_value['angry']['confidence'] == DIMENSION_CONFIDENCE[
            'conflict']

    def test_explicit_group_survives_a_multi_value_row(self):
        row = {'Tag': 'ling', 'Group': 'outfit', 'Value': 'base_swimsuit',
               '_attrs': ['happy']}
        by_value = {d['value']: d for d in explain_dimensions(
            row, facts=_facts())}
        assert by_value['base_swimsuit']['group'] == 'outfit'
        assert by_value['base_swimsuit']['source'] == 'explicit'
        assert by_value['base_swimsuit']['confidence'] == 1.0, (
            '人写的维度被第二个词拖成了"猜的"')
        # 脚本带出来的那个词仍按引擎事实查，**不是**被硬塞进 outfit
        assert by_value['happy']['group'] == 'expressions'
        assert by_value['happy']['source'] == 'fact'

    def test_explicit_group_does_not_swallow_a_word_it_cannot_place(self):
        row = {'Tag': 'ling', 'Group': 'outfit', 'Value': 'base_swimsuit',
               '_attrs': ['mystery']}
        by_value = {d['value']: d for d in explain_dimensions(
            row, facts=_facts())}
        assert by_value['mystery']['group'] == ''
        assert by_value['mystery']['source'] == 'unknown'

    def test_manifest_keeps_both_columns_and_the_conflict(self):
        """进 IR 的资产清单里两个词都在，且冲突被标出来（`attribute_meta`）。"""
        from types import SimpleNamespace

        from storygraph.pipeline.engine_exporters import build_neutral_ir

        row = {'ID': 'ling_happy', 'Tag': 'ling', 'Value': 'happy',
               'Attributes': 'angry', 'Path': 'images/ling_happy.png'}
        enrich_image_rows([row], facts=_facts(), load_project_context=False)
        reg = SimpleNamespace(images={'ling_happy': row}, characters={},
                              music={}, sounds={}, voices={},
                              transforms={})
        entry = build_neutral_ir([], registry=reg)['assets']['images'][0]
        assert entry['attributes'] == {'expressions': 'happy'}
        assert 'angry' in (entry.get('attribute_values') or []), (
            '打架的那个词没进 IR —— 等于静默丢了')
        sources = {m['value']: m['source'] for m in entry['attribute_meta']}
        assert sources['angry'] == 'conflict'
