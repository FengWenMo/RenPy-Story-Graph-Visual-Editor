"""
DSL 与编译流水线的测试（dsl.py / compile.py）。

这一层是整个重构的核心：**数据层说人话，中间层担复杂度，表现层才干净**。
所以这里锁的不是"函数能不能跑"，而是几条产品级承诺：

  1. `[显示] 凌跃捷 开心 左` 必须翻成 `show ling close_happy at left`
     —— 中文词 → 资产（靠符号表 + 模糊匹配），策划不需要记英文 ID
  2. 原生写法（`show ling happy`）必须**原样直通**，不解析不改写
     —— 程序/老策划的逃生舱口，也是不把能跑的脚本改坏的底线
  3. 找不到资源**不许报错、不许中断**，只在脚本里留 `# TODO: 缺少资源 …`
     —— 1073 条红字没人看，贴位置的 TODO 一定会被改
  4. 某一类资产**压根没建表**时一律放行 —— 否则新项目一编译满屏 TODO
  5. 编译结果表与生成的脚本**同源** —— 不允许"另写一套逻辑再对一遍"
"""

import openpyxl
import pytest

from storygraph.assets.asset_registry import AssetRegistry
from storygraph.story.dsl import (Audit, Symbols, compile_audio, compile_stage,
                 normalize_text, parse_audio, parse_stage,
                 stage_word_if_lossless)
from storygraph.story.script_table import (
    BEAT_FIELDS, BEAT_HEADERS, DROPDOWN_COLUMNS, HIDDEN_BEAT_COLUMNS, Beat,
    compose_code, compose_stage,
    export_beats_to_excel, import_beats_from_excel, make_symbols,
    render_beats_to_rpy, render_beat_to_rpy,
)

# ------------------------------------------------------------------
#  夹具
# ------------------------------------------------------------------

@pytest.fixture
def registry():
    """一个小而真实的资源表：角色显示名是中文、立绘是 tag+attribute。"""
    reg = AssetRegistry()
    reg.characters = {
        'lyj': {'ID': 'lyj', 'Name': '「凌跃捷」', 'ImageTag': 'ling'},
        'jc': {'ID': 'jc', 'Name': '「蒋忱」', 'ImageTag': 'jc'},
    }
    reg.images = {
        'ling_happy': {'ID': 'ling_happy', 'Tag': 'ling',
                       'Attributes': 'close_happy'},
        'bg_lab': {'ID': 'bg_lab', 'Tag': 'bg', 'Attributes': 'lab'},
    }
    reg.music = {'spring': {'ID': 'spring', 'Path': 'bgm/spring.mp3'}}
    reg.sounds = {'door': {'ID': 'door', 'Path': 'sfx/door.wav'}}
    reg.voices = {'v_001': {'ID': 'v_001', 'Path': 'voice/v_001.ogg'}}
    return reg


@pytest.fixture
def symbols(registry):
    return make_symbols(registry)


@pytest.fixture
def audit(symbols):
    return Audit(symbols)


# ------------------------------------------------------------------
#  一、解析：人话 → 中间表示
# ------------------------------------------------------------------

