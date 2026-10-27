"""
storygraph.pipeline.renpy_exporter
==================================
**剧情图 → Ren'Py 控制流脚本**（`<label>_control` + `jump`/`call`/`menu` 骨架）。

**ownership（本仓库有两条 `.rpy` 生成路径，职责不重叠 —— 2026-10-09 显式化）**

| 谁 | 产什么 | 谁在用 |
|---|---|---|
| **本模块** `export_to_renpy` | **控制流**：每个章节 label 的 `<label>_control` 包装 + 按边写 `jump`/`call`/`menu`；**不产台词、不产舞台** | UI「导出控制流 .rpy」（`app/session_graph.export_control_flow`） |
| `story/beat_render.render_beats_to_rpy` | **完整剧本**：台词 / 舞台 / 音频 / 自定义 transform | `pipeline/build.py`、`pipeline/compile.py`、`app/tables.py`（三条生产路径） |

两条路**共用同一套规则**，不许各写一份：
  · `beat_render.render_code_block` —— python op 的渲染（含行首紧凑标记约定）；
  · `beat_render.quote_string` —— 字符串转义（`\\ " \\n` 一份规则）。

**落盘一律过 `story.generation_guard.write_generation`**（契约 B1）：控制流脚本
同样会进 `game/`，同样可能 label 重名或覆盖手写文件。守卫
`tests/test_single_write_entry.py`（登记表 + 定向判据）。
"""
from pathlib import Path
import re

from storygraph.core import notify

from storygraph.core.safeguard import swallow


_RE_DECLARED_NAME = re.compile(
    r'^[ \t]*(?:default|define)[ \t]+([A-Za-z_]\w*)', re.MULTILINE)


def _project_declared_names(out_path) -> set:
    """
    项目里**别处**已经 `default` / `define` 过的名字 —— 这里不能再声明一次。

    为什么必须查（2026-10-28，真机 lint 实测）：

        Exception: store.Plotnodes211 is being given a default a second time.
        File "game/plottree.rpy", line 7, in execute_default
            default Plotnodes211 = 0

    `plottree.rpy` 是作者自己声明剧情变量的地方，控制流产物再写一遍
    `default Plotnodes211 = 0` → **游戏起不来**（lint 直接 exit 1）。
    以前没撞上只是因为两边名字差一个 `s`，谁都没声明成同一个。

    只看 `game/` 下（Ren'Py 真正会扫的地方），并**跳过目标文件自己**
    （它马上就要被整份重写，里面的旧声明不算数）。
    """
    if not out_path:
        return set()
    target = Path(str(out_path))
    game_dir = None
    for parent in target.parents:
        if parent.name == 'game':
            game_dir = parent
            break
    if game_dir is None:
        return set()
    try:
        target_res = target.resolve()
    except OSError:
        target_res = None
    names = set()
    try:
        files = sorted(game_dir.rglob('*.rpy'))
    except OSError:
        return names
    for path in files:
        try:
            if target_res is not None and path.resolve() == target_res:
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        names.update(_RE_DECLARED_NAME.findall(text))
    return names


def _declared_variables(graph):
    """
    该写 `default x = 0` 的变量。

    **不能**用 `collect_all_variables`（那是"出现过的名字"）：它会把
    `renpy` / `persistent` / `config` / `time` 这些模块名、以及 `for i in …`
    的循环变量一起收进来，于是产物里出现

        default renpy = 0        ← 把引擎模块遮蔽成 int，整章跑不起来
        default i = 0

    （2026-10-05 审计"双 IR 并存"时用真实项目导出实测到）。
    这里走 `core.utils.declared_variables_of_graph`：只认**被赋值的名字**，
    且剔掉引擎/标准库模块与循环临时变量。
    """
    from storygraph.core.utils import declared_variables_of_graph
    try:
        return declared_variables_of_graph(graph)
    except Exception as e:                    # noqa: BLE001 - 抽不出来就别写 default
        swallow('pipeline.renpy_exporter._declared_variables', e)
        return []


_SOURCE_CACHE: dict = {}


def _statement_source(stmt):
    """`Statement.source` → `(路径, 行号)`；缺失/畸形一律当"没有来源"。"""
    src = getattr(stmt, 'source', ('', 0)) or ('', 0)
    try:
        path, lineno = str(src[0] or '').strip(), int(src[1] or 0)
    except (TypeError, ValueError, IndexError):
        return '', 0
    return path, lineno


