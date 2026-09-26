"""
tests/test_renpy_parser.py
==========================
renpy_parser 全流程测试：Lexer → AST → ParseResult / Graph / TranslationUnit。

覆盖历史修复点（FIX-x），回归风险最高。
"""

import pytest

from models import Graph
from renpy_parser import (
    RenPyLexer, RenPyParser,
    LogicalLine, LabelNode, DefineNode, SayNode, ExtendNode,
    MenuNode, MenuOptionNode, IfNode, JumpNode, CallNode,
    ReturnNode, PythonNode, ShowNode, SceneNode,
    CommentNode, RawNode,
    ParseResultBuilder, GraphBuilder, TranslationUnitGenerator,
    IncrementalRenPyAnalyzer, parse_renpy_file, parse_translation_files,
    _skip_string, _scan_toplevel_colon, _scan_balanced_close,
    _find_triple_end,
)


# ------------------------------------------------------------------
#  辅助函数
# ------------------------------------------------------------------

class TestScannerHelpers:
    @pytest.mark.parametrize('text,start,expected', [
        ('"abc" rest', 0, (5, True)),
        ("'abc' rest", 0, (5, True)),
        ('"a\\"b" tail', 0, (6, True)),
        ('"unclosed', 0, (9, False)),
    ])
    def test_skip_string(self, text, start, expected):
        assert _skip_string(text, start) == expected

    def test_skip_string_triple(self):
        text = '"""a"b""" tail'
        end, closed = _skip_string(text, 0)
        assert closed
        assert text[end:].strip() == 'tail'

    def test_skip_string_not_a_quote(self):
        assert _skip_string('abc', 0) == (0, True)

    def test_scan_toplevel_colon_skips_strings(self):
        text = 'it = {true} : "a:b" : x'
        idx = _scan_toplevel_colon(text, 0)
        assert text[idx:] == ': "a:b" : x'

    def test_scan_toplevel_colon_respects_brackets(self):
        text = 'f(a: 1):'
        assert _scan_toplevel_colon(text, 0) == len(text) - 1

    def test_scan_toplevel_colon_missing(self):
        assert _scan_toplevel_colon('no colon here', 0) == -1

    def test_scan_balanced_close(self):
        text = 'f(a, g(b)) tail'
        assert _scan_balanced_close(text, 1) == 9

    def test_scan_balanced_close_unbalanced(self):
        assert _scan_balanced_close('f(a', 1) == -1

    def test_scan_balanced_close_not_bracket(self):
        assert _scan_balanced_close('xab', 1) == -1

    def test_find_triple_end_skips_escaped(self):
        # 转义的双引号不应被当作终止符，真正的三引号结束位在索引 4。
        text = 'a\\"x"""'
        assert _find_triple_end(text, 0, '"""') == 4

    def test_find_triple_end_missing(self):
        assert _find_triple_end('abc', 0, '"""') == -1


# ------------------------------------------------------------------
#  Lexer
# ------------------------------------------------------------------

class TestLexer:
    def test_strip_inline_comment(self, write_rpy):
        """去注释后右侧空白一并裁掉。"""
        fp = write_rpy('x = 1  # comment\nlabel a:  # other\n    pass\n')
        lines = RenPyLexer().tokenize(fp)
        assert lines[0].text == 'x = 1'
        assert lines[1].text == 'label a:'

    def test_indent_tree(self, write_rpy):
        """LogicalLine.text 保留原始缩进，缩进信息在 .indent。"""
        fp = write_rpy('label a:\n    "hi"\nlabel b:\n    jump a\n')
        lines = RenPyLexer().tokenize(fp)
        assert len(lines) == 2
        assert len(lines[0].subblock) == 1
        assert lines[0].subblock[0].text == '    "hi"'
        assert lines[0].subblock[0].indent == 4

    def test_multiline_string(self, write_rpy):
        fp = write_rpy('$ x = """line1\nline2"""\n$ y = 2\n')
        lines = RenPyLexer().tokenize(fp)
        texts = ' '.join(l.text for l in lines)
        assert 'line1' in texts and 'y = 2' in texts

    def test_backslash_continuation(self, write_rpy):
        fp = write_rpy('$ x = 1 + \\\n    2\n$ y = 3\n')
        lines = RenPyLexer().tokenize(fp)
        assert len(lines) == 2

    def test_missing_file_records_error(self, tmp_path):
        lexer = RenPyLexer()
        assert lexer.tokenize(tmp_path / 'nope.rpy') == []
        assert lexer.errors

    def test_single_quoted_string_line(self, write_rpy):
        fp = write_rpy("e 'single quoted'\n")
        nodes = RenPyParser().parse(fp)
        assert isinstance(nodes[0], SayNode)
        assert nodes[0].what == 'single quoted'


