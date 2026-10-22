"""
tests/test_flow_kind_stage2.py
==============================
控制流卡**第二阶段**：`kind='flow'` + 参数 / `from` 可视编辑。

第一阶段（`test_flow_card.py`）把 `call` / `jump` 变成了卡片上的一等公民，
但拍本身还是 `kind=narration` + `raw`，参数与 `from` 只能在高级区手写。

第二阶段这两条一起收：

  1. **`kind='flow'`** —— 控制流不再是"没台词的旁白"；
  2. **参数 `(…)` / `from …` 可编辑** —— 和目标是同一条控制流卡。

判据分四组：

  A. 升 kind：解析出 `flow`、渲染回原样、**表里写 `flow`、读回来还是 `flow`**
     （这一条最容易漏 —— 漏了就是"能导出、读回来认不出"，卡片静默变回空白）；
  B. 老数据兼容：`kind=narration` + `raw='call side'` 仍然认（表是老项目导出的）；
  C. `update_beat_flow` 的 args / from：改、清空、非法拒绝（一个字节都不动）；
  D. jump 不接受参数 / from —— 切过去时明确丢掉，而不是留着生成跑不起来的脚本。
"""

import openpyxl
import pytest

from storygraph.story.beat_model import Beat
from storygraph.story.flow import parse_flow, update_beat_flow


def _beat(raw='call side', kind='flow'):
    return Beat(kind=kind, raw=raw)


class TestKindIsFlow:
    def test_parsing_call_produces_kind_flow(self, tmp_path):
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result

        src = tmp_path / 'a.rpy'
        src.write_text('label main:\n    e "a"\n    call side\n'
                       '    jump ending\n    return\n', encoding='utf-8')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(str(src))))
        kinds = [b.kind for b in beats if b.raw]
        assert kinds == ['flow', 'flow'], kinds

    def test_flow_still_renders_verbatim(self, tmp_path):
        from storygraph.parsing.renpy_parser import (ParseResultBuilder,
                                                     RenPyParser)
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.beat_render import render_beats_to_rpy

        src = tmp_path / 'a.rpy'
        src.write_text('label main:\n    e "a"\n'
                       '    call side(1, 2) from _c1\n    return\n',
                       encoding='utf-8')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(str(src))))
        out = render_beats_to_rpy(beats)
        assert 'call side(1, 2) from _c1' in out

    def test_table_roundtrip_keeps_flow(self, tmp_path):
        """表里写 `flow`、读回来还得是 `flow` —— 漏了就是静默退化。"""
        from storygraph.app import tables
        from storygraph.story.excel_import import import_beats_from_excel

        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True)
        src = root / 'game' / 'a.rpy'
        src.write_text('label main:\n    e "a"\n    call side\n'
                       '    return\n', encoding='utf-8')
        table = tables.generate_table(src, project_root=root)['table_path']

        wb = openpyxl.load_workbook(str(table))
        ws = wb['Beats']
        heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1])
                 if c.value}
        kinds = [str(ws.cell(r, heads['类型']).value or '').strip()
                 for r in range(2, ws.max_row + 1)]
        assert 'flow' in kinds, f'表里没写 flow：{kinds}'

        back = import_beats_from_excel(table)
        flow = [b for b in back if b.kind == 'flow']
        assert flow, f'读回来没有 flow 拍了：{[b.kind for b in back]}'
        info = parse_flow(flow[0])
        assert info and info['target'] == 'side', info


class TestOldDataStillReads:
    def test_narration_kind_still_recognised(self):
        """老表里 `kind=narration` + `raw='call side'` 不能因为升级而读不出。"""
        info = parse_flow(_beat('call side', kind='narration'))
        assert info and info['target'] == 'side'

    def test_say_kind_is_not_a_flow(self):
        assert parse_flow(_beat('call side', kind='say')) is None


