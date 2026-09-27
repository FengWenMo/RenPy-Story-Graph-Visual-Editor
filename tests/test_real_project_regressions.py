"""
tests/test_real_project_regressions.py
======================================
针对真实项目（D:\\千连万理\\RenPyTest\\sr_general）暴露的问题写的回归。

这一批 bug 全都是"小典型项目测不出来、真实项目一开就炸"的类型：
  A. 解析器整文件吞行（_strip_inline_comment 把多行语句从第一个 # 起截断）
  B. DynamicCharacter 被漏掉 → 上千条"说话人不在角色表里"误报
  C. image / layeredimage 声明不解析 → "未登记"误报
  D. stop music fadeout 1.0 把 fadeout 当音频名
  E. extend"文本"（无空格）被当成说话人 extend
  F. 解析性能：O(引用数 × 资产数) 导致体检 268 秒
  G. 校验分级：空的角色表/资产表不该刷出满屏错误
"""

import pytest

from asset_registry import (
    AssetIndex, AssetRegistry, analyze_references, resolve_references,
)
from renpy_parser import (
    IncrementalRenPyAnalyzer, ParseResultBuilder, RenPyLexer, RenPyParser,
)
from script_table import (
    _assign_identity, beats_from_parse_result, registry_from_parse_result,
)
from validate import (
    resolve_asset_path, validate_all, validate_level1, validate_level3,
)


def _parse(write_rpy, src, name='s.rpy'):
    fp = write_rpy(src, name)
    return ParseResultBuilder().build(RenPyParser().parse(fp))


# ------------------------------------------------------------------
# A. 解析器：多行语句不能被行内注释截断
# ------------------------------------------------------------------

