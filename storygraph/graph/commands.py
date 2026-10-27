# commands.py
import copy
from pathlib import Path

from storygraph.core import notify
from storygraph.core.safeguard import swallow
from storygraph.core.statement import python_operation
from storygraph.parsing.renpy_parser import parse_renpy_file


def restore_beat_states(beat_states, which: str):
    """
    把拍退到 `before` / 进到 `after`（没有快照就什么都不做）。

    还原**前先对一下现在的样子**是否就是另一头（撤销前应当等于 `after`、
    重做前应当等于 `before`）：原地导出会重新解析文件，拍数 / 粒度可能变
    （只有注释的卡会并进后面那张），此时按拍号硬还原会把快照摊到
    **别人的拍**上。对不上就跳过那一拍，并在诊断台账里留一行 ——
    宁可少退一拍，也不能改错拍。

    `beat_states` 的形状（两处命令共用，见 `UpdateNodeOperationsCommand`
    与 `LiftPythonStatementsCommand`）：

        {'session': 会话, 'before': {拍号: 快照}, 'after': {…},
         'refresh': 可选回调（让卡片页列表跟上）}
    """
    st = beat_states or {}
    session = st.get('session')
    snaps = st.get(which) or {}
    if session is None or not snaps:
        return
    other = (st.get('after' if which == 'before' else 'before') or {})
    for idx, snap in sorted(snaps.items()):
        expect = other.get(idx)
        if expect is not None:
            try:
                now = session.snapshot(int(idx))
            except Exception as e:              # noqa: BLE001
                swallow('graph.commands.restore_beats.peek', e)
                now = None
            if now is None or now.fingerprint() != expect.fingerprint():
                swallow('graph.commands.restore_beats.stale',
                        RuntimeError(f'第 {int(idx) + 1} 拍已经变了，'
                                     f'跳过还原（导出重载 / 别的改动）'))
                continue
        try:
            session.restore(snap)
        except Exception as e:                  # noqa: BLE001 - 单拍还原失败不拦撤销
            swallow('graph.commands.restore_beats', e)
    try:
        session.refresh_edits_flag()            # 退回到"与磁盘一致"时别喊未导出
    except Exception as e:                      # noqa: BLE001
        swallow('graph.commands.restore_beats.dirty', e)
    cb = st.get('refresh')
    if callable(cb):
        try:
            cb()
        except Exception as e:                  # noqa: BLE001 - 刷新界面失败不拦撤销
            swallow('graph.commands.restore_beats.refresh', e)

class Command:
    def execute(self):
        raise NotImplementedError
    def undo(self):
        raise NotImplementedError

class AddNodeCommand(Command):
    def __init__(self, graph_view, label, x, y, operations=None, file_path=None, line_number=None):
        self.graph_view = graph_view
        self.label = label
        self.x = x
        self.y = y
        self.operations = operations
        self.file_path = file_path
        self.line_number = line_number
        self.node_id = None

    def execute(self):
        self.node_id = self.graph_view.graph.add_node(
            self.label, self.x, self.y,
            operations=self.operations,
            file_path=self.file_path,
            line_number=self.line_number
        )
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        if self.node_id is not None:
            self.graph_view.graph.remove_node(self.node_id)
            self.graph_view.on_graph_structure_changed()

class RemoveNodeCommand(Command):
    def __init__(self, graph_view, node_id):
        self.graph_view = graph_view
        self.node_id = node_id
        self.removed_node = None
        self.removed_edges = []

    def execute(self):
        self.removed_node = copy.deepcopy(self.graph_view.graph.nodes[self.node_id])
        self.removed_edges = [copy.deepcopy(e) for e in self.graph_view.graph.edges.values()
                              if e.from_id == self.node_id or e.to_id == self.node_id]
        self.graph_view.graph.remove_node(self.node_id)
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.nodes[self.node_id] = self.removed_node
        for edge in self.removed_edges:
            self.graph_view.graph.edges[edge.id] = edge
        self.graph_view.on_graph_structure_changed()

class AddEdgeCommand(Command):
    def __init__(self, graph_view, from_id, to_id, etype, condition,
                 option_text, from_clause='', origin='manual',
                 position='', position_line_id=''):
        self.graph_view = graph_view
        self.from_id = from_id
        self.to_id = to_id
        self.etype = etype
        self.condition = condition
        self.option_text = option_text
        #: `call X from Y` 的存档点标识（控制流卡同步用）。
        self.from_clause = from_clause
        #: 默认 **manual**：这个命令的常规用法是"用户在图上画一条线"。
        #: 控制流卡同步出来的边要显式传 `origin='source'`（它代表源文件里的
        #: `call` / `jump`，第二阶段的"从拍派生"会按这个来源区分重建范围）。
        self.origin = str(origin or 'manual')
        #: 落点（P0，2026-10-24）：空 = 让 `Edge` 按来源推断
        #: （manual → 章末；source + line_id → 指定拍）。
        self.position = str(position or '')
        self.position_line_id = str(position_line_id or '')
        self.edge_id = None

    def execute(self):
        self.edge_id = self.graph_view.graph.add_edge(
            self.from_id, self.to_id, self.etype, self.condition,
            self.option_text, self.from_clause, origin=self.origin,
            position=self.position, position_line_id=self.position_line_id
        )
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        if self.edge_id is not None:
            self.graph_view.graph.remove_edge(self.edge_id)
            self.graph_view.on_graph_structure_changed()


