# graph_view.py
import tkinter as tk
from tkinter import simpledialog, messagebox, filedialog
import tkinter.scrolledtext as st
import os
import sys
import subprocess
import json
import math
import queue
import threading
import hashlib
from pathlib import Path

from asset_engine import AssetEngine, is_under_path
from models import Graph, Node, Edge
from utils import collect_all_variables
from analysis import analyze_reachability, visualize_analysis_results, apply_operation, condition_possible, apply_condition, VariableState, pick_entry_node, analyze_reachability_full
from renpy_parser import parse_renpy_file
from renpy_exporter import export_to_renpy
from layout import layout_graph
from commands import (
    AddNodeCommand, RemoveNodeCommand, AddEdgeCommand, RemoveEdgeCommand,
    UpdateNodeOperationsCommand, UpdateEdgeCommand, SetCurrentNodeCommand,
    MoveNodeCommand, ClearGraphCommand, ImportRenPyCommand, LoadLayoutCommand,
    ReplaceGraphCommand, HistoryManager
)
from editor_utils import open_file_at_line
import story_ops


def _open_with_system_default(path) -> bool:
    """
    用系统默认程序打开文件（Windows 上是 Excel，macOS 是 Numbers/Excel）。

    返回 False 表示没能唤起任何程序——调用方应提示用户手动打开，
    而不是假装成功了。
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        if sys.platform == 'win32':
            os.startfile(str(path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
        return True
    except Exception:
        return False

# 新增：导入资产注册模块
from asset_registry import (
    AssetRegistry,
    parse_asset_tables,
    load_hooks
)
from asset_engine import AssetEngine
from asset_events import (
    AssetEventBus,
    AssetEventType,
    ConfirmationGate,
)
from script_table import (
    beats_from_parse_result, evaluate_generation, export_beats_to_excel,
    import_beats_from_excel, occupied_labels, planned_output_path,
    render_beats_to_rpy, write_generation,
)


class GraphView:
    def __init__(self, root, graph):
        self.graph = graph
        self.root = root
        self.root.title("剧情树编辑器-织言片语")
        self.root.geometry("1400x900")

        # 资产引擎（懒初始化，打开项目后才创建）
        self.asset_engine = None
        self.asset_bus = None
        self.project_root = None
        self._asset_poll_handle = None
        self._busy_desc = ''          # 当前后台任务描述（状态提示用）

        self.offset_x = 0
        self.offset_y = 0
        self.scale = 1.0

        self.selected_node = None
        self.selected_edge = None
        self.drag_data = {"node": None, "start_x": 0, "start_y": 0, "old_x": 0, "old_y": 0}
        self.connect_from = None
        self.temp_line = None

        # 空格键状态与左键平移状态
        self.space_pressed = False
        self.panning = False
        self.pan_start_x = 0
        self.pan_start_y = 0
        self.pan_start_offset_x = 0
        self.pan_start_offset_y = 0

        self.canvas_ids = {}
        self.edge_ids = {}
        self.edge_arrow_ids = {}
        self.edge_label_ids = {}

        self.compute_edge_control_cache = {}

        self.result_queue = queue.Queue()
        self.busy_count = 0
        self.lock = threading.Lock()

        self.analysis_cache = None
        self.graph_hash = None
        self.variables_cache = None

        self.history = HistoryManager(self)

        self.setup_ui()
        self.redraw_all()
        self.root.after(100, self.process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def setup_ui(self):
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(main_frame, bg='#f0f0f0', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_panel = tk.Frame(main_frame, width=350)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)

        tk.Label(right_panel, text="分析结果", font=("微软雅黑", 12, "bold")).pack(pady=5)
        self.analysis_text = st.ScrolledText(right_panel, width=45, height=5, font=("Consolas", 9))
        self.analysis_text.pack(fill=tk.BOTH, expand=True, pady=5)

        btn_frame = tk.Frame(right_panel)
        btn_frame.pack(fill=tk.X, pady=5)

        # ============ 主入口：打开本章剧本（4 项卡片） ============
        # 用户 2026-09-28 反馈："我不知道如何打开你说的卡片入口"。
        # 根因是入口埋在侧栏第三组按钮里、名字叫「在应用内编辑剧本表」，
        # 而且打开后是**空窗口**（还要自己找底部的「载入 .rpy」）。
        # 现在把它放到最上面、改成人话名字，并在打开时自动载入当前章节。
        self.btn_script = tk.Button(
            btn_frame, text="✎ 打开本章剧本（改台词）",
            command=self.open_script_table_editor,
            bg="#1976D2", fg="white",
            font=("微软雅黑", 10, "bold"))
        self.btn_script.pack(pady=(2, 2), fill=tk.X)

        # 当前要编辑哪一章：跟着画布上选中的章节节点走
        self.chapter_label = tk.Label(
            btn_frame, text="（先在画布上点一个章节，或用右键菜单选文件）",
            font=("微软雅黑", 8), fg="#666", wraplength=330, justify=tk.LEFT)
        self.chapter_label.pack(pady=(0, 4), fill=tk.X)

        # ============ 导入：一个按钮，可多选 ============
        # 用户明确要求："批量导入其实可以删掉，本来直接写导入 Renpy 文件
        # 就包括了批量导入"。`filedialog` 本来就支持多选，所以这里只留一个
        # 按钮，标题写明"可多选"——功能没少，按钮少一个。
        tk.Button(btn_frame, text="导入章节 .rpy（可多选）",
                  command=self.import_renpy).pack(pady=2, fill=tk.X)

        # ============ 项目与资源 ============
        self.btn_open_project = tk.Button(btn_frame, text="打开项目",
                                          command=self.open_project)
        self.btn_open_project.pack(pady=2, fill=tk.X)
        self.project_label = tk.Label(btn_frame, text="（未打开项目）",
                                      font=("微软雅黑", 8), fg="#666")
        self.project_label.pack(pady=2, fill=tk.X)
        self.btn_chars = tk.Button(btn_frame, text="从脚本导入角色表",
                                   command=self.import_characters_from_script)
        self.btn_chars.pack(pady=2, fill=tk.X)
        self.btn_health = tk.Button(btn_frame, text="项目体检（三级校验）",
                                    command=self.open_validation_panel,
                                    bg="#FF9800", fg="white")
        self.btn_health.pack(pady=2, fill=tk.X)

        # ============ 交换面：Excel（降级为互换格式，不是主编辑面） ============
        script_frame = tk.LabelFrame(
            right_panel, text="交给 Excel（批量改 / 校对）", padx=5, pady=5)
        script_frame.pack(fill=tk.X, pady=5)
        tk.Button(script_frame, text="导出本章为 Excel 表…",
                  command=self.export_table_for_chapter).pack(pady=2, fill=tk.X)
        tk.Button(script_frame, text="从 Excel 表生成 .rpy…",
                  command=self.export_rpy_from_table).pack(pady=2, fill=tk.X)
        tk.Button(script_frame, text="刷新下拉候选（从资源表重读）",
                  command=self.refresh_table_candidates).pack(pady=2, fill=tk.X)
        tk.Label(script_frame,
                 text="（主编辑面是上面的「打开本章剧本」；\n"
                      "Excel 用来批量改台词、本地化、QA 校对）",
                 font=("微软雅黑", 8), fg="#666", justify=tk.LEFT
                 ).pack(pady=(2, 0), fill=tk.X)

        layout_frame = tk.LabelFrame(right_panel, text="画布", padx=5, pady=5)
        layout_frame.pack(fill=tk.X, pady=5)
        tk.Button(layout_frame, text="保存布局(JSON)",
                  command=self.save_layout).pack(pady=2, fill=tk.X)
        tk.Button(layout_frame, text="加载布局",
                  command=self.load_layout).pack(pady=2, fill=tk.X)
        tk.Button(layout_frame, text="导出为Ren'Py（控制流图）",
                  command=self.export_renpy).pack(pady=2, fill=tk.X)
        tk.Button(layout_frame, text="清空图", command=self.clear_graph,
                  bg="#F44336", fg="white").pack(pady=2, fill=tk.X)

        tk.Button(btn_frame, text="检查逻辑漏洞", command=self.analyze_and_warn,
                 bg="#4CAF50", fg="white").pack(pady=10, fill=tk.X)
        tk.Button(btn_frame, text="显示详细分析", command=self.show_detailed_analysis,
                 bg="#2196F3", fg="white").pack(pady=2, fill=tk.X)

        help_frame = tk.LabelFrame(right_panel, text="支持的语法", padx=5, pady=5)
        help_frame.pack(fill=tk.X, pady=5)
        help_text = """支持的操作:
• 赋值: x = 10
• 增强赋值: x += 5
• 复杂表达式: x = y + z * 2
• 函数调用: x = abs(y)
• 逻辑运算: x = y and z > 0
• 比较: x = a > b

