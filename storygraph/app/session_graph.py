"""
storygraph.app.session_graph
============================
`EditorSession` 的**剧情图那一半**：布局存取、章节导入、结构分析、剧情变量。

为什么把它拆出来（P2-6 第二步，2026-10-05）
--------------------------------------------
`EditorSession` 是 1900 行的上帝对象（74 个方法），混装了四块互不相干的
职责：表格/拍编辑、剧情图、资源资产、协作+导出。清单里
（`docs/问题清单与优化路线_2026-10-03.md` §P2-6）一直记着它。

拆的顺序刻意**从风险最低的一块开始**：剧情图这块绝大多数方法只是
"转发给 `graph/` 与 `parsing/`，再把结果包成结构化返回值"，几乎不碰
`self.beats`。行为级 golden（`tests/test_editor_session_behavior.py`）
与内部快照（`tests/test_editor_session_snapshot.py`）两层网都在。

**做成 mixin 而不是独立对象**，是为了让调用方一行都不用改：
`EditorSession(GraphSessionMixin)` 之后 `session.save_layout(...)`
照旧能用。这是一次**搬家**，不是改契约 —— 契约改了就要连着改前端，
而前端此刻没有网兜着（`ui_qt` 那些调用点没有行为测试）。

职责边界（写下来免得下次又混回去）
----------------------------------
**属于这里**：拿 `graph` 当参数的、读写布局 JSON 的、按文件导入/移除章节的、
  分析可达性的、读写模块剧情变量的。
**不属于这里**：`self.beats` 的增删改查（表格侧）、资源表
  （`registry` / `asset_*`）、协作、导出落盘。

以后新增方法时按这条问一句："**它的主语是图还是拍？**"

与 `EditorSession` 的隐式依赖（只有读，没有写）
-----------------------------------------------
  · `self.project_root` —— `missing_chapter_files` 找章节文件时用；
  · `self.registry()` / `self.refresh_symbols()` —— `import_chapters`
    合并脚本里的 `define` 之后要重算符号表。
两者都定义在 `EditorSession` 上，mixin 只调用不分管 —— 这是 mixin
（而不是独立类）能成立的前提。
"""
from __future__ import annotations

from pathlib import Path
import re

from storygraph.core.safeguard import swallow


#: "演出语句"的正则：**有这些就说明这个文件里有正文**（台词/舞台/音频/语音）。
#: 刻意**不包含** menu —— 在用户的分工里，选择与条件属于控制流，不属于章节正文。
#:
#: 它跟着 `files_look_like_control_flow` 一起搬过来（那是它唯一的用户）。
#: `editor.py` 里还留了一个 re-export，供旧写法 import。
_BODY_LINE_RE = re.compile(
    r'^[ \t]*(?:'
    r'(?:[A-Za-z_]\w*[ \t]+)*"|'          # "台词" / jc "台词"
    r"(?:[A-Za-z_]\w*[ \t]+)*'|"          # 单引号写法
    r'(?:scene|show|hide|play|stop|queue|voice|window|extend|nvl)\b'
    r')', re.MULTILINE)


__all__ = ['GraphSessionMixin', 'files_look_like_control_flow',
           'graph_from_layout_dict']


def files_look_like_control_flow(paths) -> bool:
    """
    这批 .rpy 是不是"**控制流脚本**"？（判据只看**内容**，不看文件名/标签名）

    用户 2026-10-05 的要求：

    > 要是有人非要以 A_control 这种标签起名怎么办？……不得是变成保留字，
    > 因为你不能阻拦用户起什么名。

    判据：文件里**一条演出语句都没有**（没有台词、没有 scene/show/hide、
    没有 play/voice）→ 控制流脚本。menu / if / call / jump / `$` /
    `return` 都算控制流，不算正文 —— 与用户的分工一致（选择分支与条件分支
    住在控制流里）。
    """
    seen = False
    for fp in (paths or ()):
        try:
            text = Path(fp).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        seen = True
        if _BODY_LINE_RE.search(text):
            return False
    return seen
