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
from storygraph.core import notify

from storygraph.core.safeguard import swallow


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


def _operation_texts(node) -> list:
    """
    节点上的代码文本（**结构化真源 → 文本**）。

    迁移后 `Node` 存的是 `list[Statement]`；这里显式只取"代码类"语句的文本，
    而不是去读兼容视图 `node.operations` —— 视图是给老调用方的，
    导出这条路自己该看得见 `kind`（以后图里要是有了非代码语句，
    这里天然不会把它当 `$` 写出来）。
    """
    from storygraph.core.statement import is_code_statement, operation_source
    stmts = getattr(node, 'statements', None)
    if stmts is None:                     # 极老的鸭子类型对象（测试桩）
        return list(getattr(node, 'operations', []) or [])
    return [operation_source(s) for s in stmts if is_code_statement(s)]


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

def export_to_renpy(graph, filepath, wrapper=None, *, project_root=None):
    """
    把剧情图写成 Ren'Py 控制流脚本。

    `wrapper=None`（默认）：按 `graph.control_flow` 决定 ——
      控制流图（导入的就只有 call/jump/menu）→ **原样重建**（不再补 `_control`）；
      章节图 → 每个节点生成 `<label>_control:` 包装（`call 原label` + 按边写跳转）。
    `wrapper=True/False`：显式覆盖（界面上"按章节图导出"入口用它）。
    """
    all_vars = _declared_variables(graph)
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
    for var in sorted(all_vars):
        lines.append(f"default {var} = 0")
    lines.append("")

    for nid, node in graph.nodes.items():
        control_label = control_name(node.label_name)
        lines.append(f"label {control_label}:")
        for op in _operation_texts(node):
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
            continue

        menu_edges = [e for e in out_edges if e.option_text]
        cond_edges = [e for e in out_edges if not e.option_text and e.condition]
        uncond_edges = [e for e in out_edges if not e.option_text and not e.condition]

        if menu_edges and not cond_edges and not uncond_edges:
            lines.append("    menu:")
            for edge in menu_edges:
                target_node = graph.nodes.get(edge.to_id)
                if not target_node:
                    continue
                next_label = control_name(target_node.label_name)
                lines.append(_option_line(edge.option_text, edge.condition,
                                          ' ' * 8))
                lines.append(f"            {edge.type} {next_label}")

        elif menu_edges:
            if not cond_edges:
                lines.append("    menu:")
                for edge in menu_edges:
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    next_label = control_name(target_node.label_name)
                    lines.append(_option_line(edge.option_text, edge.condition,
                                              ' ' * 8))
                    lines.append(f"            {edge.type} {next_label}")
            else:
                for idx, edge in enumerate(cond_edges):
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    next_label = control_name(target_node.label_name)
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
                    next_label = control_name(target_node.label_name)
                    lines.append(_option_line(edge.option_text, edge.condition,
                                              ' ' * 12))
                    lines.append(f"                {edge.type} {next_label}")

        else:
            if cond_edges:
                for idx, edge in enumerate(cond_edges):
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    next_label = control_name(target_node.label_name)
                    prefix = "if" if idx == 0 else "elif"
                    lines.append(f"    {prefix} {edge.condition}:")
                    lines.append(f"        {edge.type} {next_label}")
                if uncond_edges:
                    lines.append("    else:")
                    for edge in uncond_edges:
                        target_node = graph.nodes.get(edge.to_id)
                        if not target_node:
                            continue
                        lines.append(
                            f"        {edge.type} "
                            f"{control_name(target_node.label_name)}"
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
                            f"{control_name(target_node.label_name)}"
                            + _from_suffix(edge))
                else:
                    lines.append("    return")
        lines.append("")

    text = '\n'.join(lines)
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
                                project_root=project_root)
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