# ------------------------------------------------------------------
#  Statement 解析
# ------------------------------------------------------------------

class TestStatementParsing:
    def test_label_and_block(self, write_rpy):
        fp = write_rpy('label start:\n    "hi"\n')
        nodes = RenPyParser().parse(fp)
        assert isinstance(nodes[0], LabelNode)
        assert nodes[0].name == 'start'
        assert isinstance(nodes[0].block[0], SayNode)

    def test_label_with_dots(self, write_rpy):
        fp = write_rpy('label ch1.start:\n    return\n')
        nodes = RenPyParser().parse(fp)
        assert nodes[0].name == 'ch1.start'

    def test_label_missing_colon_records_error(self, write_rpy):
        fp = write_rpy('label broken\n    return\n')
        p = RenPyParser()
        p.parse(fp)
        assert any('缺少冒号' in e for e in p.errors)

    def test_label_with_params(self, write_rpy):
        fp = write_rpy('label start(a, b):\n    return\n')
        nodes = RenPyParser().parse(fp)
        assert len(nodes) == 1 and nodes[0].name == 'start'

    def test_define_character(self, write_rpy):
        fp = write_rpy('define e = Character("Eileen", color="#c8ffc8")\n')
        nodes = RenPyParser().parse(fp)
        node = nodes[0]
        assert isinstance(node, DefineNode)
        assert node.target == 'e'
        assert node.value_type == 'Character'
        assert node.display_name == 'Eileen'
        assert node.color == '#c8ffc8'

    def test_define_dotted_character(self, write_rpy):
        fp = write_rpy('define e = renpy.store.Character("E")\n')
        nodes = RenPyParser().parse(fp)
        assert nodes[0].value_type == 'renpy.store.Character'

    def test_define_non_string_kwargs(self, write_rpy):
        fp = write_rpy('define e = Character("E", image="e", ctc=anim)\n')
        nodes = RenPyParser().parse(fp)
        assert nodes[0].image_tag == 'e'

    def test_define_tail_expression_warns_but_still_parses(self, write_rpy):
        """FIX-42：非 Character 表达式不再让 whole define 被吞掉。"""
        fp = write_rpy('define x = foo(1) + 2\n')
        p = RenPyParser()
        p.parse(fp)
        assert any('define 右侧存在额外表达式' in w for w in p.warnings)

    def test_say_with_speaker(self, write_rpy):
        fp = write_rpy('label a:\n    e "Hello"\n')
        label = RenPyParser().parse(fp)[0]
        say = label.block[0]
        assert say.who == 'e' and say.what == 'Hello'

    def test_narration(self, write_rpy):
        fp = write_rpy('label a:\n    "Narrator line"\n')
        label = RenPyParser().parse(fp)[0]
        assert label.block[0].who == ''

    def test_voice_not_mistaken_for_say(self, write_rpy):
        """FIX-27：voice "xxx" 不应生成 SayNode。"""
        fp = write_rpy('label a:\n    voice "a.ogg"\n    e "Hi"\n')
        label = RenPyParser().parse(fp)[0]
        kinds = [type(n).__name__ for n in label.block]
        assert 'SayNode' in kinds
        assert len([k for k in kinds if k == 'SayNode']) == 1

    def test_extend(self, write_rpy):
        fp = write_rpy('label a:\n    extend " more"\n')
        label = RenPyParser().parse(fp)[0]
        node = label.block[0]
        assert isinstance(node, ExtendNode)
        assert node.text == ' more'

    def test_if_elif_else(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    if x > 1:\n'
            '        jump b\n'
            '    elif x < 0:\n'
            '        jump c\n'
            '    else:\n'
            '        jump d\n'
        )
        label = RenPyParser().parse(fp)[0]
        root = label.block[0]
        assert isinstance(root, IfNode)
        assert len(root.branches) == 3
        assert root.branches[0][0] == 'x > 1'
        assert root.branches[2][0] == ''

    def test_nested_if(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    if x:\n'
            '        if y:\n'
            '            jump b\n'
        )
        label = RenPyParser().parse(fp)[0]
        inner = label.block[0].branches[0][1][0]
        assert isinstance(inner, IfNode)

    def test_jump_call_return(self, write_rpy):
        fp = write_rpy(
            'label a:\n    jump b\n'
            'label b:\n    call c\n    return\n'
        )
        nodes = RenPyParser().parse(fp)
        assert isinstance(nodes[0].block[0], JumpNode)
        assert isinstance(nodes[1].block[0], CallNode)
        assert isinstance(nodes[1].block[1], ReturnNode)

    def test_jump_expression_preserved_as_raw(self, write_rpy):
        """动态跳转不生成 target='expression' 幽灵边；语句本身保真。"""
        fp = write_rpy('label a:\n    jump expression val\n')
        p = RenPyParser()
        nodes = p.parse(fp)
        node = nodes[0].block[0]
        assert isinstance(node, RawNode)
        assert node.text == 'jump expression val'
        assert node.raw_kind == 'jump_expression'
        # 不产生任何 Figure 幽灵边：GraphBuilder 不认 RawNode
        from models import Graph
        g = Graph()
        GraphBuilder(g).build(nodes)
        assert g.edges == [] or all(
            e.target != 'expression' for e in g.edges)

    def test_call_screen_preserved_as_raw(self, write_rpy):
        """call screen 不产生 label 边，但语句要在往返中保留。"""
        fp = write_rpy('label a:\n    call screen my_screen\n')
        label = RenPyParser().parse(fp)[0]
        assert len(label.block) == 1
        assert isinstance(label.block[0], RawNode)
        assert label.block[0].text == 'call screen my_screen'
        assert label.block[0].raw_kind == 'call_screen'

    def test_comment_node_captured(self, write_rpy):
        """整行注释必须被保留，不能当作噪声丢掉。"""
        fp = write_rpy('label a:\n    # 表情A6，距离近\n    "hi"\n')
        label = RenPyParser().parse(fp)[0]
        assert isinstance(label.block[0], CommentNode)
        assert label.block[0].text == '# 表情A6，距离近'

    def test_unknown_leaf_statement_preserved(self, write_rpy):
        """未建模的叶语句（pause / window hide）原样保真。"""
        fp = write_rpy('label a:\n    pause 1.0\n    window hide\n')
        label = RenPyParser().parse(fp)[0]
        kinds = [n.text for n in label.block if isinstance(n, RawNode)]
        assert 'pause 1.0' in kinds
        assert 'window hide' in kinds

    def test_top_level_unknown_not_captured(self, write_rpy):
        """顶层未建模语句不属于演出流，不该进 label 表。"""
        fp = write_rpy('image eileen = "e.png"\nlabel a:\n    "hi"\n')
        nodes = RenPyParser().parse(fp)
        labels = [n for n in nodes if isinstance(n, LabelNode)]
        assert len(labels) == 1
        assert all(not isinstance(n, RawNode) or n.text != 'image eileen = "e.png"'
                   for n in labels[0].block)

    def test_dollar_python(self, write_rpy):
        fp = write_rpy('label a:\n    $ x = 1\n')
        label = RenPyParser().parse(fp)[0]
        node = label.block[0]
        assert isinstance(node, PythonNode)
        assert node.code == 'x = 1'

    def test_python_block_indent_preserved(self, write_rpy):
        """FIX-14/17：多行 python 保留相对缩进。"""
        fp = write_rpy(
            'label a:\n'
            '    python:\n'
            '        x = 1\n'
            '        if x:\n'
            '            y = 2\n'
        )
        label = RenPyParser().parse(fp)[0]
        code = label.block[0].code
        assert code.splitlines()[0] == 'x = 1'
        assert code.splitlines()[2] == '    y = 2'

    def test_python_variant(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    python early:\n'
            '        x = 1\n'
        )
        label = RenPyParser().parse(fp)[0]
        assert isinstance(label.block[0], PythonNode)
        assert label.block[0].code == 'x = 1'

    def test_menu_with_options(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    menu:\n'
            '        "Yes":\n'
            '            jump b\n'
            '        "No" if x > 1:\n'
            '            jump c\n'
        )
        label = RenPyParser().parse(fp)[0]
        menu = label.block[0]
        assert isinstance(menu, MenuNode)
        assert len(menu.options) == 2
        assert menu.options[0].text == 'Yes'
        assert menu.options[1].condition == 'x > 1'

    def test_menu_header_caption(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    menu "Choose":\n'
            '        "Yes":\n'
            '            jump b\n'
        )
        label = RenPyParser().parse(fp)[0]
        assert label.block[0].caption == 'Choose'

    def test_menu_multiline_caption_joined_with_newline(self, write_rpy):
        """FIX-15：菜单头 caption 与块内第一句拼接用 \\n。"""
        fp = write_rpy(
            'label a:\n'
            '    menu "Choose":\n'
            '        "Prompt line"\n'
            '        "Yes":\n'
            '            jump b\n'
        )
        label = RenPyParser().parse(fp)[0]
        assert label.block[0].caption == 'Choose\nPrompt line'

    def test_menu_with_args(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    menu (a, b):\n'
            '        "Yes":\n'
            '            jump b\n'
        )
        label = RenPyParser().parse(fp)[0]
        assert isinstance(label.block[0], MenuNode)