支持的条件:
• 比较: love >= 5, score < 100
• 逻辑组合: love > 5 and money < 100
• 变量比较: a > b, x != y
• 函数调用: abs(score) > 10"""
        tk.Label(help_frame, text=help_text, justify=tk.LEFT, font=("微软雅黑", 8)).pack()

        scale_frame = tk.Frame(right_panel)
        scale_frame.pack(fill=tk.X, pady=5)
        tk.Label(scale_frame, text="缩放：").pack(side=tk.LEFT)
        self.scale_var = tk.StringVar(value="100%")
        tk.Label(scale_frame, textvariable=self.scale_var).pack(side=tk.LEFT)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="添加新章节", command=self.add_node)
        self.menu.add_command(label="设为当前章节", command=self.set_current)
        self.menu.add_command(label="编辑变量操作", command=self.edit_operations)
        self.menu.add_command(label="在编辑器中打开", command=self.open_label_in_editor, state="disabled")
        # 主入口放这里**第一个**：用户选中的章节 = 他要编的那一章。
        # 与「在编辑器中打开」（给程序看源码）并列，一左一右各服务一类人。
        self.menu.add_command(label="改本章台词（卡片）", command=self.open_script_table_editor, state="disabled")
        # 与「在编辑器中打开」同级：同一个章节，一个给程序员看源码，
        # 一个给文案看演出表——不该藏在侧栏，得在节点本身右键。
        self.menu.add_command(label="用 Excel 打开此章节", command=self.open_node_rpy_in_excel, state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="自动布局", command=self.auto_layout)
        self.menu.add_command(label="重新扫描项目", command=self.rescan_project)
        self.menu.add_separator()
        self.menu.add_command(label="删除节点", command=self.delete_node)
        self.menu.add_command(label="删除连线", command=self.delete_edge)

        # 事件绑定（移除了右键拖拽）
        self.canvas.bind("<ButtonPress-1>", self.on_left_down)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_up)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)

        # 键盘事件：空格键按下/释放
        self.root.bind("<KeyPress-space>", self.on_space_press)
        self.root.bind("<KeyRelease-space>", self.on_space_release)

        self.root.bind("<Delete>", self.on_delete_key)
        self.root.bind("<Control-s>", lambda e: self.save_layout())
        self.root.bind("<Control-o>", lambda e: self.load_layout())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())
        self.root.bind("<Control-a>", lambda e: self.analyze_and_warn())

    # ========== 空格键状态处理 ==========
    def on_space_press(self, event):
        self.space_pressed = True

    def on_space_release(self, event):
        self.space_pressed = False
        if self.panning:
            self.panning = False

    def world_to_screen(self, wx, wy):
        return (wx + self.offset_x) * self.scale, (wy + self.offset_y) * self.scale

    def screen_to_world(self, sx, sy):
        return sx / self.scale - self.offset_x, sy / self.scale - self.offset_y

    def redraw_all(self):
        self.canvas.delete("all")
        self.canvas_ids.clear()
        self.edge_ids.clear()
        self.edge_arrow_ids.clear()
        self.edge_label_ids.clear()
        self.compute_edge_control_cache.clear()

        for eid, edge in self.graph.edges.items():
            self.draw_edge(eid)
        for nid, node in self.graph.nodes.items():
            self.draw_node(nid)

        self.update_current_highlight()
        self.update_selection_highlight()

    def draw_node(self, nid):
        node = self.graph.nodes[nid]
        sx, sy = self.world_to_screen(node.x, node.y)
        w, h = 120, 60
        if node.is_current:
            color = "#FFEB3B"
        elif nid == self.selected_node:
            color = "#FF9800"
        else:
            color = "#B3E5FC"
        rect_id = self.canvas.create_rectangle(sx - w/2, sy - h/2, sx + w/2, sy + h/2,
                                                fill=color, outline="#0D47A1", width=2,
                                                tags=("node",))
        text_id = self.canvas.create_text(sx, sy - 10, text=node.label_name,
                                            font=("微软雅黑", 10, "bold"))
        op_text_id = None
        # 只把**剧情变量操作**显示出来：技术代码（renpy./persistent. 之类）
        # 既不影响剧情走向、数量还特别多，混在一起会把真正要看的变量淹掉。
        _summary = story_ops.summarize(node)
        if _summary:
            op_text_id = self.canvas.create_text(sx, sy + 15, text=_summary,
                                                    font=("微软雅黑", 8), fill="#666")
        self.canvas_ids[nid] = (rect_id, text_id, op_text_id)

    def draw_edge(self, eid):
        edge = self.graph.edges[eid]
        from_node = self.graph.nodes.get(edge.from_id)
        to_node = self.graph.nodes.get(edge.to_id)
        if not from_node or not to_node:
            return

        sx1, sy1 = self.world_to_screen(from_node.x, from_node.y)
        sx2, sy2 = self.world_to_screen(to_node.x, to_node.y)

        ctrl_x, ctrl_y, perp_x, perp_y = self.compute_edge_control(eid, sx1, sy1, sx2, sy2)

        if edge.option_text:
            color = "#E65100"
        elif edge.condition:
            color = "#2E7D32"
        else:
            color = "#1565C0"
        width = 3
        if eid == self.selected_edge:
            color = "#D32F2F"
            width = 5
        dash = (6, 3) if edge.type == "call" else None

        line_id = self.canvas.create_line(
            sx1, sy1, ctrl_x, ctrl_y, sx2, sy2,
            smooth=True, splinesteps=32,
            fill=color, width=width, dash=dash
        )
        self.edge_ids[eid] = line_id

        t = 0.5
        mx = (sx1 + 2*ctrl_x + sx2) / 4
        my = (sy1 + 2*ctrl_y + sy2) / 4
        dx_dt = 2 * (1-t) * (ctrl_x - sx1) + 2 * t * (sx2 - ctrl_x)
        dy_dt = 2 * (1-t) * (ctrl_y - sy1) + 2 * t * (sy2 - ctrl_y)
        angle = math.atan2(dy_dt, dx_dt)

        arrow_len = 10
        arrow_angle = math.radians(22)
        tip_x = mx + math.cos(angle) * arrow_len * 0.5
        tip_y = my + math.sin(angle) * arrow_len * 0.5
        left_x = mx - math.cos(angle - arrow_angle) * arrow_len
        left_y = my - math.sin(angle - arrow_angle) * arrow_len
        right_x = mx - math.cos(angle + arrow_angle) * arrow_len
        right_y = my - math.sin(angle + arrow_angle) * arrow_len
        arrow_id = self.canvas.create_polygon(
            tip_x, tip_y, left_x, left_y, right_x, right_y,
            fill=color, outline=color
        )
        self.edge_arrow_ids[eid] = arrow_id

        display_lines = []
        if edge.option_text:
            display_lines.append(edge.option_text)
        if edge.condition:
            display_lines.append(f"if {edge.condition}")
        label_ids = []
        if display_lines:
            label_x = mx + perp_x * 15
            label_y = my + perp_y * 15
            line_height = 20
            total_height = (len(display_lines) - 1) * line_height
            start_y = label_y - total_height / 2
            font = ("微软雅黑", 8, "bold") if eid == self.selected_edge else ("微软雅黑", 8)
            for line in display_lines:
                tid = self.canvas.create_text(
                    label_x, start_y, text=line,
                    fill="darkred", font=font, width=110
                )
                label_ids.append(tid)
                start_y += line_height
        self.edge_label_ids[eid] = label_ids

    def compute_edge_control(self, eid, sx1, sy1, sx2, sy2):
        cache_key = (eid, round(sx1,2), round(sy1,2), round(sx2,2), round(sy2,2))
        if cache_key in self.compute_edge_control_cache:
            return self.compute_edge_control_cache[cache_key]

        edge = self.graph.edges[eid]
        pair_edges = []
        for other_eid, other_edge in self.graph.edges.items():
            if other_eid != eid and other_edge.from_id == edge.to_id and other_edge.to_id == edge.from_id:
                pair_edges.append(other_eid)
        if pair_edges:
            dx = sx2 - sx1
            dy = sy2 - sy1
            length = math.hypot(dx, dy) or 1
            perp_x = -dy / length
            perp_y = dx / length
            if eid < pair_edges[0]:
                bend = 0.15
            else:
                bend = -0.15
        else:
            outgoing = [eid2 for eid2, e2 in self.graph.edges.items() if e2.from_id == edge.from_id]
            count = len(outgoing)
            if count <= 1:
                bend = 0.0
            else:
                idx = outgoing.index(eid)
                bend = (idx / (count - 1)) * 0.5 - 0.25
            dx = sx2 - sx1
            dy = sy2 - sy1
            length = math.hypot(dx, dy) or 1
            perp_x = -dy / length
            perp_y = dx / length

        dist = length * 0.3
        offset_x = perp_x * bend * dist
        offset_y = perp_y * bend * dist
        ctrl_x = (sx1 + sx2) / 2 + offset_x
        ctrl_y = (sy1 + sy2) / 2 + offset_y
        result = (ctrl_x, ctrl_y, perp_x, perp_y)
        self.compute_edge_control_cache[cache_key] = result
        return result

    def update_all_positions(self):
        for nid, node in self.graph.nodes.items():
            if nid in self.canvas_ids:
                sx, sy = self.world_to_screen(node.x, node.y)
                w, h = 120, 60
                rect_id, text_id, op_text_id = self.canvas_ids[nid]
                self.canvas.coords(rect_id, sx-w/2, sy-h/2, sx+w/2, sy+h/2)
                self.canvas.coords(text_id, sx, sy-10)
                if op_text_id:
                    self.canvas.coords(op_text_id, sx, sy+15)
        for eid, edge in self.graph.edges.items():
            if eid in self.edge_ids:
                self.update_edge_position(eid)
        if len(self.compute_edge_control_cache) > 1000:
            items = list(self.compute_edge_control_cache.items())
            for key, val in items[:len(items)//2]:
                del self.compute_edge_control_cache[key]

    def update_view_transform(self, dx=0, dy=0, zoom=1.0, cx=None, cy=None):
        if cx is not None and cy is not None:
            old_scale = self.scale
            new_scale = old_scale * zoom
            world_x, world_y = self.screen_to_world(cx, cy)
            self.offset_x = cx / new_scale - world_x
            self.offset_y = cy / new_scale - world_y
            self.scale = new_scale
        else:
            self.offset_x += dx / self.scale
            self.offset_y += dy / self.scale
        self.update_all_positions()
        self.scale_var.set(f"{int(self.scale*100)}%")

    def update_node_position(self, nid):
        node = self.graph.nodes[nid]
        sx, sy = self.world_to_screen(node.x, node.y)
        w, h = 120, 60
        if nid not in self.canvas_ids:
            return
        rect_id, text_id, op_text_id = self.canvas_ids[nid]
        self.canvas.coords(rect_id, sx-w/2, sy-h/2, sx+w/2, sy+h/2)
        self.canvas.coords(text_id, sx, sy-10)
        if op_text_id:
            self.canvas.coords(op_text_id, sx, sy+15)

        for eid, edge in self.graph.edges.items():
            if edge.from_id == nid or edge.to_id == nid:
                keys_to_del = [k for k in self.compute_edge_control_cache if k[0] == eid]
                for k in keys_to_del:
                    del self.compute_edge_control_cache[k]
                self.update_edge_position(eid)

    def update_edge_position(self, eid):
        edge = self.graph.edges[eid]
        from_node = self.graph.nodes[edge.from_id]
        to_node = self.graph.nodes[edge.to_id]
        sx1, sy1 = self.world_to_screen(from_node.x, from_node.y)
        sx2, sy2 = self.world_to_screen(to_node.x, to_node.y)

        ctrl_x, ctrl_y, perp_x, perp_y = self.compute_edge_control(eid, sx1, sy1, sx2, sy2)

        if eid in self.edge_ids:
            self.canvas.coords(self.edge_ids[eid], sx1, sy1, ctrl_x, ctrl_y, sx2, sy2)

        if eid in self.edge_arrow_ids:
            t = 0.5
            mx = (sx1 + 2*ctrl_x + sx2) / 4
            my = (sy1 + 2*ctrl_y + sy2) / 4
            dx_dt = 2 * (1-t) * (ctrl_x - sx1) + 2 * t * (sx2 - ctrl_x)
            dy_dt = 2 * (1-t) * (ctrl_y - sy1) + 2 * t * (sy2 - ctrl_y)
            angle = math.atan2(dy_dt, dx_dt)
            arrow_len = 10
            arrow_angle = math.radians(22)
            tip_x = mx + math.cos(angle) * arrow_len * 0.5
            tip_y = my + math.sin(angle) * arrow_len * 0.5
            left_x = mx - math.cos(angle - arrow_angle) * arrow_len
            left_y = my - math.sin(angle - arrow_angle) * arrow_len
            right_x = mx - math.cos(angle + arrow_angle) * arrow_len
            right_y = my - math.sin(angle + arrow_angle) * arrow_len
            self.canvas.coords(self.edge_arrow_ids[eid], tip_x, tip_y, left_x, left_y, right_x, right_y)

        if eid in self.edge_label_ids:
            label_ids = self.edge_label_ids[eid]
            display_lines = []
            if edge.option_text:
                display_lines.append(edge.option_text)
            if edge.condition:
                display_lines.append(f"if {edge.condition}")
            if len(label_ids) != len(display_lines):
                self.redraw_all()
                return
            label_x = mx + perp_x * 15
            label_y = my + perp_y * 15
            line_height = 20
            total_height = (len(display_lines) - 1) * line_height
            start_y = label_y - total_height / 2
            for i, line in enumerate(display_lines):
                self.canvas.coords(label_ids[i], label_x, start_y + i*line_height)
                self.canvas.itemconfig(label_ids[i], text=line)

    def update_node_color(self, nid):
        node = self.graph.nodes[nid]
        if nid not in self.canvas_ids:
            return
        rect_id, _, _ = self.canvas_ids[nid]
        if node.is_current:
            fill = "#FFEB3B"
        elif nid == self.selected_node:
            fill = "#FF9800"
        else:
            fill = "#B3E5FC"
        outline = "#0D47A1"
        width = 2 if nid != self.selected_node else 3
        self.canvas.itemconfig(rect_id, fill=fill, outline=outline, width=width)

    def update_edge_color(self, eid):
        edge = self.graph.edges[eid]
        if eid not in self.edge_ids:
            return
        if edge.option_text:
            color = "#E65100"
        elif edge.condition:
            color = "#2E7D32"
        else:
            color = "#1565C0"
        width = 3
        if eid == self.selected_edge:
            color = "#D32F2F"
            width = 5
        self.canvas.itemconfig(self.edge_ids[eid], fill=color, width=width)
        if eid in self.edge_arrow_ids:
            self.canvas.itemconfig(self.edge_arrow_ids[eid], fill=color, outline=color)

    def update_current_highlight(self):
        for nid in self.graph.nodes:
            self.update_node_color(nid)

    def update_selection_highlight(self):
        for nid in self.graph.nodes:
            self.update_node_color(nid)
        for eid in self.graph.edges:
            self.update_edge_color(eid)
        if self.selected_node:
            self.update_node_color(self.selected_node)
        if self.selected_edge:
            self.update_edge_color(self.selected_edge)

    def invalidate_cache(self):
        self.analysis_cache = None
        self.graph_hash = None
        self.variables_cache = None

    def on_graph_structure_changed(self):
        self.invalidate_cache()
        self.redraw_all()

    def on_graph_content_changed(self, nid=None):
        self.invalidate_cache()
        if nid is not None and nid in self.canvas_ids:
            self.update_node_position(nid)
            node = self.graph.nodes[nid]
            op_text = story_ops.summarize(node)
            rect_id, text_id, op_text_id = self.canvas_ids[nid]
            if op_text_id:
                self.canvas.itemconfig(op_text_id, text=op_text)
            else:
                sx, sy = self.world_to_screen(node.x, node.y)
                op_text_id = self.canvas.create_text(sx, sy+15, text=op_text,
                                                    font=("微软雅黑", 8), fill="#666")
                self.canvas_ids[nid] = (rect_id, text_id, op_text_id)
        else:
            for eid in self.graph.edges:
                self.update_edge_position(eid)
        self.update_selection_highlight()

    def on_current_changed(self):
        self.update_current_highlight()

    # ========== 鼠标事件处理 ==========
    def on_left_down(self, event):
        # 如果空格键按下，进入平移模式
        if self.space_pressed:
            self.panning = True
            self.pan_start_x = event.x
            self.pan_start_y = event.y
            self.pan_start_offset_x = self.offset_x
            self.pan_start_offset_y = self.offset_y
            return

        nid = self.node_at(event.x, event.y)
        if nid is not None:
            self.selected_node = nid
            self.selected_edge = None
            if event.state & 0x0001:
                self.connect_from = nid
                sx, sy = self.world_to_screen(self.graph.nodes[nid].x, self.graph.nodes[nid].y)
                self.temp_line = self.canvas.create_line(sx, sy, event.x, event.y,
                                                        fill="red", width=3, arrow=tk.LAST)
            else:
                self.drag_data["node"] = nid
                self.drag_data["start_x"] = event.x
                self.drag_data["start_y"] = event.y
                self.drag_data["old_x"] = self.graph.nodes[nid].x
                self.drag_data["old_y"] = self.graph.nodes[nid].y
            self.update_selection_highlight()
            # 选中章节 → 主入口下面那行提示跟着变（用户要知道会打开哪一章）
            self._update_chapter_label(self.graph.nodes.get(nid))
            return

        eid = self.edge_at(event.x, event.y)
        if eid is not None:
            self.selected_edge = eid
            self.selected_node = None
            self.update_selection_highlight()
            self._update_chapter_label(None)
            return

        self.selected_node = None
        self.selected_edge = None
        self.drag_data["node"] = None
        self.update_selection_highlight()
        self._update_chapter_label(None)

    def on_left_drag(self, event):
        # 平移模式处理
        if self.panning:
            dx = event.x - self.pan_start_x
            dy = event.y - self.pan_start_y
            self.offset_x = self.pan_start_offset_x + dx / self.scale
            self.offset_y = self.pan_start_offset_y + dy / self.scale
            self.update_all_positions()
            self.scale_var.set(f"{int(self.scale*100)}%")
            return

        # 原有节点拖动/连线绘制逻辑
        if self.drag_data["node"] is not None:
            nid = self.drag_data["node"]
            dx = event.x - self.drag_data["start_x"]
            dy = event.y - self.drag_data["start_y"]
            node = self.graph.nodes[nid]
            node.x += dx / self.scale
            node.y += dy / self.scale
            self.drag_data["start_x"] = event.x
            self.drag_data["start_y"] = event.y
            self.update_node_position(nid)
        elif self.connect_from is not None and self.temp_line:
            sx, sy = self.world_to_screen(self.graph.nodes[self.connect_from].x,
                                         self.graph.nodes[self.connect_from].y)
            self.canvas.coords(self.temp_line, sx, sy, event.x, event.y)

    def on_left_up(self, event):
        # 结束平移模式
        if self.panning:
            self.panning = False
            return

        # 原有节点拖动结束 / 连线结束逻辑
        if self.drag_data["node"] is not None:
            nid = self.drag_data["node"]
            old_x = self.drag_data["old_x"]
            old_y = self.drag_data["old_y"]
            new_x = self.graph.nodes[nid].x
            new_y = self.graph.nodes[nid].y
            if old_x != new_x or old_y != new_y:
                cmd = MoveNodeCommand(self, nid, old_x, old_y, new_x, new_y)
                self.history.execute_command(cmd)
            self.drag_data["node"] = None
            return

        if self.connect_from is not None:
            target_nid = self.node_at(event.x, event.y)
            if target_nid and target_nid != self.connect_from:
                self.show_edge_dialog(self.connect_from, target_nid)
            if self.temp_line:
                self.canvas.delete(self.temp_line)
                self.temp_line = None
            self.connect_from = None
            self.update_selection_highlight()

    def show_edge_dialog(self, from_id, to_id):
        dlg = tk.Toplevel(self.root)
        dlg.title("选择跳转类型")
        dlg.geometry("300x220")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

        tk.Label(dlg, text="请选择跳转类型和条件类型:", pady=10).pack()
        jump_type = tk.StringVar(value="jump")
        tk.Radiobutton(dlg, text="jump", variable=jump_type, value="jump").pack()
        tk.Radiobutton(dlg, text="call", variable=jump_type, value="call").pack()
        cond_type = tk.StringVar(value="none")
        tk.Radiobutton(dlg, text="无条件", variable=cond_type, value="none").pack()
        tk.Radiobutton(dlg, text="条件分支", variable=cond_type, value="cond").pack()
        tk.Radiobutton(dlg, text="菜单选项", variable=cond_type, value="menu").pack()

        def on_ok():
            etype = jump_type.get()
            ctype = cond_type.get()
            condition = ""
            option_text = ""
            if ctype == "cond":
                condition = simpledialog.askstring("输入条件", "条件表达式（如 love>=5）:") or ""
            elif ctype == "menu":
                option_text = simpledialog.askstring("菜单选项", "选项文本:") or ""
                condition = simpledialog.askstring("条件（可选）", "条件表达式（留空为无条件）:") or ""
            cmd = AddEdgeCommand(self, from_id, to_id, etype, condition, option_text)
            self.history.execute_command(cmd)
            dlg.destroy()

        tk.Button(dlg, text="确定", command=on_ok).pack(pady=10)

    def on_right_click(self, event):
        nid = self.node_at(event.x, event.y)
        eid = self.edge_at(event.x, event.y)
        if nid is not None:
            self.selected_node = nid
            self.selected_edge = None
        elif eid is not None:
            self.selected_edge = eid
            self.selected_node = None
        else:
            self.selected_node = None
            self.selected_edge = None
        self.update_selection_highlight()

        if self.selected_node:
            node = self.graph.nodes[self.selected_node]
            has_src = bool(node.file_path and node.line_number)
            state = "normal" if has_src else "disabled"
            self.menu.entryconfig("在编辑器中打开", state=state)
            self.menu.entryconfig("用 Excel 打开此章节", state=state)
            self.menu.entryconfig("改本章台词（卡片）", state=state)
            self._update_chapter_label(node)
        else:
            self.menu.entryconfig("在编辑器中打开", state="disabled")
            self.menu.entryconfig("用 Excel 打开此章节", state="disabled")
            self.menu.entryconfig("改本章台词（卡片）", state="disabled")
            self._update_chapter_label(None)

        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_mousewheel(self, event):
        scale_factor = 1.1 if event.delta > 0 else 0.9
        self.update_view_transform(zoom=scale_factor, cx=event.x, cy=event.y)

    def on_double_click(self, event):
        nid = self.node_at(event.x, event.y)
        if nid:
            self.edit_operations_dialog(nid)
            return
        eid = self.edge_at(event.x, event.y)
        if eid:
            self.edit_edge_dialog(eid)
        else:
            wx, wy = self.screen_to_world(event.x, event.y)
            name = simpledialog.askstring("新建章节", "Label 名称:")
            if name:
                cmd = AddNodeCommand(self, name, wx, wy)
                self.history.execute_command(cmd)

    def on_delete_key(self, event):
        if self.selected_node:
            self.delete_node()
        elif self.selected_edge:
            self.delete_edge()

    def node_at(self, x, y):
        for nid, ids in self.canvas_ids.items():
            rect_id = ids[0]
            coords = self.canvas.bbox(rect_id)
            if coords and coords[0] <= x <= coords[2] and coords[1] <= y <= coords[3]:
                return nid
        return None

    def edge_at(self, x, y):
        items = self.canvas.find_overlapping(x-3, y-3, x+3, y+3)
        for eid, line_id in self.edge_ids.items():
            if line_id in items:
                return eid
        return None

    def edit_edge_dialog(self, eid):
        edge = self.graph.edges[eid]
        dlg = tk.Toplevel(self.root)
        dlg.title("编辑连线")
        dlg.geometry("600x400")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

        tk.Label(dlg, text="跳转类型:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        jump_type = tk.StringVar(value=edge.type)
        tk.Radiobutton(dlg, text="jump", variable=jump_type, value="jump").grid(row=0, column=1, sticky=tk.W)
        tk.Radiobutton(dlg, text="call", variable=jump_type, value="call").grid(row=0, column=2, sticky=tk.W)

        tk.Label(dlg, text="菜单选项文本（留空表示条件分支）:").grid(row=1, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        option_entry = tk.Entry(dlg, width=50)
        option_entry.grid(row=2, column=0, columnspan=3, padx=5, pady=5)
        option_entry.insert(0, edge.option_text)

        tk.Label(dlg, text="条件表达式（如 love>=5，留空为无条件）:").grid(row=3, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)
        cond_entry = tk.Entry(dlg, width=50)
        cond_entry.grid(row=4, column=0, columnspan=3, padx=5, pady=5)
        cond_entry.insert(0, edge.condition)

        def on_ok():
            new_type = jump_type.get()
            new_option = option_entry.get().strip()
            new_cond = cond_entry.get().strip()
            cmd = UpdateEdgeCommand(self, eid, etype=new_type, condition=new_cond, option_text=new_option)
            self.history.execute_command(cmd)
            dlg.destroy()

        tk.Button(dlg, text="确定", command=on_ok).grid(row=5, column=1, pady=20)

    def edit_operations_dialog(self, nid):
        node = self.graph.nodes[nid]
        dlg = tk.Toplevel(self.root)
        dlg.title(f"编辑变量操作 - {node.label_name}")
        dlg.geometry("700x500")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")

        help_text = """每行一个操作，支持复杂表达式：
