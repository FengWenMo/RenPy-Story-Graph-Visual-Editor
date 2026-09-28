"""
tests/test_real_world_chapter.py
=================================
用一份"现实中一章的开发结构"做端到端保真测试。

用户的原话是"现在它对 Renpy 文件的解析依然不够强大"，并贴出了真实
章节脚本。那份脚本里的这些构造，正是此前往返会丢信息的地方：

  · 整行注释（给美术的表情标注）      → Comment 列
  · `play music "bgm/xxx.mp3" fadein` → 引号 + 参数
  · `stop music`                      → M1 落表
  · `$ renpy.transition(...)`         → CodeBlock（此前进 Notes 就丢了）
  · `hide ye` + `show ye happy`       → StageOrder 保序（重置立绘）
  · `show cg .. at .. behind ye`      → 复杂 show 子句
  · `hide lan with dissolve` × 2      → 多 hide + with
  · `extend "..."`                    → 续行
  · `jump` / `call screen`            → Raw 保真

目标不是"能解析"，而是"往返不丢"——这是工业化叙事管线的底线。
"""

import pytest

from storygraph.core.statement import (
    Statement, StatementKind, validate_hash_contract, validate_statement,
)
from storygraph.story.script_table import (
    Beat, beats_from_parse_result, render_beat_to_rpy, render_beats_to_rpy,
    import_beats_from_excel, export_beats_to_excel, open_rpy_as_table,
    BEAT_FIELDS,
)


CHAPTER = (
    '# 自动生成的Ren\'py脚本\n'
    '#define jc = Character("Jc")\n'
    'define jc = Character("Jc")\n'
    'define yhj = Character("Yhj")\n'
    'define l = Character("L")\n'
    'define hidden_name = Character("？？？")\n'
    '\n'
    'label chapter1_2:\n'
    '\n'
    '    #1.2蓝之初见\n'
    '    "学姐关上门，把灯打开。"\n'
    '    scene bg bg9_5 at bgfit\n'
    '    # bgm13 bg9.5\n'
    '    stop music\n'
    '    play music "bgm/无法言说的事情…….mp3" fadein 1.0\n'
    '    "这里是一间密室。"\n'
    '    jc "没想到咱学校还有这种高科技的地方……"\n'
    '    # 表情A6，距离近\n'
    '    hide ye\n'
    '    show ye happy at f11\n'
    '    $ renpy.transition(Dissolve(0.1), layer="master")\n'
    '    yhj "这里算是青蓝的秘密实验室。"\n'
    '    # 表情A11\n'
    '    show ye gratified\n'
    '    $ renpy.transition(Dissolve(0.1), layer="master")\n'
    '    yhj "开机了。"\n'
    '    # 学姐立绘消失\n'
    '    hide ye\n'
    '    "学姐长舒一口气。"\n'
    '    play music "bgm/夜晚，断电之后…….mp3" fadein 1.0\n'
    '    hidden_name "嗨呀，痒，好痒呀。"\n'
    '    l "我是……{nw}"\n'
    '    extend "住在青蓝机器里的意识体。"\n'
    '    show cg cg02_2 at cg38402160 behind ye\n'
    '    $ persistent.lock_lst["cg02.2"] = True\n'
    '    hide lan with dissolve\n'
    '    hide ye with dissolve\n'
    '    pause 2.0\n'
    '    return\n'
)


@pytest.fixture
def beats(write_rpy):
    from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
    fp = write_rpy(CHAPTER, 'chapter1_2.rpy')
    result = ParseResultBuilder().build(RenPyParser().parse(fp))
    return beats_from_parse_result(result, label='chapter1_2')


# ------------------------------------------------------------------
#  Statement IR 契约（改进文档第九节）
# ------------------------------------------------------------------

class TestStatementContract:
    def test_hash_matches_eq(self):
        """__eq__ 相等 ⇒ __hash__ 相等（旧实现用 content_hash 会违反）。"""
        a = Statement.make_show('eileen happy', at=['left'])
        b = Statement.make_show('eileen happy', at=['left'])
        c = Statement.make_show('eileen happy', at=['right'])
        assert a == b
        assert hash(a) == hash(b)
        assert a != c
        assert len({a, b, c}) == 2

    def test_hidden_disabled_show_still_hashes_distinctly(self):
        """show 的 content_hash 恒为空，但不同 show 不能同哈希。"""
        a = Statement.make_show('bg a')
        b = Statement.make_show('bg b')
        assert a.content_hash == '' and b.content_hash == ''
        assert hash(a) != hash(b)

    def test_hash_contract_selfcheck(self):
        """工厂 payload 必须覆盖哈希输入字段（加字段忘同步会静默错位）。"""
        assert validate_hash_contract() == []

    def test_new_kinds_validate(self):
        assert validate_statement(Statement.make_comment('# x')) == []
        assert validate_statement(Statement.make_raw('pause 1.0')) == []