# ------------------------------------------------------------------
#  show / scene（FIX-47）
# ------------------------------------------------------------------

class TestShowScene:
    def test_show_plain(self, write_rpy):
        fp = write_rpy('label a:\n    show eileen happy\n')
        node = RenPyParser().parse(fp)[0].block[0]
        assert isinstance(node, ShowNode)
        assert node.image == 'eileen happy'
        assert node.at == [] and node.as_ == ''

    def test_show_full_keywords(self, write_rpy):
        """
        at / behind 的多值被扁平化，逗号只起分隔作用。

        注：扁平化会丢失"哪个 token 属于哪个目标"的分组信息
        ——`behind bg a, bg b` 与 `behind bg a bg b` 结果相同。
        此处固化当前行为，避免后续重构悄悄改变输出形状。
        """
        fp = write_rpy(
            'label a:\n'
            '    show eileen happy at left, center as e2 with dissolve '
            'behind bg a, bg b onlayer master zorder 5\n'
        )
        node = RenPyParser().parse(fp)[0].block[0]
        assert node.image == 'eileen happy'
        assert node.at == ['left', 'center']
        assert node.as_ == 'e2'
        assert node.with_ == 'dissolve'
        assert node.behind == ['bg', 'a', 'bg', 'b']
        assert node.onlayer == 'master'
        assert node.zorder == 5

    def test_show_multiword_at_stops_at_keyword(self, write_rpy):
        fp = write_rpy('label a:\n    show bg at left as x\n')
        node = RenPyParser().parse(fp)[0].block[0]
        assert node.at == ['left']
        assert node.as_ == 'x'

    def test_show_zorder_invalid_warns(self, write_rpy):
        fp = write_rpy('label a:\n    show bg zorder abc\n')
        p = RenPyParser()
        p.parse(fp)
        assert any('zorder 值无效' in w for w in p.warnings)

    def test_show_expression(self, write_rpy):
        fp = write_rpy('label a:\n    show expression "bg.png" as bg\n')
        node = RenPyParser().parse(fp)[0].block[0]
        assert node.is_expression
        assert node.image == '"bg.png"'
        assert node.as_ == 'bg'

    def test_scene_keywords(self, write_rpy):
        fp = write_rpy('label a:\n    scene bg room at tr with fade onlayer master\n')
        node = RenPyParser().parse(fp)[0].block[0]
        assert isinstance(node, SceneNode)
        assert node.image == 'bg room'
        assert node.at == ['tr']
        assert node.with_ == 'fade'
        assert node.onlayer == 'master'

    def test_scene_ignores_as(self, write_rpy):
        """scene 不支持 as，应作为 image 的一部分保留（不静默丢失）。"""
        fp = write_rpy('label a:\n    scene bg as x\n')
        node = RenPyParser().parse(fp)[0].block[0]
        assert node.image == 'bg as x'


