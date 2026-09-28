"""
tests/test_roundtrip.py
=======================
端到端往返一致性测试（这是整个编辑器的价值主线）：

  .rpy ──解析──▶ Graph / ParseResult ──导出──▶ .rpy ──再解析──▶ Graph
  Excel 资产表 ──改 ID──▶ 引擎检测重命名 ──补丁──▶ .rpy 跟着改

这些用例一旦失败，意味着"对着表格编演出"这条链路断了。
"""

import openpyxl

from models import Graph
from renpy_parser import (
    RenPyParser, ParseResultBuilder, IncrementalRenPyAnalyzer,
)
from renpy_exporter import export_to_renpy
from asset_engine import AssetEngine
from asset_registry import write_asset_excel, scan_assets
from asset_extensions import AssetWorkspace


SCRIPT = (
    'define e = Character("Eileen", color="#c8ffc8")\n'
    'define m = Character("Mary")\n'
    '\n'
    'label start:\n'
    '    $ love = 0\n'
    '    e "第一条"\n'
    '    menu:\n'
    '        "选项A":\n'
    '            jump route_a\n'
    '        "选项B" if love > 1:\n'
    '            jump route_b\n'
    '\n'
    'label route_a:\n'
    '    $ love += 1\n'
    '    jump ending\n'
    '\n'
    'label route_b:\n'
    '    m "另一条"\n'
    '    jump ending\n'
    '\n'
    'label ending:\n'
    '    e "结束"\n'
    '    return\n'
)


class TestRpyRoundTrip:
    def _repeat(self, write_rpy, times=1):
        fp = write_rpy(SCRIPT, 'roundtrip.rpy')
        result = None
        for i in range(times):
            az = IncrementalRenPyAnalyzer()
            result = az.analyze([fp])
        return result

    def test_parsed_result_is_stable(self, write_rpy):
        """同一份脚本重复解析，结果必须完全一致（缓存不能污染输出）。"""
        r1 = self._repeat(write_rpy, 1)
        r2 = self._repeat(write_rpy, 1)
        assert len(r1[0].dialogues) == len(r2[0].dialogues)
        assert set(r1[0].labels) == set(r2[0].labels)
        assert len(r1[1].nodes) == len(r2[1].nodes)

    def test_characters_and_dialogues_consistent(self, write_rpy):
        result, graph = self._repeat(write_rpy)
        assert set(result.characters.keys()) == {'e', 'm'}
        assert result.characters['e'].dialogue_count == 2
        assert result.characters['m'].dialogue_count == 1

    def test_graph_edges_capture_menu(self, write_rpy):
        _, graph = self._repeat(write_rpy)
        options = {e.option_text for e in graph.edges.values() if e.option_text}
        assert options == {'选项A', '选项B'}

    def test_exported_script_is_reparsable(self, write_rpy, tmp_path):
        """导出的控制流脚本必须能被自己的解析器吃回去，且不报错。"""
        _, graph = self._repeat(write_rpy)
        out = tmp_path / 'control.rpy'
        export_to_renpy(graph, out)

        parser = RenPyParser()
        nodes = parser.parse(out)
        assert not parser.errors, parser.errors

        labels = {n.name for n in nodes if hasattr(n, 'name')}
        assert 'start_control' in labels
        assert 'route_a_control' in labels

    def test_exported_edges_match_original(self, write_rpy, tmp_path):
        """导出的控制流里，节点间的跳转关系与原图一致（去掉 _control 后缀）。"""
        _, graph = self._repeat(write_rpy)
        out = tmp_path / 'control.rpy'
        export_to_renpy(graph, out)

        reparsed = Graph()
        IncrementalRenPyAnalyzer().analyze([out], graph=reparsed)

        original = {(_strip_control(a.label_name), _strip_control(b.label_name))
                    for a, b in _edge_pairs(graph)}
        reparsed_pairs = {
            (_strip_control(a.label_name), _strip_control(b.label_name))
            for a, b in _edge_pairs(reparsed)
        }
        # 重新解析出来的边是原图的超集：每个 control label 还会 call
        # 一次对应的剧本 label（剥掉后缀后变成自环）
        self_loops = {(n, n) for n, _ in original} | {(n, n) for _, n in original}
        assert original <= reparsed_pairs
        assert reparsed_pairs - original <= self_loops

    def test_export_is_idempotent(self, write_rpy, tmp_path):
        """同样输入导出两次，文件内容一致（分析结果不引入随机性）。"""
        _, graph = self._repeat(write_rpy)
        out1 = tmp_path / 'c1.rpy'
        out2 = tmp_path / 'c2.rpy'
        export_to_renpy(graph, out1)
        export_to_renpy(graph, out2)
        assert out1.read_text(encoding='utf-8') == \
               out2.read_text(encoding='utf-8')

    def test_script_and_control_resolve_each_other(self, write_rpy, tmp_path):
        """
        剧本与控制流一起分析时不该有"未定义的跳转目标"：
        这只是把用户真实的使用方式（整个 game 目录）跑一遍。
        """
        script = write_rpy(SCRIPT, 'roundtrip.rpy')
        _, graph = self._repeat(write_rpy)
        out = tmp_path / 'control.rpy'
        export_to_renpy(graph, out)

        result = None
        az = IncrementalRenPyAnalyzer()
        result, combined = az.analyze([script, out], graph=Graph())
        assert not any('未定义的跳转目标' in w for w in result.warnings)
        label_names = {n.label_name for n in combined.nodes.values()}
        assert {'start_control', 'route_a_control'} <= label_names
        assert {'start', 'route_a'} <= label_names


