"""
tests/test_transforms_and_preview.py
====================================
本轮（用户指定项 + P0）的回归：

  A. transform 解析        —— 名字/参数/ATL/前导注释与文档字符串
  B. transform 进注册表    —— 脚本里写的变换能在表格里选到
  C. 候选表说明列          —— 选中一个变换能看到它的注释解释
  D. 音频分类              —— 音乐目录下的曲子必须进 music（可选到）
  E. 角色定义解析加强      —— Character() 的其余参数不再丢
  F. P0 image tag ↔ ID     —— `show eileen happy` 不再让整组立绘误报未使用
  G. 引用抽取收敛          —— Statement 流 / Beat 表 走同一个解析器
  H. 资产说明与预览        —— describe_asset / asset_detail / 预览路径
"""

import pytest

from storygraph.assets.asset_registry import (
    AssetRegistry, scan_assets, write_asset_excel, describe_asset,
    resolve_reference, resolve_references, analyze_statement_references,
    references_from_statements,
)
from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
from storygraph.story.script_table import (
    Beat, export_beats_to_excel, find_unregistered_assets,
    open_rpy_as_table,
)


def _parse(write_rpy, src, name='a.rpy'):
    fp = write_rpy(src, name)
    nodes = RenPyParser().parse(fp)
    return ParseResultBuilder().build(nodes)


# ------------------------------------------------------------------
# A. transform 解析
# ------------------------------------------------------------------