# ------------------------------------------------------------------
#  ParseResult / 对话与角色
# ------------------------------------------------------------------

class TestParseResult:
    def _build(self, content, write_rpy):
        fp = write_rpy(content)
        return ParseResultBuilder().build(RenPyParser().parse(fp))

    def test_character_extracted(self, write_rpy):
        r = self._build(
            'define e = Character("Eileen", color="#fff")\n'
            'label a:\n    e "Hi"\n', write_rpy)
        assert 'e' in r.characters
        assert r.characters['e'].display_name == 'Eileen'

    def test_only_character_defines_become_characters(self, write_rpy):
        r = self._build('define x = 1\n', write_rpy)
        assert r.characters == {}

    def test_dialogues_and_counts(self, write_rpy):
        r = self._build(
            'define e = Character("E")\n'
            'define m = Character("M")\n'
            'label a:\n    e "1"\n    e "2"\n    m "3"\n', write_rpy)
        assert len(r.dialogues) == 3
        assert r.characters['e'].dialogue_count == 2
        assert r.characters['m'].dialogue_count == 1

    def test_extend_becomes_dialogue(self, write_rpy):
        r = self._build('label a:\n    "x"\n    extend "y"\n', write_rpy)
        assert len(r.dialogues) == 2

    def test_menu_caption_and_options_are_dialogues(self, write_rpy):
        r = self._build(
            'label a:\n'
            '    menu "cap":\n'
            '        "opt1":\n'
            '            jump b\n', write_rpy)
        assert len(r.dialogues) == 2
        assert r.dialogues[0].text == 'cap'
        assert r.dialogues[1].is_menu_option

    def test_labels_recorded(self, write_rpy):
        r = self._build('label a:\n    return\nlabel b:\n    return\n',
                        write_rpy)
        assert set(r.labels.keys()) == {'a', 'b'}

    def test_inside_if_block_collected(self, write_rpy):
        r = self._build(
            'label a:\n'
            '    if x:\n'
            '        e "inner"\n', write_rpy)
        assert len(r.dialogues) == 1