def _file_line(path: str, lineno: int) -> str:
    """那一行原文（1-based，去掉首尾空白）；读不到返回空串。"""
    if path not in _SOURCE_CACHE:
        try:
            _SOURCE_CACHE[path] = Path(path).read_text(
                encoding='utf-8', errors='replace').splitlines()
        except OSError:
            _SOURCE_CACHE[path] = []
    lines = _SOURCE_CACHE[path]
    if 0 < lineno <= len(lines):
        return lines[lineno - 1].strip()
    return ''


def _looks_like_line(code: str, raw: str) -> bool:
    """`code` 是不是就是 `raw` 那一行（`$` 前缀、行首标记都不算差异）。"""
    from storygraph.core.statement import strip_marker
    want = strip_marker(str(code or '')).strip().splitlines()
    want = want[0].strip() if want else ''
    got = str(raw or '').strip()
    if got.startswith('$'):
        got = got[1:].strip()
    return bool(want) and got == want


def _lives_in_its_source_file(stmt, out_path=None) -> bool:
    """
    这条代码语句是不是**本来就写在某份章节 .rpy 里、而且现在还在那儿**？

    为什么要问（2026-10-28，用户实测）：

        > 你把它擅自搬过来了会不会把 1_4 这些原本的文件毁掉？？？
        > 不该擅自搬代码的……不然也没有作用！本来在那里的就先放着吧……

    章节图里的 `$ renpy.transition(...)` / `$ msg_add(...)` 是**导入时从
    章节文件里读进来的**（`Statement.source` 记着文件+行号）。控制流导出
    再写一遍 = 同一句代码跑两遍：`transition` 是"下一次交互用哪个过渡"
    （后写覆盖先写），几百条堆在 `call chapter1_1` **之前**，原位置语义
    全丢 —— 用户看到的就是"文件被噪音淹了，还看不出真正的跳转"。

    判据三条，缺一不可：
      · `source` 有路径 → 它来自文件（编辑器里新加的那条 `source=('',0)`）；
      · 那个文件**现在**还在；
      · 那个文件**第 N 行**现在还是这句代码 —— 人改过 / 删过就别自作聪明，
        照写（宁可重复，也不许把代码吞掉）。

    `out_path` 是这次要写的文件：**源文件就是目标本身**时必须照写，否则
    等于把文件内容整段删掉（"导入控制流图再导回去"那条路）。
    """
    path, lineno = _statement_source(stmt)
    if not path or not lineno:
        return False
    try:
        same_as_target = bool(out_path) and Path(path).resolve() == Path(out_path).resolve()
    except OSError:
        same_as_target = False
    if same_as_target:
        return False
    from storygraph.core.statement import operation_source
    return _looks_like_line(operation_source(stmt), _file_line(path, lineno))


def _operation_texts(node, out_path=None) -> list:
    """
    节点上的代码文本（**结构化真源 → 文本**）。

    迁移后 `Node` 存的是 `list[Statement]`；这里显式只取"代码类"语句的文本，
    而不是去读兼容视图 `node.operations` —— 视图是给老调用方的，
    导出这条路自己该看得见 `kind`（以后图里要是有了非代码语句，
    这里天然不会把它当 `$` 写出来）。

    `out_path` 见 `_lives_in_its_source_file`：**章节文件里还在的那几行不再
    搬进控制流**（用户 2026-10-28 的 P0）。
    """
    from storygraph.core.statement import is_code_statement, operation_source
    stmts = getattr(node, 'statements', None)
    if stmts is None:                     # 极老的鸭子类型对象（测试桩）
        return list(getattr(node, 'operations', []) or [])
    return [operation_source(s) for s in stmts
            if is_code_statement(s)
            and not _lives_in_its_source_file(s, out_path)]


def imported_operation_lines(graph, out_path=None) -> set:
    """
    这次**不会写进控制流**的代码行（渲染后、去掉首尾空白）。

    给 `control_write.find_manual_lines` 用：老产物里那几百行 `$ …` 是上一版
    工具搬过去的，重导出时它们会消失 —— 那是**我们自己造成的**，不该在
    "手写内容"那条门禁上拦用户一道（否则第一次重导出必然被问一遍）。
    """
    from storygraph.core.statement import is_code_statement, operation_source
    from storygraph.story.beat_render import render_code_block
    out = set()
    for node in (getattr(graph, 'nodes', {}) or {}).values():
        for stmt in (getattr(node, 'statements', []) or []):
            if not is_code_statement(stmt):
                continue
            if not _lives_in_its_source_file(stmt, out_path):
                continue
            for ln in render_code_block(operation_source(stmt),
                                        multiline='python'):
                if str(ln).strip():
                    out.add(str(ln).strip())
    return out