class TestParse:
    def test_bracket_show(self):
        ops = parse_stage('[显示] 凌跃捷 开心 左').ops
        assert len(ops) == 1
        assert ops[0].verb == 'show' and not ops[0].native
        assert ops[0].target == '凌跃捷'
        assert ops[0].attrs == ['开心', '左']

    def test_multiple_ops_split_by_semicolon(self):
        ops = parse_stage('[显示] 凌跃捷 开心 左 ; [转场] dissolve').ops
        assert [o.verb for o in ops] == ['show', 'with']

    def test_at_with_keywords(self):
        op = parse_stage('[显示] 凌跃捷 开心 at l11 with dissolve').ops[0]
        assert op.at == 'l11'
        assert op.with_ == 'dissolve'

    def test_native_passthrough_is_marked(self):
        op = parse_stage('show ling close_happy at left').ops[0]
        assert op.native is True
        assert op.raw == 'show ling close_happy at left'

    def test_unknown_verb_is_flagged_not_crashed(self):
        parsed = parse_stage('[跳舞] 凌跃捷')
        assert parsed.ops[0].verb == 'unknown'
        assert parsed.problems

    def test_audio_stop_and_params(self):
        assert parse_audio('[停止音乐]', 'music')[0].action == 'stop'
        op = parse_audio('[音效] door', 'sound')[0]
        assert (op.name, op.channel) == ('door', 'sound')
        op = parse_audio('[音乐] spring fadein 1.0', 'music')[0]
        assert op.name == 'spring' and 'fadein 1.0' in op.params

    def test_queue_written_as_suffix(self):
        assert parse_audio('[音效(queue)] door', 'sound')[0].action == 'queue'

    def test_normalize_keeps_chinese_comma_in_paths(self):
        """
        `，` 是素材文件名里的**内容**，归一化成半角会把路径改坏。
        这是踩过的坑：生成的脚本引用了一个不存在的音频文件。
        """
        assert normalize_text('bgm/夜晚，断电之后….mp3') == \
            'bgm/夜晚，断电之后….mp3'
        assert normalize_text('[显示] 左；[转场] dissolve') == \
            '[显示] 左;[转场] dissolve'


# ------------------------------------------------------------------
#  二、编译：人话 → Ren'Py
# ------------------------------------------------------------------

class TestCompileStage:
    def test_chinese_words_become_renpy(self, symbols, audit):
        rows = compile_stage('[显示] 凌跃捷 开心 左', symbols, audit)
        assert rows == [('[显示] 凌跃捷 开心 左',
                         'show ling close_happy at left', 'ok')]

    def test_fuzzy_attribute_match(self, symbols):
        # 词表只给语义核心词 happy，属性名是 close_happy —— 必须能对上
        assert symbols.attr_for('ling', '开心') == 'close_happy'

    def test_with_transition_kept(self, symbols, audit):
        _i, code, _s = compile_stage('[显示] 凌跃捷 开心 左 with dissolve',
                                     symbols, audit)[0]
        assert code == 'show ling close_happy at left with dissolve'

    def test_native_line_untouched(self, symbols, audit):
        raw = 'show ling close_happy at left'
        assert compile_stage(raw, symbols, audit)[0][1] == raw

    def test_hide(self, symbols, audit):
        assert compile_stage('[隐藏] 凌跃捷', symbols, audit)[0][1] == 'hide ling'

    def test_scene_and_with(self, symbols, audit):
        assert compile_stage('[转场] dissolve', symbols,
                             audit)[0][1] == 'with dissolve'
        assert compile_stage('[等待] 1.0', symbols, audit)[0][1] == 'pause 1.0'

    def test_missing_asset_becomes_todo_comment(self, symbols, audit):
        """承诺 3：不报错、不中断，只留一行 TODO。"""
        _i, code, status = compile_stage('[显示] 不存在的人 开心',
                                         symbols, audit)[0]
        assert status == 'todo'
        assert code.startswith('# TODO: 缺少资源 角色')
        assert 'show' not in code          # 不生成非法语句

    def test_missing_image_becomes_todo(self, symbols, audit):
        _i, code, status = compile_stage('[场景] bg nowhere',
                                         symbols, audit)[0]
        assert status == 'todo' and '# TODO' in code

    def test_unknown_verb_becomes_todo(self, symbols, audit):
        _i, code, status = compile_stage('[跳舞] 凌跃捷', symbols, audit)[0]
        assert status == 'todo' and '无法识别' in code