# ------------------------------------------------------------------
#  解析保真：这些构造以前都会丢
# ------------------------------------------------------------------

class TestParsingFidelity:
    def test_comments_kept(self, beats):
        comments = [b.comment for b in beats if b.comment]
        joined = '\n'.join(comments)
        assert '#1.2蓝之初见' in joined
        assert '# 表情A6，距离近' in joined
        assert '# 学姐立绘消失' in joined

    def test_stop_music_landed(self, beats):
        assert any(b.music_action == 'stop' for b in beats)

    def test_audio_path_and_fadein(self, beats):
        m = [b for b in beats if b.music and '无法言说' in b.music]
        assert m, "带引号路径的音乐没落表"
        assert m[0].music_params == 'fadein 1.0'

    def test_python_into_codeblock(self, beats):
        codes = '\n'.join(b.code for b in beats if b.code)
        assert 'renpy.transition' in codes
        assert 'persistent.lock_lst' in codes

    def test_extend_kind(self, beats):
        assert any(b.kind == 'extend' and '意识体' in b.text for b in beats)

    def test_multiple_hides_kept(self, beats):
        h = [b for b in beats if b.hide and 'lan' in b.hide]
        assert h, "hide lan 被后来的 hide ye 覆盖丢了"
        assert 'lan@dissolve' in h[0].hide
        assert 'ye@dissolve' in h[0].hide

    def test_complex_show_clause(self, beats):
        b = [b for b in beats if 'cg02_2' in b.show]
        assert b
        assert 'behind ye' in b[0].show

    def test_stage_order_recorded(self, beats):
        b = [x for x in beats if x.show and 'ye happy' in x.show]
        assert b
        # 源顺序是 hide 在前
        assert b[0].stage_order.startswith('hide')

    def test_unknown_leaf_preserved(self, beats):
        raws = '\n'.join(b.raw for b in beats if b.raw)
        assert 'pause 2.0' in raws


# ------------------------------------------------------------------
#  渲染保真：往返后脚本仍然正确
# ------------------------------------------------------------------

class TestRenderFidelity:
    def test_audio_quoted_and_params(self, beats):
        text = render_beats_to_rpy(beats, label_name='chapter1_2')
        assert 'play music "bgm/无法言说的事情…….mp3" fadein 1.0' in text
        assert 'stop music' in text

    def test_hide_before_show_preserved(self, beats):
        """'重置立绘'的 hide→show 顺序不能被倒过来，否则立绘被藏掉。"""
        text = render_beats_to_rpy(beats, label_name='chapter1_2')
        lines = text.splitlines()
        i_hide = next(i for i, ln in enumerate(lines)
                      if ln.strip() == 'hide ye')
        i_show = next(i for i, ln in enumerate(lines)
                      if ln.strip().startswith('show ye happy'))
        assert i_hide < i_show

    def test_complex_show_and_hides_rendered(self, beats):
        text = render_beats_to_rpy(beats, label_name='chapter1_2')
        assert 'show cg cg02_2 at cg38402160 behind ye' in text
        assert 'hide lan with dissolve' in text
        assert 'hide ye with dissolve' in text

    def test_python_rendered(self, beats):
        text = render_beats_to_rpy(beats, label_name='chapter1_2')
        assert '$ renpy.transition(Dissolve(0.1), layer="master")' in text
        assert '$ persistent.lock_lst["cg02.2"] = True' in text

    def test_comments_rendered(self, beats):
        text = render_beats_to_rpy(beats, label_name='chapter1_2')
        assert '# 表情A6，距离近' in text

    def test_rendered_script_reparsable(self, beats, tmp_path):
        """往返产物必须能被自己的解析器吃回去。"""
        from storygraph.parsing.renpy_parser import RenPyParser
        text = render_beats_to_rpy(beats, label_name='chapter1_2')
        fp = tmp_path / 'rt.rpy'
        fp.write_text(text, encoding='utf-8')
        p = RenPyParser()
        p.parse(fp)
        assert not p.errors, p.errors


# ------------------------------------------------------------------
#  Excel 往返：新列必须落盘并读回
# ------------------------------------------------------------------