def _strip_control(name: str) -> str:
    return name[:-len('_control')] if name.endswith('_control') else name


def _edge_pairs(graph):
    for e in graph.edges.values():
        src = graph.nodes.get(e.from_id)
        dst = graph.nodes.get(e.to_id)
        if src and dst:
            yield src, dst


class TestExcelRoundTrip:
    def test_script_characters_exported_to_excel_then_back(
            self, write_rpy, tmp_path):
        """
        脚本 → 角色行 → characters.xlsx → 重新解析 → 角色集合不变。
        """
        from character_script_linker import CharacterScriptLinker
        from asset_registry import write_dict_to_excel, parse_asset_tables

        result, _ = self._run(write_rpy)
        linker = CharacterScriptLinker(result)
        linker.link()
        rows = linker.export_to_character_rows()

        asset_dir = tmp_path / 'tables'
        data = {r['ID']: {k: v for k, v in r.items()
                          if not k.startswith('_')}
                for r in rows}
        write_dict_to_excel(asset_dir / 'characters.xlsx', data,
                            ['ID', 'Name', 'Color', 'WhatColor', 'ImageTag',
                             'SideImage', 'DefaultTransform', 'Notes'])

        registry = parse_asset_tables(asset_dir, project_root=tmp_path)
        assert set(registry.characters.keys()) == set(result.characters.keys())
        assert registry.characters['e']['Name'] == 'Eileen'
        assert registry.characters['e']['Color'] == '#c8ffc8'

    def test_rename_in_excel_patches_script(self, write_rpy, tmp_path):
        """
        用户在 Excel 里改角色变量名 → 引擎检测重命名 → 脚本自动跟着改。
        这是"双向联通"的核心承诺。
        """
        script = write_rpy(
            'define e = Character("Eileen")\n'
            'label start:\n'
            '    e "hi"\n'
            '    return\n', 'rename.rpy')

        asset_dir = tmp_path / 'tables'
        write_asset_excel(
            {'images': [],
             'audio': {'music': [], 'sound': []}},
            asset_dir)
        from asset_registry import write_dict_to_excel
        write_dict_to_excel(
            asset_dir / 'characters.xlsx',
            {'e': {'ID': 'e', 'Name': 'Eileen'}},
            ['ID', 'Name'])

        engine = AssetEngine(asset_dir=asset_dir, project_root=tmp_path,
                             script_files=[script], watch_source_dirs=False)
        engine._initial_load()
        assert 'e' in engine.registry.characters

        # 用户在 Excel 里把 e 改名为 eileen
        write_dict_to_excel(
            asset_dir / 'characters.xlsx',
            {'eileen': {'ID': 'eileen', 'Name': 'Eileen'}},
            ['ID', 'Name'])

        engine._mark_dirty(engine.TASK_TABLE)
        engine.poll()

        text = script.read_text(encoding='utf-8')
        assert 'eileen "hi"' in text
        assert engine.registry.characters.get('eileen') is not None

    def test_workspace_snapshot_tracks_excel_edit(self, write_rpy, tmp_path):
        """工作区能在两次快照之间发现手工编辑。"""
        asset_dir = tmp_path / 'tables'
        write_asset_excel({'images': [
            {'ID': 'bg_room', 'Path': 'images/bg/room.png', 'Tag': 'bg',
             'Attributes': 'room', 'Category': 'background', 'Notes': ''},
        ], 'audio': {'music': [], 'sound': []}}, asset_dir)

        ws = AssetWorkspace(asset_dir, tmp_path)
        ws.load()
        ws.snapshot(tag='before')

        wb = openpyxl.load_workbook(asset_dir / 'images.xlsx')
        ws_sheet = wb.active
        ws_sheet.cell(row=2, column=6, value='手工备注')
        wb.save(asset_dir / 'images.xlsx')

        ws.load()
        ws.snapshot(tag='after')
        changes = ws.diff_latest_two()
        assert any(c.change_type == 'modified' for c in changes)

    def test_scan_then_parse_roundtrip(self, tmp_path):
        """扫描真实素材生成表，再读回来，ID 不能漂移。"""
        root = tmp_path / 'proj'
        img = root / 'game' / 'images' / 'bg'
        img.mkdir(parents=True)
        (img / 'bg_school.png').write_bytes(b'1')

        asset_dir = tmp_path / 'tables'
        scan_result = scan_assets(root, include_ui=False)
        write_asset_excel(scan_result, asset_dir)

        from asset_registry import parse_asset_tables
        registry = parse_asset_tables(asset_dir, project_root=root)
        assert 'bg_school' in registry.images
        assert registry.images['bg_school']['Path'] == \
               'game/images/bg/bg_school.png'
        # 路径存在，不应产生"文件不存在"告警
        assert not registry.warnings

    def _run(self, write_rpy):
        fp = write_rpy(SCRIPT, 'roundtrip.rpy')
        return IncrementalRenPyAnalyzer().analyze([fp])