def _from_suffix(edge) -> str:
    """
    `call X from Y` 的 ` from Y` 尾巴（没有就空串）。

    `from` 是 Ren'Py 的**存档点标识**：同一个 label 被多处 call 时靠它区分
    存档属于哪一处。以前导出时直接丢掉 → 语义等价但老存档找不到
    `_call_xxx`（用户 2026-10-05："先修复子句会丢的问题"）。
    """
    clause = str(getattr(edge, 'from_clause', '') or '').strip()
    if not clause or str(getattr(edge, 'type', '')) != 'call':
        return ''
    return f' from {clause}'


def _edge_target(edge, target_node, control_name) -> str:
    """
    这条边该写哪个 label。

    `origin='control'` 的 **call** 是 P2 的"章末 call 子过程"：它要调
    **章节本身**（`call B`），不是调 `B_control` —— 后者自己会跳走，
    不会回到调用点。jump 仍然去 `B_control`（章级编排）。
    """
    name = str(getattr(target_node, 'label_name', '') or '')
    if (str(getattr(edge, 'origin', '')) == 'control'
            and str(getattr(edge, 'type', '')) == 'call'):
        return name
    return control_name(name)


def _option_line(opt, condition: str, indent: str) -> str:
    """
    菜单选项那一行 —— **字符串转义走章节导出那一份规则**
    （`beat_render.quote_string`，见 `_quote` 的说明）。

    为什么不能自己包引号（2026-10-05 实测，清单 O4 的证据）：
    控制流导出从前写的是 `f'"{opt}"'`，于是

        option_text = '他说："你好"'  →  "他说："你好"":     ← 语法错
        option_text = '第一行\\n第二行' →  "第一行 / 第二行":   ← 跨物理行字符串

    两条都是**产物不可解析**。转义规则（`\\ " \n \r \t`）只有一份，
    两个 .rpy 生成器必须共用它。
    """
    from storygraph.story.beat_render import quote_string
    cond_str = f" if {condition}" if condition else ""
    return f'{indent}{quote_string(opt)}{cond_str}:'


def _all_menu_options_conditional(menu_edges) -> bool:
    """
    这个 `menu:` 会不会**一个选项都不出现**？

    判据只有一条：每条选项边都带 `condition`。Ren'Py 会把不成立的选项
    直接藏起来；全藏光时整个 `menu:` 被跳过、执行落到下一行 —— 所以这种
    菜单后面必须自己补 `return`（否则掉进下一个 label，静默多跑一章）。
    """
    return bool(menu_edges) and all(
        str(getattr(e, 'condition', '') or '').strip() for e in menu_edges)