class TestArgsAndFrom:
    def test_set_args(self):
        b = _beat('call side')
        res = update_beat_flow(b, args='(1, 2)')
        assert res['ok'] and res['info']['args'] == '(1, 2)'
        assert b.raw == 'call side(1, 2)'

    def test_set_from(self):
        b = _beat('call side')
        res = update_beat_flow(b, from_clause='_call_side_1')
        assert res['ok']
        assert b.raw == 'call side from _call_side_1'

    def test_both(self):
        b = _beat('call side')
        update_beat_flow(b, target='other', args='(x)', from_clause='_c')
        assert b.raw == 'call other(x) from _c'

    def test_none_keeps_old_value(self):
        """只改目标时不该把已有参数 / from 弄丢。"""
        b = _beat('call side(1) from _c1')
        update_beat_flow(b, target='ending')
        assert b.raw == 'call ending(1) from _c1'

    def test_empty_string_clears(self):
        b = _beat('call side(1) from _c1')
        update_beat_flow(b, args='', from_clause='')
        assert b.raw == 'call side'

    @pytest.mark.parametrize('bad', ['(1', '1, 2)', '(', ')'])
    def test_unbalanced_args_are_refused(self, bad):
        b = _beat('call side(1) from _c1')
        before = b.raw
        res = update_beat_flow(b, args=bad)
        assert res['ok'] is False, f'{bad!r} 竟然被接受'
        assert b.raw == before, '拒绝之后还是改了 raw'

    def test_bad_from_is_refused(self):
        b = _beat('call side')
        before = b.raw
        res = update_beat_flow(b, from_clause='not a label!')
        assert res['ok'] is False
        assert b.raw == before

    def test_jump_drops_args_and_from(self):
        """Ren'Py 的 jump 不接受这两样 —— 留着就是跑不起来的脚本。"""
        b = _beat('call side(1, 2) from _c1')
        res = update_beat_flow(b, flow_type='jump')
        assert res['ok']
        assert b.raw == 'jump side'

    def test_args_with_nested_parens_and_quotes(self):
        b = _beat('call side')
        res = update_beat_flow(b, args='(f(1), "a)b")')
        assert res['ok'], res['error']
        assert b.raw == 'call side(f(1), "a)b")'
        assert parse_flow(b)['args'] == '(f(1), "a)b")'


class TestFlowEdgesAreTheSource:
    """
    第三组：`flow_edges(beats)` —— **图边的唯一真源**（第二阶段 B）。

    这一轮先把"真源"的函数与 `Edge.origin` 立起来；把"导入时按它重建边"
    留给下一轮（那一步会改所有导入路径，要单独跑全量）。
    """

    def test_edges_carry_their_line_id(self):
        from storygraph.story.flow import flow_edges

        beats = [Beat(kind='flow', label='main', raw='call side',
                      line_id='main.0002'),
                 Beat(kind='flow', label='main', raw='call side',
                      line_id='main.0007'),
                 Beat(kind='flow', label='main', raw='jump ending',
                      line_id='main.0011')]
        edges = flow_edges(beats)
        assert [e['to'] for e in edges] == ['side', 'side', 'ending']
        # **同一个目标被 call 两次，必须是两条**（靠 line_id 区分）——
        # 老做法按 from+type+to 找第一条，这种场景下会指错。
        assert [e['line_id'] for e in edges] == ['main.0002', 'main.0007',
                                                 'main.0011']
        assert [e['type'] for e in edges] == ['call', 'call', 'jump']

    def test_args_and_from_do_not_become_part_of_the_edge(self):
        """目标只由 label 决定；参数 / from 不是路径的一部分。"""
        from storygraph.story.flow import flow_edges

        edges = flow_edges([Beat(kind='flow', label='main',
                                 raw='call side(1, 2) from _c1',
                                 line_id='main.0002')])
        assert edges[0]['to'] == 'side'
        assert edges[0]['from_clause'] == '_c1'

    def test_plain_beats_produce_no_edges(self):
        from storygraph.story.flow import flow_edges

        assert flow_edges([Beat(kind='say', speaker='e', text='hi')]) == []

    def test_graph_edges_default_to_source(self):
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('a', 0, 0)
        g.add_node('b', 1, 1)
        eid = g.add_edge(1, 2, 'call')
        assert g.edges[eid].origin == 'source'

    def test_manual_edge_keeps_its_origin(self):
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('a', 0, 0)
        g.add_node('b', 1, 1)
        eid = g.add_edge(1, 2, 'jump', origin='manual')
        assert g.edges[eid].origin == 'manual'

    def test_origin_survives_copy_and_layout_roundtrip(self):
        """一存一读不能把 `manual` 变成 `source` —— 否则草图会被派生删掉。"""
        import copy
        from storygraph.graph.models import Graph

        g = Graph()
        g.add_node('a', 0, 0)
        g.add_node('b', 1, 1)
        eid = g.add_edge(1, 2, 'jump', origin='manual')
        assert copy.deepcopy(g.edges[eid]).origin == 'manual'

        # 真的走一遍「存布局 → 读布局」（不是只看序列化函数）
        import json
        import tempfile
        from pathlib import Path
        from storygraph.app.session_graph import (GraphSessionMixin,
                                                  graph_from_layout_dict)
        d = Path(tempfile.mkdtemp()) / 'layout.json'
        # `save_layout` 是 mixin 方法，只用到 self 上的两三个属性 → 用空壳调
        class _Host(GraphSessionMixin):
            def __init__(self):
                pass
        _Host().save_layout(g, str(d))
        data = json.loads(d.read_text(encoding='utf-8'))
        assert data['edges'][str(eid)]['origin'] == 'manual'
        back = graph_from_layout_dict(data)
        assert back.edges[eid].origin == 'manual', \
            '一存一读把 manual 变成了 source —— 草图会在派生时被删掉'