# ------------------------------------------------------------------
# 脚本保真：同一段 .rpy 解析后必须**逐行还原**回同样的代码
# ------------------------------------------------------------------

# 真实项目 sr_general 里那种"手机消息"演出：`$ f()` 与 show/pause 交替。
# 这是最容易出错的地方——raw 与 code 是两列，两者之间的顺序只有
# `Beat.ordered` 记着。
PHONE_SCRIPT = (
    'label chapter1_4:\n'
    '    # bg1.2\n'
    '    scene bg bg1_2\n'
    '    "第二天一早起来。"\n'
    '    play music "bgm/night.mp3" fadein 1.0\n'
    '    "5条新消息"\n'
    '    hide screen msg_dialogue\n'
    "    $ msg_add('L|叶怀瑾|hello')\n"
    '    show screen msg_dialogue\n'
    '    pause\n'
    '    hide screen msg_dialogue\n'
    "    $ msg_add('L|叶怀瑾|world')\n"
    '    show screen msg_dialogue\n'
    '    pause\n'
    '    return\n'
)


class TestScriptFidelity:
    """从 .rpy 解析出来的 Beat，导出时必须还原成同一段代码。"""

    def _roundtrip(self, write_rpy, src):
        from script_table import (beats_from_statements,
                                  render_beats_to_rpy)
        fp = write_rpy(src, 'src.rpy')
        pr = ParseResultBuilder().build(RenPyParser().parse(fp))
        beats = beats_from_statements(pr.statements,
                                      labels=pr.statement_labels)
        text = render_beats_to_rpy(beats, label_name='chapter1_4')
        return [l.rstrip() for l in text.splitlines()]

    def test_interleaved_code_and_raw_keep_source_order(self, write_rpy):
        """
        `$ msg_add(…)` 与 `show screen`/`pause`/`hide screen` 的**交替顺序**
        必须逐行还原。曾经 raw 与 code 被拼成"先全部 raw 再全部 code"，
        演出顺序整个错乱（真实项目 chapter1_4 就是这么被导坏的）。
        """
        out = self._roundtrip(write_rpy, PHONE_SCRIPT)
        seq = [l.strip() for l in out
               if l.strip() and not l.strip().startswith('#')
               and not l.strip().startswith('label ')
               and l.strip() != 'return']
        expect = [l.strip() for l in PHONE_SCRIPT.splitlines()
                  if l.strip() and not l.strip().startswith('#')
                  and not l.strip().startswith('label')
                  and l.strip() != 'return']
        assert seq == expect, '\n'.join(
            '%2d %-55s | %s' % (i, a, b)
            for i, (a, b) in enumerate(zip(seq, expect)))

    def test_ordered_survives_being_empty(self):
        """没有 ordered（表里读回来的 Beat）走历史行为，不报错。"""
        from script_table import Beat, compose_code
        b = Beat(raw='pause', code='x = 1')
        assert compose_code(b) == 'pause\nx = 1'

    def test_image_name_is_not_mangled(self, write_rpy):
        """
        `show ling base_arms_akimbo close_happy` 这种 `tag + 多属性` 写法
        必须原样保留。曾经因为资源表里没有这个组合就改写成
        `ling_base_arms_akimbo_close_happy`，把能跑的脚本改坏。
        """
        out = self._roundtrip(
            write_rpy,
            'label a:\n'
            '    show ling base_arms_akimbo close_happy\n'
            '    lyj "台词"\n'
            '    return\n')
        assert any('show ling base_arms_akimbo close_happy' in l
                   for l in out), out
        assert not any('ling_base_arms_akimbo' in l for l in out), out

    def test_scene_at_survives(self, write_rpy):
        """`scene bg bg9_5 at bgfit` 的 at 不能丢，也不能跑到立绘位置上。"""
        out = self._roundtrip(
            write_rpy,
            'label a:\n'
            '    scene bg bg9_5 at bgfit\n'
            '    show ling happy\n'
            '    "台词"\n'
            '    return\n')
        joined = '\n'.join(out)
        assert 'scene bg bg9_5 at bgfit' in joined, joined
        # 立绘不该被套上背景的变换
        assert 'show ling happy at bgfit' not in joined, joined

    def test_duplicate_show_not_emitted_twice(self):
        """
        `hydrate_from_dsl` 是"追加"语义，而 Stage 列往往已经写了结构化列的
        内容 → 同一项会被追加第二次。导出去就是两遍 `show`（重复执行转场）。
        """
        from script_table import (Beat, hydrate_from_dsl, render_beat_to_rpy,
                                  make_symbols)
        b = Beat(seq=1, label='a', text='x',
                 show='ling close_happy@i11',
                 stage='show ling close_happy at i11')
        hydrate_from_dsl([b], make_symbols(None))
        lines = [l.strip() for l in render_beat_to_rpy(b)]
        assert lines.count('show ling close_happy at i11') == 1, lines

    def test_legacy_migration_does_not_eat_codeblock(self):
        """
        当前表也有 Show/Hide/At/With 列，所以"是不是老表"不能用它们判定
        ——否则每一行新表都会走进老表迁移，`raw = g('Raw')` 把从 CodeBlock
        拆出来的 raw 行整批清空（真实项目 `show screen`/`pause` 全丢）。
        """
        from script_table import Beat, compose_code
        cell = 'pause\nx = 1\nshow screen msg_dialogue'
        b = Beat.from_row({'Label': 'a', 'Seq': 1, 'Text': 'y',
                           'CodeBlock': cell, 'Show': 'ling happy',
                           'Hide': '', 'At': '', 'With': ''})
        code = compose_code(b)
        assert 'pause' in code and 'show screen msg_dialogue' in code, code
        assert 'x = 1' in code