def graph_from_layout_dict(data) -> 'object':
    """
    布局 JSON（dict）→ 新的 `Graph`。**只做数据转换，不碰文件**。

    为什么单独一个函数：两条路都要用它 ——
      · `EditorSession.load_layout`（从文件加载）；
      · Qt 窗口的 `_apply_layout_data`（旧版 `LoadLayoutCommand` 回调，
        图级撤销/重做要用）。
    两份转换逻辑一漂移，"撤销后加载的图"和"直接加载的图"就会不一样。

    未知字段忽略（旧文件里有 `is_current` 之类现在不用的键）；
    `next_ids` 缺失时按现有最大 id 兜底（手改过的文件也不至于崩）。
    """
    from storygraph.graph.models import Edge, Graph, Node
    graph = Graph()
    for nid_str, nd in ((data or {}).get('nodes') or {}).items():
        nid = int(nid_str)
        nd = nd or {}
        node = Node(nid, str(nd.get('label') or ''),
                    float(nd.get('x') or 0), float(nd.get('y') or 0))
        node.is_current = bool(nd.get('is_current', False))
        # 迁移（2026-10-05）：新文件带 `statements`（结构化，含出处/条件），
        # 老文件只有 `operations`（裸字符串）—— 两种都能读，
        # 读不出结构就退回字符串路径（老的 `.json` 一张都不会作废）。
        stmts = nd.get('statements')
        if isinstance(stmts, list) and stmts:
            from storygraph.core.statement import Statement
            try:
                node.statements = [Statement.from_dict(s) for s in stmts
                                   if isinstance(s, dict)]
            except Exception as e:            # noqa: BLE001 - 坏文件别让人打不开
                swallow('editor.graph_from_dict.statements', e)
                node.set_operations(list(nd.get('operations') or []))
        else:
            node.set_operations(list(nd.get('operations') or []))
        node.file_path = nd.get('file_path')
        node.line_number = nd.get('line_number')
        graph.nodes[nid] = node
    for eid_str, ed in ((data or {}).get('edges') or {}).items():
        eid = int(eid_str)
        ed = ed or {}
        graph.edges[eid] = Edge(
            eid, ed.get('from'), ed.get('to'),
            str(ed.get('type') or 'jump'),
            str(ed.get('condition') or ''),
            str(ed.get('option_text') or ''),
            str(ed.get('from_clause') or ''))
    nx = (data or {}).get('next_ids') or {}
    graph.next_node_id = int(nx.get('node')
                             or (max(graph.nodes) + 1 if graph.nodes else 1))
    graph.next_edge_id = int(nx.get('edge')
                             or (max(graph.edges) + 1 if graph.edges else 1))
    return graph


