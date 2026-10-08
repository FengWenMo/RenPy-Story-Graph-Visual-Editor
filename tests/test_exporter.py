"""
tests/test_exporter.py
======================
反向生成 .rpy 控制流脚本测试。

检查点：
  · 收集到的变量都会 default 声明
  · 每个 node 生成 <label>_control
  · 无出边 → return
  · 单一无条件边 → jump/call
  · 条件边 → if/elif
  · 菜单边 → menu 块
  · 多行 python 转成 python: 缩进块
  · 目标节点缺失时跳过该分支（不写坏 label）
"""

import pytest

from pathlib import Path

from storygraph.graph.models import Graph
from storygraph.pipeline.renpy_exporter import export_to_renpy


@pytest.fixture
def export(tmp_path, write_rpy):
    def _run(graph):
        out = tmp_path / 'generated.rpy'
        export_to_renpy(graph, out)
        assert out.exists()
        return out.read_text(encoding='utf-8')
    return _run


class TestExport:
    def test_variable_defaults_emitted(self, export):
        g = Graph()
        a = g.add_node('start', 0, 0, operations=['love = 1'])
        b = g.add_node('end', 0, 0, operations=['money += 2'])
        g.add_edge(a, b)
        text = export(g)
        assert 'default love = 0' in text
        assert 'default money = 0' in text

    def test_control_label_generated(self, export):
        g = Graph()
        g.add_node('start', 0, 0)
        text = export(g)
        assert 'label start_control:' in text
        assert 'call start' in text

    def test_no_out_edge_generates_return(self, export):
        g = Graph()
        g.add_node('lonely', 0, 0)
        text = export(g)
        assert 'return' in text

    def test_unconditional_edge(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b, etype='jump')
        text = export(g)
        assert 'jump b_control' in text

    def test_call_edge_type_preserved(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        g.add_edge(a, b, etype='call')
        assert 'call b_control' in export(g)

    def test_conditional_edges_use_if_elif(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x > 1')
        g.add_edge(a, c, condition='x <= 1')
        text = export(g)
        assert 'if x > 1:' in text
        assert 'elif x <= 1:' in text

    def test_conditional_with_else(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x > 1')
        g.add_edge(a, c)
        text = export(g)
        assert 'else:' in text
        assert 'jump c_control' in text

    def test_menu_block(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, option_text='Yes')
        g.add_edge(a, c, option_text='No')
        text = export(g)
        assert 'menu:' in text
        assert '"Yes":' in text
        assert 'jump b_control' in text

    def test_menu_with_condition_uses_outer_if(self, export):
        g = Graph()
        a = g.add_node('a', 0, 0)
        b = g.add_node('b', 0, 0)
        c = g.add_node('c', 0, 0)
        g.add_edge(a, b, condition='x > 1')
        g.add_edge(a, c, option_text='No')
        text = export(g)
        assert 'if x > 1:' in text
        assert 'else:' in text

    def test_single_line_operation(self, export):
        g = Graph()
        g.add_node('a', 0, 0, operations=['love = 5'])
        assert '$ love = 5' in export(g)

    def test_multiline_operation_uses_python_block(self, export):
        g = Graph()
        g.add_node('a', 0, 0, operations=['x = 1\ny = 2'])
        text = export(g)
        assert 'python:' in text
        assert '        x = 1' in text
        assert '        y = 2' in text

    # ---- 2026-10-05 审计"两套 .rpy 生成器"时抓到的两个真 bug ----

    def test_tight_dollar_marker_never_leaks_into_the_product(self, export):
        """
        `\\x01` 是"原文紧凑写 `$code`"的内部标记，**绝不能进产物**。

        实测（真实项目 51 个节点导出控制流）：`story/beat_render` 认得这个
        标记，而 `pipeline/renpy_exporter` 自己手写 `f"    $ {op}"` ——
        于是产物里出现 `$ ^Apersistent.lock_lst["cg01"]= True`，
        带控制字符的行 Ren'Py 直接不认。
        """
        from storygraph.core.statement import MARKER
        g = Graph()
        g.add_node('a', 0, 0, operations=[f'{MARKER}persistent.lock["cg1"]= True'])
        text = export(g)
        assert MARKER not in text, '控制字符漏进产物了'
        assert '$persistent.lock["cg1"]= True' in text, text

    def test_engine_modules_never_get_a_default_line(self, export):
        """
        `default renpy = 0` 会把引擎模块遮蔽成 int → 整章跑不起来。

        实测真实项目导出产物里同时有 `default renpy/config/persistent/time = 0`。
        只有"被赋值的名字"才配 `default`，且要剔掉引擎/标准库模块与循环临时变量。
        """
        g = Graph()
        g.add_node('a', 0, 0, operations=[
            'renpy.transition(Dissolve(0.1))',
            'persistent.lock = True',
            'love += 1',
            'for i in range(3):\n    total += i',
        ])
        text = export(g)
        defaults = [ln for ln in text.splitlines() if ln.startswith('default ')]
        assert 'default love = 0' in defaults
        assert 'default total = 0' in defaults
        for bad in ('default renpy = 0', 'default persistent = 0',
                    'default config = 0', 'default i = 0'):
            assert bad not in defaults, f'{bad} 不该出现：{defaults}'

    def test_python_op_rendering_comes_from_one_rule(self, export):
        """
        **两个生成器必须用同一套 op 渲染规则**（不许再各写一份）。

        判据：导出产物里那几行 == `beat_render.render_code_block` 的输出
        （带控制流那条路的 `multiline='python'` 取法）。

        ⚠ **判据校准（2026-10-08）**：这里原来用 `'\\x01'` 当"紧凑 `$`"的
        标记 —— 那是**过期**的（真标记 `core.statement.MARKER = '\\ue000'`，
        `\\x01` 是更早的实现）。控制流导出接上生成门禁之后，产物里那个字面
        `\\x01` 被 `PYTHON_SYNTAX_ERROR` 判成 block（**门禁是对的**：`.rpy`
        里出现 `U+0001` 就是坏文件）。改用真标记。
        """
        from storygraph.core.statement import MARKER
        from storygraph.story.beat_render import render_code_block
        ops = ['love = 5', MARKER + 'persistent.a = True', 'x = 1\ny = 2',
               'with dissolve']
        g = Graph()
        g.add_node('a', 0, 0, operations=list(ops))
        text = export(g)
        for op in ops:
            for ln in render_code_block(op, multiline='python'):
                if str(ln).strip():
                    assert f'    {ln}' in text, (op, ln, text)
        # 产物里**不许**出现控制字符（上一条判据换成真标记的副产品）
        assert '\x01' not in text

    def test_control_flow_export_goes_through_the_generation_gate(self,
                                                                 tmp_path):
        """
        **控制流导出也要过生成门禁**（2026-10-08，契约 B1）。

        它以前是裸 `open(path, 'w')` —— 于是"目标是一个手写 .rpy"时会被
        静默覆盖（`default`/`define` 全丢），label 重名也不报。
        """
        out = tmp_path / 'handwritten.rpy'
        out.write_text('# 我手写的\nlabel mine:\n    return\n', encoding='utf-8')
        g = Graph()
        g.add_node('a', 0, 0)
        artifact = export_to_renpy(g, out)
        assert artifact.written is False, '手写文件被覆盖了'
        assert out.read_text(encoding='utf-8').startswith('# 我手写的'), \
            '目标文件被改动了'
        assert artifact.blocking, '拦下却没有任何 blocking 说明'
        # 对照副本要留着（不然人没法看产物长什么样）
        assert artifact.review_path and Path(artifact.review_path).is_file()

    def test_control_flow_export_is_allowed_to_overwrite_its_own_output(
            self, tmp_path):
        """工具自己生成的产物**必须能再导出一次**（带生成标记 → 不判手写）。"""
        out = tmp_path / 'gen.rpy'
        g = Graph()
        g.add_node('a', 0, 0)
        first = export_to_renpy(g, out)
        assert first.written is True
        second = export_to_renpy(g, out)
        assert second.written is True, '再导出一次被误判成手写文件'

    def test_missing_target_node_skipped(self, export):
        """指向不存在节点的边不应写出坏 label。"""
        g = Graph()
        a = g.add_node('a', 0, 0)
        g.add_edge(a, 999)
        text = export(g)
        assert 'jump' not in text

    def test_empty_graph_exports_header(self, export):
        g = Graph()
        text = export(g)
        assert '自动生成的控制流' in text

    def test_export_failure_reports_error(self, export, notify_calls):
        g = Graph()
        g.add_node('a', 0, 0)
        export_to_renpy(g, '/nonexistent/dir/out.rpy')
        assert notify_calls.of('error')

    def test_success_reports_info(self, export, notify_calls, tmp_path):
        g = Graph()
        g.add_node('a', 0, 0)
        export_to_renpy(g, tmp_path / 'ok.rpy')
        assert notify_calls.of('info')


class TestStringRulesAreShared:
    """
    **字符串转义只有一份规则**：控制流导出与章节导出必须写出同一行。

    病根（2026-10-05，导出缺陷清单 O4 取证）：控制流导出自己手写
    `f'"{opt}"'` —— 选项文案带 `"` 时写出 `"他说："你好"":`（**语法错**）、
    带换行时写出**跨物理行**的字符串（产物不可解析）。章节那条路一直是对的
    （`beat_render._quote` 转义 `\\ " \\n \\r \\t`）。

    "两套 .rpy 生成器并存"本身是结构风险（清单 O4）；这一组把它收敛成
    **可执行的契约**：同一段文本，两条路必须给出同一个字面量，而且产物
    必须能被自己的解析器吃回去。
    """

    ADVERSARIAL = ['他说："你好"', '第一行\n第二行', 'C:\\new', 'a\tb',
                   '带 # 号', '全　角', "单引号 ' 在里面", '正常的一句']

    @staticmethod
    def _menu_graph(opt):
        g = Graph()
        a = g.add_node('start', 0, 0)
        b = g.add_node('end', 0, 0)
        c = g.add_node('other', 0, 0)
        g.add_edge(a, b, option_text=opt)
        g.add_edge(a, c, option_text='No')
        return g

    def test_it_uses_the_same_quoting_rule_as_the_chapter_renderer(self, export):
        from storygraph.story.beat_render import quote_string
        for opt in self.ADVERSARIAL:
            text = export(self._menu_graph(opt))
            assert f'        {quote_string(opt)}:' in text, (opt, text)

    def test_adversarial_option_text_never_breaks_the_script(self, tmp_path):
        import re
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        for i, opt in enumerate(self.ADVERSARIAL):
            out = tmp_path / f'opt{i}.rpy'
            export_to_renpy(self._menu_graph(opt), out)
            pr, _ = IncrementalRenPyAnalyzer().analyze(files=[out], graph=None)
            errs = list(getattr(pr, 'errors', None) or [])
            assert not errs, (opt, errs)
            # 每行的引号成对（跨物理行字符串会让行内引号落单）
            for ln in out.read_text(encoding='utf-8').splitlines():
                body = re.sub(r'\\.', '', ln)
                assert body.count('"') % 2 == 0, (opt, ln)