class TestCompileAudio:
    def test_music_uses_real_path(self, symbols, audit):
        _i, code, _s = compile_audio('[音乐] spring fadein 1.0', 'music',
                                     symbols, audit)[0]
        assert code == 'play music "bgm/spring.mp3" fadein 1.0'

    def test_stop(self, symbols, audit):
        assert compile_audio('[停止音乐]', 'music', symbols,
                            audit)[0][1] == 'stop music'

    def test_queue(self, symbols, audit):
        _i, code, _s = compile_audio('[音效(queue)] door', 'sound',
                                     symbols, audit)[0]
        assert code == 'queue sound "sfx/door.wav"'

    def test_missing_audio_becomes_todo(self, symbols, audit):
        _i, code, status = compile_audio('[音乐] 不存在的曲子', 'music',
                                        symbols, audit)[0]
        assert status == 'todo' and code.startswith('# TODO: 缺少资源 音乐')


# ------------------------------------------------------------------
#  三、承诺 4：没建表就放行，不许满屏 TODO
# ------------------------------------------------------------------

class TestNoTableNoNoise:
    def test_empty_registry_passes_everything(self):
        symbols = make_symbols(AssetRegistry())
        audit = Audit(symbols)
        _i, code, status = compile_stage('[显示] 随便谁 开心 左',
                                         symbols, audit)[0]
        assert status == 'pass'
        assert not code.startswith('# TODO')

    def test_missing_character_table_does_not_blame_character(self):
        """
        连 characters 表都没有的新项目：不该瞎报"缺少资源 角色"（无从判断）。
        这类项目往往只有图片表，那就按图片去核对——那是真的对不上。
        """
        reg = AssetRegistry()
        reg.images = {'bg_lab': {'ID': 'bg_lab', 'Tag': 'bg'}}
        symbols = make_symbols(reg)
        _i, code, _status = compile_stage('[显示] 谁 开心', symbols,
                                          Audit(symbols))[0]
        assert '缺少资源 角色' not in code

    def test_no_tables_at_all_passes(self):
        symbols = make_symbols(AssetRegistry())
        _i, code, status = compile_stage('[显示] 谁 开心 左 with dissolve',
                                         symbols, Audit(symbols))[0]
        assert status == 'pass' and not code.startswith('# TODO')


# ------------------------------------------------------------------
#  四、反向：原生 → 人话（必须自证无损）
# ------------------------------------------------------------------

class TestReverseWords:
    def test_lossless_roundtrip(self, symbols):
        word = stage_word_if_lossless(symbols, 'show', 'ling close_happy',
                                      'left')
        assert word == '[显示] 凌跃捷 开心 左'
        # 翻回去必须一模一样（函数内部就是这么自检的）
        assert compile_stage(word, symbols, Audit(symbols))[0][1] == \
            'show ling close_happy at left'

    def test_unmappable_expression_gives_up(self, symbols):
        """表情反推不出来就返回 ''，让调用方保留程序写法——绝不猜。"""
        assert stage_word_if_lossless(symbols, 'show',
                                      'ling unknown_attr', '') == ''

    def test_unmappable_attr_kept_in_place(self):
        """
        反推不出中文的属性（`base_handdown`）要**原样留在原位**，
        不能丢、不能换位置——否则表情组合会被悄悄改掉。
        """
        reg = AssetRegistry()
        reg.characters = {'yhj': {'ID': 'yhj', 'Name': '「叶怀瑾」',
                                  'ImageTag': 'ye'}}
        reg.images = {'ye_base_happy': {'ID': 'ye_base_happy', 'Tag': 'ye',
                                        'Attributes': 'base_handdown happy'}}
        symbols = make_symbols(reg)
        word = stage_word_if_lossless(symbols, 'show', 'ye base_handdown happy')
        assert word == '[显示] 叶怀瑾 base_handdown 开心'
        assert compile_stage(word, symbols, Audit(symbols))[0][1] == \
            'show ye base_handdown happy'

    def test_custom_transform_kept_as_at(self):
        """项目自定义变换（没有中文词）用 `at f11` 留人话句法里。"""
        reg = AssetRegistry()
        reg.characters = {'yhj': {'ID': 'yhj', 'Name': '「叶怀瑾」',
                                  'ImageTag': 'ye'}}
        reg.images = {'ye_happy': {'ID': 'ye_happy', 'Tag': 'ye',
                                   'Attributes': 'happy'}}
        symbols = make_symbols(reg)
        word = stage_word_if_lossless(symbols, 'show', 'ye happy', 'f11')
        assert word == '[显示] 叶怀瑾 开心 at f11'
        assert compile_stage(word, symbols, Audit(symbols))[0][1] == \
            'show ye happy at f11'

    def test_non_actor_tag_gives_up(self, symbols):
        assert stage_word_if_lossless(symbols, 'show', 'bg lab', '') == ''