class GraphSessionMixin:
    """
    剧情图那一半（见模块 docstring 的职责边界）。

    它**要求宿主提供** `registry()` / `refresh_symbols()` / `project_root`
    —— 前者是"资源从哪来"的基建（同时服务导出与这里），后两个是项目上下文。
    见 `REQUIRES` 与 `tests/test_editor_session_split.py` 的守卫。
    """

    #: 本 mixin 要求宿主（或其它 mixin）提供的成员。
    #: **实测**出来的（`self.*` 静态扫一遍），不是凭印象写的 ——
    #: 只有两个方法真正用到：`import_chapters` 要
    #: `registry()` + `refresh_symbols()`（合并脚本 `define` 后重算符号表），
    #: `missing_chapter_files` 要 `project_root`。
    REQUIRES = ('registry', 'refresh_symbols', 'project_root')

    def save_layout(self, graph, target) -> dict:
        """
        把剧情图（含每个块的手工坐标）存成 JSON。

        用户 2026-10-04：

        > 我的旧版本有…有保存布局.json！现在这个新版本呢？
        > 为何模块编辑器的功能缺失那么多？？？

        字段**故意和旧版一模一样**（旧版 `graph_view.save_layout`）：

            {"nodes": {"<id>": {label,x,y,is_current,operations,file_path,
                                line_number}},
             "edges": {"<id>": {from,to,type,condition,option_text}},
             "next_ids": {"node": n, "edge": m}}

        这样用户以前存过的 `.json` 还能直接加载回来（新格式会让旧文件
        白白作废，而这是一份"手排的版式"，重建很贵）。

        迁移（2026-10-05）：**新增** `statements` 字段（结构化，含 `source`
        文件/行号与 `condition`），`operations` 照旧写 —— 新版本能读回全部
        信息，老版本读 `operations` 也不受影响（只多一个它不认识的键）。
        """
        import json
        data = {
            'nodes': {str(nid): {
                'label': n.label_name,
                'x': n.x, 'y': n.y,
                'is_current': bool(getattr(n, 'is_current', False)),
                'operations': list(getattr(n, 'operations', []) or []),
                # 结构化真源（老读者会忽略这一项）
                'statements': [s.to_dict()
                               for s in (getattr(n, 'statements', []) or [])],
                'file_path': getattr(n, 'file_path', None),
                'line_number': getattr(n, 'line_number', None),
            } for nid, n in (getattr(graph, 'nodes', {}) or {}).items()},
            'edges': {str(eid): {
                'from': e.from_id, 'to': e.to_id, 'type': e.type,
                'condition': e.condition, 'option_text': e.option_text,
                # `from` 子句也要存：不存的话"存布局 → 读布局"就把它丢了
                'from_clause': getattr(e, 'from_clause', ''),
            } for eid, e in (getattr(graph, 'edges', {}) or {}).items()},
            'next_ids': {
                'node': int(getattr(graph, 'next_node_id', 1)),
                'edge': int(getattr(graph, 'next_edge_id', 1)),
            },
        }
        try:
            Path(target).write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8')
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'path': str(target),
                    'nodes': 0, 'edges': 0}
        return {'ok': True, 'error': '', 'path': str(target),
                'nodes': len(data['nodes']), 'edges': len(data['edges'])}
    def load_layout(self, source) -> dict:
        """
        读回一份布局 JSON，返回**新的** `Graph`（不动调用方手上那张图）。

        `next_ids` 缺失时按最大值兜底（手工编辑过的文件也不至于崩）。
        未知字段一律忽略 —— 旧文件里有 `is_current` 之类我们现在不用的键。
        """
        import json
        try:
            data = json.loads(Path(source).read_text(encoding='utf-8'))
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': f'读不了这个 JSON：{e}',
                    'graph': None, 'nodes': 0, 'edges': 0}
        if not isinstance(data, dict) or 'nodes' not in data:
            return {'ok': False, 'error': '这个文件不是剧情图的布局（缺 nodes）',
                    'graph': None, 'nodes': 0, 'edges': 0}

        try:
            graph = graph_from_layout_dict(data)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': f'布局文件里有读不懂的条目：{e}',
                    'graph': None, 'nodes': 0, 'edges': 0}
        return {'ok': True, 'error': '', 'graph': graph,
                'nodes': len(graph.nodes), 'edges': len(graph.edges),
                'path': str(source)}
    def export_control_flow(self, graph, target, wrapper=None) -> dict:
        """
        把剧情图导出成 Ren'Py **控制流脚本**（`<label>_control` 那些 label）。

        用户 2026-10-04：

        > 我的旧版本有导出控制流图.rpy文件，label末尾加_controller的那个，
        > 你自己找！……现在这个新版本呢？

        `pipeline.renpy_exporter.export_to_renpy` **一直在**（旧版右栏
        「导出为Ren'Py（控制流图）」调的就是它），Qt 版从没接上而已。
        这里只做"调用 + 把结果变成结构化返回值"，不改它的生成规则。
        """
        from storygraph.pipeline.renpy_exporter import export_to_renpy
        n_nodes = len(getattr(graph, 'nodes', {}) or {})
        if not n_nodes:
            return {'ok': False, 'error': '剧情图是空的，没有可导出的控制流',
                    'path': str(target), 'labels': [], 'count': 0}
        if not isinstance(target, (str, Path)):
            # 2026-10-05：曾经有调用方把 `wrapper` 位置传进了 `target`，
            # 于是 `str(False)` 被当路径，工作目录里多出一个名叫 `False`
            # 的文件。落盘前先认路径——不是路径就别碰磁盘。
            return {'ok': False,
                    'error': f'导出路径不是路径：{target!r}（要给 .rpy 路径）',
                    'path': '', 'labels': [], 'count': 0}
        try:
            artifact = export_to_renpy(graph, str(target), wrapper=wrapper,
                                       project_root=self.project_root)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'path': str(target),
                    'labels': [], 'count': 0}
        # **门禁拦下**（label 重名 / 目标是手写文件…）时目标路径**不会被写**，
        # 只写一份 `<stem>.blocked.rpy` 供对照 —— 这时必须报失败，不许报
        # "导出成功"（B1：门禁的结论要走到调用方）。
        if artifact is not None and not getattr(artifact, 'written', True):
            why = '；'.join(i.format() for i in (artifact.blocking or [])[:3])
            return {'ok': False,
                    'error': ('生成的脚本会让游戏跑不起来，已拒写目标路径'
                              + (f'（{why}）' if why else '')),
                    'path': str(getattr(artifact, 'review_path', None) or target),
                    'labels': [], 'count': 0}
        if not Path(target).is_file():
            return {'ok': False, 'error': '文件没有生成（写盘失败？）',
                    'path': str(target), 'labels': [], 'count': 0}
        # 与导出器**同一处判定**（算两遍必然漂移：曾经 wrapper=False 时
        # 模式文案还写着"补 _control"，而标签其实没有补）
        controller_style = (bool(getattr(graph, 'control_flow', False))
                            if wrapper is None else not bool(wrapper))
        mode = ('原样重建（控制流图）' if controller_style
                else '补 _control 包装（章节图）')
        # 混合图（既导了章节、又导了 controller）如实告知一句：
        # 章节图模式下 controller 的 chapter0_0_control 会被再包一层
        # （chapter0_0_control_control）—— 产物能跑但冗余、容易看错。
        # **不在这里猜哪个节点才是控制流标签**（那又要拿名字当保留字用），
        # 只告知 + 让用户改用另一种导出入口。
        warnings = []
        if not controller_style:
            names = {str(n.label_name or '')
                     for n in (getattr(graph, 'nodes', {}) or {}).values()}
            mixed = sorted(n for n in names if f'{n}_control' in names)
            if mixed:
                warnings.append(
                    f'这张图同时含章节与已有控制流标签（如 {mixed[0]}）：'
                    f'章节图模式会给它们再包一层。建议分开导出，'
                    f'或用「按控制流图原样导出」。')
        labels = []
        for n in (getattr(graph, 'nodes', {}) or {}).values():
            name = str(n.label_name or '')
            if controller_style:
                labels.append(name)
            else:
                # **一律**补一层（哪怕章节就叫 A_control）——不许拿名字当保留字
                labels.append(f'{name}_control')
        return {'ok': True, 'error': '', 'path': str(target),
                'labels': labels, 'count': len(labels), 'mode': mode,
                'warnings': warnings}
    def missing_chapter_files(self, graph, project_root=None) -> dict:
        """
        图上"被别的章节跳到、但那个文件还没导入"的 label 分别在哪个文件里。

        用户 2026-10-03：

        > 我连线呢！？？？tkinter那一版都有连线，你这个为何显示不出来？

        实测原因不是渲染坏了，而是**他导入的那几章彼此之间没有跳转** ——
        `chapter1_1/1_2/1_3.rpy` 各自只有一个 label + `return`，串起它们的是
        `chapter_controller.rpy`。所以图里确实没有线可画。

        工具必须把这句话说出来，而不是让用户对着一张没有线的图猜。
        返回 `{'files': [...], 'unknown': [...], 'labels': [...]}`：

          · `files`   声明了那些 label 的文件（**建议一起导入** —— 导入后
            连线就出现了）；
          · `unknown` 任何文件里都找不到的 label（脚本里写错了 / 动态跳转）。
        """
        nodes = getattr(graph, 'nodes', None) or {}
        labels = sorted({str(getattr(n, 'label_name', '') or '')
                         for n in nodes.values()
                         if not getattr(n, 'file_path', None)} - {''})
        root = project_root or self.project_root
        if not root:
            return {'files': [], 'unknown': list(labels), 'labels': labels}
        already = {str(Path(f)) for f in self.graph_files(graph)}
        files = set()
        unknown = []
        try:
            from storygraph.parsing.discover import (
                find_files_referencing_labels, find_label_files)
            # ① 声明方向：图上的占位块（被跳到、但文件没导入）由谁声明
            if labels:
                declared = find_label_files(root, labels)
                files |= {str(p) for p in declared.values()}
                unknown = sorted(lb for lb in labels if lb not in declared)
            # ② 引用方向：**谁引用了我已经导入的这些章节**
            #    （导入的章节自己不含跳转时，串起它们的就是这个文件）
            imported_labels = sorted({str(getattr(n, 'label_name', '') or '')
                                      for n in nodes.values()
                                      if getattr(n, 'file_path', None)} - {''})
            if imported_labels:
                refs = find_files_referencing_labels(root, imported_labels)
                for paths in refs.values():
                    files |= {str(p) for p in paths}
        except Exception:                     # noqa: BLE001
            return {'files': [], 'unknown': list(labels), 'labels': labels}
        files = sorted(f for f in files if str(Path(f)) not in already)
        return {'files': files, 'unknown': unknown, 'labels': labels}
    def node_variables(self, graph, nid) -> dict:
        """
        一个模块里**给策划看的剧情变量**（技术代码一条都不返回）。

        用户 2026-10-03：

        > 这个所谓的"技术代码"，它不是变量，是具体的实现细节！……
        > 我不要再在变量的窗口上看到任何内部技术代码的名字！编辑变量是为了
        > 让策划和编剧调整和编排剧情分支用的地方！不是拿来填细节的！！！

        返回 `{ok, label, variables, tech_count, file, line}`；
        `tech_count` 只用于**说明"这个块还有 N 条实现细节，不在这里编辑"**，
        连它的名字都不给前端 —— 给了就一定会被显示出来。
        """
        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        if node is None:
            return {'ok': False, 'label': '', 'variables': [], 'tech_count': 0,
                    'file': '', 'line': None}
        from storygraph.story import story_ops
        return {
            'ok': True,
            'label': getattr(node, 'label_name', '') or '',
            'variables': list(story_ops.narrative_operations(node)),
            'tech_count': len(story_ops.tech_operations(node)),
            'file': str(getattr(node, 'file_path', '') or ''),
            'line': getattr(node, 'line_number', None),
        }
    def set_node_variables(self, graph, nid, variables) -> dict:
        """
        写回**只改剧情变量**；技术代码**原位保留**。

        见 `story_ops.merge_narrative_operations`：直接把整份 operations
        换成编辑器内容的话，"改一个 love += 1"会顺手把 `renpy.transition(...)`
        全删掉（用户看不见它们，也就不会发现自己删了东西）。

        迁移（2026-10-05）：走**结构化**合并（`merge_narrative_statements`）——
        技术代码那几条是原对象带回去的，`source`（文件/行号）与 `condition`
        不会因为"变量编辑器保存一次"就丢。
        """
        node = (getattr(graph, 'nodes', {}) or {}).get(nid)
        if node is None:
            return {'ok': False, 'error': '这个模块不在了', 'variables': 0,
                    'kept_tech': 0}
        from storygraph.story import story_ops
        original = list(getattr(node, 'statements', []) or [])
        if original:
            node.statements = story_ops.merge_narrative_statements(
                original, variables)
        else:                                 # 老路径（纯字符串构造出来的节点）
            merged = story_ops.merge_narrative_operations(
                list(getattr(node, 'operations', []) or []), variables)
            node.set_operations(merged)
        merged_ops = list(node.operations)
        return {
            'ok': True, 'error': '',
            'variables': len(story_ops.narrative_operations(merged_ops)),
            'kept_tech': len(story_ops.tech_operations(merged_ops)),
        }
    def check_variable_syntax(self, lines) -> list:
        """
        「语法检查」（旧版变量对话框里就有这个按钮）：逐行过一遍 **Python
        编译器**，把编译不过的行返回 `['第2行错误: x = = 5 - invalid syntax']`
        （空列表 = 全过）。

        ⚠ **2026-10-08 修**：这条路原来是"逐行喂 `analysis.apply_operation`，
        捕获它抛的异常"。但 `apply_operation` **自己**吞掉 `SyntaxError`
        并退化成正则（见它的 docstring：那正是为了"解析失败也别给出错误结论"），
        于是 `except` 分支**永远不会进** —— 检查器对任何输入都返回空列表，
        按钮永远显示"✔ N 行都能解析"。一个永远说没问题的检查器比没有更糟：
        它会**教会作者不看它**。

        现在的判据是"**这一行是不是合法的 Python 语句**"（这才是"语法检查"
        的字面含义，也是策划写变量的真实需求）。**只查语法，不查语义** ——
        `love += 1` 里 `love` 没定义是**运行期**的事，不算语法错误，
        否则每写一行都要被红一下。`$` 前缀与整行注释按 Ren'Py 写法剥掉/跳过。
        """
        import ast
        # ⚠ 传**字符串**时按行拆 —— 不许 `list('x = 1')` 变成逐字符
        # （`['x',' ','=',' ','1']`，那个孤零零的 `=` 会被报成语法错误）。
        lines = lines.splitlines() if isinstance(lines, str) else lines
        errors = []
        for i, line in enumerate(list(lines or []), 1):
            text = str(line).strip()
            if not text or text.startswith('#'):
                continue
            code = text[1:].strip() if text.startswith('$') else text
            if not code or code.startswith('#'):
                continue
            try:
                ast.parse(code, mode='exec')
            except SyntaxError as e:
                errors.append(f'第{i}行错误: {text} - {e.msg}')
        return errors
    def import_chapters(self, files, graph=None) -> dict:
        """
        把用户选中的章节 `.rpy` 解析进剧情图（**累加**，不动没选中的章节）。

        用户 2026-10-03 的原话（这条语义就是这么定的）：

        > 我没有叫你所有的文件全部导进剧情图内！……我是让你让用户自己导入
        > 章节！所谓的导入项目只是为了表格的 xlsx 的资源管理系统！
        > 不是把整个项目都排在剧情图上面！！！剧情图上只有用户自己导入的
        > 章节排版！！！跟条件设置！

        所以：
          · 图里**只**出现用户导入过的文件里的 label（以及它们之间的
            jump / call / 菜单选项与条件）；
          · 再导入一批**累加**，不覆盖上一批（`clear_graph=False`，
            后端 `GraphBuilder._prepare` 只在 clear 时才删节点）；
          · 同一个文件重复导入不会长重复节点（边按
            `(from,to,type,condition,option_text)` 去重）。

        顺带把脚本里的 `define e = Character(...)` 合并进资源表 —— 说话人
        候选就是这么来的（这一步原来在"重新扫描项目"里，而那个动作现在
        只管资源表，不能再顺手碰图）。
        """
        paths = []
        for fp in (files or ()):
            p = Path(fp)
            if p.exists() and p.suffix.lower() == '.rpy':
                paths.append(p)
        if not paths:
            return {'ok': False, 'error': '没有选中任何 .rpy 文件',
                    'graph': graph, 'added_nodes': 0, 'added_edges': 0,
                    'nodes': len(getattr(graph, 'nodes', {}) or {}),
                    'edges': len(getattr(graph, 'edges', {}) or {}),
                    'files': [], 'warnings': [], 'errors': []}

        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.graph.models import Graph

        if graph is None:
            graph = Graph()
        # "这张图是控制流图还是章节图" —— 按**导入内容**判定（不看名字）
        batch_control_only = files_look_like_control_flow(paths)
        before_nodes = len(graph.nodes or {})
        before_edges = len(graph.edges or {})

        analyzer = IncrementalRenPyAnalyzer()
        try:
            parse_result, graph = analyzer.analyze(
                files=paths, graph=graph, clear_graph=False)
        except Exception as e:                # noqa: BLE001
            return {'ok': False, 'error': str(e), 'graph': graph,
                    'added_nodes': 0, 'added_edges': 0,
                    'nodes': before_nodes, 'edges': before_edges,
                    'files': [str(p) for p in paths],
                    'warnings': [], 'errors': []}

        # 这张图的性质（控制流图 / 章节图）：**累加式**判定 ——
        # 只有"到目前为止导入的每一批都是控制流脚本"才算控制流图。
        # 于是"先导章节、再补 controller"= 章节图（导出时按章节补 _control）；
        # "只导 controller"= 控制流图（导出时原样重建）。名字不参与判断。
        if before_nodes == 0:
            graph.control_flow = bool(batch_control_only)
        else:
            graph.control_flow = (bool(getattr(graph, 'control_flow', False))
                                  and bool(batch_control_only))

        reg = self.registry()
        if reg is not None:
            try:
                from storygraph.story.beat_build import apply_script_definitions
                apply_script_definitions(reg, parse_result)
            except Exception:                 # noqa: BLE001
                swallow('editor.import_chapters')
        self.refresh_symbols()

        return {
            'ok': True, 'error': '',
            'graph': graph,
            'control_flow': bool(getattr(graph, 'control_flow', False)),
            'files': [str(p) for p in paths],
            'added_nodes': max(0, len(graph.nodes or {}) - before_nodes),
            'added_edges': max(0, len(graph.edges or {}) - before_edges),
            'nodes': len(graph.nodes or {}),
            'edges': len(graph.edges or {}),
            'warnings': [str(w) for w in (parse_result.warnings or [])],
            'errors': [str(e) for e in (parse_result.errors or [])],
        }
    def graph_files(self, graph) -> list:
        """图上已经导入了哪些文件（去重、排序）。"""
        seen = set()
        for node in (getattr(graph, 'nodes', {}) or {}).values():
            fp = getattr(node, 'file_path', None)
            if fp:
                seen.add(str(Path(str(fp))))
        return sorted(seen)
    def drop_chapters(self, graph, files) -> dict:
        """
        把某些章节**从剧情图上移除**（只改内存里的图，不动磁盘上的 .rpy）。

        按**文件**整章移除：用户心智里"这一章"就是一个 .rpy，
        逐个 label 删会留下半章残骸和悬空连线。
        """
        if graph is None:
            return {'removed_nodes': 0, 'nodes': 0, 'edges': 0}
        wanted = set()
        for fp in (files or ()):
            try:
                wanted.add(str(Path(str(fp))))
            except Exception as e:                 # noqa: BLE001
                swallow('app.session_graph.drop_chapters', e)
                continue
        doomed = [nid for nid, node in (graph.nodes or {}).items()
                  if str(getattr(node, 'file_path', '') or '') in wanted]
        for nid in doomed:
            graph.remove_node(nid)            # 连同它的连线一起删
        return {'removed_nodes': len(doomed),
                'nodes': len(graph.nodes or {}),
                'edges': len(graph.edges or {})}
    def analyze_graph_problems(self, graph) -> dict:
        """
        结构分析（变量区间 + 可达性），**同时**给人看的文本与给机器看的节点集合。

        多返回 `stranded_ids` / `independent_ids` / `entry_id` 的原因：
        前端以前只能拿到一段文字，于是"哪些块有问题"只能靠人眼在文字里找，
        图上什么都不会变。静态分析的结论必须能**画回图上**（标红/定位），
        否则它就只是"另一个要点开的对话框"。

        `independent` 是 label 名、`stranded` 是节点 id —— 这个口径差异
        以前散在各个前端里，拼错就把整个工程显示成一堆"不可达"。
        现在会话负责拼对一次（**按 label 名反查回节点 id**，同名节点全都算）。
        """
        blank = {
            'ok': False,
            'text': '（还没有可分析的图 —— 先用「➕ 导入章节 .rpy」加几章）',
            'summary': '还没有可分析的图 —— 先用「➕ 导入章节 .rpy」加几章。',
            'entry_id': None, 'entry_name': '',
            'stranded_ids': [], 'independent_ids': [], 'issue_ids': [],
            'dead_end_ids': [], 'problem_ids': [],
            'warnings': [], 'notes': [],
            'counts': {'nodes': 0, 'edges': 0, 'stranded': 0,
                       'dead_end': 0, 'independent': 0, 'issues': 0},
        }
        if graph is None or not getattr(graph, 'nodes', None):
            return blank

        from storygraph.graph.analysis import (analyze_reachability_full,
                                               reachable_from_all_roots,
                                               root_nodes,
                                               visualize_analysis_results)
        from storygraph.story import story_ops
        res = analyze_reachability_full(graph)

        by_name: dict = {}
        for nid, node in graph.nodes.items():
            by_name.setdefault(getattr(node, 'label_name', ''), []).append(nid)

        def ids_for(names):
            out = []
            for name in names or ():
                out.extend(by_name.get(name, ()))
            return out

        # —— 把"走不到"拆成两种，只有真断了才标红 ——
        #
        # 真实项目实测（`sr_general`：51 块 / 40 边，入口是权威的 `start`）：
        # 分析层把 40 个块全判成"不可达"，可它们绝大多数是各章节文件自己的
        # label（`chapter1_1`、`mini_game`…），由菜单 / `call screen` / 外部
        # 跳转进入，而解析器只认字面 jump/call/menu。40/51 全红 = 没标。
        #
        # 所以分三类：
        #   · 从**任何**入口都走不到（关在没有入口的环里）→ 真断了，标红；
        #   · 从别的入口可达，只是从这个入口走不到 → 独立入口，灰虚线，不算问题；
        #   · 分支条件恒假（下落风险）→ 真的死胡同，标红。
        root_ids = set(root_nodes(graph))
        any_root = reachable_from_all_roots(graph)
        unreached = ids_for(res.get('stranded'))
        stranded_ids = [nid for nid in unreached if nid not in any_root]
        independent_ids = (ids_for(res.get('independent'))
                           + [nid for nid in unreached if nid in any_root])
        dead_end_ids = ids_for([str(w).split('：', 1)[-1].split(' 的')[0].strip()
                                for w in (res.get('warnings') or [])
                                if '下落风险' in str(w)])

        # 告警按同一口径过滤：指向"独立入口"的那几条不再当问题报，
        # 否则右侧面板说"不可达"、图上却是灰虚线，两边自相矛盾。
        issue_lines, note_lines = self._split_analysis_warnings(
            res.get('warnings') or [], graph, stranded_ids, independent_ids)

        text = visualize_analysis_results(
            graph,
            res.get('states') or {},
            res.get('warnings') or [],
            entry_id=res.get('entry_id'),
            independent=res.get('independent') or [],
            independent_ids=independent_ids,
            # 变量的视图里**只出现剧情变量**（用户 2026-10-03：
            # "技术代码不是变量，是具体的实现细节"）
            operations_filter=story_ops.narrative_operations,
        )
        problem_ids = list(stranded_ids) + [nid for nid in dead_end_ids
                                            if nid not in stranded_ids]
        n_nodes = len(graph.nodes)
        n_edges = len(getattr(graph, 'edges', {}) or {})
        summary = (f'节点 {n_nodes} 个 / 连线 {n_edges} 条；'
                   f'分析入口「{res.get("entry_name") or "—"}」'
                   f'{"（权威）" if res.get("authoritative") else "（猜测，不断言不可达）"}；'
                   f'真断了 {len(stranded_ids)} 个 / '
                   f'死分支 {len(dead_end_ids)} 个 / '
                   f'其它入口可达 {len(independent_ids)} 个 / '
                   f'其它告警 {len(issue_lines)} 条')
        return {
            'ok': True,
            'text': (summary + '\n\n' + text),
            'summary': summary,
            'entry_id': res.get('entry_id'),
            'entry_name': res.get('entry_name') or '',
            'stranded_ids': stranded_ids,
            'dead_end_ids': dead_end_ids,
            'independent_ids': independent_ids,
            'problem_ids': problem_ids,
            'issue_ids': problem_ids,
            'warnings': issue_lines,
            'notes': note_lines,
            'counts': {'nodes': n_nodes, 'edges': n_edges,
                       'stranded': len(stranded_ids),
                       'dead_end': len(dead_end_ids),
                       'independent': len(independent_ids),
                       'issues': len(issue_lines)},
            'authoritative': bool(res.get('authoritative')),
        }
    @staticmethod
    def _split_analysis_warnings(warnings, graph, stranded_ids, independent_ids):
        """
        把分析层的告警文本按节点集合分成"真问题"与"说明"。

        分析层给的是一串**人读的字符串**（"🚫 不可达节点：X（…）"），
        没有结构化字段 —— 所以这里按前缀 + label 名反查分类。
        分不开的那几条（下落风险 / 迭代上限 / 入口提示）一律留在"真问题"里：
        宁可多报一条，也不要悄悄吞掉一个真的警告。
        """
        stranded_names = {getattr(graph.nodes.get(nid), 'label_name', '')
                          for nid in stranded_ids}
        independent_names = {getattr(graph.nodes.get(nid), 'label_name', '')
                             for nid in independent_ids}
        issues, notes = [], []
        for raw in warnings:
            w = str(raw)
            body = w.lstrip('🚫⚠️ℹ️ ').strip()
            name = ''
            if body.startswith('不可达节点：'):
                name = body[len('不可达节点：'):].split('（')[0].strip()
            if name and name in stranded_names:
                issues.append(w)
            elif name and name in independent_names:
                notes.append(f'🔌 独立入口 / 别处可达（不算问题）：{name}')
            elif body.startswith('未接入主线的独立入口'):
                notes.append(w)
            else:
                issues.append(w)
        return issues, notes
    def analyze_graph(self, graph) -> str:
        """变量区间 + 可达性分析，只要**人看的文本**（`analyze_graph_problems` 的薄壳）。"""
        return self.analyze_graph_problems(graph)['text']
