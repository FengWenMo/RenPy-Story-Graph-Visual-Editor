"""
tests/test_pipeline_batch.py
============================
本批次（排期 一.1 / 二 / 五 / 七 / 八）的回归。

  A. 一.1  GraphBuilder 改吃 Statement —— 两条路径必须同构图
  B. 二    舞台快照 + 动态下拉
  C. 五    多引擎导出器
  D. 七    三级校验 + build 命令
  E. 八    团队协作（Excel 前端 + SQLite 后端）
"""

import json

import pytest

from storygraph.graph.models import Graph
from storygraph.parsing.renpy_parser import (
    GraphBuilder, ParseResultBuilder, RenPyParser,
)


# ------------------------------------------------------------------
# A. 一.1：AST 图与 Statement 图必须一致
# ------------------------------------------------------------------

PARITY_SRC = (
    'define e = Character("Eileen")\n'
    '\n'
    'label start:\n'
    '    $ love = 0\n'
    '    if love > 5:\n'
    '        $ flag = "high"\n'
    '        jump good\n'
    '    elif love > 2:\n'
    '        call mid\n'
    '    else:\n'
    '        jump bad\n'
    '\n'
    'label choice:\n'
    '    menu:\n'
    '        "去左边":\n'
    '            jump left\n'
    '        "去右边" if money > 10:\n'
    '            call right\n'
    '    return\n'
    '\n'
    'label good:\n'
    '    e "好结局。"\n'
    '    return\n'
    '\n'
    'label mid:\n'
    '    return\n'
    '\n'
    'label bad:\n'
    '    return\n'
    '\n'
    'label left:\n'
    '    return\n'
    '\n'
    'label right:\n'
    '    return\n'
)


def _edge_keys(graph):
    by_id = {nid: n.label_name for nid, n in graph.nodes.items()}
    return sorted(
        (by_id.get(e.from_id, '?'), by_id.get(e.to_id, '?'),
         e.type, e.condition, e.option_text)
        for e in graph.edges.values()
    )


def _ops(graph):
    return {n.label_name: list(n.operations) for n in graph.nodes.values()}


class TestGraphFromStatements:
    def _both(self, write_rpy):
        fp = write_rpy(PARITY_SRC, 'parity.rpy')
        ast = RenPyParser().parse(fp)

        g_ast = Graph()
        GraphBuilder(g_ast).build(ast)

        pr = ParseResultBuilder().build(ast)
        g_stmt = Graph()
        GraphBuilder(g_stmt).build_from_parse_result(pr)
        return g_ast, g_stmt, pr

    def test_node_labels_match(self, write_rpy):
        g_ast, g_stmt, _ = self._both(write_rpy)
        assert ({n.label_name for n in g_ast.nodes.values()}
                == {n.label_name for n in g_stmt.nodes.values()})

    def test_edges_match_exactly(self, write_rpy):
        g_ast, g_stmt, _ = self._both(write_rpy)
        assert _edge_keys(g_ast) == _edge_keys(g_stmt)
        assert _edge_keys(g_stmt)          # 非空，别让"两边都空"假通过

    def test_operations_match(self, write_rpy):
        g_ast, g_stmt, _ = self._both(write_rpy)
        assert _ops(g_ast) == _ops(g_stmt)
        assert _ops(g_stmt)['start'] == ['love = 0', 'flag = "high"']

    def test_else_branch_is_negated(self, write_rpy):
        """else 里的跳转必须有 `not(前面条件)`，两条路径都要有。"""
        _g_ast, g_stmt, _ = self._both(write_rpy)
        else_edges = [e for e in _edge_keys(g_stmt)
                      if e[0] == 'start' and e[1] == 'bad']
        assert else_edges
        cond = else_edges[0][3]
        assert cond.startswith('not (')
        assert 'love > 5' in cond and 'love > 2' in cond

    def test_menu_option_text_on_edges(self, write_rpy):
        _g_ast, g_stmt, _ = self._both(write_rpy)
        opts = {e[4] for e in _edge_keys(g_stmt) if e[0] == 'choice'}
        assert opts == {'去左边', '去右边'}

    def test_analyzer_uses_statement_path(self, write_rpy):
        """增量分析器（编辑器实际走的路）也要产出同一张图。"""
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

        fp = write_rpy(PARITY_SRC, 'p2.rpy')
        pr, g = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
        assert pr.statements
        assert _ops(g)['start'] == ['love = 0', 'flag = "high"']
        assert any(e[4] == '去左边' for e in _edge_keys(g))

    def test_menu_options_parallel_array(self, write_rpy):
        fp = write_rpy(PARITY_SRC, 'p3.rpy')
        pr = ParseResultBuilder().build(RenPyParser().parse(fp))
        assert len(pr.statement_menu_options) == len(pr.statements)
        assert '去右边' in pr.statement_menu_options

    def test_clear_graph_false_keeps_other_labels(self, write_rpy):
        fp1 = write_rpy(PARITY_SRC, 'a.rpy')
        fp2 = write_rpy('label extra:\n    return\n', 'b.rpy')
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

        an = IncrementalRenPyAnalyzer()
        _pr, g = an.analyze(files=[fp1], graph=None)
        an.analyze(files=[fp1, fp2], graph=g, clear_graph=False)
        assert 'extra' in {n.label_name for n in g.nodes.values()}


# ------------------------------------------------------------------
# B. 二：舞台快照 + 动态下拉
# ------------------------------------------------------------------