# ------------------------------------------------------------------
#  五、列契约：26 列遗物收敛 → 20 列结构化 + Stage/BGM/SFX + 1 隐藏列
# ------------------------------------------------------------------

class TestSheetContract:
    def test_structured_columns_with_dropdowns(self):
        """
        现在的契约：**24 列**（21 列结构化编辑面 + 3 列隐藏的内部元数据）。
        结构化列（Scene/Show/At/With/Hide/BGM/BGM 参数/SFX/音效参数/Voice）
        每列都能下拉，是策划主要的编辑面；Stage 是"结构化列表达不了的"
        高级通道；真正的旧列（Transform/Raw、Music*/Sound*、Notes）
        已被收敛掉。

        `Condition` **2026-10-05 加回来**（`Text` 之后）：它当初被当成
        旧列删掉，于是"这一拍在什么条件下才出现"**没有任何列的出口** ——
        渲染层一直是对的，可 `.rpy → Excel → .rpy` 往返会把所有 `if` 分支
        和菜单选项条件**静默抹平**（真实项目 `mini_game1` 实测 7 条 → 0 条，
        if 行 5 → 1，分支变顺序执行）。它不是排版列，是**语义列**：
        删掉它等于丢剧情逻辑，所以必须回表。

        三列隐藏元数据都由**工具维护**，放在最后、整列隐藏：

          · `StageOrder`    —— 舞台指令之间的相对顺序（`hide,show` = 重置立绘）
          · `OpOrder`       —— 本拍**全部操作**的源码顺序（跨类别：
            `play music` 写在 `show` 之前时，音频就得先出）
          · `CodeBlocks`    —— 「代码」列里哪些行构成 `python:` 块
            （2026-10-05 加；用户：「新增的列放远点，甚至可默认隐藏，
            因为事实上策划和文案不需要」）

        三者都整列隐藏，策划的界面里看不到。注意它们和 26 列时代的
        `StageOrder` 不是一回事——那次它和结构化列并列出现。
        """
        assert len(BEAT_FIELDS) == 24, BEAT_FIELDS
        for col in ('Stage', 'BGM', 'SFX'):
            assert col in BEAT_FIELDS
        # 每列都挂得上下拉的"结构化编辑面"
        for col in ('Speaker', 'Scene', 'Show', 'At', 'With', 'Hide',
                    'BGM', 'SFX', 'Voice'):
            assert col in DROPDOWN_COLUMNS, f'{col} 应该有下拉'
        for gone in ('Transform', 'Raw', 'Music', 'MusicParams', 'MusicAction',
                     'Sound', 'SoundParams', 'SoundAction',
                     'Notes'):
            assert gone not in BEAT_FIELDS, f'{gone} 应该被收敛掉了'
        # 条件列必须在表里，而且落在「编辑区」（不是隐藏列）：
        # 它是剧情逻辑，策划要能看见、能改。
        assert 'Condition' in BEAT_FIELDS, '条件列不能丢（见 docstring）'
        assert BEAT_FIELDS.index('Condition') < \
            BEAT_FIELDS.index('Scene'), '条件列应紧挨着台词，不该被赶到技术列区'
        assert 'Condition' not in HIDDEN_BEAT_COLUMNS
        # 三列内部元数据在表里，但必须整列隐藏；且 `CodeBlocks` 要**排在最后**
        # （用户明确要求"放远点"）
        for col in ('StageOrder', 'OpOrder', 'CodeBlocks'):
            assert col in BEAT_FIELDS, col
            assert col in HIDDEN_BEAT_COLUMNS, f'{col} 必须隐藏'
        assert BEAT_FIELDS[-1] == 'CodeBlocks', BEAT_FIELDS
        # 其余列的中文表头里不该出现它们，否则策划会去找那一列
        hidden_headers = [h for k, h in BEAT_HEADERS.items()
                          if k in ('StageOrder', 'OpOrder', 'CodeBlocks')]
        assert len(hidden_headers) == 3, '隐藏列也该有表头（隐藏≠没有这一列）'
        # 顺带锁住"行"与"列"不会错位
        assert len(Beat(seq=1, label='a', text='x').to_row()) == \
            len(BEAT_FIELDS)

    def test_sheet_has_help_and_keyword_tabs(self, registry, tmp_path):
        path = tmp_path / 'ch.xlsx'
        export_beats_to_excel([Beat(seq=1, label='a', text='x')], path,
                              registry=registry)
        wb = openpyxl.load_workbook(path)
        assert '使用说明' in wb.sheetnames
        assert '词表' in wb.sheetnames
        assert 'Candidates_角色' in wb.sheetnames
        # 词表里能查到中文写法（策划照着抄）
        words = [c.value for c in wb['词表']['B'] if c.value]
        assert '开心' in words and '左' in words

    def test_legacy_26_col_table_migrates(self, tmp_path):
        """老表（Show/Hide/At/With/Transform/Music）必须自动迁移到 Stage/BGM。"""
        path = tmp_path / 'old.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Beats'
        headers = ['LineId', 'Seq', 'Label', 'Kind', 'Speaker', 'Text',
                   'Scene', 'Show', 'Hide', 'At', 'With', 'Transform',
                   'StageOrder', 'Raw', 'CodeBlock', 'Music', 'MusicParams',
                   'MusicAction', 'Sound', 'SoundParams', 'SoundAction',
                   'Voice', 'Notes']
        ws.append(headers)
        ws.append(['a.0001', 1, 'a', 'say', 'e', '你好', 'bg lab',
                   'ling happy', '', 'left', '', '', 'show',
                   'with dissolve', '$ x = 1', 'spring', 'fadein 1.0',
                   'play', '', '', 'play', '', ''])
        wb.save(path)

        beats = import_beats_from_excel(path)
        assert len(beats) == 1
        b = beats[0]
        # 旧表的 Show/At/Scene 现在是结构化列（不再塞进 Stage）
        assert 'ling happy' in b.show
        assert b.scene == 'bg lab'
        assert b.at == 'left'
        assert b.bgm.startswith('[音乐] spring')
        # raw（with dissolve）与 code（$ x = 1）都归到同一个保真后门
        code_cell = compose_code(b)
        assert 'with dissolve' in code_cell and 'x = 1' in code_cell
        # 迁移后还能编译出脚本
        text = render_beats_to_rpy(beats)
        # 无资源表时图像名按约定退回下划线形态（ling_happy），所以只校验
        # "show 指令 + 位置"确实存活，不绑定具体空格/下划线写法。
        assert 'show ling' in text and 'at left' in text
        # 无资源表时音频只输出资产 ID（spring），路径解析由带 registry 的
        # 用例覆盖；这里只校验"音乐指令 + 名字 + 参数"确实随迁移存活。
        assert 'play music spring fadein 1.0' in text
        assert 'with dissolve' in text