class RetargetEdgeCommand(Command):
    """
    改一条已有边的目标 / 类型 / from 子句（控制流卡保存后同步图）。

    2026-10-22（P0-4 第一阶段）：卡片上把 `call side` 改成 `call other`
    时，图上的边不能还指着 side —— 否则用户看到的是"改了、图上没变"。
    只改边，不删边再加边：这样边的 id、布局与其它属性都保住。
    """

    def __init__(self, graph_view, edge_id, to_id=None, etype=None,
                 from_clause=None):
        self.graph_view = graph_view
        self.edge_id = edge_id
        edge = graph_view.graph.edges[edge_id]
        self.old = copy.deepcopy(edge)
        self.to_id = edge.to_id if to_id is None else to_id
        self.etype = edge.type if etype is None else etype
        self.from_clause = (getattr(edge, 'from_clause', '')
                            if from_clause is None else from_clause)

    def execute(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.to_id = self.to_id
        edge.type = self.etype
        edge.from_clause = self.from_clause
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.edges[self.edge_id] = self.old
        self.graph_view.on_graph_structure_changed()

class RemoveEdgeCommand(Command):
    def __init__(self, graph_view, edge_id):
        self.graph_view = graph_view
        self.edge_id = edge_id
        self.removed_edge = None

    def execute(self):
        self.removed_edge = copy.deepcopy(self.graph_view.graph.edges[self.edge_id])
        self.graph_view.graph.remove_edge(self.edge_id)
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.edges[self.edge_id] = self.removed_edge
        self.graph_view.on_graph_structure_changed()

class SetEdgePositionCommand(Command):
    """
    改一条**草图**的落点（P2，2026-10-24）：章末 ↔ 第 N 拍。

    为什么不复用 `UpdateEdgeCommand`：那个命令只改语义（类型/条件/选项），
    落点是 P0 才有的独立字段；混在一起会让"改类型"也顺手清掉落点。
    草图的落点只影响图上的打算，**不碰剧本**；要真写进去仍走「写入剧本」。
    """

    def __init__(self, graph_view, edge_id, position='chapter_end',
                 position_line_id=''):
        self.graph_view = graph_view
        self.edge_id = edge_id
        edge = graph_view.graph.edges[edge_id]
        self.old_position = str(getattr(edge, 'position', '') or '')
        self.old_line_id = str(getattr(edge, 'position_line_id', '') or '')
        self.new_position = str(position or 'chapter_end')
        self.new_line_id = (str(position_line_id or '')
                            if self.new_position == 'beat' else '')

    def execute(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.position = self.new_position
        edge.position_line_id = self.new_line_id
        self.graph_view.on_graph_content_changed(edge.from_id)

    def undo(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.position = self.old_position
        edge.position_line_id = self.old_line_id
        self.graph_view.on_graph_content_changed(edge.from_id)


class ControlRouteCommand(Command):
    """
    章末接线写进**编排层**（P2，2026-10-24）：只改 `X_control` 那一行。

    为什么单独一个命令、而不是 `_apply_wire` 里的又一条分支：

      · 它改的是**另一个文件**，必须能整行撤回（文件内容不在会话的撤销栈里）；
      · 图上还要多一条 `origin='control'` 的边 —— 那条边不是从拍派生的，
        关掉再打开也得在（布局里已经存 origin/position）；
      · 失败时**不建边**，不许出现"图上有了、文件没写"的假完成。

    `mode='add'` 用 `plan_control_route` 的结果；`mode='remove'` 用
    `plan_control_unroute`。文件的旧全文存在命令里，撤销时逐字节写回。

    P2.5：`init_plan` 是"接线前要先补的那个 `_control` 包装"（源章缺、
    目标章缺都算）。先补、再重算、再写；撤销把**补之前**的全文写回
    （一次 Ctrl+Z 全回去）。

    2026-10-27 真机（`chapter2_4 → chapter2_4_1`）：两头包装都缺是**最常见**
    的情形，所以补包装走 `apply_init_chain` —— 一次能把两个都补上。
    """

    def __init__(self, graph_view, plan, mode='add', from_id=None, to_id=None,
                 etype=None, init_plan=None, project_root=None):
        self.graph_view = graph_view
        self.plan = plan
        self.mode = str(mode or 'add')
        self.from_id = from_id
        self.to_id = to_id
        self.etype = str(etype or getattr(plan, 'etype', 'jump'))
        self.init_plan = init_plan
        self.project_root = project_root
        self.edge_id = None
        self.removed_edge = None
        self.added_edge = False
        self.old_text = ''
        self.new_text = ''
        self.path = ''
        self.error = ''
        self._init_written = False
        #: `apply_init_chain` 的落地记录：`[(path, 补之前, 补之后)]`
        self.init_snapshots = []

    def execute(self):
        from storygraph.story.control_route import (
            apply_control_route, apply_control_unroute, apply_init_chain,
            plan_control_route, rollback_wrapper_writes)
        # 重做时从干净状态开始：上一次 execute 记的 edge_id 已经随 undo 失效。
        self.edge_id = None
        self.removed_edge = None
        self.added_edge = False
        self.old_text = ''
        self.new_text = ''
        self.path = ''
        self._init_written = False
        self.init_snapshots = []
        if self.mode == 'remove':
            res = apply_control_unroute(self.plan)
        else:
            res = {'ok': True}
            # 重做 / 被别的动作改过之后：`self.plan` 可能是上一次「补完包装」的
            # 结果（`ok=True`、路径齐全），而文件已经被撤销回原样。照着旧计划
            # 直接写，会写出"跳到一个根本没补出来的 `_control`"（2026-10-27
            # 真机 redo 用例抓到的）。按同一份输入重算一次，判据只在规划层。
            if self.project_root:
                fresh = plan_control_route(
                    self.project_root,
                    str(getattr(self.plan, 'src_label', '') or ''),
                    str(getattr(self.plan, 'dst_label', '') or ''),
                    self.etype)
                if getattr(fresh, 'path', ''):
                    self.plan = fresh
            # 计划里自带"要补的包装"（源章 / 目标章都有可能），也可能由调用方
            # 显式塞进来 `init_plan`。统一走同一条链：补 → 重算 → 直到能写。
            chain = self._init_chain(apply_init_chain, plan_control_route,
                                     rollback_wrapper_writes)
            if chain is not None:
                return chain
            res = apply_control_route(self.plan)
        if not res.get('ok'):
            self._rollback_init()
            self.error = str(res.get('error') or '写编排层失败')
            raise RuntimeError(self.error)
        self.path = str(res.get('path') or self.path)
        self.old_text = (self._original_text(self.path)
                         or str(res.get('old_text') or ''))
        self.new_text = str(res.get('new_text') or '')

        graph = self.graph_view.graph
        if self.mode == 'remove':
            # 删掉与这条路由对应的图边（只删 origin='control' 的）
            for eid, edge in list(graph.edges.items()):
                if (str(getattr(edge, 'origin', '')) == 'control'
                        and edge.from_id == self.from_id
                        and edge.type == self.etype
                        and (self.to_id is None or edge.to_id == self.to_id)):
                    self.removed_edge = copy.deepcopy(edge)
                    graph.remove_edge(eid)
                    break
        else:
            # 已经有一条同样的编排边 → 不重复画
            for edge in graph.edges.values():
                if (str(getattr(edge, 'origin', '')) == 'control'
                        and edge.from_id == self.from_id
                        and edge.type == self.etype
                        and (self.to_id is None or edge.to_id == self.to_id)):
                    self.edge_id = edge.id
                    break
            if self.edge_id is None and self.from_id is not None \
                    and self.to_id is not None:
                self.edge_id = graph.add_edge(
                    self.from_id, self.to_id, self.etype, '', '',
                    '', origin='control', position='control_end')
                self.added_edge = True
        self.graph_view.on_graph_structure_changed()

    def _init_chain(self, apply_init_chain, plan_control_route,
                    rollback_wrapper_writes):
        """
        计划里带着"还没补的 `_control` 包装"时：**先补、再重算、再写**。

        返回 `None` = 不用补（调用方直接写路由）；抛 `RuntimeError` = 补不成，
        文件已经退回补之前的样子。

        这条链一次性处理两头的包装（2026-10-27 真机 `chapter2_4 → 2_4_1`：
        源章、目标章都没有 `_control`，只补一个永远写不进去）。
        """
        seed = self.plan
        candidates = [getattr(seed, name, None) for name in ('init', 'src_init')]
        if self.init_plan is not None:
            candidates.append(self.init_plan)
        if not any(c is not None and getattr(c, 'ok', False)
                   for c in candidates):
            return None
        if self.init_plan is not None and getattr(seed, 'init', None) is None:
            try:
                seed.init = self.init_plan
            except AttributeError:              # noqa: BLE001 - 只读计划就算了
                swallow('graph.commands.ControlRouteCommand.seed_init',
                        AttributeError('计划不可写'))
        if not self.project_root:
            self.error = '要先补编排入口，但不知道项目根，无法重算路由'
            raise RuntimeError(self.error)
        src_label = str(getattr(seed, 'src_label', '') or '')
        dst_label = str(getattr(seed, 'dst_label', '') or '')
        etype = self.etype

        def _replan():
            return plan_control_route(self.project_root, src_label, dst_label,
                                      etype)

        out = apply_init_chain(seed, _replan)
        self.init_snapshots = list(out.get('snapshots') or ())
        if not out.get('ok'):
            rollback_wrapper_writes(self.init_snapshots)
            self.init_snapshots = []
            self.error = str(out.get('error') or '补完包装后仍然写不了')
            raise RuntimeError(self.error)
        self.plan = out.get('plan')
        self._init_written = bool(self.init_snapshots)
        if self.init_snapshots:
            # 包装落在哪个文件，路由就要写进哪个文件（否则一次撤销回不去）。
            self.path = str(self.init_snapshots[-1][0] or '')
        if self.path and getattr(self.plan, 'path', '') \
                and Path(self.path) != Path(self.plan.path):
            rollback_wrapper_writes(self.init_snapshots)
            self.init_snapshots = []
            self.error = '补包装与写路由落到了不同文件；已回滚'
            raise RuntimeError(self.error)
        return None

    def _original_text(self, path) -> str:
        """补包装**之前**那个文件的全文本（撤销要写回它）。"""
        for item in self.init_snapshots:
            try:
                item_path, old_text, _new_text = item
            except (TypeError, ValueError):
                continue
            if path and Path(item_path) == Path(str(path)):
                return str(old_text or '')
        return ''

    def _rollback_init(self):
        """补包装成功、但后面的步骤失败：把补过的文件退回补之前的样子。"""
        if not self.init_snapshots:
            return
        from storygraph.story.control_route import rollback_wrapper_writes
        rollback_wrapper_writes(self.init_snapshots)
        self.init_snapshots = []

    def undo(self):
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import read_text
        if self.path:
            try:
                now, style = read_text(self.path)
            except OSError as exc:                # noqa: BLE001
                raise RuntimeError(f'回滚编排层失败：{exc}') from exc
            if now != self.new_text:
                raise RuntimeError(
                    '编排层在接线之后又被改过；为了不覆盖新改动，'
                    '这次 Ctrl+Z 没有回滚那个文件')
            write_text_atomic(self.path, style.apply(self.old_text,
                                                     source=self.path))
        graph = self.graph_view.graph
        if self.mode == 'remove':
            if self.removed_edge is not None:
                graph.edges[self.removed_edge.id] = self.removed_edge
        elif self.added_edge and self.edge_id is not None:
            graph.remove_edge(self.edge_id)
        self.graph_view.on_graph_structure_changed()


class EditControlRouteCommand(Command):
    """
    在「编辑连线」里改**编排层那一行**的类型 / 目标（原地改写，可撤销）。

    与 `ControlRouteCommand`（新增/删除路由）分工：这里只替换已有的一行，
    行号 / 原文由 `plan_control_edit` 算好；文件旧全文存在命令里，
    `Ctrl+Z` 逐字节写回，并把图上的 `to_id` / `type` 一起还原。

    目标章缺 `_control` 包装时，`plan.init` 是补包装计划：先补、再重算
    （行号可能没变，但文件长了），撤销时把**补之前**的全文写回 ——
    一次 Ctrl+Z 全回去。
    """

    def __init__(self, graph_view, plan, edge_id, to_id, etype,
                 project_root=None):
        self.graph_view = graph_view
        self.plan = plan
        self.edge_id = int(edge_id)
        self.to_id = to_id
        self.etype = str(etype or getattr(plan, 'new_etype', 'jump'))
        self.project_root = project_root
        self.old_text = ''
        self.new_text = ''
        self.path = ''
        self.old_to_id = None
        self.old_etype = ''
        self._init_written = False
        self.error = ''

    def execute(self):
        from storygraph.story.control_route import (apply_control_edit,
                                                    apply_control_init,
                                                    plan_control_edit)
        plan = self.plan
        self.old_text = ''
        self.new_text = ''
        self.path = ''
        self._init_written = False
        init_old_text = ''
        init = getattr(plan, 'init', None)
        if init is not None and str(getattr(init, 'kind', '')) == 'append':
            init_res = apply_control_init(init)
            if not init_res.get('ok'):
                self.error = str(init_res.get('error') or '补包装失败')
                raise RuntimeError(self.error)
            init_old_text = str(init_res.get('old_text') or '')
            self.path = str(init_res.get('path') or '')
            self._init_written = True
            plan = plan_control_edit(
                self.project_root, plan.src_label, plan.old_dst_label,
                plan.old_etype, plan.new_dst_label, plan.new_etype)
            if not plan.ok:
                self._rollback_init(init_old_text)
                self.error = (plan.reason or '补完包装之后仍然算不出要改哪一行')
                raise RuntimeError(self.error)
        res = apply_control_edit(plan)
        if not res.get('ok'):
            self._rollback_init(init_old_text)
            self.error = str(res.get('error') or '改编排层失败')
            raise RuntimeError(self.error)
        self.old_text = init_old_text or str(res.get('old_text') or '')
        self.new_text = str(res.get('new_text') or '')
        self.path = str(res.get('path') or self.path)
        graph = self.graph_view.graph
        edge = graph.edges.get(self.edge_id)
        if edge is not None:
            self.old_to_id = edge.to_id
            self.old_etype = str(getattr(edge, 'type', 'jump') or 'jump')
            edge.to_id = self.to_id
            edge.type = self.etype
            edge.origin = 'control'
            edge.position = 'control_end'
        self.graph_view.on_graph_structure_changed()

    def _rollback_init(self, old_text):
        if not self._init_written or not old_text or not self.path:
            return
        try:
            from storygraph.story.paths import write_text_atomic
            from storygraph.story.source_style import read_text
            now, style = read_text(self.path)
            expected = str(getattr(self.plan.init, 'new_text', '') or '')
            if expected and now != expected:
                swallow('graph.commands.EditControlRouteCommand.rollback',
                        RuntimeError('编排层在补包装之后又被改过；不回滚'))
                return
            write_text_atomic(self.path, style.apply(old_text, source=self.path))
        except OSError as exc:                    # noqa: BLE001 - 回滚失败只能记账
            swallow('graph.commands.EditControlRouteCommand.rollback', exc)

    def undo(self):
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import read_text
        if self.path:
            try:
                now, style = read_text(self.path)
            except OSError as exc:                # noqa: BLE001
                raise RuntimeError(f'回滚编排层失败：{exc}') from exc
            if now != self.new_text:
                raise RuntimeError(
                    '编排层在这次修改之后又被改过；为了不覆盖新改动，'
                    '这次 Ctrl+Z 没有回滚那个文件')
            write_text_atomic(self.path, style.apply(self.old_text,
                                                     source=self.path))
        edge = self.graph_view.graph.edges.get(self.edge_id)
        if edge is not None and self.old_to_id is not None:
            edge.to_id = self.old_to_id
            edge.type = self.old_etype
        self.graph_view.on_graph_structure_changed()


class EditSourceFlowCommand(Command):
    """
    改一条**源边**在章节剧本里的那一行（类型 / 目标），可撤销。

    只改 `Beat.raw` 那一行（`story.flow.update_beat_flow` 的唯一入口），
    不碰卡片 / 舞台 / 台词。撤销把原 raw 写回，再让图按拍重派生一次
    （`source` 边恒等于 `flow_edges(beats)`，见 `session_graph`）。
    """

    def __init__(self, graph_view, session, beat, flow_type, target,
                 refresh=None):
        self.graph_view = graph_view
        self.session = session
        self.beat = beat
        self.flow_type = str(flow_type or 'jump')
        self.target = str(target or '')
        self.refresh = refresh
        self.old_raw = str(getattr(beat, 'raw', '') or '')
        self.new_raw = ''
        self.error = ''

    def execute(self):
        from storygraph.story.flow import update_beat_flow
        res = update_beat_flow(self.beat, self.flow_type, self.target)
        if not res.get('ok'):
            self.error = str(res.get('error') or '改剧本里那一行失败')
            raise RuntimeError(self.error)
        self.new_raw = str(getattr(self.beat, 'raw', '') or '')
        try:
            self.session.mark_edits_dirty()
        except Exception as e:                    # noqa: BLE001 - 标记失败不拦改动
            swallow('graph.commands.EditSourceFlowCommand.dirty', e)
        self._refresh()

    def _refresh(self):
        try:
            self.session.refresh_edits_flag()
        except Exception as e:                    # noqa: BLE001 - 未导出标记刷不动不拦
            swallow('graph.commands.EditSourceFlowCommand.dirty_flag', e)
        if callable(self.refresh):
            try:
                self.refresh()
            except Exception as e:                # noqa: BLE001 - 图没刷不该回滚剧本
                swallow('graph.commands.EditSourceFlowCommand.refresh', e)

    def undo(self):
        self.beat.raw = self.old_raw
        self._refresh()


class DeleteSourceLineCommand(Command):
    """
    删一条**不在当前章节里**的源边（P2）：只删源文件那一行，不切章节。

    与 `ControlRouteCommand` 一样，文件旧全文存在命令里，撤销时逐字节写回；
    区别是它删的是章节源文件，删完还要把图边拿走（撤销时放回）。
    """

    def __init__(self, graph_view, plan, edge_id):
        self.graph_view = graph_view
        self.plan = plan
        self.edge_id = int(edge_id)
        self.removed_edge = None
        self.error = ''

    def execute(self):
        from storygraph.app.source_line_delete import (
            apply_source_line_delete)
        res = apply_source_line_delete(self.plan)
        if not res.get('ok'):
            self.error = str(res.get('error') or '删源文件失败')
            raise RuntimeError(self.error)
        graph = self.graph_view.graph
        edge = graph.edges.get(self.edge_id)
        if edge is not None:
            self.removed_edge = copy.deepcopy(edge)
            graph.remove_edge(self.edge_id)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import read_text
        path = str(getattr(self.plan, 'path', '') or '')
        if path:
            now, style = read_text(path)
            if now != self.plan.new_text:
                raise RuntimeError(
                    '源文件在删除之后又被改过；为了不覆盖新改动，'
                    '这次 Ctrl+Z 没有回滚那个文件')
            write_text_atomic(path, style.apply(self.plan.old_text, source=path))
        if self.removed_edge is not None:
            self.graph_view.graph.edges[self.removed_edge.id] = self.removed_edge
        self.graph_view.on_graph_structure_changed()


class UpdateNodeOperationsCommand(Command):
    """
    改一个节点的代码块（可撤销）。

    迁移（2026-10-05）：快照存的是**结构化语句**（`node.statements`），
    撤销时整份还原 —— 用 `operations` 字符串往返会把语句上的
    `source`（文件/行号）/ `condition` 元信息丢掉。

    `beat_states`（2026-10-22，变量工作台）：这条改动**同时**落在图节点和
    章节的拍上（`session.beats`），而图级撤销从前只退节点 —— 撤销之后
    "工作台里那条没了、导出里却还在"（实测：Ctrl+Z 之后 `compile()` 仍带
    `$ mark = 1`）。所以把"改之前 / 改之后"的**逐拍快照**一并带进来：

        {'session': 会话, 'before': {拍号: 快照}, 'after': {…},
         'refresh': 可选回调（让卡片页列表跟上）}

    用逐拍快照而不是整份 `beats` 深拷贝：拍对象保持同一个身份，
    `ChapterDocument` 的树、卡片页的映射都还指得对。
    """

    def __init__(self, graph_view, node_id, new_ops, old_ops=None,
                 beat_states=None):
        self.graph_view = graph_view
        self.node_id = node_id
        self.new_ops = list(new_ops)
        #: 调用方**已经改完**的情况（"只记历史、不重复 execute"）：
        #: 把改动前的文本显式给进来 —— 这时 `execute()` 不会被调，
        #: 所以快照必须在构造时就备好，否则撤销会恢复不了。
        if old_ops is not None:
            self.old_statements = [python_operation(o) for o in old_ops]
        else:
            self.old_statements = None
        self.beat_states = dict(beat_states or {})

    def _restore_beats(self, which: str):
        """把拍退到 `before` / 进到 `after`（语义见模块级 `restore_beat_states`）"""
        restore_beat_states(self.beat_states, which)

    def execute(self):
        node = self.graph_view.graph.nodes[self.node_id]
        if self.old_statements is None:
            self.old_statements = copy.deepcopy(node.statements)
        node.set_operations(self.new_ops)
        self._restore_beats('after')            # 重做：拍也跟着回到"改之后"
        self.graph_view.on_graph_content_changed(self.node_id)

    def undo(self):
        node = self.graph_view.graph.nodes[self.node_id]
        if self.old_statements is not None:
            node.statements = copy.deepcopy(self.old_statements)
        self._restore_beats('before')           # 撤销：拍也跟着退回"改之前"
        self.graph_view.on_graph_content_changed(self.node_id)

class LiftPythonStatementsCommand(Command):
    """
    把 `python:` 块里的赋值搬到脚本层（2026-10-22，`session.apply_python_lift`）。

    与 `UpdateNodeOperationsCommand` 的区别：那条按**代码字符串**重建语句
    （`python_operation(text)`，`source` / `condition` 会丢），而搬块这件事
    **必须保住**每条语句的来源行与条件 —— 否则搬完之后变量工作台"定位到这一拍"
    全指错地方，再改一次就写到别的拍上。所以这里存的是**整份语句表**
    （old / new），撤销 / 重做整份还原。

    拍那边只改了 `code_blocks`（行文本一个字节没动），恢复走 `beat_states`
    的逐拍快照 —— 与变量工作台共用同一套（含"旧快照守卫"）。
    """

    def __init__(self, graph_view, node_id, old_statements, new_statements,
                 beat_states=None):
        self.graph_view = graph_view
        self.node_id = node_id
        self.old_statements = copy.deepcopy(list(old_statements or ()))
        self.new_statements = copy.deepcopy(list(new_statements or ()))
        self.beat_states = dict(beat_states or {})

    def execute(self):
        node = self.graph_view.graph.nodes[self.node_id]
        node.statements = copy.deepcopy(self.new_statements)
        restore_beat_states(self.beat_states, 'after')
        self.graph_view.on_graph_content_changed(self.node_id)

    def undo(self):
        node = self.graph_view.graph.nodes[self.node_id]
        node.statements = copy.deepcopy(self.old_statements)
        restore_beat_states(self.beat_states, 'before')
        self.graph_view.on_graph_content_changed(self.node_id)

class UpdateEdgeCommand(Command):
    def __init__(self, graph_view, edge_id, etype=None, condition=None,
                 option_text=None, from_clause=None, position=None,
                 position_line_id=None):
        self.graph_view = graph_view
        self.edge_id = edge_id
        self.new_etype = etype
        self.new_condition = condition
        self.new_option_text = option_text
        self.new_from_clause = from_clause
        self.new_position = position
        self.new_position_line_id = position_line_id
        self.old_etype = None
        self.old_condition = None
        self.old_option_text = None
        self.old_from_clause = None
        self.old_position = None
        self.old_position_line_id = None

    def execute(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        self.old_etype = edge.type
        self.old_condition = edge.condition
        self.old_option_text = edge.option_text
        self.old_from_clause = getattr(edge, 'from_clause', '')
        self.old_position = getattr(edge, 'position', '')
        self.old_position_line_id = getattr(edge, 'position_line_id', '')
        if self.new_etype is not None:
            edge.type = self.new_etype
        if self.new_condition is not None:
            edge.condition = self.new_condition
        if self.new_option_text is not None:
            edge.option_text = self.new_option_text
        if self.new_from_clause is not None:
            edge.from_clause = self.new_from_clause
        if self.new_position is not None:
            edge.position = self.new_position
        if self.new_position_line_id is not None:
            edge.position_line_id = self.new_position_line_id
        self.graph_view.on_graph_content_changed(edge.from_id)

    def undo(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.type = self.old_etype
        edge.condition = self.old_condition
        edge.option_text = self.old_option_text
        edge.from_clause = self.old_from_clause
        edge.position = self.old_position
        edge.position_line_id = self.old_position_line_id
        self.graph_view.on_graph_content_changed(edge.from_id)

class SetCurrentNodeCommand(Command):
    def __init__(self, graph_view, node_id):
        self.graph_view = graph_view
        self.node_id = node_id
        self.previous_current = None

    def execute(self):
        for nid, node in self.graph_view.graph.nodes.items():
            if node.is_current:
                self.previous_current = nid
                node.is_current = False
                break
        if self.node_id is not None:
            self.graph_view.graph.nodes[self.node_id].is_current = True
        self.graph_view.on_current_changed()

    def undo(self):
        for nid, node in self.graph_view.graph.nodes.items():
            node.is_current = False
        if self.previous_current is not None:
            self.graph_view.graph.nodes[self.previous_current].is_current = True
        self.graph_view.on_current_changed()

class MoveNodeCommand(Command):
    def __init__(self, graph_view, node_id, old_x, old_y, new_x, new_y):
        self.graph_view = graph_view
        self.node_id = node_id
        self.old_x = old_x
        self.old_y = old_y
        self.new_x = new_x
        self.new_y = new_y

    def execute(self):
        node = self.graph_view.graph.nodes[self.node_id]
        node.x = self.new_x
        node.y = self.new_y
        self.graph_view.update_node_position(self.node_id)

    def undo(self):
        node = self.graph_view.graph.nodes[self.node_id]
        node.x = self.old_x
        node.y = self.old_y
        self.graph_view.update_node_position(self.node_id)

class ClearGraphCommand(Command):
    def __init__(self, graph_view):
        self.graph_view = graph_view
        self.old_graph = None

    def execute(self):
        self.old_graph = self.graph_view.graph.copy()
        self.graph_view.graph.clear()
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()

class ImportRenPyCommand(Command):
    def __init__(self, graph_view, file_path):
        self.graph_view = graph_view
        self.file_path = file_path
        self.old_graph = None

    def execute(self):
        self.old_graph = self.graph_view.graph.copy()
        parse_renpy_file(self.file_path, self.graph_view.graph)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()


class ReplaceGraphCommand(Command):
    """整图替换（用于批量导入后撤销）：graph_view.graph 在两个快照间切换。"""

    def __init__(self, graph_view, new_graph, old_graph):
        self.graph_view = graph_view
        self.new_graph = new_graph
        self.old_graph = old_graph

    def execute(self):
        self.graph_view.graph = self.new_graph
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()

class LoadLayoutCommand(Command):
    def __init__(self, graph_view, data):
        self.graph_view = graph_view
        self.data = data
        self.old_graph = None

    def execute(self):
        self.old_graph = self.graph_view.graph.copy()
        self.graph_view._apply_layout_data(self.data)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph = self.old_graph
        self.graph_view.on_graph_structure_changed()

class TakeoverCommand(Command):
    """
    接管进入点（`jump X` → `jump X_control` + 补 `X_control` 包装）。

    一次改多个文件，撤销时**逐字节**写回旧全文；写之前先核对每个文件都
    还是计划时那一份（`apply_takeover` 已经做了），撤销前再核对一次
    "现在 == 写下去的新全文"，否则拒绝撤销，绝不覆盖别人的新改动。
    """

    def __init__(self, graph_view, plan):
        self.graph_view = graph_view
        self.plan = plan
        self.error = ''
        self.written = []

    def execute(self):
        from storygraph.story.control_takeover import apply_takeover
        res = apply_takeover(self.plan)
        if not res.get('ok'):
            self.error = str(res.get('error') or '接管失败')
            raise RuntimeError(self.error)
        self.written = list(res.get('written') or [])
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import read_text
        for edit in reversed(list(self.plan.edits)):
            path = Path(edit.path)
            if not path.exists():
                continue
            now, style = read_text(path)
            if now != edit.new_text:
                raise RuntimeError(
                    f'{path.name} 在接管之后又被改过；为了不覆盖新改动，'
                    f'这次 Ctrl+Z 没有回滚它')
            if getattr(edit, 'creates', False):
                # 这个文件是接管新建的：撤销 = 删掉，而不是留一个空 .rpy
                path.unlink()
                continue
            write_text_atomic(path, style.apply(edit.old_text, source=path))
        self.graph_view.on_graph_structure_changed()


class _ControlBranchBase:
    """条件 / 菜单分支命令的公共部分（补目标包装 + 写段 + 回滚）。"""

    def _plan(self):
        from storygraph.story.control_branch import plan_control_branch
        return plan_control_branch(
            self.project_root, self.src_label, self.dst_label, self.etype,
            self.condition, self.option_text, edge_id=self.edge_id,
            from_clause=self.from_clause,
            replace_dst_label=getattr(self, 'replace_dst_label', ''),
            replace_etype=getattr(self, 'replace_etype', ''))

    def _replan(self):
        """补完目标包装之后按同一份输入重算（标记已存在时 replace 自然失效）。"""
        return self._plan()

    def _apply_plan(self, plan):
        """应用计划；缺 `_control` 包装时先补包装（源章 / 目标章都可能缺）再重算。"""
        from storygraph.story.control_branch import (apply_control_branch,
                                                     plan_control_branch)
        from storygraph.story.control_route import (apply_init_chain,
                                                    rollback_wrapper_writes)
        self.init_old_text = ''
        self.init_new_text = ''
        self.init_snapshots = []
        if any(getattr(getattr(plan, name, None), 'ok', False)
               for name in ('init', 'src_init')):
            out = apply_init_chain(plan, self._replan)
            self.init_snapshots = list(out.get('snapshots') or ())
            if not out.get('ok'):
                rollback_wrapper_writes(self.init_snapshots)
                self.init_snapshots = []
                raise RuntimeError(out.get('error') or '补完包装后仍然写不了分支')
            plan = out.get('plan')
            if self.init_snapshots:
                # 撤销要写回**最早**那一版（同一个文件可能被补过两次：
                # 源章一个、目标章一个），回滚校验用的是**最后**那一版。
                self.path = str(self.init_snapshots[-1][0] or '')
                self.init_old_text = str(self.init_snapshots[0][1] or '')
                self.init_new_text = str(self.init_snapshots[-1][2] or '')
            if self.path and getattr(plan, 'path', '') \
                    and Path(self.path) != Path(plan.path):
                rollback_wrapper_writes(self.init_snapshots)
                self.init_snapshots = []
                raise RuntimeError('补包装与写分支落到了不同文件；已回滚')
        if not plan.ok:
            return plan
        res = apply_control_branch(plan)
        if not res.get('ok'):
            self._rollback_init()
            raise RuntimeError(str(res.get('error') or '写编排层分支失败'))
        self.path = str(res.get('path') or self.path)
        self.old_text = self.init_old_text or plan.old_text
        self.new_text = plan.new_text
        return plan

    def _rollback_init(self):
        if not self.init_old_text or not self.path:
            return
        try:
            from storygraph.story.paths import write_text_atomic
            from storygraph.story.source_style import read_text
            now, style = read_text(self.path)
            if self.init_new_text and now != self.init_new_text:
                return
            write_text_atomic(self.path, style.apply(self.init_old_text,
                                                     source=self.path))
        except OSError as exc:                     # noqa: BLE001 - 回滚失败只能记账
            swallow('graph.commands.ControlBranch.rollback_init', exc)

    def _restore_text(self):
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import read_text
        if not self.path:
            return
        now, style = read_text(self.path)
        if now != self.new_text:
            raise RuntimeError(
                '编排层在这次修改之后又被改过；为了不覆盖新改动，'
                '这次 Ctrl+Z 没有回滚那个文件')
        write_text_atomic(self.path, style.apply(self.old_text,
                                                 source=self.path))


class AddControlBranchCommand(_ControlBranchBase, Command):
    """
    **新增**一条条件 / 菜单边：写进 `X_control`，图上加一条 `origin='control'`。

    边 id 由这里先预留（标记注释要用它），写成功之后才真正进图 ——
    写失败不会留下一条"图上有、文件里没有"的假线。
    """

    def __init__(self, graph_view, project_root, from_id, to_id,
                 etype='jump', condition='', option_text='',
                 from_clause=''):
        self.graph_view = graph_view
        self.project_root = project_root
        self.from_id = from_id
        self.to_id = to_id
        self.etype = str(etype or 'jump')
        self.condition = str(condition or '')
        self.option_text = str(option_text or '')
        self.from_clause = str(from_clause or '')
        self.edge_id = 0
        self.src_label = ''
        self.dst_label = ''
        self.path = ''
        self.old_text = ''
        self.new_text = ''
        self.init_old_text = ''
        self.error = ''

    def execute(self):
        from storygraph.graph.models import Edge
        graph = self.graph_view.graph
        src = (getattr(graph, 'nodes', {}) or {}).get(self.from_id)
        dst = (getattr(graph, 'nodes', {}) or {}).get(self.to_id)
        if src is None or dst is None:
            raise RuntimeError('这条线的两端必须都在图上')
        self.src_label = str(getattr(src, 'label_name', '') or '')
        self.dst_label = str(getattr(dst, 'label_name', '') or '')
        self.edge_id = int(getattr(graph, 'next_edge_id', 1) or 1)
        plan = self._plan()
        plan = self._apply_plan(plan)
        if not plan.ok:
            self.error = str(plan.reason or '写不了这条分支')
            raise RuntimeError(self.error)
        edge = Edge(self.edge_id, self.from_id, self.to_id, self.etype,
                    self.condition, self.option_text, self.from_clause,
                    origin='control', position='control_end')
        graph.edges[self.edge_id] = edge
        graph.next_edge_id = max(int(graph.next_edge_id), self.edge_id + 1)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self._restore_text()
        graph = self.graph_view.graph
        graph.edges.pop(self.edge_id, None)
        self.graph_view.on_graph_structure_changed()


class WriteControlBranchCommand(_ControlBranchBase, Command):
    """
    **改写**一条已有的条件 / 菜单边（类型 / 目标 / 条件 / 菜单项）。

    按标记段原地换一小段；文件旧全文存在命令里，`Ctrl+Z` 写回并把边字段
    一起还原。
    """

    def __init__(self, graph_view, project_root, edge_id, etype, condition,
                 option_text, from_clause=''):
        self.graph_view = graph_view
        self.project_root = project_root
        self.edge_id = int(edge_id)
        self.etype = str(etype or 'jump')
        self.condition = str(condition or '')
        self.option_text = str(option_text or '')
        self.from_clause = str(from_clause or '')
        self.src_label = ''
        self.dst_label = ''
        self.path = ''
        self.old_text = ''
        self.new_text = ''
        self.init_old_text = ''
        self.error = ''
        self.old_fields = None
        self.replace_dst_label = ''
        self.replace_etype = ''

    def execute(self):
        graph = self.graph_view.graph
        edge = graph.edges.get(self.edge_id)
        if edge is None:
            raise RuntimeError('这条线已经不在图上了')
        src = (graph.nodes or {}).get(edge.from_id)
        dst = (graph.nodes or {}).get(edge.to_id)
        if src is None or dst is None:
            raise RuntimeError('这条线的两端必须都在图上')
        self.src_label = str(getattr(src, 'label_name', '') or '')
        self.dst_label = str(getattr(dst, 'label_name', '') or '')
        self.old_fields = {
            'type': str(getattr(edge, 'type', 'jump') or 'jump'),
            'condition': str(getattr(edge, 'condition', '') or ''),
            'option_text': str(getattr(edge, 'option_text', '') or ''),
            'from_clause': str(getattr(edge, 'from_clause', '') or ''),
        }
        # 普通编排路由（没有条件 / 菜单项）加成条件分支：原来那一行要
        # 换成分支段，不能"加一段、旧路由留着"（旧路由会无条件先执行）。
        if not (self.old_fields['condition']
                or self.old_fields['option_text']):
            self.replace_dst_label = self.dst_label
            self.replace_etype = self.old_fields['type']
        plan = self._plan()
        plan = self._apply_plan(plan)
        if not plan.ok:
            self.error = str(plan.reason or '这条分支改不了')
            raise RuntimeError(self.error)
        edge.type = self.etype
        edge.condition = self.condition
        edge.option_text = self.option_text
        edge.from_clause = self.from_clause if self.etype == 'call' else ''
        edge.origin = 'control'
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self._restore_text()
        edge = self.graph_view.graph.edges.get(self.edge_id)
        if edge is not None and self.old_fields:
            edge.type = self.old_fields['type']
            edge.condition = self.old_fields['condition']
            edge.option_text = self.old_fields['option_text']
            edge.from_clause = self.old_fields['from_clause']
        self.graph_view.on_graph_structure_changed()


class MoveSourceFlowCommand(Command):
    """
    把源剧本里的一条 `jump` / `call` 行**移到本章另一拍之后**（可撤销）。

    为什么用 `session.move_beat` 而不是"删一行再插一行"：块结构、缩进、
    条件都挂在拍上，删了重插会丢 `nest_path` / `condition`，还会把块体
    拆散。`ChapterTree.move_beat` 会按落点重写结构位。

    撤销走**结构撤销栈**（`undo_structural`），它连 `nest_path` / `condition`
    一起还原。执行后栈深必须正好 +1；否则说明中间又被别的结构编辑动过，
    这时拒绝撤销，绝不误撤别人的动作。
    """

    def __init__(self, graph_view, session, old_index, new_index, refresh=None):
        self.graph_view = graph_view
        self.session = session
        self.old_index = int(old_index)
        self.new_index = int(new_index)
        self.refresh = refresh
        self.actual_index = -1
        self.depth_before = 0
        self.depth_after = -1
        self.error = ''

    def execute(self):
        self.depth_before = self.session.struct_undo_depth()
        res = self.session.move_beat(self.old_index, self.new_index)
        if not res.get('ok'):
            self.error = str(res.get('error') or '移动那一行失败')
            raise RuntimeError(self.error)
        self.actual_index = int(res.get('index', -1))
        self.depth_after = self.session.struct_undo_depth()
        if self.depth_after != self.depth_before + 1:
            raise RuntimeError('结构撤销栈没有记住这次移动，不能继续')
        try:
            self.session.mark_edits_dirty()
        except Exception as e:                    # noqa: BLE001 - 标记失败不拦改动
            swallow('graph.commands.MoveSourceFlowCommand.dirty', e)
        self._refresh()

    def undo(self):
        if self.session.struct_undo_depth() != self.depth_after:
            raise RuntimeError(
                '这次移动之后又发生了别的结构编辑，无法只撤回这一次；'
                '请按 Ctrl+Z 顺序撤回，或重新载入这一章')
        res = self.session.undo_structural()
        if not res.get('ok'):
            raise RuntimeError(str(res.get('error') or '撤销移动失败'))
        self._refresh()

    def _refresh(self):
        if callable(self.refresh):
            try:
                self.refresh()
            except Exception as e:                # noqa: BLE001 - 图没刷不该回滚剧本
                swallow('graph.commands.MoveSourceFlowCommand.refresh', e)


class RemoveControlBranchCommand(Command):
    """
    删一条**编排层分支**（标记段；菜单组里只删那一项）。

    与 `ControlRouteCommand(mode='remove')` 分工：后者只认普通路由一行；
    分支段有标记、菜单项还在组里，必须按 `ControlBranchPlan` 删。
    撤销把文件逐字节写回，并把图边放回。
    """

    def __init__(self, graph_view, plan, edge_id):
        self.graph_view = graph_view
        self.plan = plan
        self.edge_id = int(edge_id)
        self.removed_edge = None
        self.error = ''
        self.path = str(getattr(plan, 'path', '') or '')

    def execute(self):
        from storygraph.story.control_branch import apply_control_branch
        res = apply_control_branch(self.plan)
        if not res.get('ok'):
            self.error = str(res.get('error') or '删编排层分支失败')
            raise RuntimeError(self.error)
        edge = self.graph_view.graph.edges.get(self.edge_id)
        if edge is not None:
            self.removed_edge = copy.deepcopy(edge)
            self.graph_view.graph.remove_edge(self.edge_id)
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import read_text
        if self.path:
            now, style = read_text(self.path)
            if now != self.plan.new_text:
                raise RuntimeError(
                    '编排层在删除之后又被改过；为了不覆盖新改动，'
                    '这次 Ctrl+Z 没有回滚那个文件')
            write_text_atomic(self.path, style.apply(self.plan.old_text,
                                                     source=self.path))
        if self.removed_edge is not None:
            self.graph_view.graph.edges[self.edge_id] = self.removed_edge
        self.graph_view.on_graph_structure_changed()


class HistoryManager:
    def __init__(self, graph_view):
        self.graph_view = graph_view
        self.undo_stack = []
        self.redo_stack = []
        self.max_steps = 30

    def execute_command(self, command):
        command.execute()
        self.undo_stack.append(command)
        if len(self.undo_stack) > self.max_steps:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            notify.info('没有可撤销的操作', title='提示')
            return
        command = self.undo_stack.pop()
        try:
            command.undo()
        except Exception:
            # 撤销失败（例如文件被外部改过）→ 命令留在原位，别让它消失。
            self.undo_stack.append(command)
            raise
        self.redo_stack.append(command)

    def redo(self):
        if not self.redo_stack:
            notify.info('没有可重做的操作', title='提示')
            return
        command = self.redo_stack.pop()
        try:
            command.execute()
        except Exception:
            self.redo_stack.append(command)
            raise
        self.undo_stack.append(command)