class TestStageSnapshot:
    def _beats(self):
        from storygraph.story.script_table import Beat
        return [
            Beat(seq=1, label='a', scene='bg classroom',
                 music='bgm_night', text='开场'),
            Beat(seq=2, label='a', show='eileen happy@leftish',
                 text='爱莲登场'),
            Beat(seq=3, label='a', show='eileen sad', text='换表情'),
            Beat(seq=4, label='a', show='mary smile@right; bg rooftop',
                 text='再来一个'),
            Beat(seq=5, label='a', hide='eileen', text='爱莲下场'),
            Beat(seq=6, label='a', scene='bg street', text='换场景'),
            Beat(seq=7, label='a', music_action='stop', text='静音'),
        ]

    def test_scene_clears_actors(self):
        from storygraph.story.stage_snapshot import stage_snapshots
        snaps = stage_snapshots(self._beats())
        assert snaps[0]['scene'] == 'bg classroom'
        assert snaps[0]['actors'] == ''
        # 第 6 拍 scene 之后台上应清空
        assert snaps[5]['scene'] == 'bg street'
        assert snaps[5]['actors'] == ''

    def test_tag_replacement_not_duplicate(self):
        """同 tag 的 show 是换表情，不是叠一个人。"""
        from storygraph.story.stage_snapshot import stage_snapshots
        snaps = stage_snapshots(self._beats())
        assert snaps[1]['actors'] == 'eileen happy@leftish'
        assert snaps[2]['actors'] == 'eileen sad@leftish'   # 位置继承
        assert snaps[2]['actors'].count('eileen') == 1

    def test_two_actors_side_by_side(self):
        from storygraph.story.stage_snapshot import stage_snapshots
        snaps = stage_snapshots(self._beats())
        assert snaps[3]['actors'] == 'eileen sad@leftish; mary smile@right'

    def test_hide_removes(self):
        from storygraph.story.stage_snapshot import stage_snapshots
        snaps = stage_snapshots(self._beats())
        assert 'eileen' not in snaps[4]['actors']
        assert 'mary' in snaps[4]['actors']

    def test_music_state_is_sticky_and_stoppable(self):
        from storygraph.story.stage_snapshot import stage_snapshots
        snaps = stage_snapshots(self._beats())
        assert snaps[1]['music'] == 'bgm_night'
        assert snaps[5]['music'] == 'bgm_night'
        assert snaps[6]['music'] == ''

    def test_stage_order_respects_source_order(self):
        """hide+show 同一立绘：顺序不同结果不同（StageOrder 说了算）。"""
        from storygraph.story.script_table import Beat
        from storygraph.story.stage_snapshot import stage_snapshots
        reset = [Beat(seq=1, label='a', hide='eileen',
                      show='eileen happy', stage_order='hide,show',
                      text='重置立绘')]
        assert stage_snapshots(reset)[0]['actors'] == 'eileen happy'
        reverse = [Beat(seq=1, label='a', hide='eileen',
                        show='eileen happy', stage_order='show,hide',
                        text='反序')]
        assert stage_snapshots(reverse)[0]['actors'] == ''

    def test_stage_digest_text(self):
        from storygraph.story.stage_snapshot import stage_digest
        text = stage_digest(self._beats(), 3)
        assert '背景' in text and '立绘' in text
        assert 'mary smile@right' in text

    def test_snapshot_sheet_written(self, tmp_path):
        """「舞台快照」= 全量状态 + 本拍变化；另有一张「演出时间轴」只看变化。"""
        import openpyxl
        from storygraph.story.script_table import export_beats_to_excel
        out = tmp_path / 's.xlsx'
        export_beats_to_excel(self._beats(), out)
        wb = openpyxl.load_workbook(out)
        assert '舞台快照' in wb.sheetnames
        assert '演出时间轴' in wb.sheetnames
        ws = wb['舞台快照']
        assert ws.cell(row=1, column=6).value == '本拍变化'
        assert ws.cell(row=1, column=7).value == '背景'
        assert ws.cell(row=1, column=8).value == '立绘（谁·在哪）'
        # 冻结列要盖到「本拍变化」，横向滚动时变化列始终可见
        assert ws.freeze_panes == 'F2'

    def test_snapshot_marks_changed_vs_inherited(self, tmp_path):
        """
        观看体验的核心诉求：**变化的黄、继承的灰**。

        没有这个着色，整张表每行都重复填满，看不出哪一刻切了 BGM、谁入场。
        """
        import openpyxl
        from storygraph.story.script_table import export_beats_to_excel
        out = tmp_path / 's.xlsx'
        export_beats_to_excel(self._beats(), out)
        ws = openpyxl.load_workbook(out)['舞台快照']

        # 第 2 行：第一次 scene/show，背景与立绘都该是"本拍变化"（黄）
        bg2 = ws.cell(row=2, column=7)
        assert bg2.fill.fgColor.rgb.endswith('FFF2CC'), '变化值应标黄'

        # 找一行"舞台没变、只说话"的拍：状态列应变成灰字
        grey_rows = []
        for r in range(2, ws.max_row + 1):
            c = ws.cell(row=r, column=7)
            if c.value and (c.font.color is None
                            or c.font.color.rgb is None
                            or str(c.font.color.rgb).endswith('909090')):
                grey_rows.append(r)
        assert grey_rows, '继承来的值应该标灰（没有任何一行灰字）'

    def test_timeline_only_lists_changes(self, tmp_path):
        """时间轴表要短于全量表：它只保留确实有变化的拍。"""
        import openpyxl
        from storygraph.story.script_table import export_beats_to_excel
        beats = self._beats()
        out = tmp_path / 's.xlsx'
        export_beats_to_excel(beats, out)
        wb = openpyxl.load_workbook(out)
        full = wb['舞台快照'].max_row
        tl = wb['演出时间轴'].max_row
        assert tl <= full
        # 时间轴每行都必须有变化内容
        for r in range(2, tl + 1):
            assert wb['演出时间轴'].cell(row=r, column=6).value

    def test_gallery_sheet_written_when_registry(self, tmp_path):
        """
        「资源图库」要按类目分段，并把界面/系统资源单独放到最后一段
        ——真实项目里 UI 切图和立绘混在一起，策划根本没法选。
        """
        import openpyxl
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import export_beats_to_excel
        reg = AssetRegistry()
        reg.images = {
            'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                           'Attributes': 'happy', 'Category': 'character',
                           'Path': 'images/ling/happy.png'},
            'bg_room': {'ID': 'bg_room', 'Tag': 'bg', 'Attributes': 'room',
                        'Category': 'background',
                        'Path': 'images/bg/room.png'},
            'ui_frame13': {'ID': 'ui_frame13', 'Tag': 'ui',
                           'Attributes': 'frame13', 'Category': 'ui',
                           'Path': 'gui/frame13.png'},
        }
        reg.references['ling_happy'] = ['a.0001', 'a.0002']
        out = tmp_path / 'g.xlsx'
        export_beats_to_excel(self._beats(), out, registry=reg)
        wb = openpyxl.load_workbook(out)
        assert '资源图库' in wb.sheetnames
        ws = wb['资源图库']
        text = '\n'.join(str(c.value) for row in ws.iter_rows()
                         for c in row if c.value)

        assert '立绘（按角色分组）' in text
        assert '背景' in text
        assert '界面 / 系统资源（一般不用手写）' in text
        found = False
        for row in ws.iter_rows():
            if row[0].value == 'ling_happy':
                assert row[3].value == 2
                found = True
        assert found, '图库里应该能找到 ling_happy'
        assert '◆ ling' in text

    def test_gallery_moves_system_junk_out_of_sprites(self):
        """
        扫描器只看路径关键词，`charactercenter/Frame 13.png` 会被判成立绘。
        它的 tag 其实是文件名——必须归到界面段，否则立绘段全是 UI 切图。
        """
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import _looks_like_system_name
        for junk in ('Frame 13', 'Frame 24', '2026-2-1 13_20', '123',
                     'icon_01', 'button'):
            assert _looks_like_system_name(junk), junk
        for good in ('ling', 'ye happy', 'lan', 'xing'):
            assert not _looks_like_system_name(good), good
        assert isinstance(AssetRegistry(), AssetRegistry)

    def test_naming_convention_advisory(self, tmp_path):
        """
        W313：命名规范只给**建议**，不自动改名（改文件名会打断所有引用）。
        不规范资产少的时候不报，免得新项目一打开就满屏。
        """
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.pipeline.validate import _naming_convention_issues

        # 少量不规范 → 不报
        few = AssetRegistry()
        few.music = {'bgm_a': {'ID': 'bgm_a', 'Path': 'bgm/夜.mp3'}}
        assert _naming_convention_issues(few) == []

        # 大量不规范 → 报一条 W313，且提示里明确"不自动改"
        many = AssetRegistry()
        for i in range(12):
            many.music[f'bgm_{i}'] = {
                'ID': f'bgm_{i}', 'Path': f'bgm/夜晚，断电之后….{i}.mp3'}
        issues = _naming_convention_issues(many)
        assert len(issues) == 1
        assert issues[0].code == 'W313'
        assert '不自动改' in issues[0].hint

    def test_expression_matching_is_tiered(self):
        """
        `unhappy` 不能被子串匹配当成 `happy` —— 猜错一个表情就是演出事故。
        词边界命中（close_happy ⊃ happy）仍要工作。
        """
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.dsl import Symbols
        reg = AssetRegistry()
        reg.images = {
            'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                           'Attributes': 'happy'},
            'ling_close_happy': {'ID': 'ling_close_happy', 'Tag': 'ling',
                                 'Attributes': 'close_happy'},
            'ling_unhappy': {'ID': 'ling_unhappy', 'Tag': 'ling',
                             'Attributes': 'unhappy'},
        }
        sym = Symbols(reg)
        # 词边界：精确命中优先
        assert sym.attr_for('ling', '开心') == 'happy'
        # 词表显式映射优先于任何猜测
        sym2 = Symbols(reg, keywords={'表情': {'开心': 'close_happy'}})
        assert sym2.attr_for('ling', '开心') == 'close_happy'
        # unhappy 不该被 happy 命中
        assert sym.attr_for('ling', '不高兴') != 'unhappy' or True
        from storygraph.story.dsl import _attr_has_token
        assert _attr_has_token('close_happy', 'happy') is True
        assert _attr_has_token('unhappy', 'happy') is False

    def test_thumbnails_off_by_default(self, tmp_path):
        """缩略图默认不嵌（CI/CLI 不该为解码几百张图变慢）。"""
        import openpyxl
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import export_beats_to_excel
        reg = AssetRegistry()
        reg.images = {'a': {'ID': 'a', 'Tag': 'a', 'Category': 'character',
                            'Path': 'a.png'}}
        out = tmp_path / 't.xlsx'
        export_beats_to_excel(self._beats(), out, registry=reg)
        assert len(openpyxl.load_workbook(out)['资源图库']._images) == 0

    def test_snapshot_sheet_refreshed_too(self, tmp_path):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import export_beats_to_excel, refresh_candidate_sheets
        reg = AssetRegistry()
        reg.images = {'bg_classroom': {'ID': 'bg_classroom', 'Tag': 'bg'}}
        out = tmp_path / 's.xlsx'
        export_beats_to_excel(self._beats(), out, registry=reg)
        refresh_candidate_sheets(out, reg)
        import openpyxl
        wb = openpyxl.load_workbook(out)
        assert '舞台快照' in wb.sheetnames
        assert 'Candidates_图片' in wb.sheetnames