# ------------------------------------------------------------------
#  六、端到端：表 → 脚本 + 编译结果表
# ------------------------------------------------------------------

class TestCompilePipeline:
    def test_bracket_form_survives_excel_and_compiles(self, registry,
                                                     tmp_path):
        from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
        from storygraph.story.script_table import beats_from_parse_result

        beats = [Beat(seq=1, label='ch01', kind='say', speaker='lyj',
                      text='挺精致的实验室哎…',
                      stage='[显示] 凌跃捷 开心 左 ; [场景] bg lab',
                      bgm='[音乐] spring fadein 1.0',
                      sfx='[音效] door')]
        path = tmp_path / 'ch01.xlsx'
        export_beats_to_excel(beats, path, registry=registry)
        back = import_beats_from_excel(path, registry=registry)

        text = render_beats_to_rpy(back, label_name='ch01',
                                   registry=registry)
        assert 'show ling close_happy at left' in text
        assert 'scene bg lab' in text
        assert 'play music "bgm/spring.mp3" fadein 1.0' in text
        assert 'play sound "sfx/door.wav"' in text

        # 产物必须能被自己的解析器吃回去
        fp = tmp_path / 'out.rpy'
        fp.write_text(text, encoding='utf-8')
        parser = RenPyParser()
        parser.parse(fp)
        assert not parser.errors, parser.errors

    def test_export_sheet_matches_generated_code(self, registry):
        """承诺 5：编译结果表与脚本同源，不是"另写一套再对一遍"。"""
        from storygraph.pipeline.compile import compile_beats

        beats = [Beat(seq=1, label='ch01', kind='say', speaker='lyj',
                      text='台词', stage='[显示] 凌跃捷 开心 左'),
                 Beat(seq=2, label='ch01', kind='say', speaker='jc',
                      text='再来', stage='[显示] 不存在的人')]
        result = compile_beats(beats, registry=registry)

        codes = [r['code'] for r in result.rows]
        assert 'show ling close_happy at left' in codes
        assert result.todo_count == 1
        # 结果表里每一行代码都能在生成的脚本里找到
        for code in codes:
            assert code in result.rpy_text

    def test_compile_table_writes_new_file_and_keeps_source(self, registry,
                                                           tmp_path):
        from storygraph.pipeline.compile import compile_table

        src = tmp_path / 'ch01.xlsx'
        export_beats_to_excel(
            [Beat(seq=1, label='ch01', kind='say', speaker='lyj',
                  text='台词', stage='[显示] 凌跃捷 开心 左')],
            src, registry=registry)
        before = src.read_bytes()

        rpy, sheet, result = compile_table(src, registry=registry)
        assert rpy.exists() and 'show ling close_happy at left' in \
            rpy.read_text(encoding='utf-8')
        # 承诺：绝不动策划的表
        assert src.read_bytes() == before
        # 编译结果写到独立工作簿
        assert sheet.name.endswith('.export.xlsx')
        wb = openpyxl.load_workbook(sheet)
        assert 'Export_RenPy' in wb.sheetnames
        assert wb['Export_RenPy']['A1'].value == 'LineId'

    def test_todo_shows_up_in_generated_script(self, registry):
        from storygraph.pipeline.compile import compile_beats

        beats = [Beat(seq=1, label='ch01', kind='say', speaker='lyj',
                      text='台词', stage='[显示] 凌跃捷 开心 左',
                      bgm='[音乐] 还没做的曲子')]
        result = compile_beats(beats, registry=registry)
        assert '# TODO: 缺少资源 音乐' in result.rpy_text
        assert 'play music' not in result.rpy_text.replace(
            'play music "bgm/', '')