class TestExcelRoundTripFidelity:
    def test_new_columns_persist(self, tmp_path, beats):
        path = tmp_path / 'ch12.xlsx'
        export_beats_to_excel(beats, path)
        back = import_beats_from_excel(path)

        assert len(back) == len(beats)
        joined_comment = '\n'.join(b.comment for b in back)
        assert '表情A6' in joined_comment
        joined_code = '\n'.join(b.code for b in back)
        assert 'renpy.transition' in joined_code
        # 音频参数、复杂 show、多 hide 全都要活过 Excel 一圈
        m = [b for b in back if b.music and '无法言说' in b.music]
        assert m and m[0].music_params == 'fadein 1.0'
        cg = [b for b in back if 'cg02_2' in b.show]
        assert cg and 'behind ye' in cg[0].show
        hides = [b for b in back if b.hide and 'lan@dissolve' in b.hide]
        assert hides

    def test_full_excel_roundtrip_renders(self, tmp_path, write_rpy, beats):
        from storygraph.parsing.renpy_parser import RenPyParser
        path = tmp_path / 'ch12.xlsx'
        export_beats_to_excel(beats, path)
        back = import_beats_from_excel(path)
        text = render_beats_to_rpy(back, label_name='chapter1_2')
        assert 'play music "bgm/夜晚，断电之后…….mp3" fadein 1.0' in text
        assert 'hide ye with dissolve' in text
        fp = tmp_path / 'from_xlsx.rpy'
        fp.write_text(text, encoding='utf-8')
        p = RenPyParser()
        p.parse(fp)
        assert not p.errors, p.errors

    def test_open_rpy_as_table_keeps_everything(self, tmp_path, write_rpy):
        from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
        fp = write_rpy(CHAPTER, 'chapter1_2.rpy')
        table = open_rpy_as_table(fp, tmp_path / 'c.xlsx')
        back = import_beats_from_excel(table)
        codes = '\n'.join(b.code for b in back)
        assert 'renpy.transition' in codes
        comments = '\n'.join(b.comment for b in back)
        assert '表情A6' in comments


# ------------------------------------------------------------------
#  自定义 Transform（改进文档第四节）
# ------------------------------------------------------------------

class TestTransformGeneration:
    def _registry(self):
        from storygraph.assets.asset_registry import AssetRegistry
        reg = AssetRegistry()
        reg.transforms = {
            'slide_in_left': {
                'ID': 'slide_in_left',
                'ATL': 'xalign 0.0\nlinear 0.5 xalign 1.0',
                'Params': 'duration=0.5, distance=200',
                'Category': '入场',
            },
            'bounce': {
                'ID': 'bounce',
                'ATL': 'yoffset 0\nlinear 0.1 yoffset -20\nlinear 0.1 yoffset 0',
            },
            'no_atl': {'ID': 'no_atl', 'Notes': '仅登记未定义'},
        }
        return reg

    def test_collect_used_excludes_builtins(self):
        from storygraph.story.script_table import collect_used_transforms
        beats = [
            Beat(text='x', at='left slide_in_left'),
            Beat(text='y', transform='dissolve'),
            Beat(text='z', show='eileen happy@slide_in_left'),
        ]
        used = collect_used_transforms(beats)
        assert 'slide_in_left' in used
        assert 'left' not in used and 'dissolve' not in used

    def test_definitions_generated_with_params(self):
        from storygraph.story.script_table import render_beats_to_rpy
        reg = self._registry()
        beats = [Beat(label='a', speaker='e', text='hi', at='slide_in_left')]
        text = render_beats_to_rpy(beats, registry=reg)
        assert 'transform slide_in_left(duration=0.5, distance=200):' in text
        assert '    xalign 0.0' in text
        assert '    linear 0.5 xalign 1.0' in text

    def test_transform_without_atl_skipped(self):
        from storygraph.story.script_table import render_beats_to_rpy
        reg = self._registry()
        beats = [Beat(label='a', speaker='e', text='hi', at='no_atl')]
        text = render_beats_to_rpy(beats, registry=reg)
        assert 'transform no_atl' not in text

    def test_no_registry_no_transform_block(self):
        from storygraph.story.script_table import render_beats_to_rpy
        beats = [Beat(label='a', speaker='e', text='hi', at='slide_in_left')]
        text = render_beats_to_rpy(beats)
        assert 'transform slide_in_left' not in text

    def test_generated_script_reparsable(self, tmp_path):
        from storygraph.story.script_table import render_beats_to_rpy
        from storygraph.parsing.renpy_parser import RenPyParser
        reg = self._registry()
        beats = [Beat(label='a', speaker='e', text='hi',
                      at='slide_in_left')]
        text = render_beats_to_rpy(beats, registry=reg)
        fp = tmp_path / 't.rpy'
        fp.write_text(text, encoding='utf-8')
        p = RenPyParser()
        p.parse(fp)
        assert not p.errors, p.errors