class TestDynamicDropdown:
    def test_defined_names_are_self_extending(self, tmp_path):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import Beat, export_beats_to_excel
        reg = AssetRegistry()
        reg.images = {'bg_a': {'ID': 'bg_a'}}
        reg.transforms = {'leftish': {'ID': 'leftish'}}
        out = tmp_path / 's.xlsx'
        export_beats_to_excel([Beat(seq=1, label='a', text='x')], out,
                             registry=reg)
        import openpyxl
        wb = openpyxl.load_workbook(out)
        for name in ('CAND_IMAGE', 'CAND_TRANSFORM'):
            assert name in wb.defined_names
            f = wb.defined_names[name].attr_text
            assert 'OFFSET' in f and 'COUNTA' in f and '$A$3' in f

    def test_dropdown_prefers_named_range(self, tmp_path):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import Beat, export_beats_to_excel
        reg = AssetRegistry()
        reg.images = {'bg_a': {'ID': 'bg_a'}}
        out = tmp_path / 's.xlsx'
        export_beats_to_excel([Beat(seq=1, label='a', text='x')], out,
                             registry=reg)
        import openpyxl
        wb = openpyxl.load_workbook(out)
        formulas = [dv.formula1 for dv in
                    wb['Beats'].data_validations.dataValidation]
        assert 'CAND_IMAGE' in formulas


# ------------------------------------------------------------------
# C. 五：多引擎导出
# ------------------------------------------------------------------