# ------------------------------------------------------------------
#  七、舞台/音频列互不重复输出（曾经的坑）
# ------------------------------------------------------------------

class TestNoDoubleEmit:
    def test_native_stage_not_duplicated_in_codeblock(self, symbols):
        """
        Stage 与 CodeBlock 是两条通道，同一行语句**只能出现一次**——
        否则生成的脚本会重复执行转场/立绘指令。
        """
        beat = Beat(seq=1, label='a', kind='say', speaker='lyj', text='x',
                    stage='show ling close_happy at left')
        beat.show = 'ling close_happy@left'
        # 合成原生写法时只有一个 show
        assert compose_stage(beat, None).count('show') == 1
        # 渲染出来也只有一行 show（raw 里没有 show，不会重复）
        lines = render_beat_to_rpy(beat, audit=Audit(symbols))
        assert sum(1 for ln in lines if ln.startswith('show')) == 1

    def test_raw_statement_emitted_once(self, symbols):
        """raw 里的 `with dissolve` 只从 CodeBlock 通道出去一次。"""
        beat = Beat(seq=1, label='a', kind='say', text='x',
                    stage='show ling close_happy at left',
                    raw='with dissolve')
        lines = render_beat_to_rpy(beat, audit=Audit(symbols))
        assert lines.count('with dissolve') == 1
        assert sum(1 for ln in lines if ln.startswith('show')) == 1

    def test_stop_music_not_lost(self, registry):
        beats = [Beat(seq=1, label='a', kind='say', text='x',
                      bgm='[停止音乐]', sfx='[停止音效]')]
        text = render_beats_to_rpy(beats, registry=registry)
        assert 'stop music' in text and 'stop sound' in text