def _export_control_node(nid, node, lines, graph, controller_style,
                        control_name, out_path=None):
    """
    控制流图里**一个节点**的产物（M4 从 `export_to_renpy` 搬出）。

    菜单边/条件边/无条件边三路；`_control` 节点不再 call 自己。
    原来那个"跳过后面的边"的 `continue` 改成 `return`（等价）。

    `out_path` 只影响"哪些代码行算这个文件该写的"（见 `_operation_texts`）。
    """
    control_label = control_name(node.label_name)
    lines.append(f"label {control_label}:")
    for op in _operation_texts(node, out_path):
        # **渲染 python op 只有一份规则**（`story.beat_render.render_code_block`）：
        # 以前这里手写 `f"    $ {op}"`，而它不知道行首 `\x01`
        # （= 原文是紧凑 `$code`）这个约定，于是把控制字符原样写进产物
        # （实测：`$ ^Apersistent.lock_lst["cg01"]= True` → Ren'Py 不认）。
        from storygraph.story.beat_render import render_code_block
        for ln in render_code_block(op, multiline='python'):
            lines.append(f"    {ln}" if str(ln).strip() else '')
    # 控制流图里的 _control 节点**不要**再 call 自己：那一拍在控制流
    # 里本来就由一条 call 边表示（自发的话会变成"call 两次"）。
    if not controller_style:
        lines.append(f"    call {node.label_name}")

    out_edges = [e for e in graph.edges.values() if e.from_id == nid]
    if not out_edges:
        lines.append("    return")
        lines.append("")
        return

    menu_edges = [e for e in out_edges if e.option_text]
    cond_edges = [e for e in out_edges if not e.option_text and e.condition]
    uncond_edges = [e for e in out_edges if not e.option_text and not e.condition]

    if menu_edges and not cond_edges and not uncond_edges:
        lines.append("    menu:")
        for edge in menu_edges:
            target_node = graph.nodes.get(edge.to_id)
            if not target_node:
                continue
            next_label = _edge_target(edge, target_node, control_name)
            lines.append(_option_line(edge.option_text, edge.condition,
                                      ' ' * 8))
            lines.append(f"            {edge.type} {next_label}")
        if _all_menu_options_conditional(menu_edges):
            # Ren'Py：**所有**选项条件都不成立时，整个 `menu:` 块会被跳过，
            # 执行落到 `menu:` 的下一行 —— 也就是**下一个 label**，把别的章
            # 顺带跑一遍。只要还有一个无条件选项，玩家总能选中，落不到外面。
            lines.append("    return")

    elif menu_edges:
        if not cond_edges:
            lines.append("    menu:")
            for edge in menu_edges:
                target_node = graph.nodes.get(edge.to_id)
                if not target_node:
                    continue
                next_label = _edge_target(edge, target_node, control_name)
                lines.append(_option_line(edge.option_text, edge.condition,
                                          ' ' * 8))
                lines.append(f"            {edge.type} {next_label}")
            if uncond_edges:
                # 既有菜单、又有一条无条件出口：菜单选项全不成立时走这条。
                # 从前这里**静默丢掉** uncond 边（产物直接掉进下一个 label），
                # 与 `else` 分支只写 cond 的那条路同源。
                for edge in uncond_edges:
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    lines.append(
                        f"    {edge.type} "
                        f"{_edge_target(edge, target_node, control_name)}"
                        + _from_suffix(edge))
            elif _all_menu_options_conditional(menu_edges):
                lines.append("    return")
        else:
            for idx, edge in enumerate(cond_edges):
                target_node = graph.nodes.get(edge.to_id)
                if not target_node:
                    continue
                next_label = _edge_target(edge, target_node, control_name)
                prefix = "if" if idx == 0 else "elif"
                lines.append(f"    {prefix} {edge.condition}:")
                lines.append(f"        {edge.type} {next_label}")

            if uncond_edges:
                lines.append("    # 警告：无条件跳转被忽略，因为存在菜单分支")

            lines.append("    else:")
            lines.append("        menu:")
            for edge in menu_edges:
                target_node = graph.nodes.get(edge.to_id)
                if not target_node:
                    continue
                next_label = _edge_target(edge, target_node, control_name)
                lines.append(_option_line(edge.option_text, edge.condition,
                                          ' ' * 12))
                lines.append(f"                {edge.type} {next_label}")
            if _all_menu_options_conditional(menu_edges):
                # 同上：全条件选项的菜单可能一个都不出现 → 必须自己兜底。
                lines.append("        return")

    else:
        if cond_edges:
            for idx, edge in enumerate(cond_edges):
                target_node = graph.nodes.get(edge.to_id)
                if not target_node:
                    continue
                next_label = _edge_target(edge, target_node, control_name)
                prefix = "if" if idx == 0 else "elif"
                lines.append(f"    {prefix} {edge.condition}:")
                lines.append(f"        {edge.type} {next_label}")
            if not uncond_edges:
                # 条件全不成立时这一章/这一段就结束了；没有这句，执行会
                # 直接落进**下一个 `label`** —— 那是一条静默的错路线。
                lines.append("    return")
            else:
                lines.append("    else:")
                for edge in uncond_edges:
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    lines.append(
                        f"        {edge.type} "
                        f"{_edge_target(edge, target_node, control_name)}"
                        + _from_suffix(edge))
        else:
            if uncond_edges:
                # **每一条都要写**：控制流节点天然有两条无条件边
                # （call 原label + jump 下一个control），以前只写
                # uncond_edges[0] → 那 4 条 jump 全被丢掉，
                # 产物还是"看起来能跑"的（只是剧情不会往下走）。
                for edge in uncond_edges:
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    lines.append(
                        f"    {edge.type} "
                        f"{_edge_target(edge, target_node, control_name)}"
                        + _from_suffix(edge))
                last = uncond_edges[-1]
                if (str(getattr(last, 'origin', '')) == 'control'
                        and str(getattr(last, 'type', '')) == 'call'):
                    # call 会回来；后面没有别的路由时必须自己收尾，
                    # 否则会掉进下一个 label（P2，2026-10-24）。
                    lines.append("    return")
            else:
                lines.append("    return")
    lines.append("")