class TestTransformParsing:
    SRC = (
        'define e = Character("Eileen")\n'
        '\n'
        '# 靠左站，双人对话时主角在左\n'
        'transform leftish:\n'
        '    xpos 0.2\n'
        '    xanchor 0.0\n'
        '\n'
        'transform shake_hard(t=0.05, n=6):\n'
        '    "剧烈抖动，挨打/惊吓用"\n'
        '    block:\n'
        '        linear t xoffset 8\n'
        '        linear t xoffset -8\n'
        '    repeat n\n'
        '\n'
        'transform no_doc:\n'
        '    alpha 0.0\n'
        '    linear 0.6 alpha 1.0\n'
    )

    def test_parsed_into_parse_result(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert set(res.transforms) == {'leftish', 'shake_hard', 'no_doc'}

    def test_params_captured(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert res.transforms['shake_hard'].params == 't=0.05, n=6'
        assert res.transforms['leftish'].params == ''

    def test_atl_body_keeps_relative_indent(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        lines = res.transforms['leftish'].body.split('\n')
        assert lines == ['    xpos 0.2', '    xanchor 0.0']

    def test_nested_block_indent(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        lines = res.transforms['shake_hard'].body.split('\n')
        assert '    block:' in lines
        assert '        linear t xoffset 8' in lines
        assert '    repeat n' in lines

    def test_doc_from_leading_comment(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert '靠左站' in res.transforms['leftish'].doc

    def test_doc_from_docstring(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert '剧烈抖动' in res.transforms['shake_hard'].doc

    def test_no_doc_is_empty(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert res.transforms['no_doc'].doc == ''

    def test_builtin_like_line_not_treated_as_transform(self, write_rpy):
        """`transform` 只是普通单词时不误判（没有 name）。"""
        res = _parse(write_rpy, self.SRC)
        assert 'transform' not in res.transforms

    def test_indented_comment_inside_block_kept_verbatim(self, write_rpy):
        """
        块体里的**带缩进整行注释**必须逐字保留（连同 `#`）。

        2026-09-28 真实项目事故（sr_general / game/transform.rpy 的 endcgshow2）：
        原文 `    #anchor(0.5, 0.5)` 在生成的 .rpy 里变成 `    hor(0.5, 0.5)` ——
        注释的 `#` 与开头字符被吃掉，**注释直接变成了非法 ATL 代码**
        （`hor` / `gn` 都不是 ATL 属性），产物跑不起来。

        根因：lexer 对整行注释存的是 `stripped`（已去缩进），而
        `_render_block_source` 按"text 含行首缩进"再切一次 → 缩进被扣两次。
        """
        src = (
            'transform endcgshow2:\n'
            '    perspective True\n'
            '    #anchor(0.5, 0.5)\n'
            '    #align (0, 0)\n'
            '    linear 2:\n'
            '        xzoom 0.1\n'
            '        #rotate 2\n'
            '        yrotate 40\n'
        )
        res = _parse(write_rpy, src)
        body = res.transforms['endcgshow2'].body
        assert '    #anchor(0.5, 0.5)' in body
        assert '    #align (0, 0)' in body
        assert '        #rotate 2' in body
        # 反向断言：不能只剩"被啃过"的残骸。
        # 注意不能用 `'hor(' not in body` —— `#anchor(` 里本身就含 `hor(`，
        # 要按"整行开头"判：被啃过的那行会**以 `hor(` 开头且没有 `#`**。
        bare = [ln.strip() for ln in body.split('\n')]
        assert not any(ln.startswith('hor(') for ln in bare)
        assert not any(ln.startswith('gn ') for ln in bare)


class TestTransformDefinitionFollowsScript:
    """
    脚本改了 transform 定义，资源表必须跟着变。

    2026-09-28 修：此前 `apply_script_definitions` 对"已登记的同名条目"
    一律"只补空缺"，于是**改了 .rpy、表格永远不更新**；更糟的是解析器历史上
    写坏的 ATL（`    #anchor(0.5, 0.5)` 被啃成 `    hor(0.5, 0.5)`）会
    **永久固化**在 transforms.xlsx 里 —— 解析器修好了，产物依旧是错的。

    现在 `ATL` / `Params` / `Doc` 以脚本为权威（值不同就更新），
    人写的 `Category` / `Notes` 一律不动。
    """

    SRC = (
        'transform endcgshow2:\n'
        '    perspective True\n'
        '    #anchor(0.5, 0.5)\n'
    )

    def test_stale_atl_is_updated_from_script(self, write_rpy):
        from storygraph.story.beat_build import apply_script_definitions

        reg = AssetRegistry()
        reg.transforms['endcgshow2'] = {
            'ID': 'endcgshow2', 'Path': '', 'ATL': '    hor(0.5, 0.5)',
            'Params': '', 'Doc': '', 'Category': '人工分类',
            'Notes': '人写的备注',
        }
        res = _parse(write_rpy, self.SRC)
        added = apply_script_definitions(reg, res)

        row = reg.transforms['endcgshow2']
        assert row['ATL'] == '    perspective True\n    #anchor(0.5, 0.5)'
        assert added['updated'] >= 1, '更新必须被计数（否则不会落盘）'
        # 人写的列不许被工具改
        assert row['Category'] == '人工分类'
        assert row['Notes'] == '人写的备注'

    def test_update_persists_to_table_with_whitelist(self, tmp_path):
        from storygraph.assets.asset_registry import (
            append_rows_to_table, parse_asset_tables)

        asset_dir = tmp_path / 'asset_tables'
        row = {'ID': 'endcgshow2', 'Path': '', 'ATL': '    hor(0.5, 0.5)',
               'Params': '', 'Doc': '', 'Category': 'custom',
               'Notes': '人写'}
        assert append_rows_to_table(asset_dir, 'transforms', [row]) == 1

        # 默认策略"只追加不覆盖"：已存在的 ID 跳过
        fresh = {**row, 'ATL': '    #anchor(0.5, 0.5)'}
        assert append_rows_to_table(asset_dir, 'transforms', [fresh]) == 0
        assert (parse_asset_tables(asset_dir).transforms['endcgshow2']['ATL']
                == '    hor(0.5, 0.5)')

        # 给 update_keys → 白名单列被更新，其它列不动
        assert append_rows_to_table(asset_dir, 'transforms', [fresh],
                                    update_keys=('ATL',)) == 1
        after = parse_asset_tables(asset_dir).transforms['endcgshow2']
        assert after['ATL'] == '    #anchor(0.5, 0.5)'
        assert after['Notes'] == '人写'


# ------------------------------------------------------------------
# B/C. transform 进注册表 + 候选表说明列
# ------------------------------------------------------------------

class TestTransformReachesTable:
    @pytest.fixture
    def registry(self, write_rpy):
        res = _parse(write_rpy, TestTransformParsing.SRC)
        reg = AssetRegistry()
        reg.images = {'bg_classroom': {'ID': 'bg_classroom', 'Path': 'x.png',
                                       'Tag': 'bg'}}
        reg.music = {'bgm_theme': {'ID': 'bgm_theme'}}
        reg.characters = {'e': {'ID': 'e', 'Name': 'Eileen'}}
        # 模拟 AssetEngine._sync_transform_definitions 的产物
        for name, tf in res.transforms.items():
            reg.transforms[name] = {
                'ID': name, 'ATL': tf.body, 'Params': tf.params,
                'Doc': tf.doc, 'Category': 'custom',
            }
        return reg

    def _sheet(self, registry, tmp_path):
        import openpyxl
        out = tmp_path / 's.xlsx'
        beats = [Beat(seq=1, label='a', text='hi', at='leftish')]
        export_beats_to_excel(beats, out, registry=registry)
        wb = openpyxl.load_workbook(out)
        return wb

    def test_transform_candidate_sheet_exists(self, registry, tmp_path):
        wb = self._sheet(registry, tmp_path)
        assert 'Candidates_变换' in wb.sheetnames

    def test_candidate_sheet_has_description(self, registry, tmp_path):
        wb = self._sheet(registry, tmp_path)
        ws = wb['Candidates_变换']
        rows = {ws.cell(row=r, column=1).value:
                ws.cell(row=r, column=2).value
                for r in range(3, ws.max_row + 1)}
        # 选中 leftish 时能读到它的注释解释
        assert '靠左站' in (rows.get('leftish') or '')
        assert rows.get('shake_hard')       # 参数/ATL 摘要非空

    def test_dropdown_uses_dynamic_named_range(self, registry, tmp_path):
        """
        下拉走**工作簿级动态名称**（二）：区间自伸缩，候选表里加一行
        Excel 立刻能选到，不必回头点"刷新候选"。

        能挂格级下拉的是"一格一个值"的列（说话人/场景/语音）。
        变换现在写在 Stage 文本里，但它的候选名称照样登记着，
        `词表` 里也能查到——严格性由编译器提供，不再靠拆列。
        """
        wb = self._sheet(registry, tmp_path)
        ws = wb['Beats']
        formulas = [dv.formula1 for dv in ws.data_validations.dataValidation]
        assert 'CAND_CHARACTER' in formulas

        dn = wb.defined_names['CAND_CHARACTER']
        text = dn.attr_text
        assert 'Candidates_角色' in text
        assert '$A$3' in text
        assert 'COUNTA' in text and 'OFFSET' in text

        # 变换的候选名称也还在（供 Stage 词表 / 未来扩展使用）
        tf = wb.defined_names['CAND_TRANSFORM'].attr_text
        assert 'Candidates_变换' in tf

    def test_character_sheet_shows_identity(self, registry, tmp_path):
        wb = self._sheet(registry, tmp_path)
        ws = wb['Candidates_角色']
        assert ws.cell(row=1, column=1).value          # 第 1 行是来源说明
        assert ws.cell(row=2, column=2).value == '说明'
        assert 'Eileen' in (ws.cell(row=3, column=2).value or '')

    def test_transform_doc_rendered_as_comment(self, registry, tmp_path):
        from storygraph.story.script_table import render_beats_to_rpy
        beats = [Beat(seq=1, label='a', text='hi', at='leftish')]
        text = render_beats_to_rpy(beats, registry=registry)
        assert '# 靠左站' in text
        assert 'transform leftish:' in text


# ------------------------------------------------------------------
# D. 音频分类：音乐必须能被选到
# ------------------------------------------------------------------

class TestAudioClassification:
    def _project(self, tmp_path, layout):
        root = tmp_path / 'proj'
        for rel in layout:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'x')
        (root / 'game').mkdir(parents=True, exist_ok=True)
        return root

    def test_music_dir_without_bgm_prefix_is_music(self, tmp_path):
        root = self._project(tmp_path, ['game/music/night.ogg'])
        assets = scan_assets(root, include_ui=False)
        ids = {a['ID'] for a in assets['audio']['music']}
        assert 'bgm_night' in ids

    def test_sound_dir_is_sound(self, tmp_path):
        root = self._project(tmp_path, ['game/sounds/click.ogg'])
        assets = scan_assets(root, include_ui=False)
        assert {a['ID'] for a in assets['audio']['sound']} == {'sfx_click'}

    def test_music_excel_written_and_loadable(self, tmp_path):
        root = self._project(tmp_path, ['game/music/night.ogg',
                                        'game/audio/night2.ogg'])
        out = tmp_path / 'tables'
        write_asset_excel(scan_assets(root, include_ui=False), out)
        from storygraph.assets.asset_registry import parse_asset_tables
        reg = parse_asset_tables(out, project_root=root)
        # 两条都进音乐（目录 music/ 语义 + 关键词兜底）
        assert 'bgm_night' in reg.music
        assert 'bgm_night2' in reg.music

    def test_voice_files_not_double_counted_as_sound(self, tmp_path):
        root = self._project(tmp_path,
                             ['game/audio/bgm_a.ogg',
                              'game/voice/v_01.ogg'])
        assets = scan_assets(root, include_ui=False)
        sound_ids = {a['ID'] for a in assets['audio']['sound']}
        assert 'sfx_v_01' not in sound_ids
        assert {a['ID'] for a in assets['voices']} == {'v_01'}


# ------------------------------------------------------------------
# E. 角色定义解析加强
# ------------------------------------------------------------------

class TestCharacterParsingRich:
    SRC = (
        'define ee = Character("Eileen", color="#c8ffc8", image="eileen",\n'
        '                      voice_tag="eileen", kind=adv,\n'
        '                      what_prefix="（", what_suffix="）",\n'
        '                      side_image="eileen_side")\n'
        '\n'
        'label start:\n'
        '    ee "你好。"\n'
    )

    def test_extra_fields_captured(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        c = res.characters['ee']
        assert c.display_name == 'Eileen'
        assert c.color == '#c8ffc8'
        assert c.image_tag == 'eileen'
        assert c.voice_tag == 'eileen'
        assert c.kind == 'adv'
        assert c.what_prefix == '（' and c.what_suffix == '）'
        assert c.side_image == 'eileen_side'
        assert c.dialogue_count == 1

    def test_engine_writes_rich_character_row(self, tmp_path, write_rpy):
        """_sync_character_definitions 落进 registry 的行要有新字段。"""
        from storygraph.assets.asset_engine import AssetEngine
        from storygraph.assets.asset_events import AssetEventBus, ConfirmationGate

        game = tmp_path / 'proj' / 'game'
        game.mkdir(parents=True)
        script = write_rpy(self.SRC, 's.rpy')
        (game / 's.rpy').write_text(script.read_text(encoding='utf-8'),
                                    encoding='utf-8')
        asset_dir = tmp_path / 'tables'
        asset_dir.mkdir()

        eng = AssetEngine(
            asset_dir=asset_dir, project_root=tmp_path / 'proj',
            script_files=[game / 's.rpy'], event_bus=AssetEventBus(),
            confirmation_gate=ConfirmationGate(ask_callback=lambda r: True),
            watch_source_dirs=False)
        eng._initial_load()
        reg = eng.get_registry()
        row = reg.characters['ee']
        assert row['Name'] == 'Eileen'
        assert row['VoiceTag'] == 'eileen'
        assert row['Kind'] == 'adv'
        assert row['WhatPrefix'] == '（'
        assert row['_from_script'] is True


# ------------------------------------------------------------------
# F. P0：image tag ↔ 资产 ID 映射
# ------------------------------------------------------------------

class TestImageTagIdMapping:
    def _reg(self):
        """一个真实的立绘目录结构：eileen/ 下多张表情。"""
        reg = AssetRegistry()
        reg.images = {
            'eileen_happy': {'ID': 'eileen_happy', 'Tag': 'eileen',
                             'Attributes': 'happy', 'Path': 'p1.png'},
            'eileen_happy_blush': {'ID': 'eileen_happy_blush',
                                   'Tag': 'eileen',
                                   'Attributes': 'happy_blush',
                                   'Path': 'p2.png'},
            'eileen_sad': {'ID': 'eileen_sad', 'Tag': 'eileen',
                           'Attributes': 'sad', 'Path': 'p3.png'},
            'bg_classroom': {'ID': 'bg_classroom', 'Tag': 'bg',
                             'Attributes': 'classroom', 'Path': 'p4.png'},
        }
        return reg

    def test_exact_sprite_resolves(self):
        assert resolve_references('eileen happy', self._reg()) \
            == ['eileen_happy']

    def test_prefix_covers_variants(self):
        """`show eileen happy` 覆盖 happy / happy_blush 两个变体。"""
        got = set(resolve_references('eileen happy blush', self._reg()))
        assert got == {'eileen_happy_blush'}

    def test_tag_only_maps_to_one_representative(self):
        got = resolve_references('eileen', self._reg())
        assert len(got) == 1

    def test_unknown_name_is_empty(self):
        assert resolve_references('mysterious', self._reg()) == []

    def test_single_resolve_backward_compatible(self):
        assert resolve_reference('bg classroom', self._reg()) == 'bg_classroom'

    def test_script_reference_marks_image_used(self, write_rpy):
        from storygraph.assets.asset_registry import analyze_references
        reg = self._reg()
        fp = write_rpy('label a:\n    show eileen happy\n')
        analyze_references([fp], reg)
        assert 'eileen_happy' in reg.references

    def test_no_whole_library_false_positive(self, tmp_path, write_rpy):
        """
        目录结构是 eileen/*.png、bg/*.png，脚本只 show 了少数几张；
        未使用清单里不该出现被 show 的那些。
        """
        reg = self._reg()
        fp = write_rpy('label a:\n'
                       '    scene bg classroom\n'
                       '    show eileen happy\n')
        from storygraph.assets.asset_registry import analyze_references
        analyze_references([fp], reg)
        unused = set(reg.images) - set(reg.references)
        assert unused == {'eileen_happy_blush', 'eileen_sad'}

    def test_engine_unused_not_all_images(self, tmp_path):
        """端到端：engine.find_unused_assets 不该把整库图片判成未使用。"""
        from storygraph.assets.asset_engine import AssetEngine
        from storygraph.assets.asset_events import AssetEventBus, ConfirmationGate

        project = tmp_path / 'proj'
        game = project / 'game'
        (game / 'images' / 'bg').mkdir(parents=True)
        (game / 'images' / 'eileen').mkdir(parents=True)
        for rel in ('images/bg/bg_room.png', 'images/eileen/happy.png',
                    'images/eileen/sad.png'):
            (game / rel).write_bytes(b'x')
        (game / 's.rpy').write_text(
            'label a:\n    scene bg room\n    show eileen happy\n',
            encoding='utf-8')
        asset_dir = tmp_path / 'tables'

        eng = AssetEngine(
            asset_dir=asset_dir, project_root=project,
            script_files=[game / 's.rpy'], event_bus=AssetEventBus(),
            confirmation_gate=ConfirmationGate(ask_callback=lambda r: True),
            watch_source_dirs=False)
        eng._initial_load()
        unused_images = {u['asset_id'] for u in eng.find_unused_assets()
                         if u['category'] == 'images'}
        assert 'bg_room' not in unused_images
        assert 'happy' not in unused_images

    def test_validate_imagetag_uses_tag_index(self, tmp_path):
        reg = self._reg()
        reg.characters = {'e': {'ID': 'e', 'ImageTag': 'eileen'}}
        from storygraph.assets.asset_registry import validate_registry
        validate_registry(reg, tmp_path)
        assert not any('ImageTag' in w for w in reg.warnings)


# ------------------------------------------------------------------
# G. 引用抽取收敛（Statement 流 = 统一真源）
# ------------------------------------------------------------------

class TestReferenceExtractionConverged:
    def test_statement_stream_extracts_images_and_audio(self, write_rpy):
        res = _parse(write_rpy,
                     'label a:\n'
                     '    scene bg classroom\n'
                     '    show eileen happy at leftish\n'
                     '    play music bgm_theme\n'
                     '    voice v_001\n')
        kinds = {k for k, _raw, _c, _loc in
                 references_from_statements(res.statements)}
        assert {'image', 'transform', 'music', 'voice'} <= kinds

    def test_statement_analysis_fills_references(self, write_rpy):
        reg = TestImageTagIdMapping()._reg()
        reg.music = {'bgm_theme': {}}
        res = _parse(write_rpy,
                     'label a:\n'
                     '    show eileen happy\n'
                     '    play music bgm_theme\n')
        analyze_statement_references(res.statements, reg)
        assert 'eileen_happy' in reg.references
        assert 'bgm_theme' in reg.references

    def test_unregistered_uses_shared_resolver(self):
        """`show eileen happy` 在资源表里以 Tag/Attributes 登记时不算未登记。"""
        reg = TestImageTagIdMapping()._reg()
        beats = [Beat(seq=1, label='a', text='x', show='eileen happy')]
        assert find_unregistered_assets(beats, reg) == []

    def test_unregistered_still_flags_missing(self):
        reg = AssetRegistry()
        beats = [Beat(seq=1, label='a', text='x', show='nobody_here')]
        found = find_unregistered_assets(beats, reg)
        assert ('image', 'nobody_here') in found


class TestScriptSelfRegistry:
    """没打开项目时，也要能从脚本自身造出候选（变换/角色可选）。"""

    SRC = (
        '# 靠左站\n'
        'transform leftish:\n'
        '    xpos 0.2\n'
        '\n'
        'define e = Character("Eileen", image="eileen")\n'
        '\n'
        'label ch:\n'
        '    show eileen happy at leftish\n'
        '    e "你好。"\n'
    )

    def test_registry_from_parse_result(self, write_rpy):
        from storygraph.story.script_table import registry_from_parse_result
        res = _parse(write_rpy, self.SRC)
        reg = registry_from_parse_result(res)
        assert 'leftish' in reg.transforms
        assert reg.transforms['leftish']['Doc'].startswith('靠左站')
        assert reg.transforms['leftish']['ATL'].strip() == 'xpos 0.2'
        assert reg.characters['e']['Name'] == 'Eileen'

    def test_open_rpy_as_table_without_registry_has_candidates(
            self, tmp_path, write_rpy):
        import openpyxl
        script = write_rpy(self.SRC, 'ch.rpy')
        out = open_rpy_as_table(script, tmp_path / 'ch.xlsx')   # 无 registry
        wb = openpyxl.load_workbook(out)
        assert 'Candidates_变换' in wb.sheetnames
        ws = wb['Candidates_变换']
        ids = [ws.cell(row=r, column=1).value
               for r in range(3, ws.max_row + 1)]
        assert 'leftish' in ids


class TestAudioAliases:
    """扫描给音频加了 bgm_/sfx_ 前缀，脚本里却不带前缀。"""

    def _reg(self):
        reg = AssetRegistry()
        reg.music = {'bgm_night': {'ID': 'bgm_night',
                                   'Path': 'game/music/night.ogg'}}
        reg.sounds = {'sfx_door': {'ID': 'sfx_door',
                                   'Path': 'game/sounds/door.ogg'}}
        reg.voices = {'v_eileen_01': {'ID': 'v_eileen_01',
                                      'Path': 'game/voice/v_eileen_01.ogg'}}
        return reg

    def test_music_stem_resolves(self):
        assert resolve_references('night', self._reg()) == ['bgm_night']

    def test_sfx_stem_resolves(self):
        assert resolve_references('door', self._reg()) == ['sfx_door']

    def test_exact_id_still_resolves(self):
        assert resolve_references('bgm_night', self._reg()) == ['bgm_night']

    def test_voice_exact_resolves(self):
        assert resolve_references('v_eileen_01', self._reg()) \
            == ['v_eileen_01']

    def test_audio_not_reported_unused(self, write_rpy):
        from storygraph.assets.asset_registry import analyze_references
        reg = self._reg()
        fp = write_rpy('label a:\n'
                       '    play music night\n'
                       '    play sound door\n'
                       '    voice v_eileen_01\n')
        analyze_references([fp], reg)
        assert 'bgm_night' in reg.references
        assert 'sfx_door' in reg.references
        assert 'v_eileen_01' in reg.references


# ------------------------------------------------------------------
# H. 资产说明 / 预览
# ------------------------------------------------------------------

class TestDescriptionAndPreview:
    def test_describe_transform_uses_doc(self):
        text = describe_asset('transform', {
            'ID': 'leftish', 'Doc': '靠左站', 'Params': '',
            'ATL': 'xpos 0.2'})
        assert '靠左站' in text

    def test_describe_transform_falls_back_to_atl(self):
        text = describe_asset('transform', {'ID': 't', 'ATL': 'xpos 0.2'})
        assert 'xpos 0.2' in text

    def test_describe_character_lists_identity(self):
        text = describe_asset('character', {
            'ID': 'e', 'Name': 'Eileen', 'Color': '#c8ffc8',
            'ImageTag': 'eileen', '_dialogue_count': 7})
        assert 'Eileen' in text and 'eileen' in text and '7' in text

    def test_describe_image_lists_category_and_path(self):
        text = describe_asset('image', {
            'ID': 'bg_room', 'Category': 'background',
            'Tag': 'bg', 'Path': 'images/bg/bg_room.png'})
        assert '背景' in text and 'bg_room.png' in text

    def test_asset_detail_includes_transform_atl(self, write_rpy):
        from storygraph.app.beats import asset_detail

        class _Engine:
            def __init__(self, reg):
                self._reg = reg

            def get_registry(self):
                return self._reg

        reg = AssetRegistry()
        reg.transforms = {'leftish': {
            'ID': 'leftish', 'Doc': '靠左站', 'ATL': 'xpos 0.2\nypos 0.1'}}
        detail = asset_detail(_Engine(reg), 'transform', 'leftish')
        assert '靠左站' in detail
        assert 'ATL' in detail

    def test_preview_path_resolution(self, tmp_path):
        from storygraph.app.beats import asset_preview_path

        class _Engine:
            def __init__(self, reg, root):
                self._reg = reg
                self.project_root = root

            def get_registry(self):
                return self._reg

        img = tmp_path / 'images' / 'a.png'
        img.parent.mkdir(parents=True)
        img.write_bytes(b'\x89PNG\r\n\x1a\n')
        reg = AssetRegistry()
        reg.images = {'a': {'ID': 'a', 'Path': 'images/a.png'}}
        eng = _Engine(reg, tmp_path)
        assert asset_preview_path(eng, 'image', 'a') == img
        assert asset_preview_path(eng, 'image', 'nope') is None
        assert asset_preview_path(eng, 'music', 'a') is None

    def test_thumbnail_decoding_is_graceful(self, tmp_path):
        """
        坏图 / 缺图都不能抛异常，最多返回 None。

        2026-10-01：原来测的是 tkinter 的 `load_thumbnail`（PIL → PhotoImage）。
        tkinter 删除后，对应的内核是 `app.thumbs.decode_sync`（PIL Image，
        框架无关）—— 它才是"两个前端共用一份解码"的那个真源。
        """
        from storygraph.app.thumbs import decode_sync
        bad = tmp_path / 'bad.png'
        bad.write_bytes(b'not an image')
        assert decode_sync(bad, (32, 32)) is None
        assert decode_sync(tmp_path / 'missing.png', (32, 32)) is None


# ------------------------------------------------------------------
# I. UI 入口（Excel 打开移到节点右键）
# ------------------------------------------------------------------

class TestUiEntryPoints:
    def test_node_has_an_excel_entry(self):
        """
        右键节点必须能"用 Excel 打开这一章的演出表" ——
        tkinter 版有，删 tkinter 时**不能弄丢**。

        逻辑现在住在 `app.tables`（框架无关），Qt 窗口只挑路径 + 展示结果。
        """
        from storygraph.app import tables
        assert callable(tables.generate_table)
        from storygraph.ui_qt.main_window import MainWindowQt
        assert hasattr(MainWindowQt, 'open_node_in_excel')

    def test_menu_label_present_in_source(self):
        # 2026-10-05：菜单文案搬到了 `window_nodes.py`（右键菜单那一块），
        # 所以要扫**整个前端**（入口 + 5 个 mixin），不能只读入口文件。
        from _source_paths import main_window_source_text
        text = main_window_source_text()
        assert '用 Excel 打开此章节' in text
        # 旧版还有「在编辑器中打开」（上一版漏了它）
        assert '在编辑器中打开' in text
        # 旧版的主入口叫「改本章台词（卡片）」—— 名字保持原样
        assert '改本章台词（卡片）' in text
        # 没有源文件的块要置灰（而不是点完再弹一句"未关联源文件"）：
        # 判据看**运行时**（`test_ui_qt.py::TestNodeMenu`），这里只确认
        # "有一组动作会被一起禁用"这段逻辑还在。
        assert 'setEnabled(False)' in text


# ------------------------------------------------------------------
# J. 端到端：一个真实章节过一遍新管线
# ------------------------------------------------------------------

class TestEndToEnd:
    SCRIPT = (
        'define e = Character("Eileen", color="#c8ffc8", image="eileen")\n'
        '\n'
        '# 靠左站\n'
        'transform leftish:\n'
        '    xpos 0.2\n'
        '\n'
        'label ch01:\n'
        '    scene bg classroom\n'
        '    play music bgm_theme\n'
        '    show eileen happy at leftish\n'
        '    e "早上好。"\n'
        '    return\n'
    )

    def test_open_rpy_as_table_with_transforms(self, tmp_path, write_rpy):
        import openpyxl
        res = _parse(write_rpy, self.SCRIPT, 'ch01.rpy')
        reg = AssetRegistry()
        reg.images = {'bg_classroom': {'ID': 'bg_classroom', 'Tag': 'bg',
                                       'Attributes': 'classroom'},
                      'eileen_happy': {'ID': 'eileen_happy', 'Tag': 'eileen',
                                       'Attributes': 'happy'}}
        reg.music = {'bgm_theme': {'ID': 'bgm_theme'}}
        reg.characters = {'e': {'ID': 'e', 'Name': 'Eileen'}}
        for name, tf in res.transforms.items():
            reg.transforms[name] = {'ID': name, 'ATL': tf.body,
                                    'Doc': tf.doc}

        script = tmp_path / 'ch01.rpy'
        script.write_text(self.SCRIPT, encoding='utf-8')
        out = open_rpy_as_table(script, tmp_path / 'ch01.xlsx', registry=reg)
        wb = openpyxl.load_workbook(out)
        assert 'Candidates_变换' in wb.sheetnames
        ws = wb['Beats']
        headers = [c.value for c in ws[1]]
        rows = [dict(zip(headers, r)) for r in
                ws.iter_rows(min_row=2, values_only=True)]
        texts = [r['台词'] for r in rows]
        assert '早上好。' in texts
        # 未登记资产一个都不该报
        from storygraph.story.script_table import import_beats_from_excel
        assert find_unregistered_assets(
            import_beats_from_excel(out), reg) == []

class TestBackgroundResolutionFallsBackToDisk:
    """
    用户 2026-10-05：

    > 还有，这个演出预览，背景没有显示出来啊……

    根因：
esolve_stage_asset 在**资源表里查不到** tag/属性时**直接 return None**
    —— 后面那句有界磁盘回退（image_index_lookup）成了**死代码**。
    真实项目的背景图根本没登记进 images.xlsx（g bg1_2 一条都搜不到），
    而 game/images/bg/bg1.2.png 就在磁盘上 → 预览里背景永远空白
    （立绘却正常，因为它们登记在表里）。
    """

    def test_table_miss_still_tries_the_disk_index(self, monkeypatch):
        """
        资源表里**查不到**时，也必须走磁盘回退（这正是曾经的死代码）。

        真实项目实测：g bg1_2 在 images.xlsx 里一条都搜不到，而
        game/images/bg/bg1.2.png 在磁盘上 —— 修前返回 None（预览背景空白），
        修后返回真实路径。
        """
        from storygraph.app import stage as S
        seen = []

        def fake_lookup(spec, index):
            seen.append(spec)
            return 'FOUND'

        monkeypatch.setattr(S, 'image_index_lookup', fake_lookup)
        # images 不能是空字典：空表会走'没有资源表'的早退分支
        images = {'other': {'ID': 'other', 'Tag': 'nobody',
                           'Attributes': 'nobody', 'Path': None}}
        hit = S.resolve_stage_asset('bg bg1_2', images, None, {})
        assert seen == ['bg bg1_2'], '表里查不到时没有尝试磁盘回退（死代码又回来了）'
        assert hit == 'FOUND'

    def test_table_hit_still_wins(self, tmp_path):
        """表里查得到时仍走表（磁盘回退只是兜底）。"""
        from storygraph.app.stage import resolve_stage_asset
        real = tmp_path / 'game' / 'images' / 'characters' / 'ling'
        real.mkdir(parents=True)
        (real / 'happy.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        images = {'_happy': {'ID': '_happy', 'Tag': 'ling',
                             'Attributes': 'happy',
                             'Path': 'game/images/characters/ling/happy.png'}}
        hit = resolve_stage_asset('ling happy', images, tmp_path)
        assert hit is not None and str(hit).endswith('happy.png'), hit

class TestRegisterMissingAssets:
    """
    用户 2026-10-05：

    > 先查背景图为何没有被登记进去，然后，让背景图被登记进去。

    实测真实项目：game/images/bg 下 39 张、表里只认到 30 张，而且表里是
    下划线名（g_1）而磁盘是点号名（g1.2.png）—— 脚本里的 g bg1_2
    在表里查不到（演出预览背景只能靠磁盘回退兜着）。补登 63 条后表内直接可解析。
    """

    def _project(self, tmp_path):
        import openpyxl
        game = tmp_path / 'game'
        (game / 'images' / 'bg').mkdir(parents=True)
        (game / 'images' / 'bg' / 'bg1.2.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (game / 'images' / 'bg' / 'bg1.3.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        tables = game / 'asset_tables'
        tables.mkdir(parents=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['ID', 'Path', 'Tag', 'Attributes', 'Category', 'Notes'])
        ws.append(['bg_1', 'images/bg_1.jpg', 'bg', 'bg_1', 'background', ''])
        wb.save(tables / 'images.xlsx')
        return tmp_path

    def test_appends_only_missing_rows_and_backs_up(self, tmp_path):
        from storygraph.assets.asset_registry import (read_excel_rows,
                                                      register_missing_assets)
        root = self._project(tmp_path)
        tables = root / 'game' / 'asset_tables'
        before = read_excel_rows(tables / 'images.xlsx')
        res = register_missing_assets(root, asset_dir=tables)
        assert res['ok'], res
        assert res['added'] >= 2, res           # bg1.2 / bg1.3
        assert (tables / 'images.bak.xlsx').is_file(), '写前必须备份'
        after = read_excel_rows(tables / 'images.xlsx')
        # 已登记的行一个字都不许动
        assert [r for r in after if r.get('ID') == 'bg_1'] == \
            [r for r in before if r.get('ID') == 'bg_1']
        ids = {r.get('ID') for r in after}
        assert 'bg1_2' in ids, f'点号名没有归一成脚本用的下划线名：{sorted(ids)[:6]}'

    def test_is_idempotent(self, tmp_path):
        from storygraph.assets.asset_registry import (read_excel_rows,
                                                      register_missing_assets)
        root = self._project(tmp_path)
        tables = root / 'game' / 'asset_tables'
        register_missing_assets(root, asset_dir=tables)
        n1 = len(read_excel_rows(tables / 'images.xlsx'))
        res2 = register_missing_assets(root, asset_dir=tables)
        assert res2['ok'] and res2['added'] == 0, res2
        assert len(read_excel_rows(tables / 'images.xlsx')) == n1