# ==================================================================
#  缺资源：要给一句"是不是想写这个"（2026-09-30）
# ==================================================================

class TestMissingAssetSuggestion:
    """
    真实场景（`chapter1_4.rpy` 的 7 条音乐 TODO）：脚本里写的是
    `bgm/夜晚，断电之后…….mp3` —— 文件名被手写截断了（带省略号）。
    **资产其实就在盘里**，叫 `夜晚，断电之后，不存在的教室.mp3`。
    只报"缺少资源"会让人去找一张根本不缺的曲。
    """

    def _audit(self):
        from storygraph.assets.asset_registry import AssetRegistry
        from storygraph.story.dsl import Audit, Symbols
        reg = AssetRegistry()
        reg.music = {
            'bgm_night': {'ID': 'bgm_night',
                          'Path': 'sr_general/game/bgm/夜晚，断电之后，不存在的教室.mp3'},
            'bgm_spring': {'ID': 'bgm_spring',
                           'Path': 'sr_general/game/bgm/春天花瓣剥落的早晨，抹去睡眼.mp3'},
            'bgm_other': {'ID': 'bgm_other', 'Path': 'bgm/别的曲.mp3'},
        }
        return Audit(Symbols(reg))

    def test_ellipsis_truncated_name_gets_a_suggestion(self):
        from storygraph.story.beat_render import _suggest_missing_ref
        aud = self._audit()
        got = _suggest_missing_ref(aud, 'music', 'bgm/夜晚，断电之后…….mp3')
        assert got == '夜晚，断电之后，不存在的教室'

    def test_no_suggestion_when_nothing_remotely_matches(self):
        from storygraph.story.beat_render import _suggest_missing_ref
        aud = self._audit()
        assert _suggest_missing_ref(aud, 'music', 'bgm/毫不相干.mp3') == ''

    def test_suggestion_lands_in_the_todo_comment(self):
        """建议要写进 `# TODO`（导出对话框列的就是这一行）"""
        from storygraph.story.beat_model import Beat
        from storygraph.story.beat_render import render_beat_to_rpy
        aud = self._audit()
        b = Beat(label='a', kind='say', speaker='x', text='台词',
                 music='', bgm='bgm/夜晚，断电之后…….mp3')
        lines = render_beat_to_rpy(b, audit=aud)
        joined = '\n'.join(str(x) for x in lines)
        assert '缺少资源' in joined, joined
        assert '是不是想写' in joined, joined
        assert '夜晚，断电之后，不存在的教室' in joined, joined