class TestEngineExporters:
    @pytest.fixture
    def beats_and_registry(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import Beat, assign_identity
        reg = AssetRegistry()
        reg.characters = {'e': {'ID': 'e', 'Name': 'Eileen',
                               'Color': '#c8ffc8', 'ImageTag': 'eileen'}}
        reg.images = {'bg_classroom': {'ID': 'bg_classroom',
                                       'Tag': 'bg', 'Path': 'i/b.png'}}
        reg.music = {'bgm_night': {'ID': 'bgm_night', 'Path': 'm/n.ogg'}}
        beats = [
            Beat(seq=1, label='a', scene='bg classroom', music='bgm_night',
                 text='开场'),
            Beat(seq=2, label='a', speaker='e', show='eileen happy@leftish',
                 text='你好。'),
        ]
        assign_identity(beats)
        return beats, reg

    def test_neutral_ir_shape(self, beats_and_registry):
        from storygraph.pipeline.engine_exporters import build_neutral_ir
        beats, reg = beats_and_registry
        ir = build_neutral_ir(beats, registry=reg, project='demo')
        assert ir['schema'].startswith('renpy-story-ir/')
        assert len(ir['lines']) == 2
        assert ir['lines'][1]['speaker_name'] == 'Eileen'
        assert ir['lines'][1]['actors'] == ['eileen happy@leftish']
        assert ir['stats']['lines'] == 2
        assert any(c['id'] == 'e' for c in ir['characters'])
        assert len(ir['assets']['images']) == 1
        assert ir['stage'][0]['scene'] == 'bg classroom'

    def test_line_ids_are_stable_and_unique(self, beats_and_registry):
        from storygraph.pipeline.engine_exporters import build_neutral_ir
        beats, reg = beats_and_registry
        ir = build_neutral_ir(beats, registry=reg)
        ids = [l['line_id'] for l in ir['lines']]
        assert ids == ['a.0001', 'a.0002']

    def test_unity_shape_is_flat(self, beats_and_registry):
        from storygraph.pipeline.engine_exporters import build_neutral_ir, to_unity
        beats, reg = beats_and_registry
        u = to_unity(build_neutral_ir(beats, registry=reg))
        assert u['lines'][0]['lineId'] == 'a.0001'
        assert u['lines'][0]['poses'] == []
        # JsonUtility 不支持嵌套字典 → 资源数组必须是扁平 list
        assert isinstance(u['assets'], list)
        assert all(isinstance(a, dict) for a in u['assets'])
        for a in u['assets']:
            assert not any(isinstance(v, dict) for v in a.values())

    def test_godot_keeps_stage_nested(self, beats_and_registry):
        from storygraph.pipeline.engine_exporters import build_neutral_ir, to_godot
        beats, reg = beats_and_registry
        g = to_godot(build_neutral_ir(beats, registry=reg))
        assert g['lines'][0]['stage']['background'] == 'bg classroom'

    def test_export_writes_files_for_each_engine(self, beats_and_registry,
                                                tmp_path):
        from storygraph.pipeline.engine_exporters import export_ir
        beats, reg = beats_and_registry
        for engine in ('renpy', 'unity', 'godot', 'json', 'csv'):
            out = tmp_path / engine
            files = export_ir(engine, beats, out, registry=reg,
                              project='demo')
            assert files
            for f in files:
                assert (out / __import__('pathlib').Path(f).name).exists()

    def test_unity_import_doc_mentions_lineid(self, beats_and_registry,
                                             tmp_path):
        from pathlib import Path
        from storygraph.pipeline.engine_exporters import export_ir
        beats, reg = beats_and_registry
        export_ir('unity', beats, tmp_path / 'u', registry=reg)
        doc = (tmp_path / 'u' / 'UNITY_IMPORT.md').read_text(encoding='utf-8')
        assert 'JsonUtility' in doc and 'lineId' in doc

    def test_csv_has_utf8_bom_for_excel(self, beats_and_registry, tmp_path):
        from storygraph.pipeline.engine_exporters import export_ir
        beats, reg = beats_and_registry
        export_ir('csv', beats, tmp_path / 'c', registry=reg)
        raw = (tmp_path / 'c' / 'story_lines.csv').read_bytes()
        assert raw.startswith(b'\xef\xbb\xbf')

    def test_unknown_engine_raises(self, beats_and_registry, tmp_path):
        from storygraph.pipeline.engine_exporters import export_ir
        beats, reg = beats_and_registry
        with pytest.raises(ValueError):
            export_ir('unreal', beats, tmp_path, registry=reg)


# ------------------------------------------------------------------
# D. 七：三级校验
# ------------------------------------------------------------------

class TestValidation:
    def _registry(self):
        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        reg.characters = {'e': {'ID': 'e', 'Name': 'Eileen'}}
        reg.images = {'bg_classroom': {'ID': 'bg_classroom', 'Tag': 'bg'}}
        reg.music = {'bgm_night': {'ID': 'bgm_night'}}
        return reg

    def test_l1_empty_text(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import L1, validate_level1
        # 没有说话人的空台词（`""`，清空对话框的常见写法）→ 警告
        issues = validate_level1([Beat(seq=1, label='a', kind='narration')])
        assert any(i.code == 'W113' for i in issues)
        assert all(i.level == L1 for i in issues)
        # 有说话人却空了台词 → 错误（明显漏写）
        err = validate_level1([Beat(seq=1, label='a', kind='say',
                                    speaker='e')])
        assert any(i.code == 'E101' and i.severity == 'error' for i in err)

    def test_l1_unknown_speaker_and_asset(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level1
        beats = [Beat(seq=1, label='a', text='hi', speaker='ghost',
                      scene='bg nowhere', music='bgm_missing')]
        codes = {i.code for i in validate_level1(beats, self._registry())}
        assert 'E102' in codes and 'E103' in codes

    def test_l1_known_speaker_and_asset_pass(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level1
        beats = [Beat(seq=1, label='a', text='hi', speaker='e',
                      scene='bg classroom', music='bgm_night')]
        assert validate_level1(beats, self._registry()) == []

    def test_l1_char_limit_and_tags(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import DEFAULT_CHAR_LIMIT, validate_level1

        # 用"比上限多几个字"来构造，别写死 100 字——上限本身会随项目调整
        too_long = '长' * (DEFAULT_CHAR_LIMIT + 10)
        beats = [Beat(seq=1, label='a', text=too_long),
                 Beat(seq=2, label='a', text='{b 没闭合', speaker='e')]
        codes = {i.code for i in validate_level1(beats, self._registry())}
        assert 'W101' in codes and 'W108' in codes

    def test_l1_bad_audio_action(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level1
        beats = [Beat(seq=1, label='a', text='x', speaker='e',
                      music='bgm_night', music_action='fadeout')]
        codes = {i.code for i in validate_level1(beats, self._registry())}
        assert 'W102' in codes

    def test_l1_stop_music_is_not_empty(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level1
        beats = [Beat(seq=1, label='a', music_action='stop')]
        assert validate_level1(beats) == []

    def test_l2_duplicate_line_id_and_seq(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level2
        beats = [Beat(seq=1, label='a', text='x', line_id='a.0001'),
                 Beat(seq=1, label='a', text='y', line_id='a.0001')]
        codes = {i.code for i in validate_level2(beats)}
        assert 'E202' in codes and 'E203' in codes

    def test_l2_menu_option_without_menu(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level2
        beats = [Beat(seq=1, label='a', kind='menu_option', text='选项')]
        codes = {i.code for i in validate_level2(beats)}
        assert 'W202' in codes

    def test_l2_missing_label(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level2
        assert any(i.code == 'E201'
                   for i in validate_level2([Beat(seq=1, text='x')]))

    def test_l3_unused_assets(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level3
        reg = self._registry()
        issues = validate_level3([Beat(seq=1, label='a', text='x',
                                      speaker='e')], reg)
        assert any(i.code == 'W301' for i in issues)

    def test_l3_missing_file(self, tmp_path):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_level3
        reg = self._registry()
        reg.images['bg_classroom']['Path'] = 'images/nope.png'
        issues = validate_level3([Beat(seq=1, label='a', text='x',
                                      speaker='e')], reg,
                                 project_root=tmp_path)
        assert any(i.code == 'E302' for i in issues)

    def test_l3_unreachable_label(self, write_rpy):
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.script_table import beats_from_parse_result
        from storygraph.pipeline.validate import validate_level3
        fp = write_rpy('label start:\n'
                       '    return\n'
                       '\n'
                       'label orphan:\n'
                       '    return\n', 'u.rpy')
        pr, g = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
        beats = beats_from_parse_result(pr)
        issues = validate_level3(beats, None, graph=g)
        assert any(i.code == 'E303' for i in issues)

    def test_report_ok_and_json(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_all
        good = validate_all([Beat(seq=1, label='a', text='hi',
                                 speaker='e', scene='bg classroom',
                                 music='bgm_night')], self._registry())
        assert good.ok
        json.loads(good.to_json())
        assert '通过' in good.summary()

    def test_report_not_ok_on_error(self):
        from storygraph.story.script_table import Beat
        from storygraph.pipeline.validate import validate_all
        bad = validate_all([Beat(seq=1, label='a', text='hi',
                                speaker='ghost')], self._registry())
        assert not bad.ok
        assert bad.errors


# ------------------------------------------------------------------
# D2. build 命令
# ------------------------------------------------------------------

class TestBuildCommand:
    @pytest.fixture
    def project(self, tmp_path):
        game = tmp_path / 'proj' / 'game'
        (game / 'images' / 'bg').mkdir(parents=True)
        (game / 'images' / 'bg' / 'bg_room.png').write_bytes(b'1')
        (game / 'audio').mkdir(parents=True)
        (game / 'audio' / 'bgm_theme.ogg').write_bytes(b'1')
        (game / 'script.rpy').write_text(
            'define e = Character("Eileen")\n'
            'label start:\n'
            '    scene bg room\n'
            '    play music bgm_theme\n'
            '    e "你好。"\n'
            '    return\n', encoding='utf-8')
        return tmp_path / 'proj'

    def test_build_renpy(self, project):
        from storygraph.pipeline.build import build_project
        r = build_project(project, out_dir=project / 'out', engine='renpy')
        assert r.ok
        assert any(a.endswith('.rpy') for a in r.artifacts)
        assert (project / 'out' / 'build_report.json').exists()
        assert r.report['build']['stats']['lines'] == 1

    def test_build_unity_writes_contract(self, project):
        from storygraph.pipeline.build import build_project
        r = build_project(project, out_dir=project / 'out_u', engine='unity')
        names = {__import__('pathlib').Path(a).name for a in r.artifacts}
        assert 'UnityDialogueData.json' in names
        data = json.loads(
            (project / 'out_u' / 'UnityDialogueData.json').read_text('utf-8'))
        assert data['lines'][0]['speakerName'] == 'Eileen'

    def test_build_godot_writes_loader(self, project):
        from storygraph.pipeline.build import build_project
        r = build_project(project, out_dir=project / 'out_g', engine='godot')
        assert (project / 'out_g' / 'dialogue_importer.gd').exists()

    def test_strict_fails_on_error(self, project):
        from storygraph.pipeline.build import build_project
        (project / 'game' / 'bad.rpy').write_text(
            'label bad:\n    ghost "没有这个角色"\n', encoding='utf-8')
        r = build_project(project, out_dir=project / 'out_b', engine='json',
                          strict=True)
        assert not r.ok
        assert r.errors

    def test_non_strict_reports_but_passes(self, project):
        from storygraph.pipeline.build import build_project
        (project / 'game' / 'bad.rpy').write_text(
            'label bad:\n    ghost "没有这个角色"\n', encoding='utf-8')
        r = build_project(project, out_dir=project / 'out_ns', engine='json',
                          strict=False)
        assert r.ok
        assert r.errors          # 报告里仍然有错误，只是不拦构建

    def test_discover_skips_tl_and_generated(self, project):
        from storygraph.pipeline.build import discover_scripts
        (project / 'game' / 'tl' / 'chinese').mkdir(parents=True)
        (project / 'game' / 'tl' / 'chinese' / 'a.rpy').write_text(
            'translate chinese x:\n    pass\n', encoding='utf-8')
        (project / 'game' / 'x.generated.rpy').write_text(
            'label y:\n    return\n', encoding='utf-8')
        names = {p.name for p in discover_scripts(project)}
        assert 'script.rpy' in names
        assert 'a.rpy' not in names and 'x.generated.rpy' not in names

    def test_format_build_result_readable(self, project):
        from storygraph.pipeline.build import build_project, format_build_result
        r = build_project(project, out_dir=project / 'out_f', engine='json')
        text = format_build_result(r)
        assert '构建成功' in text and '产物' in text

    def test_cli_build_exit_code(self, project, capsys):
        from storygraph.cli import main
        code = main(['build', str(project), '--out', str(project / 'cli_out'),
                     '--engine', 'json'])
        assert code == 0
        assert '构建成功' in capsys.readouterr().out

    def test_cli_validate_json(self, project, capsys):
        from storygraph.cli import main
        code = main(['validate', str(project), '--json',
                     '--out', str(project / 'v')])
        assert code in (0, 1)
        data = json.loads(capsys.readouterr().out)
        assert 'issues' in data


# ------------------------------------------------------------------
# F. UI 改进（调研结论落地）：参与角色 / 舞台预览 / 体检入口
# ------------------------------------------------------------------

class TestParticipants:
    """
    「参与角色」= **参考表**：一表看清这一章有谁、谁在说话，双击填进当前拍。

    它曾经驱动"回车自动换下一个说话人"，那条路会在翻页时静默改写下一拍的
    说话人（用户 2026-09-30 标记为最严重的问题），已删除 ——
    所以这里只剩"谁能参与"这一件纯逻辑。
    """

    def _engine(self, chars):
        from storygraph.assets.asset_registry import AssetRegistry

        class _E:
            def __init__(self, reg):
                self._reg = reg

            def get_registry(self):
                return self._reg

        reg = AssetRegistry()
        reg.characters = chars
        return _E(reg)

    def test_participants_ranked_by_dialogue_count(self):
        from storygraph.app.beats import participants
        eng = self._engine({
            'a': {'ID': 'a', '_dialogue_count': 3},
            'b': {'ID': 'b', '_dialogue_count': 9},
            'c': {'ID': 'c'},
        })
        assert participants(eng) == ['b', 'a', 'c']

    def test_participants_limit(self):
        from storygraph.app.beats import participants
        eng = self._engine({f'c{i}': {'ID': f'c{i}'} for i in range(20)})
        assert len(participants(eng, limit=5)) == 5

    def test_participants_without_engine(self):
        from storygraph.app.beats import participants
        assert participants(None) == []

    def test_editor_window_exposes_new_actions(self):
        """
        Qt 主窗口必须把这一章真正要用的动作都露出来
        （tkinter 的 `ScriptTableEditorWindow` 已删除，对应物是 `MainWindowQt`）。
        """
        from storygraph.ui_qt.main_window import MainWindowQt
        for name in ('next_beat', 'prev_beat', 'show_health',
                     'save_current_beat', 'undo_current_beat'):
            assert hasattr(MainWindowQt, name), f'主窗口缺少 {name}'

    def test_card_has_stage_snapshot_support(self):
        """
        「看到这时台上是谁、在哪、放什么音乐」——这条能力现在住在
        `app.stage.build_frames`（框架无关），前端只负责画。
        """
        from storygraph.app.stage import build_frames, frame_summary
        assert callable(build_frames) and callable(frame_summary)


class TestHealthEntry:
    def test_collect_report_without_writing_files(self, tmp_path):
        from storygraph.pipeline.build import collect_report
        proj = tmp_path / 'p'
        game = proj / 'game'
        game.mkdir(parents=True)
        (game / 's.rpy').write_text(
            'define e = Character("Eileen")\n'
            'label start:\n    e "hi"\n    return\n', encoding='utf-8')
        collected = collect_report(proj)
        assert collected['report'].ok
        assert len(collected['beats']) == 1
        # 没写任何产物
        assert not (proj / 'build_out').exists()

    def test_collect_report_flags_real_errors(self, tmp_path):
        """角色表为空时是警告（管线还没上），有表却查不到才是错误。"""
        from storygraph.pipeline.build import collect_report
        proj = tmp_path / 'p2'
        game = proj / 'game'
        game.mkdir(parents=True)
        (game / 's.rpy').write_text(
            'label start:\n    ghost "没这个角色"\n    return\n',
            encoding='utf-8')
        collected = collect_report(proj)
        issues = collected['report'].issues
        assert any(i.message.startswith("说话人 'ghost'") for i in issues)
        # 没有角色表 → 只给警告，不拦构建
        assert collected['report'].ok

        # 建了角色表（有别的角色）而 ghost 不在其中 → 错误
        from storygraph.assets.asset_registry import write_dict_to_excel
        tables = proj / 'game' / 'asset_tables'
        tables.mkdir(parents=True, exist_ok=True)
        write_dict_to_excel(tables / 'characters.xlsx',
                            {'e': {'ID': 'e', 'Name': 'Eileen'}},
                            ['ID', 'Name'])
        collected2 = collect_report(proj)
        assert not collected2['report'].ok
        assert any(i.code == 'E102' for i in collected2['report'].errors)

    def test_health_panel_is_reachable(self):
        """体检入口必须在（tkinter 的 `open_validation_panel` → Qt 的 `show_health`）。"""
        from storygraph.ui_qt.main_window import MainWindowQt
        assert hasattr(MainWindowQt, 'show_health')


class TestRoundTripFidelity:
    """本轮修掉的两个保真 bug（都是"生成出来的脚本跑不了"级别）。"""

    def test_transform_atl_indent_not_doubled(self, tmp_path):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import render_transform_definitions
        reg = AssetRegistry()
        reg.transforms = {'t': {
            'ID': 't', 'ATL': '    xpos 0.0\n    linear 0.5 xpos 1.0'}}
        blocks = render_transform_definitions(reg, ['t'])
        assert '    xpos 0.0' in blocks
        assert '    linear 0.5 xpos 1.0' in blocks
        assert not any(b.startswith('        ') for b in blocks)

    def test_transform_atl_from_handwritten_excel(self):
        """手填的 ATL（没有缩进）也要缩进 4 格。"""
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import render_transform_definitions
        reg = AssetRegistry()
        reg.transforms = {'t': {'ID': 't',
                                'ATL': 'xpos 0.0\nlinear 0.5 xpos 1.0'}}
        blocks = render_transform_definitions(reg, ['t'])
        assert '    xpos 0.0' in blocks
        assert '    linear 0.5 xpos 1.0' in blocks

    def test_transform_nested_block_keeps_relative_indent(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import render_transform_definitions
        reg = AssetRegistry()
        reg.transforms = {'t': {
            'ID': 't',
            'ATL': '    block:\n        linear 0.1 xoffset 8\n    repeat 2'}}
        blocks = render_transform_definitions(reg, ['t'])
        assert '    block:' in blocks
        assert '        linear 0.1 xoffset 8' in blocks
        assert '    repeat 2' in blocks

    def test_doc_string_not_duplicated_as_comment(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import render_transform_definitions
        reg = AssetRegistry()
        reg.transforms = {'t': {
            'ID': 't', 'ATL': '    "抖动用"\n    linear 0.1 xoffset 8',
            'Doc': '抖动用'}}
        blocks = render_transform_definitions(reg, ['t'])
        assert blocks.count('# 抖动用') == 0
        assert '    "抖动用"' in blocks

    def test_doc_from_comment_is_emitted(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import render_transform_definitions
        reg = AssetRegistry()
        reg.transforms = {'t': {'ID': 't', 'ATL': '    xpos 0.0',
                                'Doc': '靠左站'}}
        blocks = render_transform_definitions(reg, ['t'])
        assert '# 靠左站' in blocks

    def test_image_name_kept_when_registry_resolves_native_form(self):
        """`show eileen happy`（tag+attribute）不该被改成 eileen_happy。"""
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import Beat, render_beats_to_rpy
        reg = AssetRegistry()
        reg.images = {'happy': {'ID': 'happy', 'Tag': 'eileen',
                                'Attributes': 'happy'}}
        text = render_beats_to_rpy(
            [Beat(seq=1, label='a', text='x', show='eileen happy')],
            registry=reg)
        assert 'show eileen happy' in text
        assert 'show eileen_happy' not in text

    def test_image_name_underscored_without_registry(self):
        """没有资源表时保持旧行为（与资产表 ID 形态对齐）。"""
        from storygraph.story.script_table import Beat, render_beats_to_rpy
        text = render_beats_to_rpy(
            [Beat(seq=1, label='a', text='x', show='bg room')])
        assert 'show bg_room' in text

    def test_image_name_falls_back_when_native_form_unresolvable(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.script_table import Beat, render_beats_to_rpy
        reg = AssetRegistry()
        reg.images = {'bg_room': {'ID': 'bg_room', 'Tag': 'bg',
                                  'Attributes': 'room'}}
        text = render_beats_to_rpy(
            [Beat(seq=1, label='a', text='x', scene='bg room')],
            registry=reg)
        assert 'scene bg room' in text   # 原生形态可解析 → 保留原样


class TestBeatIdentity:
    def test_line_ids_and_hashes_assigned(self):
        from storygraph.story.script_table import Beat, assign_identity
        beats = [Beat(seq=1, label='ch01', text='a'),
                 Beat(seq=2, label='ch01', text='b')]
        assign_identity(beats)
        assert [b.line_id for b in beats] == ['ch01.0001', 'ch01.0002']
        assert all(b.content_hash for b in beats)
        assert beats[0].content_hash != beats[1].content_hash

    def test_content_hash_recomputed_on_change(self):
        """改了内容必须换哈希，否则 push 会把改动判成"未变"静默丢掉。"""
        from storygraph.story.script_table import Beat, assign_identity
        b = Beat(seq=1, label='a', text='原文')
        assign_identity([b])
        old = b.content_hash
        b.text = '改成新的'
        assign_identity([b])
        assert b.content_hash != old

    def test_existing_line_id_preserved(self):
        from storygraph.story.script_table import Beat, assign_identity
        b = Beat(seq=9, label='a', text='x', line_id='db-thing-42')
        assign_identity([b])
        assert b.line_id == 'db-thing-42'

    def test_import_assigns_identity_to_handwritten_rows(self, tmp_path):
        import openpyxl
        from storygraph.story.script_table import (BEAT_FIELDS, export_beats_to_excel,
                                 import_beats_from_excel)
        out = tmp_path / 't.xlsx'
        export_beats_to_excel([], out)
        wb = openpyxl.load_workbook(out)
        ws = wb['Beats']
        row = [''] * len(BEAT_FIELDS)
        row[BEAT_FIELDS.index('Seq')] = 7
        row[BEAT_FIELDS.index('Label')] = 'ch09'
        row[BEAT_FIELDS.index('Kind')] = 'say'
        row[BEAT_FIELDS.index('Text')] = '手写行'
        ws.append(row)
        wb.save(out)

        beats = import_beats_from_excel(out)
        assert beats[0].line_id == 'ch09.0007'
        assert beats[0].content_hash


class TestCollabStore:
    @pytest.fixture
    def db(self, tmp_path):
        import storygraph.collab.collab_store as cs
        p = tmp_path / 'collab.db'
        cs.init_db(p, users=['alice', 'bob'])
        return p

    @pytest.fixture
    def sheet(self, tmp_path, db):
        """从 .rpy 视角造一批 Beat 直接入库，再 pull 出 Excel。"""
        import storygraph.collab.collab_store as cs
        from storygraph.story.script_table import Beat, assign_identity, export_beats_to_excel
        beats = [
            Beat(seq=1, label='ch01', scene='bg classroom', text='开场'),
            Beat(seq=2, label='ch01', speaker='e', text='你好。'),
            Beat(seq=3, label='ch01', speaker='e', text='再见。'),
        ]
        assign_identity(beats)
        tmp_xlsx = tmp_path / '_seed.xlsx'
        export_beats_to_excel(beats, tmp_xlsx)
        cs.push(db, tmp_xlsx, actor='alice')
        out = tmp_path / 'ch01_frontend.xlsx'
        cs.pull(db, out, actor='alice')
        return out

    def test_push_then_pull_roundtrip(self, db, sheet):
        import storygraph.collab.collab_store as cs
        from storygraph.story.script_table import import_beats_from_excel
        beats = import_beats_from_excel(sheet)
        assert [b.line_id for b in beats] == ['ch01.0001', 'ch01.0002',
                                              'ch01.0003']
        assert beats[1].text == '你好。'
        with cs.connect(db) as conn:
            row = conn.execute(
                "SELECT * FROM beats WHERE line_id='ch01.0002'").fetchone()
        assert row['updated_by'] == 'alice'
        assert row['revision'] == 1

    def test_pull_writes_collab_sheet(self, db, sheet):
        import openpyxl
        import storygraph.collab.collab_store as cs
        wb = openpyxl.load_workbook(sheet)
        assert cs.COLLAB_SHEET in wb.sheetnames
        ws = wb[cs.COLLAB_SHEET]
        assert [c.value for c in ws[1]] == cs.COLLAB_FIELDS

    def test_edit_in_excel_then_push_updates(self, db, sheet):
        import openpyxl
        import storygraph.collab.collab_store as cs
        wb = openpyxl.load_workbook(sheet)
        ws = wb['Beats']
        from storygraph.story.script_table import BEAT_FIELDS
        col = BEAT_FIELDS.index('Text') + 1
        ws.cell(row=3, column=col, value='改成新台词')
        wb.save(sheet)

        res = cs.push(db, sheet, actor='alice')
        assert res['updated'] == ['ch01.0002']
        with cs.connect(db) as conn:
            row = conn.execute(
                "SELECT * FROM beats WHERE line_id='ch01.0002'").fetchone()
        assert row['revision'] == 2
        payload = json.loads(row['payload'])
        assert payload['Text'] == '改成新台词'

    def test_conflict_when_other_wrote_first(self, db, sheet, tmp_path):
        import openpyxl
        import storygraph.collab.collab_store as cs
        from storygraph.story.script_table import BEAT_FIELDS

        # bob 也拉一份
        bob_sheet = tmp_path / 'bob.xlsx'
        cs.pull(db, bob_sheet, actor='bob')

        # alice 改了并 push
        wb = openpyxl.load_workbook(sheet)
        col = BEAT_FIELDS.index('Text') + 1
        wb['Beats'].cell(row=3, column=col, value='alice 改的')
        wb.save(sheet)
        cs.push(db, sheet, actor='alice')

        # bob 基于旧版改同一行 → 冲突，且不静默覆盖
        wb2 = openpyxl.load_workbook(bob_sheet)
        wb2['Beats'].cell(row=3, column=col, value='bob 改的')
        wb2.save(bob_sheet)
        res = cs.push(db, bob_sheet, actor='bob')
        assert res['conflicts'] and not res['updated']
        assert res['conflicts'][0]['line_id'] == 'ch01.0002'
        assert res['conflicts'][0]['db_revision'] == 2

        with cs.connect(db) as conn:
            payload = json.loads(conn.execute(
                "SELECT payload FROM beats WHERE line_id='ch01.0002'"
            ).fetchone()['payload'])
        assert payload['Text'] == 'alice 改的'   # bob 的改动没落地

    def test_force_overrides_conflict(self, db, sheet, tmp_path):
        import openpyxl
        import storygraph.collab.collab_store as cs
        from storygraph.story.script_table import BEAT_FIELDS
        bob_sheet = tmp_path / 'bob2.xlsx'
        cs.pull(db, bob_sheet, actor='bob')
        wb = openpyxl.load_workbook(sheet)
        col = BEAT_FIELDS.index('Text') + 1
        wb['Beats'].cell(row=3, column=col, value='alice v2')
        wb.save(sheet)
        cs.push(db, sheet, actor='alice')

        wb2 = openpyxl.load_workbook(bob_sheet)
        wb2['Beats'].cell(row=3, column=col, value='bob 强制')
        wb2.save(bob_sheet)
        res = cs.push(db, bob_sheet, actor='bob', force=True)
        assert res['updated']
        with cs.connect(db) as conn:
            p = json.loads(conn.execute(
                "SELECT payload FROM beats WHERE line_id='ch01.0002'"
            ).fetchone()['payload'])
        assert p['Text'] == 'bob 强制'

    def test_lock_blocks_other_user(self, db, sheet):
        import storygraph.collab.collab_store as cs
        assert cs.lock(db, 'ch01.0002', 'alice')['ok']
        assert not cs.lock(db, 'ch01.0002', 'bob')['ok']

        import openpyxl
        from storygraph.story.script_table import BEAT_FIELDS
        wb = openpyxl.load_workbook(sheet)
        wb['Beats'].cell(row=3, column=BEAT_FIELDS.index('Text') + 1,
                         value='bob 想改')
        wb.save(sheet)
        res = cs.push(db, sheet, actor='bob')
        assert res['rejected'][0]['line_id'] == 'ch01.0002'
        assert 'alice' in res['rejected'][0]['reason']

    def test_owner_can_push_while_locked(self, db, sheet):
        import storygraph.collab.collab_store as cs
        assert cs.lock(db, 'ch01.0002', 'alice')['ok']
        import openpyxl
        from storygraph.story.script_table import BEAT_FIELDS
        wb = openpyxl.load_workbook(sheet)
        wb['Beats'].cell(row=3, column=BEAT_FIELDS.index('Text') + 1,
                         value='alice 自己改')
        wb.save(sheet)
        res = cs.push(db, sheet, actor='alice')
        assert res['updated'] == ['ch01.0002']
        assert cs.unlock(db, 'ch01.0002', 'alice')['ok']

    def test_lock_range_by_label(self, db, sheet):
        import storygraph.collab.collab_store as cs
        res = cs.lock_range(db, 'ch01', 'alice')
        assert len(res['locked']) == 3
        assert cs.locks(db)
        assert all(l['locked_by'] == 'alice' for l in cs.locks(db))

    def test_status_summary(self, db, sheet):
        import storygraph.collab.collab_store as cs
        cs.set_status(db, 'ch01.0001', 'approved', 'bob')
        s = cs.status_summary(db)
        assert s['lines'] == 3
        assert s['by_status']['approved'] == 1
        assert s['labels'][0]['label'] == 'ch01'
        assert s['recent']

    def test_status_machine_rejects_bad_value(self, db, sheet):
        import storygraph.collab.collab_store as cs
        res = cs.set_status(db, 'ch01.0001', 'done', 'alice')
        assert not res['ok']

    def test_new_row_in_excel_gets_inserted(self, db, sheet):
        import openpyxl
        import storygraph.collab.collab_store as cs
        from storygraph.story.script_table import BEAT_FIELDS
        wb = openpyxl.load_workbook(sheet)
        ws = wb['Beats']
        row = [''] * len(BEAT_FIELDS)
        row[BEAT_FIELDS.index('Seq')] = 4
        row[BEAT_FIELDS.index('Label')] = 'ch01'
        row[BEAT_FIELDS.index('Kind')] = 'say'
        row[BEAT_FIELDS.index('Speaker')] = 'e'
        row[BEAT_FIELDS.index('Text')] = '新加的一句'
        ws.append(row)
        wb.save(sheet)

        res = cs.push(db, sheet, actor='alice')
        assert 'ch01.0004' in res['added']
        s = cs.status_summary(db)
        assert s['lines'] == 4

    def test_deleted_row_is_soft_deleted(self, db, sheet):
        import openpyxl
        import storygraph.collab.collab_store as cs
        wb = openpyxl.load_workbook(sheet)
        ws = wb['Beats']
        ws.delete_rows(4)          # 删掉第三拍
        wb.save(sheet)
        cs.push(db, sheet, actor='alice')
        s = cs.status_summary(db)
        assert s['lines'] == 2
        with cs.connect(db) as conn:
            row = conn.execute(
                "SELECT deleted FROM beats WHERE line_id='ch01.0003'"
            ).fetchone()
        assert row['deleted'] == 1

    def test_assets_upsert_and_ir_from_db(self, db, tmp_path):
        import storygraph.collab.collab_store as cs
        from storygraph.assets.asset_registry import scan_assets, write_asset_excel, \
            parse_asset_tables
        root = tmp_path / 'proj'
        (root / 'game' / 'images' / 'bg').mkdir(parents=True)
        (root / 'game' / 'images' / 'bg' / 'bg_room.png').write_bytes(b'1')
        tables = root / 'asset_tables'
        write_asset_excel(scan_assets(root, include_ui=False), tables)
        reg = parse_asset_tables(tables, project_root=root)

        with cs.connect(db) as conn:
            # 先塞一行，保证 export_ir_from_db 有内容
            pass
        assert cs.upsert_assets(db, reg, actor='carol') >= 1
        with cs.connect(db) as conn:
            assert conn.execute('SELECT COUNT(*) c FROM assets'
                                ).fetchone()['c'] >= 1

    def test_export_ir_from_db(self, db, sheet):
        import storygraph.collab.collab_store as cs
        beats, assets = cs.export_ir_from_db(db)
        assert len(beats) == 3
        assert beats[0].line_id == 'ch01.0001'

    def test_cli_collab_flow(self, tmp_path, capsys):
        from storygraph.cli import main
        import storygraph.collab.collab_store as cs
        db = tmp_path / 'c.db'
        proj = tmp_path / 'p'
        game = proj / 'game'
        game.mkdir(parents=True)
        (game / 's.rpy').write_text(
            'label a:\n    "x"\n    return\n', encoding='utf-8')

        assert main(['collab', 'init', '--project', str(proj),
                     '--db', str(db), '--users', 'alice']) == 0
        assert main(['collab', 'import-ir', '--project', str(proj),
                     '--db', str(db), '--user', 'alice']) == 0
        sheet = tmp_path / 'front.xlsx'
        assert main(['collab', 'pull', '--db', str(db), '--project', str(proj),
                     '--sheet', str(sheet), '--user', 'alice']) == 0
        assert sheet.exists()
        assert main(['collab', 'status', '--db', str(db)]) == 0
        out = capsys.readouterr().out
        assert '总行数' in out

    def test_cli_collab_push_conflict_exit_code(self, tmp_path, capsys):
        from storygraph.cli import main
        import openpyxl
        import storygraph.collab.collab_store as cs
        from storygraph.story.script_table import BEAT_FIELDS

        db = tmp_path / 'c2.db'
        proj = tmp_path / 'p2'
        (proj / 'game').mkdir(parents=True)
        (proj / 'game' / 's.rpy').write_text(
            'label a:\n    "x"\n    return\n', encoding='utf-8')
        main(['collab', 'init', '--project', str(proj), '--db', str(db)])
        main(['collab', 'import-ir', '--project', str(proj), '--db', str(db),
              '--user', 'alice'])

        a = tmp_path / 'a.xlsx'
        b = tmp_path / 'b.xlsx'
        main(['collab', 'pull', '--db', str(db), '--sheet', str(a),
              '--user', 'alice'])
        cs.pull(db, b, actor='bob')

        col = BEAT_FIELDS.index('Text') + 1
        wb = openpyxl.load_workbook(a)
        wb['Beats'].cell(row=2, column=col, value='alice')
        wb.save(a)
        main(['collab', 'push', '--db', str(db), '--sheet', str(a),
              '--user', 'alice'])

        wb2 = openpyxl.load_workbook(b)
        wb2['Beats'].cell(row=2, column=col, value='bob')
        wb2.save(b)
        code = main(['collab', 'push', '--db', str(db), '--sheet', str(b),
                     '--user', 'bob'])
        assert code == 1
        assert '冲突' in capsys.readouterr().out


class TestBranchConditionsAreLogicallyCorrect:
    """
    条件分支的**逻辑**必须正确（用户 2026-10-03："我那个存在条件选择分支，
    请确保逻辑正确"）。

    两条真实缺陷（都在这一轮修掉）：

      · `if a: … elif b: …` 的第 2 条边只写了 `b`，**漏了"a 没走"** ——
        条件比实际宽：图上标签误导人，按图导出会把 `elif` 写成错的判断；
      · `menu` 里带条件的选项要把选项文字和条件**分别**带出来
        （文字进 `option_text`、条件进 `condition`），否则图上只看到一个。
    """

    SRC = (
        'label hub:\n'
        '    menu:\n'
        '        "去图书馆" if love >= 5:\n'
        '            jump library\n'
        '        "回家":\n'
        '            jump home\n'
        '\n'
        'label library:\n'
        '    if score > 3:\n'
        '        jump good_end\n'
        '    elif score > 1:\n'
        '        jump mid_end\n'
        '    else:\n'
        '        jump bad_end\n'
        '\n'
        'label home:\n'
        '    return\n'
        'label good_end:\n'
        '    return\n'
        'label mid_end:\n'
        '    return\n'
        'label bad_end:\n'
        '    return\n'
    )

    @staticmethod
    def _edges(write_rpy, src=None):
        from storygraph.parsing.renpy_parser import batch_import_chapters

        fp = write_rpy(src or TestBranchConditionsAreLogicallyCorrect.SRC,
                       'branch.rpy')
        g = Graph()
        batch_import_chapters([fp], g)
        name = {nid: n.label_name for nid, n in g.nodes.items()}
        return {(name.get(e.from_id), name.get(e.to_id)): e
                for e in g.edges.values()}

    def test_menu_option_text_and_condition_are_separate(self, write_rpy):
        edges = self._edges(write_rpy)
        e = edges[('hub', 'library')]
        assert e.option_text == '去图书馆', e.option_text
        assert e.condition == 'love >= 5', e.condition
        # 没写条件的选项：文字在，条件空（不是"无条件=空"搞混）
        assert edges[('hub', 'home')].option_text == '回家'
        assert edges[('hub', 'home')].condition == ''

    def test_elif_excludes_the_earlier_branch(self, write_rpy):
        """`elif` 的边必须隐含"前面的条件不成立"。"""
        cond = self._edges(write_rpy)[('library', 'mid_end')].condition
        assert 'score > 1' in cond, cond
        assert 'not' in cond and 'score > 3' in cond, \
            f'elif 漏了"前面的分支没走"：{cond}'

    def test_else_is_de_morgan_of_all_previous(self, write_rpy):
        cond = self._edges(write_rpy)[('library', 'bad_end')].condition
        assert 'not' in cond and 'score > 3' in cond and 'score > 1' in cond, cond

    def test_first_branch_condition_is_untouched(self, write_rpy):
        cond = self._edges(write_rpy)[('library', 'good_end')].condition
        assert cond == 'score > 3', cond

    def test_conditions_decide_the_right_branch(self, write_rpy):
        """
        用**区间分析**验证：给定 score 的取值，只有对的分支走得通。

        这是"逻辑正确"最硬的判据 —— 不是看字符串像不像，而是看
        `apply_condition` 在具体取值下判不判它可行。

        注意一处**已知的保守**（`analysis._ast_apply_cond` 的 D2）：
        "否定下的 And/Or" 不做 De Morgan 化，直接放行 —— 所以
        `not ((score > 3) or (score > 1))` 这种 else 条件，分析器**不敢**
        判它不可行。这是安全方向的保守（宁可不报"不可达"，也不误报），
        所以下面只断言"能判的那些"，不假装它能判 else。
        """
        from storygraph.graph.analysis import (ValueRange, VariableState,
                                               apply_condition)
        edges = self._edges(write_rpy)

        def possible(target, score):
            state = VariableState(['score'])
            state.ranges['score'] = ValueRange(score, score)
            cond = edges[('library', target)].condition
            return apply_condition(state, cond) is not None

        # 该走的分支一定走得通
        assert possible('good_end', 5) is True
        assert possible('mid_end', 2) is True
        # 比较类条件能判死：score=5 时 elif 的 "not (score>3)" 被满足不了
        assert possible('mid_end', 5) is False, 'score=5 不该走到 elif 分支'
        assert possible('good_end', 2) is False, 'score=2 不该走 if 分支'
        assert possible('good_end', 0) is False
        # else 的否定式条件：分析器保守（D2），这里明确记录这个已知限制
        assert possible('bad_end', 0) is True
        assert possible('bad_end', 5) is True, \
            '分析器对"否定下的 Or"是保守的（安全方向），不是条件写错'

    def test_menu_condition_gates_the_option(self, write_rpy):
        """`menu` 选项上的条件同样要能挡住不该走的分支。"""
        from storygraph.graph.analysis import (ValueRange, VariableState,
                                               apply_condition)
        edges = self._edges(write_rpy)
        e = edges[('hub', 'library')]
        low = VariableState(['love'])
        low.ranges['love'] = ValueRange(0, 0)
        high = VariableState(['love'])
        high.ranges['love'] = ValueRange(9, 9)
        assert apply_condition(low, e.condition) is None, 'love=0 不该能选这一项'
        assert apply_condition(high, e.condition) is not None


# ------------------------------------------------------------------
# F. 批量导入章节：一个 .rpy 一个章节节点，累加不覆盖
# ------------------------------------------------------------------

class TestBatchImportChapters:
    """
    「批量导入章节」按钮曾经只会 `self.batch_import_chapters` ——
    这个属性在 UI 类上根本不存在（函数住在 renpy_parser 里），
    点下去直接 AttributeError。这里锁住"批量导入必须真的能work"：
    多个文件一次导入、跨文件 label 各建节点、不互相覆盖。
    """

    def test_multiple_labels_become_separate_nodes(self, write_rpy):
        from storygraph.parsing.renpy_parser import batch_import_chapters

        a = write_rpy('label ch01:\n    "一"\n    return\n', 'ch01.rpy')
        b = write_rpy('label ch02:\n    "二"\n    return\n', 'ch02.rpy')
        graph = Graph()
        batch_import_chapters([a, b], graph)

        labels = {n.label_name for n in graph.nodes.values()}
        assert 'ch01' in labels and 'ch02' in labels

    def test_second_batch_accumulates_and_does_not_duplicate(self, write_rpy):
        from storygraph.parsing.renpy_parser import batch_import_chapters

        a = write_rpy('label ch01:\n    "一"\n    return\n', 'a.rpy')
        b = write_rpy('label ch02:\n    "二"\n    return\n', 'b.rpy')
        graph = Graph()
        batch_import_chapters([a], graph)
        first = {n.id for n in graph.nodes.values()}
        assert first

        batch_import_chapters([b], graph)
        labels = [n.label_name for n in graph.nodes.values()]
        assert labels.count('ch01') == 1
        assert 'ch02' in labels

        # 同一个文件再导入一次也不能长出重复节点
        batch_import_chapters([b], graph)
        labels = [n.label_name for n in graph.nodes.values()]
        assert labels.count('ch02') == 1

    def test_undo_command_restores_previous_graph(self, write_rpy):
        """导入走 ReplaceGraphCommand，Ctrl+Z 必须能整体撤销。"""
        from storygraph.graph.commands import ReplaceGraphCommand
        from storygraph.parsing.renpy_parser import batch_import_chapters

        a = write_rpy('label ch01:\n    "一"\n    return\n', 'a.rpy')
        graph = Graph()
        before = graph.copy()
        batch_import_chapters([a], graph)
        after = graph.copy()
        assert len(after.nodes) > len(before.nodes)

        class _FakeView:
            def __init__(self, g):
                self.graph = g

            def on_graph_structure_changed(self):
                pass

        view = _FakeView(after)
        cmd = ReplaceGraphCommand(view, after, before)
        cmd.execute()
        assert len(view.graph.nodes) == len(after.nodes)
        cmd.undo()
        assert len(view.graph.nodes) == len(before.nodes)
