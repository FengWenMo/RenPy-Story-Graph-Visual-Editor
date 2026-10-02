"""
tests/test_exporter_modes.py
============================
控制流导出的**模式判定**回归（用户 2026-10-05）。

> 要是有人非要以 A_control 这种标签起名怎么办？（想一个解决方案并执行，
> 不得是变成保留字，因为你不能阻拦用户起什么名）

方案：判据来自 **内容 + 显式标志**（`Graph.control_flow`，导入时按
"文件里有没有演出语句"设定），**名字完全不参与**。
曾经靠"节点名是否以 `_control` 结尾"猜 —— 用户把章节命名成 `A_control`
就会被误判（既会漏生成包装，又等于把名字变成保留字）。
"""


def _export(graph, tmp_path, **kw) -> str:
    from storygraph.pipeline.renpy_exporter import export_to_renpy
    out = tmp_path / 'flow.rpy'
    export_to_renpy(graph, str(out), **kw)
    return out.read_text(encoding='utf-8')


def test_chapter_named_a_control_still_gets_a_wrapper(tmp_path):
    """章节真的叫 `A_control` → 包装成 `A_control_control`（不靠名字猜）。"""
    from storygraph.graph.models import Graph
    g = Graph()
    a = g.add_node('A_control', 0, 0)
    g.add_edge(a, a, etype='jump')
    g.control_flow = False                      # 有正文 → 章节图
    text = _export(g, tmp_path)
    assert 'label A_control_control:' in text, text
    assert 'call A_control' in text
    assert '\nlabel A_control:\n' not in text, '名字像控制流就不给包装 = 拿名字当保留字'


def test_control_flow_graph_is_rebuilt_verbatim(tmp_path):
    """控制流图（导入的只有 call/jump）→ 原样重建，一个 `_control` 都不加。"""
    from storygraph.graph.models import Graph
    g = Graph()
    c = g.add_node('chapter1_1_control', 0, 0)
    n = g.add_node('chapter1_2_control', 0, 0)
    g.add_edge(c, n, etype='jump')
    g.control_flow = True
    text = _export(g, tmp_path)
    assert 'label chapter1_1_control:' in text
    assert 'chapter1_1_control_control' not in text, '控制流图不许再补一层'
    assert 'jump chapter1_2_control' in text


def test_all_unconditional_edges_are_emitted(tmp_path):
    """`call` + `jump` 两条无条件边都要写出来（以前只写第一条 → jump 全丢）。"""
    from storygraph.graph.models import Graph
    g = Graph()
    a = g.add_node('chapter1_1_control', 0, 0)
    b = g.add_node('chapter1_1', 0, 0)
    c = g.add_node('chapter1_2_control', 0, 0)
    g.add_edge(a, b, etype='call')
    g.add_edge(a, c, etype='jump')
    g.control_flow = True
    text = _export(g, tmp_path)
    assert 'call chapter1_1' in text
    assert 'jump chapter1_2_control' in text, f'jump 被丢掉了：{text}'


def test_wrapper_argument_overrides_the_flag(tmp_path):
    """界面的"按章节图导出"入口可以覆盖标志（不需要用户改名）。"""
    from storygraph.graph.models import Graph
    g = Graph()
    g.add_node('A_control', 0, 0)
    g.control_flow = True                        # 标志说"控制流"
    forced = _export(g, tmp_path, wrapper=True)  # 但用户显式要求章节图
    assert 'label A_control_control:' in forced, forced


def test_content_judgement_ignores_names(tmp_path):
    """导入时的内容判据：只有 call/jump 的文件 = 控制流；有台词 = 章节。"""
    from storygraph.app.editor import files_look_like_control_flow
    control = tmp_path / 'whatever_name.rpy'
    control.write_text(
        'label anything_control:\n'
        '    call chapter1_1\n'
        '    jump other_thing\n',
        encoding='utf-8')
    chapter = tmp_path / 'also_control_named.rpy'
    chapter.write_text(
        'label A_control:\n'
        '    "这是一句台词"\n'
        '    return\n',
        encoding='utf-8')
    assert files_look_like_control_flow([control]) is True
    assert files_look_like_control_flow([chapter]) is False
    assert files_look_like_control_flow([control, chapter]) is False