def render_control_text(graph, wrapper=None, out_path=None) -> str:
    """
    按图生成控制流脚本文本（**纯计算，不落盘、不弹通知**）。

    与 `export_to_renpy` 的文本部分**同一份实现**：导出、diff 预览、"改一条
    分支后重生成"都必须看到同一段产物，否则预览里说会写什么、实际写下去
    又是另一份（那种漂移最难查）。

    `wrapper` 语义与 `export_to_renpy` 完全一致。
    `out_path`：这次要写到哪个文件（`None` = 还没定）。它只影响"哪些代码
    **行**该由这个文件写"：来源就是目标本身的必须写，来源是别的章节文件的
    不写（见 `_lives_in_its_source_file`）。
    """
    # 这张图是"章节图"还是"控制流图"？
    #
    # 判据来自**内容 / 显式标志**（`Graph.control_flow`，导入时按"文件里有没有
    # 演出语句"设定），**绝不看名字**：用户完全可以把一个章节命名为
    # `A_control`，靠名字猜必然误判 —— 用户 2026-10-05 明确要求
    # "不得是变成保留字，因为你不能阻拦用户起什么名"。
    # （上一版的名字启发式已删除。）
    if wrapper is None:
        controller_style = bool(getattr(graph, 'control_flow', False))
    else:
        controller_style = not bool(wrapper)

    def control_name(label):
        name = str(label or '')
        if controller_style:
            return name                       # 已经是最终标签名，一个字符都不加
        # 章节图：**一律**补一层 `_control` —— 哪怕这个章节真的叫
        # `A_control`（那它的包装就是 `A_control_control`）。
        # 这里以前有一句"名字已经以 `_control` 结尾就原样返回"，那是拿名字
        # 当保留字用，正是用户 2026-10-05 明确否掉的写法：**不能拦用户起名**。
        return f'{name}_control'

    lines = ["# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）"]
    # **别处已经声明过的变量不再声明**：重复的 `default` 会让游戏起不来
    # （见 `_project_declared_names` 的实测记录）。
    declared = set(_declared_variables(graph)) - _project_declared_names(out_path)
    for var in sorted(declared):
        lines.append(f"default {var} = 0")
    lines.append("")

    for nid, node in graph.nodes.items():
        _export_control_node(nid, node, lines, graph,
                             controller_style, control_name, out_path)

    return '\n'.join(lines)


def export_to_renpy(graph, filepath, wrapper=None, *, project_root=None,
                    force=False):
    """
    把剧情图写成 Ren'Py 控制流脚本。

    `wrapper=None`（默认）：按 `graph.control_flow` 决定 ——
      控制流图（导入的就只有 call/jump/menu）→ **原样重建**（不再补 `_control`）；
      章节图 → 每个节点生成 `<label>_control:` 包装（`call 原label` + 按边写跳转）。
    `wrapper=True/False`：显式覆盖（界面上"按章节图导出"入口用它）。

    `force=True`（2026-10-28）：目标已存在且**没有生成标记**时也写。
    调用方（界面）必须已经拿到用户确认，并且**先备份** —— 见
    `app.session_graph.export_control_flow(force_overwrite=True)`。
    它只越过"目标配不上产物"那两条（HANDWRITTEN_TARGET / OPAQUE_LOST），
    label 重名、语法错一律照拦。
    """
    text = render_control_text(graph, wrapper=wrapper, out_path=filepath)
    # ⚠ 落盘一律过**生成门禁**（契约 B1，2026-10-08 修）。
    #
    # 这里以前是裸 `open(filepath, 'w')` —— 等于把门禁漏在门外：控制流脚本
    # 同样会进 `game/`，同样可能 ① 生成的 `xxx_control` 与项目里别的 .rpy
    # label 重名（Ren'Py 把 game/ 下所有 .rpy 当同一个脚本，重名**游戏直接
    # 起不来**）② 覆盖一个手写文件（把作者的 `default`/`define` 静默抹掉）。
    # `write_generation` 会把产物首行写成 `GENERATOR_MARKER`（于是"再导出
    # 一次"仍被放行），命中 block 级问题时**不写目标路径**、改写
    # `<stem>.blocked.rpy` 供对照。
    from storygraph.story.generation_guard import (ensure_generator_marker,
                                                   write_generation)
    # ⚠ **标记必须在写盘前确保**（`ensure_generator_marker`）：护栏靠"首行有没有
    # 标记"判断已存在的文件是不是工具写的；不带标记的话，第二次导出会被判成
    # "手写文件"而**必然失败**（"再导出一次"的正常操作被拦）。
    artifact = write_generation(ensure_generator_marker(text), filepath,
                                project_root=project_root, force=force)
    if artifact.written:
        notify.info(f"控制流已写入 {filepath}\n"
                    f"请确保对应的剧本 label 末尾有 return。",
                    title="导出成功")
    else:
        why = '；'.join(i.format() for i in (artifact.blocking or [])[:3])
        notify.error(
            f'控制流**没有**写进目标路径（生成的脚本会让游戏跑不起来）'
            f'{("：" + why) if why else ""}\n'
            f'对照副本：{artifact.review_path}',
            title="导出被拦下")
    return artifact
