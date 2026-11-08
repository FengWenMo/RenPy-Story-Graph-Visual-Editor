# -*- coding: utf-8 -*-
"""
`return` 后面那条 `$ Plotnode223=1`：工具自己写的死代码（2026-11-08 第二十轮）。

用户原话 + 截图（`演出剧本/chapter2_2_2.rpy` 第 272 行）：

> 还有个问题……那个 Plotnode223=1 完全就是工具自己改的啊？？？我也不知道
> 为什么会有这么个东西存在……太苦恼了！谁没事儿手敲这玩意儿啊！！！
> 是不是 bug？能不能测试复刻一下……？

**是 bug，能复刻**：

    label c1:
        e "一"
        "二"
        return
        $ Plotnodes223=1        ← 永远执行不到

根因：`session.apply_node_variable_edits` 里"新增的剧情变量"没有锚点时
落到 **`beats[-1]`（本章最后一拍）**，而最后一拍几乎总是 `return`
（`kind='flow'`）。一拍渲染出来是"控制流语句 → 代码"，所以 `$ …` 排到了
`return` **后面** —— 死代码，标志位永远是初值（`plottree.rpy` 里
`default Plotnodes223 = 0`）。

修法：
  · 新增变量落到**最后一个非控制流拍**（`_last_code_beat`）；
  · 整章只有控制流拍 → 报 `unplaced`，不写一行不执行的；
  · 体检加 W204，给**已经写坏的文件**留一张网（用户那份就是这种）。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

SCRIPT = ('label c1:\n'
          '    e "一"\n'
          '    "二"\n'
          '    return\n')

ONLY_RETURN = ('label c2:\n'
               '    return\n')


def _project(tmp_path, text=SCRIPT, name='c1.rpy'):
    ch = tmp_path / 'game' / 'script' / name
    ch.parent.mkdir(parents=True, exist_ok=True)
    ch.write_text(text, encoding='utf-8')
    return ch


def _import(session, ch):
    from storygraph.graph.models import Graph

    res = session.import_chapters([ch], Graph())
    assert res['ok'], res
    return res['graph']


def _session(tmp_path):
    from storygraph.app.editor import EditorSession

    return EditorSession(project_root=tmp_path)


class TestANewVariableLandsBeforeTheReturn:
    def test_the_flow_beat_does_not_get_the_code(self, tmp_path, qapp):
        ch = _project(tmp_path)
        s = _session(tmp_path)
        graph = _import(s, ch)
        node = next(n for n in graph.nodes.values() if n.label_name == 'c1')

        out = s.apply_node_variable_edits(graph, node.id, ['Plotnodes223=1'])
        assert out['ok'], out
        assert out['applied'] == 1 and out['unplaced'] == 0, out
        flow = next(b for b in s.beats if b.kind == 'flow')
        assert not str(flow.code or '').strip(), \
            f'代码又落到控制流拍上了：{flow.code!r}'
        assert any('Plotnodes223=1' in str(b.code or '') for b in s.beats)

    def test_the_export_puts_it_before_return(self, tmp_path, qapp):
        ch = _project(tmp_path)
        s = _session(tmp_path)
        graph = _import(s, ch)
        node = next(n for n in graph.nodes.values() if n.label_name == 'c1')
        s.apply_node_variable_edits(graph, node.id, ['Plotnodes223=1'])
        res = s.export(str(ch))
        assert res['ok'], res
        lines = [ln.strip() for ln in
                 ch.read_text(encoding='utf-8').splitlines() if ln.strip()]
        assert '$ Plotnodes223=1' in lines, lines
        assert lines.index('$ Plotnodes223=1') < lines.index('return'), lines

    def test_an_old_broken_file_is_reported_not_silently_kept(self, tmp_path,
                                                              qapp):
        """已经写坏的那种（`return` 后面一行 `$ …`）→ 体检点名 W204。"""
        from storygraph.pipeline.validate import validate_level2

        ch = _project(tmp_path,
                      'label c1:\n    e "一"\n    return\n    $ Plotnodes223=1\n')
        s = _session(tmp_path)
        s.load(str(ch))
        issues = validate_level2(s.beats)
        codes = [i.code for i in issues]
        assert 'W204' in codes, [(i.code, i.message) for i in issues]
        msg = ' '.join(i.message for i in issues if i.code == 'W204')
        assert '不会执行' in msg and 'Plotnodes223' in msg, msg


class TestALabelWithNothingButReturn:
    def test_it_reports_unplaced_and_writes_no_dead_line(self, tmp_path, qapp):
        ch = _project(tmp_path, ONLY_RETURN, name='c2.rpy')
        s = _session(tmp_path)
        graph = _import(s, ch)
        node = next(n for n in graph.nodes.values() if n.label_name == 'c2')

        out = s.apply_node_variable_edits(graph, node.id, ['flag=1'])
        assert out['ok'], out
        assert out['unplaced'] == 1, out
        assert not any(str(b.code or '').strip() for b in s.beats), \
            [(b.kind, b.code) for b in s.beats]


def test_a_variable_anchored_to_a_chosen_beat_still_works(tmp_path, qapp):
    """老契约不能破：给了"落在第几拍"就照那个拍写（不跳到别的拍）。"""
    ch = _project(tmp_path)
    s = _session(tmp_path)
    graph = _import(s, ch)
    node = next(n for n in graph.nodes.values() if n.label_name == 'c1')
    out = s.apply_node_variable_edits(graph, node.id, ['flag=1'], insert_beat=0)
    assert out['ok'] and out['applied'] == 1, out
    assert 'flag=1' in str(s.beats[0].code or ''), s.beats[0].code