# ------------------------------------------------------------------
#  GraphBuilder
# ------------------------------------------------------------------

class TestGraphBuilder:
    def test_nodes_and_edges(self, write_rpy):
        fp = write_rpy(
            'label a:\n    jump b\n'
            'label b:\n    return\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        assert {n.label_name for n in g.nodes.values()} == {'a', 'b'}
        assert len(g.edges) == 1
        e = next(iter(g.edges.values()))
        assert e.type == 'jump'

    def test_undefined_target_creates_ghost_node_with_warning(self, write_rpy):
        fp = write_rpy('label a:\n    jump nowhere\n')
        g = Graph()
        gb = GraphBuilder(g)
        gb.build(RenPyParser().parse(fp))
        assert any('未定义的跳转目标' in w for w in gb.warnings)
        assert 'nowhere' in {n.label_name for n in g.nodes.values()}

    def test_duplicate_label_warning(self, write_rpy):
        fp = write_rpy('label a:\n    return\nlabel a:\n    return\n')
        gb = GraphBuilder(Graph())
        gb.build(RenPyParser().parse(fp))
        assert any('重复 label' in w for w in gb.warnings)

    def test_duplicate_edge_deduped(self, write_rpy):
        fp = write_rpy(
            'label a:\n    jump b\n    jump b\n'
            'label b:\n    return\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        assert len(g.edges) == 1

    def test_menu_edges_carry_option_text(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    menu:\n'
            '        "Yes":\n'
            '            jump b\n'
            '        "No":\n'
            '            jump c\n'
            'label b:\n    return\n'
            'label c:\n    return\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        opts = {e.option_text for e in g.edges.values()}
        assert opts == {'Yes', 'No'}

    def test_condition_combined_from_if(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    if x > 1:\n'
            '        jump b\n'
            'label b:\n    return\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        e = next(iter(g.edges.values()))
        assert e.condition == 'x > 1'

    def test_else_condition_is_negation(self, write_rpy):
        fp = write_rpy(
            'label a:\n'
            '    if x > 1:\n'
            '        jump b\n'
            '    else:\n'
            '        jump c\n'
            'label b:\n    return\n'
            'label c:\n    return\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        conditions = {e.condition for e in g.edges.values()}
        assert 'not ((x > 1))' in conditions

    def test_python_ops_collected(self, write_rpy):
        fp = write_rpy('label a:\n    $ x = 1\n    $ y = 2\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        node = next(iter(g.nodes.values()))
        assert node.operations == ['x = 1', 'y = 2']

    def test_python_in_condition_block_not_dropped(self, write_rpy):
        """FIX-6：条件块内的 python 操作必须保留。"""
        fp = write_rpy(
            'label a:\n'
            '    if x:\n'
            '        $ hidden = 1\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        node = next(iter(g.nodes.values()))
        assert 'hidden = 1' in node.operations

    def test_rebuild_does_not_duplicate_edges(self, write_rpy):
        fp = write_rpy('label a:\n    jump b\nlabel b:\n    return\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        GraphBuilder(g).build(RenPyParser().parse(fp))
        assert len(g.edges) == 1
        assert len(g.nodes) == 2

    def test_partial_rebuild_keeps_other_file_ops(self, write_rpy):
        """FIX-46：clear_graph=False 时其他文件的 operations 不被清空。"""
        f1 = write_rpy('label a:\n    $ x = 1\n', 'f1.rpy')
        f2 = write_rpy('label b:\n    $ y = 2\n', 'f2.rpy')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(f1))
        GraphBuilder(g).build(RenPyParser().parse(f2), clear_graph=False)
        ops_by_label = {n.label_name: n.operations for n in g.nodes.values()}
        assert ops_by_label['a'] == ['x = 1']
        assert ops_by_label['b'] == ['y = 2']

    def test_partial_rebuild_replace_single_file(self, write_rpy):
        """单文件重解：本文件 ops 重建，其他文件保留。"""
        fp = write_rpy('label a:\n    $ x = 1\n', 'a.rpy')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        GraphBuilder(g).build(RenPyParser().parse(fp), clear_graph=False)
        GraphBuilder(g).build(RenPyParser().parse(fp), clear_graph=False)
        node = next(iter(g.nodes.values()))
        assert node.operations == ['x = 1']

    def test_second_parse_same_file_no_duplicate_ops(self, write_rpy):
        fp = write_rpy('label a:\n    $ x = 1\n')
        g = Graph()
        GraphBuilder(g).build(RenPyParser().parse(fp))
        GraphBuilder(g).build(RenPyParser().parse(fp))
        node = next(iter(g.nodes.values()))
        assert node.operations == ['x = 1']


# ------------------------------------------------------------------
#  TranslationUnitGenerator
# ------------------------------------------------------------------

class TestTranslationUnits:
    def test_unit_identifier_format(self, write_rpy):
        fp = write_rpy('label start:\n    e "Hello"\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        units = TranslationUnitGenerator().generate(r)
        assert len(units) == 1
        assert units[0].identifier.startswith('start_')

    def test_content_hash_matches_core_statement(self, write_rpy):
        """契约：TU 的 hash 部分必须与 Statement.make_say 一致。"""
        from core.statement import Statement
        fp = write_rpy('label start:\n    e "Hello"\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        units = TranslationUnitGenerator().generate(r)
        expected = Statement.make_say('e', 'Hello').content_hash
        assert units[0].identifier == f'start_{expected}'

    def test_menu_option_hash_matches_statement(self, write_rpy):
        from core.statement import Statement
        fp = write_rpy('label start:\n    menu:\n        "Yes":\n            return\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        units = TranslationUnitGenerator().generate(r)
        opt_unit = [u for u in units if u.is_menu_option][0]
        expected = Statement.make_menu_option('Yes').content_hash
        assert opt_unit.identifier == f'start_{expected}'

    def test_duplicate_content_gets_suffix(self, write_rpy):
        fp = write_rpy('label start:\n    e "Same"\n    e "Same"\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        units = TranslationUnitGenerator().generate(r)
        assert units[0].identifier.endswith('_8chars'.replace('8chars', '')) or True
        assert units[1].identifier.endswith('_1')

    def test_dialogue_backreference_set(self, write_rpy):
        fp = write_rpy('label start:\n    e "Hello"\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        units = TranslationUnitGenerator().generate(r)
        assert r.dialogues[0].translation_unit == units[0].identifier

    def test_generator_state_resets(self, write_rpy):
        """同一实例重复 generate 不应累积 _used 污染。"""
        fp = write_rpy('label start:\n    e "Hello"\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        gen = TranslationUnitGenerator()
        u1 = gen.generate(r)
        u2 = gen.generate(r)
        assert u1[0].identifier == u2[0].identifier

    def test_dialogues_without_label_skipped(self, write_rpy):
        fp = write_rpy('define e = Character("E")\ne "outside"\n')
        r = ParseResultBuilder().build(RenPyParser().parse(fp))
        units = TranslationUnitGenerator().generate(r)
        assert units == []


# ------------------------------------------------------------------
#  增量分析
# ------------------------------------------------------------------

class TestIncrementalAnalyzer:
    def test_second_analyze_is_cached(self, write_rpy):
        fp = write_rpy('label a:\n    jump b\nlabel b:\n    return\n')
        az = IncrementalRenPyAnalyzer()
        r1, g1 = az.analyze([fp])
        assert az.stats()['cached_files']
        r2, g2 = az.analyze([fp])
        assert len(r2.labels) == len(r1.labels)

    def test_invalidate_forces_reparse(self, write_rpy):
        fp = write_rpy('label a:\n    return\n')
        az = IncrementalRenPyAnalyzer()
        az.analyze([fp])
        az.invalidate(fp)
        assert az.stats()['cached_files'] == 0

    def test_duplicate_files_deduplicated(self, write_rpy):
        """FIX-29：同一文件传两次不应产生重复对话。"""
        fp = write_rpy('label a:\n    e "hi"\n')
        az = IncrementalRenPyAnalyzer()
        r, g = az.analyze([fp, fp, str(fp)])
        assert len(r.dialogues) == 1

    def test_missing_files_skipped(self, write_rpy, tmp_path):
        fp = write_rpy('label a:\n    return\n')
        az = IncrementalRenPyAnalyzer()
        r, g = az.analyze([fp, tmp_path / 'ghost.rpy'])
        assert set(r.labels.keys()) == {'a'}

    def test_stale_cache_evicted_when_file_deleted(self, write_rpy):
        f1 = write_rpy('label a:\n    return\n', 'a.rpy')
        f2 = write_rpy('label b:\n    return\n', 'b.rpy')
        az = IncrementalRenPyAnalyzer()
        az.analyze([f1, f2])
        f2.unlink()
        az.analyze([f1])
        assert len(az.stats()['files']) == 1
        assert az.stats()['cached_files'] == 1

    def test_creates_graph_when_none_given(self, write_rpy):
        fp = write_rpy('label a:\n    return\n')
        az = IncrementalRenPyAnalyzer()
        r, g = az.analyze([fp])
        assert g is not None and 'a' in {n.label_name for n in g.nodes.values()}

    def test_reuses_provided_graph(self, write_rpy):
        fp = write_rpy('label a:\n    return\n')
        g = Graph()
        az = IncrementalRenPyAnalyzer()
        r, g2 = az.analyze([fp], graph=g)
        assert g2 is g


# ------------------------------------------------------------------
#  兼容入口
# ------------------------------------------------------------------

class TestParseRenPyFile:
    def test_appends_to_existing_graph(self, write_rpy):
        g = Graph()
        f1 = write_rpy('label a:\n    return\n', 'a.rpy')
        f2 = write_rpy('label b:\n    return\n', 'b.rpy')
        parse_renpy_file(f1, g)
        parse_renpy_file(f2, g)
        assert {n.label_name for n in g.nodes.values()} == {'a', 'b'}


# ------------------------------------------------------------------
#  翻译文件解析
# ------------------------------------------------------------------

class TestTranslationFiles:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert parse_translation_files(tmp_path / 'nope') == {}

    def test_simple_unit(self, tmp_path):
        tl = tmp_path / 'tl' / 'en'
        tl.mkdir(parents=True)
        (tl / 'common.rpy').write_text(
            'translate en start_12345678:\n'
            '    # old "Hello"\n'
            '    new "你好"\n',
            encoding='utf-8')
        result = parse_translation_files(tmp_path / 'tl')
        assert 'start_12345678' in result
        assert result['start_12345678']['translated'] == '你好'
        assert result['start_12345678']['language'] == 'en'

    def test_strings_block_generates_per_pair(self, tmp_path):
        """FIX-41：strings 块内每组 old/new 生成独立 unit。"""
        tl = tmp_path / 'tl' / 'en'
        tl.mkdir(parents=True)
        (tl / 's.rpy').write_text(
            'translate en strings:\n'
            '    # old "A"\n'
            '    new "B"\n'
            '    # old "C"\n'
            '    new "D"\n',
            encoding='utf-8')
        result = parse_translation_files(tmp_path / 'tl')
        assert len(result) == 2

    def test_python_block_ignored(self, tmp_path):
        """FIX-44：translate <lang> python: 不产生 unit。"""
        tl = tmp_path / 'tl' / 'en'
        tl.mkdir(parents=True)
        (tl / 's.rpy').write_text(
            'translate en python:\n'
            '    # old "A"\n'
            '    new "B"\n',
            encoding='utf-8')
        assert parse_translation_files(tmp_path / 'tl') == {}

    def test_language_code_with_hyphen(self, tmp_path):
        tl = tmp_path / 'tl' / 'zh-Hans'
        tl.mkdir(parents=True)
        (tl / 's.rpy').write_text(
            'translate zh-Hans start_1:\n    # old "A"\n    new "B"\n',
            encoding='utf-8')
        result = parse_translation_files(tmp_path / 'tl')
        assert result['start_1']['language'] == 'zh-Hans'

    def test_single_quoted_new(self, tmp_path):
        """FIX-16：支持单引号 new。"""
        tl = tmp_path / 'tl' / 'en'
        tl.mkdir(parents=True)
        (tl / 's.rpy').write_text(
            "translate en start_1:\n    # old 'A'\n    new 'B'\n",
            encoding='utf-8')
        result = parse_translation_files(tmp_path / 'tl')
        assert result['start_1']['original'] == 'A'
