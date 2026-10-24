# -*- coding: utf-8 -*-
"""
控制流导出：**条件 / 菜单分支必须有兜底出口**。

Ren'Py 的语义（实测）：

  · `if cond:` 不成立 → 执行落到 `if` 块后面那一行；
  · `menu:` 的所有选项条件都不成立 → 整个 menu 被跳过，同样落到下一行。

如果 `X_control` 的末尾正好是这两种块、后面又没有 `return`，执行会**掉进
文件的下一块 label** —— 把另一章顺带跑一遍，而且不报任何错。这里钉住
"全条件分支后一定兜一句 return"，同时钉住"有无条件选项时不要多写 return"。
"""
from __future__ import annotations

from storygraph.graph.models import Graph
from storygraph.pipeline.renpy_exporter import (export_to_renpy,
                                                render_control_text)


def _graph():
    g = Graph()
    a = g.add_node('a', 0, 0)
    b = g.add_node('b', 0, 0)
    c = g.add_node('c', 0, 0)
    return g, a, b, c


def _section(text, label):
    """截出 `label …:` 到下一个 `label ` 之间的文本。"""
    start = text.index(f'label {label}:')
    rest = text[start + 1:]
    nxt = rest.find('\nlabel ')
    return text[start:] if nxt < 0 else text[start:start + 1 + nxt]


def test_conditional_edge_returns_when_false():
    g, a, b, _c = _graph()
    g.add_edge(a, b, condition='score > 3')
    sec = _section(render_control_text(g), 'a_control')
    lines = [ln for ln in sec.splitlines() if ln.strip()]
    assert lines[-1].strip() == 'return', (
        '条件不成立时会掉进下一个 label：\n' + sec)
    assert 'if score > 3:' in sec
    assert 'jump b_control' in sec


def test_all_conditional_menu_returns_when_nothing_is_selectable():
    g, a, b, c = _graph()
    g.add_edge(a, b, option_text='去屋顶', condition='has_key')
    g.add_edge(a, c, option_text='留在教室', condition='not has_key')
    sec = _section(render_control_text(g), 'a_control')
    lines = [ln for ln in sec.splitlines() if ln.strip()]
    assert lines[-1].strip() == 'return', (
        '所有选项都可能不出现时没有兜底：\n' + sec)


def test_menu_with_an_unconditional_option_does_not_add_return():
    g, a, b, c = _graph()
    g.add_edge(a, b, option_text='去屋顶')
    g.add_edge(a, c, option_text='留在教室', condition='flag')
    sec = _section(render_control_text(g), 'a_control')
    assert '\n    return' not in sec, (
        '总有一个选项可选，不该多写 return：\n' + sec)


def test_render_is_what_export_writes(tmp_path):
    g, a, b, _c = _graph()
    g.add_edge(a, b, condition='flag')
    text = render_control_text(g)
    out = tmp_path / 'flow.rpy'
    assert export_to_renpy(g, out, project_root=tmp_path).written
    written = out.read_text(encoding='utf-8')
    assert text in written, 'render_control_text 与落盘产物不是同一份实现'