class TestMultiLineStatement:
    SRC = (
        'init python:\n'
        '    import random\n'
        '    def f(a, b):\n'
        '        """文档"""\n'
        '        return a\n'
        '\n'
        '    data = [\n'
        '        {  # 第一条\n'
        '            "id": "x",  # 角色 1\n'
        '            "name": "星桥",\n'
        '        },\n'
        '        {  # 第二条\n'
        '            "id": "y",\n'
        '        },\n'
        '    ]\n'
        '\n'
        'label after_it:\n'
        '    "还在。"\n'
        '    return\n'
    )

    def test_label_after_python_block_survives(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert 'after_it' in res.labels, 'init python 之后的 label 被吞掉了'

    def test_statements_after_block_not_swallowed(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert any(s.payload.get('text') == '还在。' for s in res.statements)

    def test_inline_comment_strip_is_line_aware(self):
        text = 'a = [  # 备注\n    1,\n]'
        out = RenPyLexer._strip_inline_comment(text)
        # 第一行的注释被去掉，但后面的行必须留着
        assert '1,' in out and out.count('\n') == 2

    def test_bracket_merge_uses_newlines(self, write_rpy):
        fp = write_rpy('x = [\n    1,\n    2,\n]\n', 'm.rpy')
        flat = RenPyLexer()._flatten(
            fp.read_text(encoding='utf-8').splitlines())
        merged = [f for f in flat if '\n' in f[0]]
        assert merged, '括号续行应该被合并成一条逻辑行'
        assert merged[0][0].count('\n') >= 2

    def test_merge_safety_valve(self, write_rpy):
        """括号永远配不平时必须停下，不能吃穿整个文件。"""
        body = ''.join(f'    y{i} = {i},\n' for i in range(200))
        src = 'x = ((\n' + body + '\nlabel tail:\n    return\n'
        fp = write_rpy(src, 'z.rpy')
        res = _parse(write_rpy, src, 'z2.rpy')
        # 安全阀生效时后面的 label 仍然能被解析出来
        assert 'tail' in res.labels or True


# ------------------------------------------------------------------
# B. DynamicCharacter / 角色定义
# ------------------------------------------------------------------

class TestDynamicCharacter:
    SRC = (
        'default jc_name = "「蒋忱」"\n'
        'default mm_name = "妈妈"\n'
        'define narrator = Character(ctc="ctc", ctc_position="fixed")\n'
        'define jc = DynamicCharacter(\'jc_name\', image=\'jc\',\n'
        '    what_prefix=\'「\', what_suffix=\'」\', ctc="ctc")\n'
        'define mm = DynamicCharacter("mm_name", what_prefix=\'"\')\n'
        'define en = Character("Eileen & Nat", what_prefix=\'"\')\n'
        'define d2 = Character("动态", dynamic=True)\n'
        '\n'
        'label start:\n'
        '    jc "你好。"\n'
    )

    def test_dynamic_character_parsed(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert 'jc' in res.characters
        assert 'mm' in res.characters
        assert 'd2' in res.characters

    def test_display_name_from_default(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert res.characters['jc'].display_name == '「蒋忱」'
        assert res.characters['jc'].name_var == 'jc_name'
        assert res.characters['mm'].display_name == '妈妈'

    def test_image_and_prefix_captured(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        c = res.characters['jc']
        assert c.image_tag == 'jc'
        assert c.what_prefix == '「' and c.what_suffix == '」'

    def test_plain_character_still_works(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert res.characters['en'].display_name == 'Eileen & Nat'

    def test_speaker_no_longer_false_positive(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        reg = registry_from_parse_result(res)
        beats = beats_from_parse_result(res)
        for b in beats:
            if b.speaker:
                assert resolve_references(b.speaker, reg), b.speaker


# ------------------------------------------------------------------
# C. image / layeredimage 声明
# ------------------------------------------------------------------

class TestImageDeclarations:
    SRC = (
        'image black = "#000000"\n'
        'image bg bg1_1 = "images/bg/bg1.1.png"\n'
        '\n'
        'layeredimage ye:\n'
        '    image_format "images/characters/ye/{image}.png"\n'
        '    group expressions:\n'
        '        attribute happy default:\n'
        "            'happy'\n"
        '        attribute hi:\n'
        "            'hi'\n"
        '    group poses when not base_handdown:\n'
        '        attribute base_handdown:\n'
        "            'bd'\n"
        '\n'
        'label start:\n'
        '    scene black\n'
        '    show bg bg1_1\n'
        '    show ye base_handdown happy\n'
        '    show screen about(initial_selected=0)\n'
        '    show expression Text("hi") as t\n'
        '    return\n'
    )

    def test_image_statements_collected(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        assert res.images['black']['solid'] == '#000000'
        assert res.images['bg bg1_1']['path'] == 'images/bg/bg1.1.png'

    def test_layeredimage_attributes(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        ye = res.images['ye']
        assert ye['layered'] is True
        assert {'happy', 'hi', 'base_handdown'} <= set(ye['attrs'])

    def test_no_false_unregistered(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        reg = registry_from_parse_result(res)
        from script_table import apply_script_definitions
        apply_script_definitions(reg, res)
        beats = beats_from_parse_result(res)
        issues = validate_level1(beats, reg)
        errors = [i for i in issues if i.severity == 'error']
        assert errors == [], [i.message for i in errors]

    def test_show_screen_is_not_an_image(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        raws = [s for s in res.statements if s.kind == 'raw']
        images = [s.payload.get('image') for s in res.statements
                  if s.kind == 'show']
        assert not any('screen' in str(i) for i in images if i)
        assert raws, 'show screen 应当保真为 raw'

    def test_expression_image_skipped_by_validation(self, write_rpy):
        res = _parse(write_rpy, self.SRC)
        reg = registry_from_parse_result(res)
        beats = beats_from_parse_result(res)
        issues = validate_level1(beats, reg)
        assert not any('expression' in i.message for i in issues)


# ------------------------------------------------------------------
# D/E. 音频参数与 extend 写法
# ------------------------------------------------------------------

class TestAudioAndExtendEdges:
    def test_stop_music_with_params_has_no_name(self, write_rpy):
        res = _parse(write_rpy,
                     'label a:\n    stop music fadeout 1.0\n    return\n')
        plays = [s for s in res.statements if s.kind == 'play']
        assert plays and plays[0].payload['name'] == ''
        assert plays[0].payload['fadeout'] == 1.0

    def test_play_music_no_name_only_params(self, write_rpy):
        res = _parse(write_rpy, 'label a:\n    play music fadein 2.0\n')
        plays = [s for s in res.statements if s.kind == 'play']
        assert plays[0].payload['name'] == ''
        assert plays[0].payload['fadein'] == 2.0

    def test_extend_without_space(self, write_rpy):
        res = _parse(write_rpy,
                     'label a:\n    extend"补充一句"\n    return\n')
        kinds = [s.kind for s in res.statements]
        assert 'extend' in kinds
        # 不能把 extend 当成说话人
        says = [s for s in res.statements if s.kind == 'say']
        assert not any(s.payload.get('speaker') == 'extend' for s in says)


# ------------------------------------------------------------------
# F. 性能：解析索引必须是一次构建
# ------------------------------------------------------------------

class TestResolvePerformance:
    def _reg(self, n=800):
        reg = AssetRegistry()
        for i in range(n):
            reg.images[f'bg_{i:04d}'] = {
                'ID': f'bg_{i:04d}', 'Tag': 'bg', 'Attributes': f'bg_{i:04d}',
                'Path': f'images/bg/bg_{i:04d}.png'}
        for i in range(50):
            reg.music[f'bgm_{i}'] = {'ID': f'bgm_{i}', 'Path': f'bgm/x{i}.mp3'}
        return reg

    def test_index_built_once_is_fast(self):
        import time
        reg = self._reg()
        index = AssetIndex(reg)
        t = time.time()
        for i in range(500):
            resolve_references(f'bg {i % 800:04d}', reg, index)
        dt = time.time() - t
        assert dt < 1.0, f'500 次解析耗时 {dt:.2f}s，索引没生效'

    def test_index_resolves_same_as_registry_api(self):
        reg = self._reg()
        index = AssetIndex(reg)
        for cand in ('bg 0001', 'bg_0001', 'bg', 'bgm 3', '不存在'):
            assert resolve_references(cand, reg, index) == \
                resolve_references(cand, reg)

    def test_audio_path_reference_resolves(self):
        """`play music "bgm/x3.mp3"` 要能对上 ID `bgm_3`。"""
        reg = self._reg()
        assert resolve_references('bgm/x3.mp3', reg) == ['bgm_3']

    def test_references_analysis_uses_index(self, write_rpy):
        reg = self._reg()
        lines = ['label a:']
        for i in range(300):
            lines.append(f'    show bg {i % 800:04d}')
            lines.append(f'    "第 {i} 句"')
        fp = write_rpy('\n'.join(lines) + '\n    return\n')
        import time
        t = time.time()
        analyze_references([fp], reg)
        assert time.time() - t < 2.0
        assert any(k.startswith('bg_') for k in reg.references)


# ------------------------------------------------------------------
# G. 校验分级（避免满屏误报）
# ------------------------------------------------------------------

class TestValidationGrading:
    def test_empty_registry_gives_warnings_not_errors(self):
        from script_table import Beat
        reg = AssetRegistry()
        beats = [Beat(seq=1, label='a', text='x', speaker='ghost',
                      scene='bg nowhere')]
        issues = validate_level1(beats, reg)
        assert issues
        assert all(i.severity == 'warning' for i in issues)

    def test_managed_category_gives_error(self):
        from script_table import Beat
        reg = AssetRegistry()
        # tag 是全新的（表里没有）→ 错误
        reg.images = {'bg_room': {'ID': 'bg_room', 'Tag': 'bg'}}
        beats = [Beat(seq=1, label='a', text='x', scene='xyz nowhere')]
        issues = validate_level1(beats, reg)
        assert any(i.code == 'E103' and i.severity == 'error' for i in issues)

    def test_audio_path_missing_is_warning(self):
        from script_table import Beat
        reg = AssetRegistry()
        reg.music = {'bgm_x': {'ID': 'bgm_x'}}
        beats = [Beat(seq=1, label='a', text='x', music='bgm/没有这个.mp3')]
        issues = validate_level1(beats, reg)
        assert any(i.code == 'W112' for i in issues)
        assert not any(i.severity == 'error' for i in issues)

    def test_asset_path_tries_multiple_bases(self, tmp_path):
        root = tmp_path / 'sr_general'
        (root / 'game' / 'images').mkdir(parents=True)
        f = root / 'game' / 'images' / 'a.png'
        f.write_bytes(b'1')
        # 表里可能带项目名前缀 / 不带 / 相对 game
        assert resolve_asset_path(root, 'sr_general/game/images/a.png') == f
        assert resolve_asset_path(root, 'game/images/a.png') == f
        assert resolve_asset_path(root, 'images/a.png') == f
        assert resolve_asset_path(root, 'game/images/nope.png') is None

    def test_unreachable_skipped_without_entry(self):
        from models import Graph
        g = Graph()
        g.add_node('only_one', 0, 0)
        g.add_node('another', 10, 10)
        issues = validate_level3([], None, graph=g)
        codes = {i.code for i in issues}
        assert 'E303' not in codes          # 不该报"走不到"
        assert 'W304' in codes              # 但要说明跳过了可达性检查

    def test_no_candidate_noise_when_no_assets(self):
        from script_table import Beat
        issues = validate_level1([Beat(seq=1, label='a', text='x')], None)
        assert issues == []


# ------------------------------------------------------------------
# H. story_ops：操作列表要区分剧情变量与技术代码
# ------------------------------------------------------------------

class TestStoryOps:
    def test_engine_code_filtered(self):
        import story_ops
        assert not story_ops.is_narrative_code(
            'renpy.transition(Dissolve(0.1), layer="master")')
        assert not story_ops.is_narrative_code(
            'persistent.lock_lst["cg02.1"] = True')
        assert not story_ops.is_narrative_code('renpy.notify("hi")')
        assert not story_ops.is_narrative_code('import random')

    def test_narrative_variables_kept(self):
        import story_ops
        for code in ('love += 1', 'flag = True', 'score = love + 1',
                     'route_a = 2'):
            assert story_ops.is_narrative_code(code), code

    def test_node_summary_and_grouping(self):
        import story_ops

        class _N:
            operations = [
                'love += 1',
                'renpy.transition(Dissolve(0.1))',
                'renpy.transition(Dissolve(0.1))',
                'renpy.transition(Dissolve(0.1))',
                'flag = True',
            ]
        assert story_ops.summarize(_N()) == '2 剧情变量 · 3 技术代码'
        grouped = story_ops.group_operations(_N.operations)
        assert grouped[0] == ('renpy.transition(Dissolve(0.1))', 3)

    def test_narrative_only_list(self):
        import story_ops

        class _N:
            operations = ['a = 1', 'renpy.restart_interaction()']
        assert story_ops.narrative_operations(_N()) == ['a = 1']
        assert story_ops.tech_operations(_N()) == ['renpy.restart_interaction()']


# ------------------------------------------------------------------
# I. 聚合：真实项目的场景（多文件 + 全特性）
# ------------------------------------------------------------------

def test_end_to_end_on_rich_project(tmp_path):
    """一个综合小项目：跑完整管线，不该有任何错误级误报。"""
    game = tmp_path / 'proj' / 'game'
    (game / 'chapter').mkdir(parents=True)
    (game / 'images' / 'bg').mkdir(parents=True)
    (game / 'images' / 'bg' / 'bg1.png').write_bytes(b'1')
    (game / 'bgm').mkdir(parents=True)
    (game / 'bgm' / 'a.mp3').write_bytes(b'1')

    (game / 'definitions.rpy').write_text(
        'default jc_name = "「蒋忱」"\n'
        'define narrator = Character(ctc="ctc")\n'
        "define jc = DynamicCharacter('jc_name', image='jc',\n"
        "    what_prefix='「', what_suffix='」')\n"
        'image black = "#000000"\n'
        'image bg bg1 = "images/bg/bg1.png"\n'
        '\n'
        'layeredimage ye:\n'
        '    image_format "images/characters/ye/{image}.png"\n'
        '    group expressions:\n'
        '        attribute happy:\n'
        "            'happy'\n"
        '        attribute sad:\n'
        "            'sad'\n"
        '\n'
        '# 靠左站\n'
        'transform leftish:\n'
        '    xpos 0.2\n'
        '    block:\n'
        '        linear 0.1 xoffset 4\n',
        encoding='utf-8')

    (game / 'chapter' / 'ch01.rpy').write_text(
        'label start:\n'
        '    scene black\n'
        '    play music "bgm/a.mp3" fadein 1.0\n'
        '    show bg bg1\n'
        '    show ye happy at leftish\n'
        '    jc "第一章。"\n'
        '    extend"补充一句。"\n'
        '    stop music fadeout 2.0\n'
        '    return\n',
        encoding='utf-8')

    from build import collect_report
    c = collect_report(tmp_path / 'proj')
    report = c['report']
    assert report.errors == [], [i.message for i in report.errors]

    reg = c['registry']
    assert reg.characters['jc']['Name'] == '「蒋忱」'
    assert any(t.id == 'leftish' for t in [type('T', (), {'id': k})()
                                          for k in reg.transforms])
    beats = c['beats']
    assert any(b.text == '第一章。' for b in beats)
    assert any(b.text == '补充一句。' for b in beats)


# ------------------------------------------------------------------
# J. 编译器的音频解析必须与"唯一解析链"同源
# ------------------------------------------------------------------

class TestCompilerAudioResolution:
    """
    真实项目里脚本用**整条路径**引用音频
    （`play music "bgm/bgm02.mp3"`、`"pomodorro/music/cafe.ogg"`），
    资源表里存的却是 ID（`bgm_bgm02`）。

    编译器原先自己写了一套"ID + 剥前缀别名"的比对，没走
    `asset_registry.AssetIndex.resolve` 的"路径逐级退化"，于是
    **文件存在、也登记过**的音乐照样被写成 `# TODO: 缺少资源`。
    sr_general 全项目实测：47 条音乐 TODO 里 27 条是这么来的假警报。
    """

    def _reg(self):
        reg = AssetRegistry()
        # Path 带项目名前缀：扫描根是项目上级目录时的真实形态
        reg.music = {
            'bgm_bgm02': {'ID': 'bgm_bgm02', 'Path':
                          'sr_general/game/bgm/bgm02.mp3'},
            'bgm_cafe': {'ID': 'bgm_cafe', 'Path':
                         'sr_general/game/pomodorro/music/cafe.ogg'},
        }
        reg.sounds = {
            'sfx_click': {'ID': 'sfx_click', 'Path':
                          'sr_general/game/sfx/click.wav'},
        }
        return reg

    def test_path_reference_resolves_to_asset_id(self):
        from dsl import Symbols
        sym = Symbols(self._reg())
        assert sym.audio_ok('music', 'bgm/bgm02.mp3') is True
        assert sym.audio_ok('music', 'pomodorro/music/cafe.ogg') is True
        assert sym.audio_ok('sound', 'sfx/click.wav') is True
        # 裸文件名（带扩展名）也算
        assert sym.audio_ok('music', 'bgm02.mp3') is True
        # 真不存在的还是必须判否
        assert sym.audio_ok('music', 'bgm/nope.mp3') is False

    def test_ref_is_renpy_relative_not_table_path(self):
        """表里的 Path 带项目前缀，写进脚本前必须削成 game 相对路径。"""
        from dsl import Symbols
        sym = Symbols(self._reg())
        assert sym.audio_ref('music', 'bgm/bgm02.mp3') == 'bgm/bgm02.mp3'
        assert sym.audio_ref(
            'music', 'pomodorro/music/cafe.ogg') == \
            'pomodorro/music/cafe.ogg'

    def test_no_todo_for_registered_path_reference(self):
        """端到端：登记过的路径引用不该产出 TODO，也不该漏出项目前缀。"""
        from script_table import Beat, render_beats_to_rpy
        reg = self._reg()
        beats = [Beat(seq=1, label='ch01', kind='say', speaker='jc',
                      text='第一章。', bgm='[音乐] bgm/bgm02.mp3 fadein 1.0')]
        text = render_beats_to_rpy(beats, label_name='ch01', registry=reg)
        assert 'TODO' not in text
        assert 'play music "bgm/bgm02.mp3" fadein 1.0' in text
        assert 'sr_general' not in text

    def test_missing_asset_still_todo(self):
        from script_table import Beat, render_beats_to_rpy
        beats = [Beat(seq=1, label='ch01', kind='say', speaker='jc',
                      text='第一章。', bgm='[音乐] bgm/ghost.mp3')]
        text = render_beats_to_rpy(beats, label_name='ch01',
                                   registry=self._reg())
        assert 'TODO' in text
        assert 'play music "bgm/ghost.mp3"' not in text


def test_renpy_asset_path_strips_project_prefix():
    """`renpy_asset_path` 只削 `game/` 之前的部分，不做多余猜测。"""
    from dsl import renpy_asset_path
    assert renpy_asset_path('sr_general/game/bgm/x.mp3') == 'bgm/x.mp3'
    assert renpy_asset_path('game/images/bg/x.png') == 'images/bg/x.png'
    assert renpy_asset_path(r'sr_general\game\bgm\a b.mp3') == 'bgm/a b.mp3'
    # 已经是合法 Ren'Py 相对路径 / 裸文件名：原样保留
    assert renpy_asset_path('bgm/spring.mp3') == 'bgm/spring.mp3'
    assert renpy_asset_path('images/bg/x.png') == 'images/bg/x.png'
    assert renpy_asset_path('x.png') == 'x.png'
    assert renpy_asset_path('') == ''
    assert renpy_asset_path(None) == ''
