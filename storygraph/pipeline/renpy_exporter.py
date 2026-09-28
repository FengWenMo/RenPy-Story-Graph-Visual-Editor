# renpy_exporter.py
import tkinter.messagebox as messagebox
from storygraph.core.utils import collect_all_variables

def export_to_renpy(graph, filepath):
    all_vars = collect_all_variables(graph)
    lines = ["# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）"]
    for var in sorted(all_vars):
        lines.append(f"default {var} = 0")
    lines.append("")

    for nid, node in graph.nodes.items():
        control_label = f"{node.label_name}_control"
        lines.append(f"label {control_label}:")
        for op in node.operations:
            if '\n' in op:
                lines.append("    python:")
                for line in op.splitlines():
                    lines.append(f"        {line}")
            else:
                lines.append(f"    $ {op}")
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
                next_label = f"{target_node.label_name}_control"
                opt = edge.option_text
                cond_str = f" if {edge.condition}" if edge.condition else ""
                lines.append(f'        "{opt}"{cond_str}:')
                lines.append(f"            {edge.type} {next_label}")

        elif menu_edges:
            if not cond_edges:
                lines.append("    menu:")
                for edge in menu_edges:
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    next_label = f"{target_node.label_name}_control"
                    opt = edge.option_text
                    cond_str = f" if {edge.condition}" if edge.condition else ""
                    lines.append(f'        "{opt}"{cond_str}:')
                    lines.append(f"            {edge.type} {next_label}")
            else:
                for idx, edge in enumerate(cond_edges):
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    next_label = f"{target_node.label_name}_control"
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
                    next_label = f"{target_node.label_name}_control"
                    opt = edge.option_text
                    cond_str = f" if {edge.condition}" if edge.condition else ""
                    lines.append(f'            "{opt}"{cond_str}:')
                    lines.append(f"                {edge.type} {next_label}")

        else:
            if cond_edges:
                for idx, edge in enumerate(cond_edges):
                    target_node = graph.nodes.get(edge.to_id)
                    if not target_node:
                        continue
                    next_label = f"{target_node.label_name}_control"
                    prefix = "if" if idx == 0 else "elif"
                    lines.append(f"    {prefix} {edge.condition}:")
                    lines.append(f"        {edge.type} {next_label}")
                if uncond_edges:
                    target_node = graph.nodes.get(uncond_edges[0].to_id)
                    if target_node:
                        next_label = f"{target_node.label_name}_control"
                        lines.append("    else:")
                        lines.append(f"        {uncond_edges[0].type} {next_label}")
            else:
                if uncond_edges:
                    target_node = graph.nodes.get(uncond_edges[0].to_id)
                    if target_node:
                        next_label = f"{target_node.label_name}_control"
                        lines.append(f"    {uncond_edges[0].type} {next_label}")
                else:
                    lines.append("    return")
        lines.append("")

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        messagebox.showinfo("导出成功",
            f"控制流已写入 {filepath}\n请确保对应的剧本 label 末尾有 return。")
    except Exception as e:
        messagebox.showerror("导出失败", str(e))