x = 10
y += 5
score = love + intelligence * 2
result = abs(value) + min(a, b)
is_unlocked = love > 5 and money >= 100"""
        tk.Label(dlg, text=help_text, justify=tk.LEFT, font=("Consolas", 9)).pack(pady=5)

        text_widget = st.ScrolledText(dlg, width=80, height=20, font=("Consolas", 10))
        text_widget.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        # 默认只列剧情变量操作；技术代码（引擎 API / 重复的 renpy.transition）
        # 收进下方折叠区，避免"55 条操作"其实大半是噪音。
        _nar = story_ops.narrative_operations(node)
        for op in _nar:
            text_widget.insert(tk.END, op + '\n')
        text_widget.focus_set()

        def on_ok():
            lines = text_widget.get("1.0", tk.END).splitlines()
            new_ops = [l.strip() for l in lines if l.strip()]
            # 面板只显示剧情变量；技术代码必须原样保留回去，
            # 否则"编辑一次变量"就把 renpy.transition/persistent 全删了。
            new_ops = new_ops + story_ops.tech_operations(node)
            cmd = UpdateNodeOperationsCommand(self, nid, new_ops)
            self.history.execute_command(cmd)
            dlg.destroy()

        def on_test():
            content = text_widget.get("1.0", tk.END)
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            test_state = VariableState(["test"])
            errors = []
            for i, line in enumerate(lines, 1):
                try:
                    test_state = apply_operation(test_state, line)
                except Exception as e:
                    errors.append(f"第{i}行错误: {line} - {str(e)}")
            if errors:
                messagebox.showwarning("语法检查", "发现错误:\n" + "\n".join(errors))
            else:
                messagebox.showinfo("语法检查", "所有操作语法正确！")

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="语法检查", command=on_test).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="确定", command=on_ok).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消", command=dlg.destroy).pack(side=tk.LEFT, padx=5)

    def add_node(self):
        wx, wy = self.screen_to_world(200, 200)
        name = simpledialog.askstring("新建章节", "Label 名称:")
        if name:
            cmd = AddNodeCommand(self, name, wx, wy)
            self.history.execute_command(cmd)

    def set_current(self):
        if self.selected_node:
            cmd = SetCurrentNodeCommand(self, self.selected_node)
            self.history.execute_command(cmd)
        else:
            messagebox.showinfo("提示", "请先选中一个节点")

    def edit_operations(self):
        if self.selected_node:
            self.edit_operations_dialog(self.selected_node)
        else:
            messagebox.showinfo("提示", "请先选中一个节点")

    def delete_node(self):
        if self.selected_node:
            if messagebox.askyesno("删除", "确定删除该章节及相关连线吗？"):
                cmd = RemoveNodeCommand(self, self.selected_node)
                self.history.execute_command(cmd)
                self.selected_node = None
        else:
            messagebox.showinfo("提示", "请先选中一个节点")

    def delete_edge(self):
        if self.selected_edge:
            if messagebox.askyesno("删除", "确定删除此连线吗？"):
                cmd = RemoveEdgeCommand(self, self.selected_edge)
                self.history.execute_command(cmd)
                self.selected_edge = None
        else:
            messagebox.showinfo("提示", "请先选中一个节点")

    def clear_graph(self):
        if messagebox.askyesno("清空图", "将清除所有节点和连线，是否继续？"):
            cmd = ClearGraphCommand(self)
            self.history.execute_command(cmd)
            self.analysis_text.delete(1.0, tk.END)

    def open_label_in_editor(self):
        if not self.selected_node:
            return
        node = self.graph.nodes[self.selected_node]
        if node.file_path and node.line_number:
            open_file_at_line(node.file_path, node.line_number)
        else:
            messagebox.showinfo("提示", "该节点未关联源文件，请先导入或手动设置文件路径。")

    def auto_layout(self):
        if not self.graph.nodes:
            return
        if self.busy_count > 0:
            messagebox.showinfo("提示", "已有任务正在进行，请稍候...")
            return
        with self.lock:
            self.busy_count += 1
        graph_copy = self.graph.copy()
        start_nid, _reason = pick_entry_node(graph_copy)
        def worker():
            try:
                layout_graph(graph_copy, start_id=start_nid)
                self.result_queue.put(("layout", graph_copy))
            except Exception as e:
                self.result_queue.put(("error", str(e)))
            finally:
                self.result_queue.put(("busy_end",))
        threading.Thread(target=worker, daemon=True).start()

    def analyze_and_warn(self):
        if not self.graph.nodes:
            messagebox.showinfo("分析中止", "图中无节点。")
            return
        if self.busy_count > 0:
            messagebox.showinfo("提示", "已有任务正在进行，请稍候...")
            return
        with self.lock:
            self.busy_count += 1
        graph_copy = self.graph.copy()
        def worker():
            try:
                result = analyze_reachability_full(graph_copy)
                entry_states = result['states']
                warnings = result['warnings']
                result_text = visualize_analysis_results(
                    graph_copy, entry_states, warnings,
                    entry_id=result.get('entry_id'),
                    independent=result.get('independent'),
                )
                self.result_queue.put(("analysis", result_text, warnings))
            except Exception as e:
                self.result_queue.put(("error", str(e)))
            finally:
                self.result_queue.put(("busy_end",))
        threading.Thread(target=worker, daemon=True).start()

    def show_detailed_analysis(self):
        if not self.graph.nodes:
            return
        if self.busy_count > 0:
            messagebox.showinfo("提示", "已有任务正在进行，请稍候...")
            return
        with self.lock:
            self.busy_count += 1
        graph_copy = self.graph.copy()
        def worker():
            try:
                result = analyze_reachability_full(graph_copy)
                report = self._build_detailed_report(
                    graph_copy, result['states'], result['warnings'],
                    result=result)
                self.result_queue.put(("detailed_report", report))
            except Exception as e:
                self.result_queue.put(("error", str(e)))
            finally:
                self.result_queue.put(("busy_end",))
        threading.Thread(target=worker, daemon=True).start()

    def _build_detailed_report(self, graph, entry_states, warnings,
                               result=None):
        result = result or {}
        entry_id = result.get('entry_id')
        independent_ids = set(result.get('independent') or ())
        report = "=" * 80 + "\n"
        report += "详细变量区间分析报告\n"
        if result.get('entry_name'):
            report += f"分析入口: {result['entry_name']}\n"
        report += "=" * 80 + "\n\n"
        all_vars = collect_all_variables(graph)
        report += f"检测到的变量 ({len(all_vars)}个): {', '.join(sorted(all_vars))}\n\n"
        for nid, node in graph.nodes.items():
            report += f"节点: {node.label_name}\n"
            report += "-" * 40 + "\n"
            state = entry_states.get(nid)
            if state and not state.is_bottom():
                if nid == entry_id:
                    report += "入口: 是（本次分析起点）\n"
                report += f"入口状态:\n{state}\n\n"
                current = state.copy()
                if node.operations:
                    report += "操作序列:\n"
                    for op in node.operations:
                        report += f"  $ {op}\n"
                        current = apply_operation(current, op)
                    report += f"\n出口状态:\n{current}\n"
                out_edges = [e for e in graph.edges.values() if e.from_id == nid]
                if out_edges:
                    report += "\n出边分析:\n"
                    for edge in out_edges:
                        target = graph.nodes[edge.to_id].label_name
                        cond = edge.condition if edge.condition else "无条件"
                        opt = f'"{edge.option_text}" ' if edge.option_text else ""
                        report += f"  → {target} ({opt}{cond})\n"
                        possible = condition_possible(current, edge.condition)
                        report += f"    条件可能: {'是' if possible else '否'}\n"
            elif nid in independent_ids:
                report += "状态: 独立入口（无入边，不参与主线可达性分析）\n"
            else:
                report += "状态: 不可达\n"
            report += "\n"
            if warnings:
                report += "=" * 80 + "\n警告\n" + "=" * 80 + "\n"
                for w in warnings:
                    report += f"• {w}\n"
        return report

    # ===================== 资产引擎接线 =====================

    @staticmethod
    def _scan_script_files(game_dir: Path, tl_dir: Path) -> list:
        """
        递归扫描 game_dir 下的 .rpy，排除 tl_dir 子树、隐藏目录与常见缓存目录。
        返回去重、排序、resolve 后的绝对路径列表。
        """
        if not game_dir.exists():
            return []

        tl_resolved = tl_dir.resolve()
        skip_dirs = {'__pycache__', 'node_modules', '.git', '.svn', '.hg'}
        found: dict = {}

        for dirpath, dirnames, filenames in os.walk(str(game_dir)):
            current = Path(dirpath)

            # 剪枝 1：进入 tl 子树立刻停止向下
            if is_under_path(current, tl_resolved):
                dirnames[:] = []
                continue

            # 剪枝 2：跳过隐藏目录和缓存目录
            dirnames[:] = [
                d for d in dirnames
                if d not in skip_dirs and not d.startswith('.')
            ]

            for fn in filenames:
                if not fn.endswith('.rpy'):
                    continue
                fp = (current / fn).resolve()

                # 双保险：即便前面剪枝漏了（比如符号链接），这里也拦一次
                if is_under_path(fp, tl_resolved):
                    continue

                found[str(fp)] = fp

        return sorted(found.values(), key=lambda p: str(p).lower())

    def open_project(self):
        project_root = filedialog.askdirectory(
            title="选择游戏项目根目录（包含 game 文件夹）"
        )
        if not project_root:
            return
        project_root = Path(project_root).resolve()

        game_dir = project_root / 'game'
        if not game_dir.exists():
            if not messagebox.askyesno(
                "确认", "所选目录下没有 'game' 文件夹，是否仍要继续？"
            ):
                return

        asset_dir = game_dir / 'asset_tables'
        tl_dir = (game_dir / 'tl').resolve()

        # 一次扫描，tl/ 已排除
        script_files = self._scan_script_files(game_dir, tl_dir)

        # 若已有引擎在跑，先停掉
        self._stop_asset_engine()

        self.project_root = project_root
        self.asset_bus = AssetEventBus()
        self.asset_bus.subscribe(None, self._on_asset_event)

        gate = ConfirmationGate(
            ask_callback=self._ask_asset_confirmation,
            rename_threshold=5,
        )

        # 建引擎 + 首次全量加载（扫描资产表、解析所有 .rpy）是重活，
        # 放到后台线程，UI 只显示"正在加载"。
        def work():
            engine = AssetEngine(
                asset_dir=asset_dir,
                project_root=project_root,
                script_files=script_files,
                tl_dir=tl_dir,
                event_bus=self.asset_bus,
                confirmation_gate=gate,
                watch_source_dirs=True,
                poll_interval=1.5,
            )
            engine.start()
            return engine

        def done(engine):
            self.asset_engine = engine
            self.project_label.config(text=f"项目：{project_root.name}",
                                      fg="#2E7D32")
            self._poll_asset_engine()
            reg = self._current_registry()
            if reg is not None:
                self._append_analysis(
                    f"[项目] 已加载：脚本 {len(script_files)} 个 · "
                    f"图片 {len(reg.images)} · 音乐 {len(reg.music)} · "
                    f"音效 {len(reg.sounds)} · 语音 {len(reg.voices)} · "
                    f"变换 {len(reg.transforms)} · 角色 {len(reg.characters)}\n")

        def failed(err):
            self.asset_engine = None
            messagebox.showerror("引擎启动失败", str(err))

        self._run_async(f"正在加载项目 {project_root.name}（扫描资产表与脚本）",
                        work, done, failed)

    def rescan_project(self):
        """手动触发一次全量重载。"""
        if self.asset_engine is not None:
            self.asset_engine.reload_all()

    # ===================== 剧本表（Excel 双向） =====================

    def _current_registry(self):
        """当前项目的资产注册表；没有打开项目时为 None。"""
        if self.asset_engine is None:
            return None
        try:
            return self.asset_engine.get_registry()
        except Exception:
            return None

    def _generate_and_open_table(self, script_path):
        """
        把一个 .rpy 解析成演出表（同名 .xlsx）并用系统默认程序打开。

        Excel 打开路径与"在编辑器中打开"共用同一份选节点逻辑，区别只是
        一个给程序员看源码、一个给文案看演出表。
        """
        script_path = Path(script_path)
        if not script_path.exists():
            messagebox.showwarning("找不到脚本", f"文件不存在：\n{script_path}")
            return

        table_path = script_path.with_suffix('.xlsx')
        try:
            from script_table import open_rpy_as_table
            registry = self._current_registry()
            open_rpy_as_table(script_path, table_path, registry=registry,
                              with_thumbnails=True)
        except Exception as e:
            messagebox.showerror("生成剧本表失败", str(e))
            return

        if not _open_with_system_default(table_path):
            messagebox.showinfo(
                "已生成剧本表",
                f"未能自动调用 Excel，请手动打开：\n{table_path}")

        self._append_analysis(
            f"[剧本表] 已生成 {table_path.name}\n"
            f"（改完保存后，用「从剧本表生成 .rpy」写回脚本）\n"
        )

    def open_node_rpy_in_excel(self):
        """
        右键章节节点 → 用它对应的 .rpy 生成演出表并用 Excel 打开。

        这是"用表格编演出"的入口：表里一行一句台词，立绘/背景/音乐/
        变换都是下拉框，候选直接来自资产表。
        """
        if not self.selected_node:
            messagebox.showinfo("提示", "请先选中一个章节节点")
            return
        node = self.graph.nodes[self.selected_node]
        if not node.file_path:
            messagebox.showinfo(
                "提示", "该节点未关联源文件，请先导入或手动设置文件路径。")
            return
        self._generate_and_open_table(node.file_path)

    # 角色表的列顺序：脚本解析出来的字段全部落表，导入完能一眼看清
    # 这个角色是怎么定义的（显示名/颜色/立绘标签/语音/前后缀…）。
    CHARACTER_TABLE_FIELDS = [
        'ID', 'Name', 'Color', 'WhatColor', 'ImageTag', 'Kind', 'VoiceTag',
        'SideImage', 'WhatPrefix', 'WhatSuffix', 'WhoPrefix', 'WhoSuffix',
        'DefaultTransform', 'Notes',
    ]

    def import_characters_from_script(self):
        """
        把脚本里 `define xxx = Character(...)` 解析出的角色写进 characters.xlsx。

        以前角色只"悄悄"合并进内存注册表，用户看不到导入了什么；这个动作
        把结果显式落表并打开，缺哪个字段一目了然。
        """
        if self.asset_engine is None:
            messagebox.showwarning("未打开项目", "请先「打开项目」。")
            return
        registry = self._current_registry()
        if registry is None or not registry.characters:
            messagebox.showinfo("没有角色", "脚本里没解析到 Character 定义。")
            return

        asset_dir = getattr(self.asset_engine, 'asset_dir', None)
        if asset_dir is None:
            messagebox.showwarning("未知资源表目录", "该项目没有资产表目录。")
            return

        from asset_registry import write_dict_to_excel
        target = Path(asset_dir) / 'characters.xlsx'
        data = {}
        for cid, row in registry.characters.items():
            data[cid] = {k: row.get(k, '') for k in self.CHARACTER_TABLE_FIELDS}
        try:
            write_dict_to_excel(target, data,
                                list(self.CHARACTER_TABLE_FIELDS))
        except Exception as e:
            messagebox.showerror("写角色表失败", str(e))
            return

        self._append_analysis(
            f"[角色] 已把脚本中的 {len(data)} 个角色写入 {target.name}\n")
        messagebox.showinfo(
            "导入完成",
            f"已写入 {len(data)} 个角色：\n{target}\n\n"
            f"列：{' / '.join(self.CHARACTER_TABLE_FIELDS)}")
        _open_with_system_default(target)

    def open_validation_panel(self):
        """
        项目体检：L1 单条 / L2 表内 / L3 全库 三级校验，集中一处看。

        用 build.collect_report 而不是 build_project —— 体检不该往工作区
        写产物；这也保证"面板看到的结论"和"CI 构建时的结论"是同一套规则。
        """
        if self.project_root is None:
            messagebox.showwarning("未打开项目", "请先「打开项目」。")
            return
        asset_dir = getattr(self.asset_engine, 'asset_dir', None)

        def work():
            from build import collect_report
            return collect_report(self.project_root, asset_dir=asset_dir)

        self._run_async("项目体检（三级校验）", work, self._show_validation_panel)

    def _show_validation_panel(self, collected):
        report = collected['report']
        stats = {
            'scripts': len(collected['scripts']),
            'lines': len(collected['beats']),
        }
        dlg = tk.Toplevel(self.root)
        dlg.title(f"项目体检 · {report.summary()}")
        dlg.geometry("900x600")
        body = tk.Frame(dlg)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        tk.Label(body,
                 text=(f"项目：{Path(self.project_root).name} · "
                       f"章节脚本 {stats['scripts']} 个 · "
                       f"演出 {stats['lines']} 行"),
                 font=("微软雅黑", 9, "bold")).pack(anchor='w')
        text = st.ScrolledText(body, font=("Consolas", 9))
        text.pack(fill=tk.BOTH, expand=True, pady=6)
        text.insert(tk.END, report.format(max_per_level=80))

        def save_report():
            from tkinter import filedialog as fd
            p = fd.asksaveasfilename(title="保存体检报告",
                                     defaultextension=".json",
                                     filetypes=[("JSON", "*.json")])
            if not p:
                return
            Path(p).write_text(report.to_json(), encoding='utf-8')
            messagebox.showinfo("已保存", p)

        bar = tk.Frame(dlg)
        bar.pack(fill=tk.X, padx=8, pady=(0, 8))
        tk.Button(bar, text="保存报告 JSON", command=save_report
                  ).pack(side='left')
        tk.Button(bar, text="关闭", command=dlg.destroy).pack(side='right')

        self._append_analysis(
            f"[体检] {report.summary()}\n")

    def _run_async(self, desc: str, work, on_done=None, on_error=None):
        """
        在后台线程跑重活，主线程收结果。

        为什么必须有：打开项目要扫几千个文件、解析几十个 .rpy；体检要
        跑全库分析。这些都放在 UI 线程里做，窗口就是"未响应"——
        用户第一次打开项目的体验直接毁掉（真实反馈）。tkinter 不允许
        子线程碰控件，所以统一走"后台干活 + 队列回主线程"这条路。

        work: 无参函数，返回值交给 on_done(result)
        """
        self._set_busy(desc)

        def runner():
            try:
                result = work()
                self.result_queue.put(("job_done", on_done, result))
            except Exception as e:                     # noqa: BLE001
                self.result_queue.put(("job_failed", on_error, desc, e))

        threading.Thread(target=runner, daemon=True).start()

    def _set_busy(self, desc: str = ''):
        """状态提示 + 禁掉会重入的按钮（避免连点触发两遍重活）。"""
        self._busy_desc = desc or ''
        if desc:
            self._append_analysis(f'[进行中] {desc}…\n')
        try:
            self.root.config(cursor='watch' if desc else '')
        except Exception:
            pass
        for name in ('btn_open_project', 'btn_health', 'btn_chars'):
            btn = getattr(self, name, None)
            if btn is None:
                continue
            try:
                btn.config(state='disabled' if desc else 'normal')
            except Exception:
                pass

    def _finish_job(self, desc: str = ''):
        self._set_busy('')
        if desc:
            self._append_analysis(f'[完成] {desc}\n')

    def open_rpy_in_excel(self):
        """
        选一个 .rpy，解析成剧本表并用 Excel 打开（文件选择器版本）。

        正常流程走节点右键；这里作为"没有图/没有节点"时的兜底入口。
        """
        script_path = filedialog.askopenfilename(
            title="选择要用 Excel 打开的 .rpy 文件",
            filetypes=[("Ren'Py 脚本", "*.rpy"), ("所有文件", "*.*")],
        )
        if not script_path:
            return
        self._generate_and_open_table(script_path)

    def export_rpy_from_table(self):
        """
        把剧本表写回 .rpy。

        表格优先：如果当前 .rpy 旁边有同名 .xlsx，直接选它；
        否则让用户手动挑一张表。
        """
        initial = None
        if self.project_root is not None:
            initial = str(self.project_root)

        table_path = filedialog.askopenfilename(
            title="选择剧本表（.xlsx）",
            initialdir=initial,
            filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")],
        )
        if not table_path:
            return

        table_path = Path(table_path)
        try:
            beats = import_beats_from_excel(table_path)
        except Exception as e:
            messagebox.showerror("读取剧本表失败", str(e))
            return

        if not beats:
            messagebox.showwarning("剧本表为空", "没有读到任何演出行。")
            return

        # M4：检测演出表里引用、但资源表未登记的资产，提示文案去登记
        registry = self._current_registry()
        unregistered = []
        if registry is not None:
            try:
                from script_table import find_unregistered_assets
                unregistered = find_unregistered_assets(beats, registry)
            except Exception:
                unregistered = []
        if unregistered:
            self._show_unregistered_dialog(unregistered)

        out_path = planned_output_path(
            table_path, project_root=self.project_root)
        text = render_beats_to_rpy(beats, label_name=None, registry=registry)

        # 生成安全门禁（在 script_table 里跑，UI 只摆结果）。
        # 这一道闸是为一次真实事故加的：产物曾直接写进 game/chapter/，
        # 与原文抢同一个 label，还带了 18 行中文图像 tag。
        # 顺序刻意是"先评、再问、最后写"——不在用户点头前落任何盘。
        occupied = occupied_labels(project_root=self.project_root,
                                   exclude={out_path, table_path})
        issues = evaluate_generation(text, out_path,
                                     project_root=self.project_root,
                                     occupied=occupied)
        blocking = [i for i in issues if i.is_blocking]
        warnings = [i for i in issues if not i.is_blocking]

        if blocking:
            # 拒写，但**不把产物丢掉**：另存一份 .blocked.rpy 供对比，
            # 否则"拒写"会让人连问题长什么样都看不到（信任 > 方便）。
            review = None
            try:
                review = out_path.with_suffix('.blocked.rpy')
                review.parent.mkdir(parents=True, exist_ok=True)
                review.write_text(text, encoding='utf-8')
            except OSError:
                review = None

            messagebox.showerror(
                '拒绝导出：生成的脚本会让游戏跑不起来',
                '发现 %d 条致命问题，**没有写入目标文件**'
                '（原文与演出表都没动）。\n\n%s\n\n%s'
                % (len(blocking),
                   '\n'.join('· ' + i.format() for i in blocking[:12]),
                   f'产物已另存一份供对比：\n{review}' if review
                   else '（产物副本也没能写出，请检查目录权限）'))
            self._append_analysis(
                f'[剧本表] 导出被拦下：{len(blocking)} 条致命问题'
                + (f'，产物存为 {review.name}' if review else '') + '\n')
            return

        # 确认框必须把"写到哪 / 门禁怎么看"说清楚。以前这里只写一句
        # "将生成 N 个演出单元到 xxx.generated.rpy"，没有任何警告——
        # 产物和原文抢 label 就是这样被点掉的。
        tip = f'将生成 {len(beats)} 个演出单元到：\n{out_path}\n'
        if warnings:
            tip += '\n提醒（不阻止导出）：\n' + '\n'.join(
                '· ' + i.format() for i in warnings[:6]) + '\n'
        else:
            tip += '\n生成安全门禁：无问题。\n'
        tip += '\n继续？'
        if not messagebox.askyesno('确认导出', tip):
            return

        art = write_generation(text, out_path)
        if not art.written:
            messagebox.showerror(
                '导出失败', f'写不出去：\n{art.out_path}\n（检查文件是否被占用）')
            return

        self._append_analysis(
            f"[剧本表] 已生成脚本 {out_path.name}（{len(beats)} 个演出单元）\n")
        messagebox.showinfo("导出完成", f"已写入：\n{out_path}")

    def refresh_table_candidates(self):
        """
        刷新一张已导出演出表的候选下拉，不动 Beats 里的任何用户数据。

        资源表新增了资产 → 演出表下拉看不见，点这里即可让下拉跟上，
        不必重新导出 .rpy、丢掉文案改好的台词/立绘。
        """
        initial = str(self.project_root) if self.project_root else None
        table_path = filedialog.askopenfilename(
            title="选择要刷新候选的演出表（.xlsx）",
            initialdir=initial,
            filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")],
        )
        if not table_path:
            return

        registry = self._current_registry()
        if registry is None:
            messagebox.showwarning(
                "未打开项目",
                "请先打开项目以加载资源表，否则没有候选可刷新。")
            return

        try:
            from script_table import refresh_candidate_sheets
            refresh_candidate_sheets(table_path, registry)
        except Exception as e:
            messagebox.showerror("刷新候选失败", str(e))
            return

        self._append_analysis(
            f"[剧本表] 已刷新 {Path(table_path).name} 的候选下拉\n")
        messagebox.showinfo(
            "刷新完成",
            f"已用当前资源表更新候选下拉：\n{table_path}")

    def _selected_chapter_script(self):
        """
        画布上当前选中的章节对应哪个 .rpy（找不到返回 None）。

        这是所有"针对本章"的按钮共用的定位逻辑：
        选中章节 → 知道文件路径 → 打开卡片 / 导出 Excel / 导入 Excel。
        找不到就直接返回 None，由调用方给出可操作的提示。
        """
        node_id = self.selected_node
        if not node_id:
            return None
        node = self.graph.nodes.get(node_id)
        if node is None or not getattr(node, 'file_path', None):
            return None
        path = Path(node.file_path)
        return path if path.exists() else None

    def _update_chapter_label(self, node=None):
        """
        更新主入口下面那行"当前编哪一章"的提示。

        为什么需要：用户反馈"不知道如何打开卡片入口"。
        入口不只要求**看得见**，还要让人在点之前就知道**会打开哪一章**。
        """
        label = getattr(self, 'chapter_label', None)
        # 控件在 setup_ui 里才创建；早于它被调用（或测试里的桩）要安全退出
        if label is None or not hasattr(label, 'config'):
            return
        path = None
        if node is not None and getattr(node, 'file_path', None):
            path = Path(node.file_path)
        if path and path.exists():
            name = getattr(node, 'name', '') or path.stem
            label.config(text=f'当前章节：{name}（{path.name}）')
        elif node is not None:
            label.config(text='该章节没有关联源文件——用「导入章节」先导进来')
        else:
            label.config(text='（先在画布上点一个章节，再点上面的按钮）')
    def export_table_for_chapter(self):
        """
        把当前选中的章节导出成 Excel 表（交换面）。

        **这个方法是补的**：侧栏写着「导出本章为 Excel 表」但原来没有对应
        实现——用户点下去什么也不会发生。现在它复用"用 Excel 打开此章节"
        那条已经被验证过的路径（生成表 + 交给系统默认程序打开）。
        """
        script_path = self._selected_chapter_script()
        if script_path is None:
            messagebox.showinfo(
                "先选一章",
                "请先在画布上点选一个章节节点（右键 → 用 Excel 打开此章节\n"
                "也可以），再点这里导出成 Excel 表。")
            return
        self._generate_and_open_table(script_path)

    def open_script_table_editor(self):
        """
        主入口：在应用内编辑本章剧本（4 项卡片）。

        **自动载入当前选中的章节**——以前这里打开的是**空窗口**，
        还要用户自己找到底部的「载入 .rpy」，等于没有入口（用户反馈
        "我不知道如何打开你说的卡片入口"）。没选章节时才退回让用户选文件。
        """
        try:
            from beat_editor import ScriptTableEditorWindow
            from asset_registry import add_asset_to_registry
        except Exception as e:
            messagebox.showerror("无法打开编辑器", str(e))
            return

        script_path = self._selected_chapter_script()
        if script_path is None:
            # 没选中章节 → 让用户挑一个文件，而不是开一个空窗口
            picked = filedialog.askopenfilename(
                title="选择要编辑的章节脚本（.rpy）",
                initialdir=str(self.project_root) if self.project_root else None,
                filetypes=[("Ren'Py 脚本", "*.rpy"), ("所有文件", "*.*")])
            if not picked:
                return
            script_path = Path(picked)

        registry = self._current_registry()
        asset_dir = (getattr(self.asset_engine, 'asset_dir', None)
                     if self.asset_engine else None)

        def on_new_asset(asset_type, asset_id, path):
            if registry is None:
                return
            add_asset_to_registry(registry, asset_type, asset_id, path,
                                 asset_dir)
            self._append_analysis(
                f"[剧本表] 已新建资产 {asset_type}/{asset_id}"
                f"{('（' + path + '）') if path else ''}\n")

        ScriptTableEditorWindow(
            self.root, self.asset_engine, script_path=script_path,
            asset_dir=asset_dir, on_new_asset=on_new_asset)
        self._append_analysis(
            f'[剧本] 已打开 {script_path.name}（卡片式编辑）\n')

    _ASSET_TABLE_FILE = {
        'character': 'characters.xlsx',
        'image': 'images.xlsx',
        'transform': 'transforms.xlsx',
        'music': 'music.xlsx',
        'sound': 'sounds.xlsx',
        'voice': 'voices.xlsx',
    }

    def _show_unregistered_dialog(self, unregistered):
        """
        M4：演出表引用了资源表里没有的资产 → 弹窗提示去登记。

        每个出现的资产类型给一个"打开资源表"按钮（直接定位到对应的
        .xlsx），另给"忽略"放行。导出的脚本仍可继续生成，但这里先把
        断裂引用的风险摆在文案眼前。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("待登记资产")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text="以下资产在演出表中被引用，但资源表里没有：",
            font=("微软雅黑", 10, "bold"),
        ).pack(padx=12, pady=8, anchor="w")

        list_frame = tk.Frame(dlg)
        list_frame.pack(padx=12, fill=tk.X)
        shown = set()
        for atype, aid in unregistered:
            if (atype, aid) in shown:
                continue
            shown.add((atype, aid))
            tk.Label(list_frame, text=f"  · [{atype}] {aid}",
                     anchor="w").pack(fill=tk.X)

        tk.Label(
            dlg,
            text="请在资源表登记后再导出，否则脚本会出现断裂引用。",
            fg="#a00",
        ).pack(padx=12, pady=6, anchor="w")

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(padx=12, pady=8, fill=tk.X)
        asset_dir = (getattr(self.asset_engine, 'asset_dir', None)
                     if self.asset_engine else None)

        def open_table(fname):
            if asset_dir is None:
                return
            p = Path(asset_dir) / fname
            if p.exists():
                _open_with_system_default(p)
            else:
                messagebox.showinfo("资源表不存在", f"未找到：{p}")

        opened = set()
        for atype, _ in unregistered:
            if atype in opened:
                continue
            opened.add(atype)
            fname = self._ASSET_TABLE_FILE.get(atype)
            if not fname:
                continue
            tk.Button(btn_frame, text=f"打开 {atype} 表",
                      command=lambda f=fname: open_table(f)
                      ).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="忽略", command=dlg.destroy
                  ).pack(side=tk.RIGHT, padx=4)
        dlg.wait_window()

    def _stop_asset_engine(self):
        """停止当前引擎并释放轮询句柄。"""
        if self._asset_poll_handle is not None:
            try:
                self.root.after_cancel(self._asset_poll_handle)
            except Exception:
                pass
            self._asset_poll_handle = None
        if self.asset_engine is not None:
            try:
                self.asset_engine.stop()
            except Exception:
                pass
            self.asset_engine = None

    def _poll_asset_engine(self):
        """主线程轮询：消费引擎事件 + 重新调度。"""
        if self.asset_engine is None:
            return
        try:
            self.asset_engine.poll()
        except Exception as e:
            # 单次异常不终止轮询
            print(f"[AssetEngine] poll 异常: {e}")
        # 200ms 一轮，足够快也不会占 CPU
        self._asset_poll_handle = self.root.after(200, self._poll_asset_engine)

    def _on_asset_event(self, event):
        """
        事件总线订阅者。所有事件都在主线程被调用。
        只做轻量响应：更新面板文字、标记下拉脏。
        """
        et = event.event_type

        # 资产集合发生变化 → 标记下拉脏，下次打开时刷新
        if et in (
            AssetEventType.TABLE_RELOADED,
            AssetEventType.ASSET_ADDED,
            AssetEventType.ASSET_REMOVED,
            AssetEventType.ASSET_RENAMED,
            AssetEventType.ASSET_MOVED,
            AssetEventType.ASSET_MODIFIED,
            AssetEventType.SCRIPT_REFERENCES_REBUILT,
        ):
            # 若你有资产下拉缓存，置 dirty 即可
            # 目前无下拉，先留占位
            pass

        # 显式警告类事件 → 输出到分析面板
        elif et == AssetEventType.BROKEN_REFERENCES_FOUND:
            count = event.payload.get('count', 0)
            self._append_analysis(
                f"[资产] 断裂引用 {count} 处\n"
            )

        elif et == AssetEventType.UNUSED_ASSETS_FOUND:
            count = event.payload.get('count', 0)
            self._append_analysis(
                f"[资产] 未使用资产 {count} 个\n"
            )

        elif et == AssetEventType.ENGINE_NOTICE:
            info = event.payload.get('info', '')
            if info in ('undefined_characters', 'unused_characters'):
                count = event.payload.get('count', 0)
                label = '未定义角色' if info == 'undefined_characters' else '未使用角色'
                self._append_analysis(f"[脚本] {label} {count} 个\n")
            elif info == 'auto_scan_generated':
                msg = event.payload.get('message', '')
                self._append_analysis(f"[资产] {msg}\n")
            # 其余（rename_rejected / delete_rejected / source_changed）静默或日志

        elif et == AssetEventType.ENGINE_ERROR:
            err = event.payload.get('error', '未知错误')
            phase = event.payload.get('phase', '')
            self._append_analysis(f"[引擎错误:{phase}] {err}\n")

        elif et == AssetEventType.ENGINE_READY:
            p = event.payload
            self._append_analysis(
                f"[引擎就绪] 图片{p.get('image_count',0)} "
                f"音乐{p.get('music_count',0)} "
                f"音效{p.get('sound_count',0)} "
                f"角色{p.get('character_count',0)}\n"
            )

    def _append_analysis(self, text: str):
        """向分析面板追加一行文字（保留原内容）。"""
        self.analysis_text.insert(tk.END, text)
        self.analysis_text.see(tk.END)

    def _ask_asset_confirmation(self, request) -> bool:
        """ConfirmationGate 的回调：弹窗询问用户。"""
        # request.reason / request.summary / request.details
        summary = getattr(request, 'summary', '需要确认')
        details = getattr(request, 'details', {}) or {}
        ref_count = details.get('ref_count', 0)

        body = summary
        if ref_count:
            body += f"\n\n影响引用数：{ref_count}"

        return messagebox.askyesno("资产变更确认", body)

    def _on_window_close(self):
        self._stop_asset_engine()
        self.root.destroy()

    def get_asset_completion(self, prefix, asset_types=None, limit=20):
        """编辑框自动补全用。"""
        if self.asset_engine is None:
            return []
        from asset_completion import AssetCompletion
        return AssetCompletion(self.asset_engine).complete(
            prefix, asset_types=asset_types, limit=limit
        )

    def import_renpy(self):
        """
        导入章节脚本（**可多选**）。

        用户 2026-09-28 明确反馈：「批量导入其实可以删掉，本来直接写
        导入 Renpy 文件就包括了批量导入」。确实如此——`filedialog` 本来就
        支持多选，多文件导入是**同一个动作**，没有理由拆成两个按钮。
        所以这里直接走多文件路径（后台线程 + 整图可 Ctrl+Z 撤销）。
        """
        self.batch_import_chapters()

    def batch_import_chapters(self):
        """
        一次选多个 .rpy，全部解析进同一张图 → 每个 label 一个章节节点。

        以前这里只挂了「导入Ren'Py文件」单个文件；整部剧本（40 个 .rpy）
        要重复点 40 次，而且中途一次 `clear_graph=True` 就会把前面导入的
        全部冲掉。这里改成一次性累加导入：

          · `batch_import_chapters(..., clear_graph=False)` 一次 analyzer
            调用吃下所有文件，跨文件 label 互不覆盖、不重复建节点；
          · 解析几十个文件是重活，走 `_run_async` 后台线程，窗口不会假死；
          · 结果用 `ReplaceGraphCommand` 提交，Ctrl+Z 能整体撤销这次导入。
        """
        file_paths = filedialog.askopenfilenames(
            title="选择要导入的章节脚本（可多选）",
            filetypes=[("Ren'Py 脚本", "*.rpy"), ("所有文件", "*.*")],
        )
        if not file_paths:
            return

        from renpy_parser import batch_import_chapters as _batch_import

        before = self.graph.copy()
        work_graph = self.graph.copy()

        def work():
            _batch_import(list(file_paths), work_graph)
            return work_graph

        def on_done(new_graph):
            self._finish_job(f'批量导入 {len(file_paths)} 个章节')
            self.history.execute_command(
                ReplaceGraphCommand(self, new_graph, before))
            self.fit_view()
            added = len(new_graph.nodes) - len(before.nodes)
            messagebox.showinfo(
                "批量导入完成",
                f"共选择 {len(file_paths)} 个文件，"
                f"新增 {max(added, 0)} 个章节节点，"
                f"当前共 {len(new_graph.nodes)} 个节点。\n"
                f"（Ctrl+Z 可整体撤销这次导入）")

        def on_error(err):
            self._finish_job()
            messagebox.showerror("批量导入失败", str(err))

        self._run_async(f'批量导入 {len(file_paths)} 个章节脚本',
                        work, on_done=on_done, on_error=on_error)

    def export_renpy(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".rpy", filetypes=[("Ren'Py 文件", "*.rpy")])
        if file_path:
            export_to_renpy(self.graph, file_path)

    def save_layout(self):
        data = {
            "nodes": {nid: {
                "label": n.label_name,
                "x": n.x,
                "y": n.y,
                "is_current": n.is_current,
                "operations": n.operations,
                "file_path": n.file_path,
                "line_number": n.line_number
            } for nid, n in self.graph.nodes.items()},
            "edges": {eid: {
                "from": e.from_id,
                "to": e.to_id,
                "type": e.type,
                "condition": e.condition,
                "option_text": e.option_text
            } for eid, e in self.graph.edges.items()},
            "next_ids": {"node": self.graph.next_node_id, "edge": self.graph.next_edge_id}
        }
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("保存成功", "布局已保存")

    def load_layout(self):
        file_path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if file_path:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cmd = LoadLayoutCommand(self, data)
            self.history.execute_command(cmd)

    def _apply_layout_data(self, data):
        self.graph.clear()
        for nid_str, ndata in data["nodes"].items():
            nid = int(nid_str)
            node = Node(nid, ndata["label"], ndata["x"], ndata["y"])
            node.is_current = ndata.get("is_current", False)
            node.operations = ndata.get("operations", [])
            node.file_path = ndata.get("file_path")
            node.line_number = ndata.get("line_number")
            self.graph.nodes[nid] = node
        for eid_str, edata in data["edges"].items():
            eid = int(eid_str)
            edge = Edge(eid, edata["from"], edata["to"], edata["type"],
                       edata.get("condition", ""), edata.get("option_text", ""))
            self.graph.edges[eid] = edge
        self.graph.next_node_id = data["next_ids"]["node"]
        self.graph.next_edge_id = data["next_ids"]["edge"]

    def process_queue(self):
        try:
            while True:
                msg = self.result_queue.get_nowait()
                if msg[0] == "layout":
                    graph_copy = msg[1]
                    self.graph = graph_copy
                    self.redraw_all()
                    self.fit_view()
                elif msg[0] == "analysis":
                    result_text, warnings = msg[1], msg[2]
                    self.analysis_text.delete(1.0, tk.END)
                    self.analysis_text.insert(1.0, result_text)
                    if warnings:
                        messagebox.showwarning("逻辑警告", f"发现 {len(warnings)} 个问题\n详情请查看右侧分析面板")
                    else:
                        messagebox.showinfo("分析完成", "未发现明显的逻辑漏洞。")
                elif msg[0] == "detailed_report":
                    report = msg[1]
                    self._show_report_window(report)
                elif msg[0] == "error":
                    messagebox.showerror("错误", msg[1])
                elif msg[0] == "job_done":
                    callback, result = msg[1], msg[2]
                    self._finish_job(self._busy_desc)
                    if callback is not None:
                        callback(result)
                elif msg[0] == "job_failed":
                    on_error, desc, err = msg[1], msg[2], msg[3]
                    self._finish_job('')
                    if on_error is not None:
                        on_error(err)
                    else:
                        messagebox.showerror(f'{desc}失败', str(err))
                elif msg[0] == "busy_end":
                    with self.lock:
                        self.busy_count -= 1
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def _show_report_window(self, report):
        dlg = tk.Toplevel(self.root)
        dlg.title("详细分析报告")
        dlg.geometry("900x700")
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")
        text_widget = st.ScrolledText(dlg, width=110, height=45, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text_widget.insert(1.0, report)
        def save_report():
            file_path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
            )
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(report)
                messagebox.showinfo("保存成功", f"分析报告已保存到 {file_path}")
        tk.Button(dlg, text="保存报告", command=save_report).pack(pady=10)

    def fit_view(self):
        if not self.graph.nodes:
            return
        min_x = min(n.x for n in self.graph.nodes.values())
        max_x = max(n.x for n in self.graph.nodes.values())
        min_y = min(n.y for n in self.graph.nodes.values())
        max_y = max(n.y for n in self.graph.nodes.values())
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        if canvas_w < 50 or canvas_h < 50:
            return
        padding = 80
        graph_width = max_x - min_x + 2 * padding
        graph_height = max_y - min_y + 2 * padding
        if graph_width == 0 or graph_height == 0:
            return
        scale_x = canvas_w / graph_width
        scale_y = canvas_h / graph_height
        self.scale = min(scale_x, scale_y, 1.5)
        self.offset_x = (canvas_w / self.scale - (max_x + min_x)) / 2
        self.offset_y = (canvas_h / self.scale - (max_y + min_y)) / 2
        self.redraw_all()
        self.scale_var.set(f"{int(self.scale*100)}%")

    def undo(self):
        self.history.undo()

    def redo(self):
        self.history.redo()