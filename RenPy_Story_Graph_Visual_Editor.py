import tkinter as tk
from tkinter import simpledialog, messagebox, filedialog
import tkinter.scrolledtext as st
import re
import json
import math
import ast
import copy

# ===================== 数据结构 =====================
class Node:
    def __init__(self, node_id, label_name, x=100, y=100):
        self.id = node_id
        self.label_name = label_name
        self.x = x
        self.y = y
        self.is_current = False
        self.operations = []   # $ 变量操作列表

class Edge:
    def __init__(self, edge_id, from_id, to_id, etype="jump", condition="", option_text=""):
        self.id = edge_id
        self.from_id = from_id
        self.to_id = to_id
        self.type = etype          # jump / call
        self.condition = condition          # 条件表达式
        self.option_text = option_text      # 菜单选项文字

class Graph:
    def __init__(self):
        self.nodes = {}       # id -> Node
        self.edges = {}       # id -> Edge
        self.next_node_id = 1
        self.next_edge_id = 1

    def copy(self):
        """深拷贝整个图，用于历史记录"""
        g = Graph()
        g.next_node_id = self.next_node_id
        g.next_edge_id = self.next_edge_id
        for nid, node in self.nodes.items():
            new_node = Node(nid, node.label_name, node.x, node.y)
            new_node.is_current = node.is_current
            new_node.operations = copy.deepcopy(node.operations)
            g.nodes[nid] = new_node
        for eid, edge in self.edges.items():
            new_edge = Edge(eid, edge.from_id, edge.to_id, edge.type,
                            edge.condition, edge.option_text)
            g.edges[eid] = new_edge
        return g

    def add_node(self, label_name, x, y, operations=None):
        nid = self.next_node_id
        self.next_node_id += 1
        node = Node(nid, label_name, x, y)
        if operations:
            node.operations = operations
        self.nodes[nid] = node
        return nid

    def add_edge(self, from_id, to_id, etype="jump", condition="", option_text=""):
        eid = self.next_edge_id
        self.next_edge_id += 1
        edge = Edge(eid, from_id, to_id, etype, condition, option_text)
        self.edges[eid] = edge
        return eid

    def remove_node(self, nid):
        if nid in self.nodes:
            del self.nodes[nid]
        to_del = [eid for eid, e in self.edges.items() if e.from_id == nid or e.to_id == nid]
        for eid in to_del:
            del self.edges[eid]

    def remove_edge(self, eid):
        if eid in self.edges:
            del self.edges[eid]

    def clear(self):
        self.nodes.clear()
        self.edges.clear()
        self.next_node_id = 1
        self.next_edge_id = 1
# ===================== 工具函数 =====================
def extract_variables(text):
    """提取文本中的所有变量名"""
    tokens = re.findall(r'\b([a-zA-Z_]\w*)\b', text)
    keywords = {'if','else','elif','and','or','not','True','False','None','jump','call',
               'int','str','bool','float','len','abs','min','max','range','sum'}
    return [t for t in tokens if t not in keywords]

def collect_all_variables(graph):
    """从图中收集所有变量"""
    vars_set = set()
    for node in graph.nodes.values():
        for op in node.operations:
            vars_set.update(extract_variables(op))
    for edge in graph.edges.values():
        if edge.condition:
            vars_set.update(extract_variables(edge.condition))
    return vars_set

# ===================== 变量区间分析模块 =====================
class ValueRange:
    def __init__(self, lo, hi):
        self.lo = lo
        self.hi = hi
    
    def is_top(self):
        return self.lo == -math.inf and self.hi == math.inf
    
    def is_bottom(self):
        return self.lo > self.hi
    
    def is_const(self):
        return self.lo == self.hi and not self.is_top() and not self.is_bottom()
    
    def join(self, other):
        """并集操作"""
        if self.is_bottom():
            return other.copy()
        if other.is_bottom():
            return self.copy()
        if self.is_top() or other.is_top():
            return ValueRange(-math.inf, math.inf)
        return ValueRange(min(self.lo, other.lo), max(self.hi, other.hi))
    
    def intersect(self, other):
        """交集操作"""
        if self.is_bottom() or other.is_bottom():
            return ValueRange(1, -1)  # bottom
        if self.is_top():
            return other.copy()
        if other.is_top():
            return self.copy()
        return ValueRange(max(self.lo, other.lo), min(self.hi, other.hi))
    
    def add(self, delta):
        if self.is_top() or self.is_bottom():
            return ValueRange(self.lo, self.hi)
        try:
            return ValueRange(self.lo + delta, self.hi + delta)
        except OverflowError:
            return ValueRange(-math.inf, math.inf)
    
    def mul(self, delta):
        """乘法操作，简化处理"""
        if self.is_top() or self.is_bottom():
            return ValueRange(self.lo, self.hi)
        if delta == 0:
            return ValueRange(0, 0)
        try:
            values = [self.lo * delta, self.hi * delta]
            return ValueRange(min(values), max(values))
        except OverflowError:
            return ValueRange(-math.inf, math.inf)
    
    def copy(self):
        return ValueRange(self.lo, self.hi)
    
    def __eq__(self, other):
        if isinstance(other, ValueRange):
            return self.lo == other.lo and self.hi == other.hi
        return False
    
    def __repr__(self):
        if self.is_top():
            return "⊤"
        if self.is_bottom():
            return "⊥"
        if self.lo == self.hi:
            return str(self.lo)
        return f"[{self.lo}, {self.hi}]"

class VariableState:
    def __init__(self, var_names=None):
        self.ranges = {}  # name -> ValueRange
        if var_names:
            for v in var_names:
                self.ranges[v] = ValueRange(0, 0)  # 初始值0
    
    def copy(self):
        new_state = VariableState()
        new_state.ranges = {k: v.copy() for k, v in self.ranges.items()}
        return new_state
    
    def merge(self, other):
        """合并两个状态（并集）"""
        keys = set(self.ranges.keys()) | set(other.ranges.keys())
        new_state = VariableState()
        for k in keys:
            r1 = self.ranges.get(k, ValueRange(-math.inf, math.inf))
            r2 = other.ranges.get(k, ValueRange(-math.inf, math.inf))
            new_state.ranges[k] = r1.join(r2)
        return new_state
    
    def set_top(self, var_name=None):
        """将指定变量或所有变量设为未知(⊤)"""
        if var_name:
            if var_name in self.ranges:
                self.ranges[var_name] = ValueRange(-math.inf, math.inf)
        else:
            for k in self.ranges:
                self.ranges[k] = ValueRange(-math.inf, math.inf)
    
    def is_bottom(self):
        """检查是否为全bottom状态（不可达）"""
        return all(r.is_bottom() for r in self.ranges.values())
    
    def __str__(self):
        items = []
        for var, rng in sorted(self.ranges.items()):
            items.append(f"{var}: {rng}")
        return "{" + ", ".join(items) + "}"

# ===================== AST 区间求值器 =====================
class IntervalEvaluator:
    def __init__(self, state):
        self.state = state  # VariableState 实例

    def eval(self, node):
        """返回节点对应的 ValueRange，如果无法求值则返回 ⊤"""
        if isinstance(node, ast.Constant):
            return self._eval_constant(node)
        if isinstance(node, ast.Name):
            return self._eval_name(node)
        if isinstance(node, ast.UnaryOp):
            return self._eval_unary(node)
        if isinstance(node, ast.BinOp):
            return self._eval_binop(node)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.Compare):
            # 比较表达式返回 0/1 的区间
            return ValueRange(0, 1)
        # 其它情况
        return ValueRange(-math.inf, math.inf)

    def _eval_constant(self, node):
        val = node.value
        if isinstance(val, (int, float)):
            return ValueRange(int(val), int(val))
        if isinstance(val, bool):
            return ValueRange(int(val), int(val))
        return ValueRange(-math.inf, math.inf)

    def _eval_name(self, node):
        if node.id in self.state.ranges:
            return self.state.ranges[node.id].copy()
        return ValueRange(-math.inf, math.inf)

    def _eval_unary(self, node):
        operand = self.eval(node.operand)
        if operand.is_top() or operand.is_bottom():
            return ValueRange(-math.inf, math.inf)
        if isinstance(node.op, ast.USub):
            return ValueRange(-operand.hi, -operand.lo)
        if isinstance(node.op, ast.UAdd):
            return operand.copy()
        if isinstance(node.op, ast.Not):
            return ValueRange(1, 1) if operand == ValueRange(0,0) else ValueRange(0,1)
        return ValueRange(-math.inf, math.inf)

    def _eval_binop(self, node):
        left = self.eval(node.left)
        right = self.eval(node.right)
        if left.is_top() or right.is_top() or left.is_bottom() or right.is_bottom():
            return ValueRange(-math.inf, math.inf)

        lo1, hi1 = left.lo, left.hi
        lo2, hi2 = right.lo, right.hi

        if isinstance(node.op, ast.Add):
            return ValueRange(lo1 + lo2, hi1 + hi2)
        if isinstance(node.op, ast.Sub):
            return ValueRange(lo1 - hi2, hi1 - lo2)
        if isinstance(node.op, ast.Mult):
            candidates = [lo1*lo2, lo1*hi2, hi1*lo2, hi1*hi2]
            return ValueRange(min(candidates), max(candidates))
        if isinstance(node.op, ast.Div):
            return ValueRange(-math.inf, math.inf)
        if isinstance(node.op, ast.FloorDiv):
            return ValueRange(-math.inf, math.inf)
        return ValueRange(-math.inf, math.inf)

    def _eval_call(self, node):
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        if func_name == 'abs' and len(node.args) == 1:
            arg = self.eval(node.args[0])
            if arg.is_top() or arg.is_bottom():
                return ValueRange(-math.inf, math.inf)
            if arg.lo >= 0:
                return ValueRange(arg.lo, arg.hi)
            elif arg.hi <= 0:
                return ValueRange(-arg.hi, -arg.lo)
            else:
                return ValueRange(0, max(-arg.lo, arg.hi))
        if func_name == 'min' and len(node.args) >= 1:
            ranges = [self.eval(a) for a in node.args]
            if any(r.is_top() or r.is_bottom() for r in ranges):
                return ValueRange(-math.inf, math.inf)
            return ValueRange(min(r.lo for r in ranges), min(r.hi for r in ranges))
        if func_name == 'max' and len(node.args) >= 1:
            ranges = [self.eval(a) for a in node.args]
            if any(r.is_top() or r.is_bottom() for r in ranges):
                return ValueRange(-math.inf, math.inf)
            return ValueRange(max(r.lo for r in ranges), max(r.hi for r in ranges))
        return ValueRange(-math.inf, math.inf)

# ===================== 操作解析 =====================
def apply_operation(state, op_line):
    """使用 AST 解析操作并更新变量状态"""
    new_state = state.copy()
    op = op_line.strip()
    if op.startswith('$'):
        op = op[1:].strip()
    try:
        tree = ast.parse(op, mode='exec')
    except SyntaxError:
        for var in extract_variables(op):
            if var in new_state.ranges:
                new_state.ranges[var] = ValueRange(-math.inf, math.inf)
        return new_state

    evaluator = IntervalEvaluator(new_state)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value_range = evaluator.eval(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in new_state.ranges:
                    new_state.ranges[target.id] = value_range.copy()
                else:
                    if isinstance(target, ast.Name) and target.id in new_state.ranges:
                        new_state.ranges[target.id] = ValueRange(-math.inf, math.inf)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id in new_state.ranges:
                var = node.target.id
                current_range = new_state.ranges[var]
                if current_range.is_top() or current_range.is_bottom():
                    new_state.ranges[var] = ValueRange(-math.inf, math.inf)
                else:
                    delta_range = evaluator.eval(node.value)
                    if isinstance(node.op, ast.Add):
                        new_state.ranges[var] = ValueRange(current_range.lo + delta_range.lo,
                                                           current_range.hi + delta_range.hi)
                    elif isinstance(node.op, ast.Sub):
                        new_state.ranges[var] = ValueRange(current_range.lo - delta_range.hi,
                                                           current_range.hi - delta_range.lo)
                    else:
                        new_state.ranges[var] = ValueRange(-math.inf, math.inf)
            else:
                pass
        else:
            for var in extract_variables(op):
                if var in new_state.ranges:
                    new_state.ranges[var] = ValueRange(-math.inf, math.inf)
    return new_state

# ===================== 增强的条件分析 =====================
def condition_possible(state, cond_str):
    """使用 AST 判断条件是否可能成立"""
    if not cond_str or not cond_str.strip():
        return True
    try:
        tree = ast.parse(cond_str.strip(), mode='eval')
    except SyntaxError:
        return True

    return _ast_cond_possible(tree.body, state)

def _ast_cond_possible(node, state):
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_ast_cond_possible(v, state) for v in node.values)
        elif isinstance(node.op, ast.Or):
            return any(_ast_cond_possible(v, state) for v in node.values)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return True
    elif isinstance(node, ast.Compare):
        return _ast_compare_possible(node, state)
    if isinstance(node, ast.Constant):
        return bool(node.value)
    return True

def _ast_compare_possible(node, state):
    left_evaluator = IntervalEvaluator(state)
    left = left_evaluator.eval(node.left)
    left_name = None
    if isinstance(node.left, ast.Name):
        left_name = node.left.id
    else:
        return True

    current_satisfied = True
    for op, comp in zip(node.ops, node.comparators):
        if not current_satisfied:
            break
        if isinstance(op, ast.Eq):
            if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
                val = int(comp.value)
                rng = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))
                if rng.is_bottom() or rng.is_top():
                    continue
                if rng.lo > val or rng.hi < val:
                    return False
            elif isinstance(comp, ast.Name):
                rng1 = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))
                rng2 = state.ranges.get(comp.id, ValueRange(-math.inf, math.inf))
                if rng1.is_bottom() or rng1.is_top() or rng2.is_bottom() or rng2.is_top():
                    continue
                if rng1.hi < rng2.lo or rng1.lo > rng2.hi:
                    return False
        elif isinstance(op, ast.NotEq):
            if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
                val = int(comp.value)
                rng = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))
                if rng.lo == rng.hi == val:
                    return False
        elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
            if isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
                val = int(comp.value)
                rng = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))
                if rng.is_bottom() or rng.is_top():
                    continue
                if isinstance(op, ast.Lt) and rng.lo >= val:
                    return False
                if isinstance(op, ast.LtE) and rng.lo > val:
                    return False
                if isinstance(op, ast.Gt) and rng.hi <= val:
                    return False
                if isinstance(op, ast.GtE) and rng.hi < val:
                    return False
            elif isinstance(comp, ast.Name):
                rng1 = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))
                rng2 = state.ranges.get(comp.id, ValueRange(-math.inf, math.inf))
                if rng1.is_bottom() or rng1.is_top() or rng2.is_bottom() or rng2.is_top():
                    continue
                if isinstance(op, ast.Lt) and rng1.lo >= rng2.hi:
                    return False
                if isinstance(op, ast.LtE) and rng1.lo > rng2.hi:
                    return False
                if isinstance(op, ast.Gt) and rng1.hi <= rng2.lo:
                    return False
                if isinstance(op, ast.GtE) and rng1.hi < rng2.lo:
                    return False
    return True

def apply_condition(state, cond_str):
    """使用 AST 解析条件并收缩区间，返回新状态或 None（不可能）"""
    if not cond_str or not cond_str.strip():
        return state.copy()
    try:
        tree = ast.parse(cond_str.strip(), mode='eval')
    except SyntaxError:
        return state.copy()

    new_state = state.copy()
    if _ast_apply_cond(tree.body, new_state):
        return new_state
    else:
        return None

def _ast_apply_cond(node, state):
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not _ast_apply_cond(v, state):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            return True
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return True
    elif isinstance(node, ast.Compare):
        return _ast_apply_compare(node, state)
    elif isinstance(node, ast.Constant):
        return bool(node.value)
    return True

def _ast_apply_compare(node, state):
    left_name = None
    if isinstance(node.left, ast.Name):
        left_name = node.left.id
    else:
        return True

    for op, comp in zip(node.ops, node.comparators):
        if left_name not in state.ranges:
            continue
        rng = state.ranges[left_name]
        if rng.is_top() or rng.is_bottom():
            continue
        lo, hi = rng.lo, rng.hi

        if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
            val = int(comp.value)
            if lo > val or hi < val:
                return False
            state.ranges[left_name] = ValueRange(val, val)
        elif isinstance(op, ast.NotEq) and isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
            pass
        elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and isinstance(comp, ast.Constant) and isinstance(comp.value, (int, float)):
            val = int(comp.value)
            if isinstance(op, ast.Lt):
                hi = min(hi, val - 1)
            elif isinstance(op, ast.LtE):
                hi = min(hi, val)
            elif isinstance(op, ast.Gt):
                lo = max(lo, val + 1)
            elif isinstance(op, ast.GtE):
                lo = max(lo, val)
            if lo > hi:
                return False
            state.ranges[left_name] = ValueRange(lo, hi)
    return True

# ===================== 可达性分析 =====================
def analyze_reachability(graph, start_node_id):
    if start_node_id not in graph.nodes:
        return {}, ["起始节点不存在！"]
    
    all_vars = collect_all_variables(graph)
    entry_states = {}
    
    for nid in graph.nodes:
        bottom_state = VariableState(all_vars)
        for v in all_vars:
            bottom_state.ranges[v] = ValueRange(1, -1)  # lo > hi 表示 bottom
        entry_states[nid] = bottom_state
    
    init_state = VariableState(all_vars)
    for v in all_vars:
        init_state.ranges[v] = ValueRange(0, 0)
    entry_states[start_node_id] = init_state
    
    worklist = [start_node_id]
    in_worklist = {start_node_id}
    
    while worklist:
        nid = worklist.pop(0)
        in_worklist.discard(nid)
        
        state = entry_states[nid]
        if state.is_bottom():
            continue
        
        current = state.copy()
        node = graph.nodes[nid]
        for op in node.operations:
            current = apply_operation(current, op)
        
        out_edges = [e for e in graph.edges.values() if e.from_id == nid]
        for edge in out_edges:
            target = edge.to_id
            
            if not condition_possible(current, edge.condition):
                continue
            
            new_state = apply_condition(current, edge.condition)
            if new_state is None:
                continue
            
            old_state = entry_states[target]
            merged = old_state.merge(new_state)
            
            changed = False
            for var in all_vars:
                old_r = old_state.ranges[var]
                new_r = merged.ranges[var]
                if old_r.lo != new_r.lo or old_r.hi != new_r.hi:
                    changed = True
                    break
            
            if changed:
                entry_states[target] = merged
                if target not in in_worklist:
                    worklist.append(target)
                    in_worklist.add(target)
    
    warnings = []
    for nid, node in graph.nodes.items():
        instate = entry_states[nid]
        if instate.is_bottom():
            warnings.append(f"🚫 不可达节点：{node.label_name}")
            continue
        
        out_edges = [e for e in graph.edges.values() if e.from_id == nid]
        if not out_edges:
            continue
        
        has_possible = False
        for edge in out_edges:
            if condition_possible(instate, edge.condition):
                has_possible = True
                break
        
        if not has_possible:
            warnings.append(f"⚠️ 下落风险：{node.label_name} 的所有分支条件在当前变量范围内不可能满足")
    
    return entry_states, warnings

def visualize_analysis_results(graph, entry_states, warnings):
    """在图形界面上显示分析结果"""
    result_text = "=== 变量区间分析结果 ===\n\n"
    
    for nid, node in graph.nodes.items():
        result_text += f"节点: {node.label_name}\n"
        state = entry_states.get(nid)
        if state and not state.is_bottom():
            result_text += f"  入口状态: {state}\n"
            current = state.copy()
            for op in node.operations:
                current = apply_operation(current, op)
            if node.operations:
                result_text += f"  出口状态: {current}\n"
            result_text += f"  操作: {node.operations if node.operations else '无'}\n"
        else:
            result_text += f"  状态: 不可达\n"
        result_text += "\n"
    
    if warnings:
        result_text += "\n=== 警告 ===\n"
        for warning in warnings:
            result_text += f"{warning}\n"
    
    return result_text

#=====================历史管理器=====================
class HistoryManager:
    """管理撤销/重做，存储 Graph 的快照"""
    def __init__(self, graph_view):
        self.graph_view = graph_view
        self.history = []       # 存放 Graph 对象
        self.future = []        # 重做栈
        self.max_steps = 30

    def snapshot(self):
        """在修改图之前调用，将当前状态压入历史"""
        while len(self.history) >= self.max_steps:
            self.history.pop(0)
        self.history.append(self.graph_view.graph.copy())
        self.future.clear()

    def undo(self):
        if not self.history:
            messagebox.showinfo("提示", "没有可撤销的操作")
            return
        self.future.append(self.graph_view.graph.copy())
        prev = self.history.pop()
        self.graph_view.graph = prev
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.redraw()

    def redo(self):
        if not self.future:
            messagebox.showinfo("提示", "没有可重做的操作")
            return
        self.history.append(self.graph_view.graph.copy())
        next_state = self.future.pop()
        self.graph_view.graph = next_state
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.redraw()

# ===================== Ren'Py 解析器 =====================
def parse_renpy_file(filepath, graph):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"读取失败: {filepath}, {e}")
        return
    
    # 确保所有 label 都存在
    for line in lines:
        m = re.match(r'^\s*label\s+(\w+)\s*:', line)
        if m:
            lab = m.group(1)
            if not any(n.label_name == lab for n in graph.nodes.values()):
                graph.add_node(lab, 200, 200)
    
    current_label = None
    in_menu = False
    label_indent = -1
    
    for i, line in enumerate(lines):
        lm = re.match(r'^(\s*)label\s+(\w+)\s*:', line)
        if lm:
            current_label = lm.group(2)
            label_indent = len(lm.group(1))
            in_menu = False
            continue
        
        if current_label is None:
            continue
        
        # menu: 行
        if re.match(r'^\s*menu\s*:', line):
            in_menu = True
            continue
        
        # 在 menu 内解析选项
        if in_menu:
            opt_match = re.match(r'^\s*"([^"]*)"(\s+if\s+(.+?))?\s*:', line)
            if opt_match:
                option_text = opt_match.group(1)
                condition = opt_match.group(3) if opt_match.group(3) else ""
                if i+1 < len(lines):
                    next_line = lines[i+1]
                    j_match = re.match(r'\s*(jump|call)\s+(\w+)', next_line)
                    if j_match:
                        jtype, target = j_match.group(1), j_match.group(2)
                        if not any(n.label_name == target for n in graph.nodes.values()):
                            graph.add_node(target, 200, 200)
                        from_id = next((nid for nid, n in graph.nodes.items() if n.label_name == current_label), None)
                        to_id = next((nid for nid, n in graph.nodes.items() if n.label_name == target), None)
                        if from_id and to_id:
                            graph.add_edge(from_id, to_id, etype=jtype, condition=condition, option_text=option_text)
            # 退出 menu 的简单判断
            if line.strip() and not re.match(r'\s*"', line) and not re.match(r'\s*(jump|call|if|elif|else)', line):
                in_menu = False
            continue
        
        # 不在 menu 内：收集 $ 操作
        m_dollar = re.match(r'^(\s*)\$\s+(.+)', line)
        if m_dollar:
            indent = len(m_dollar.group(1))
            if indent > label_indent:
                node = next((n for n in graph.nodes.values() if n.label_name == current_label), None)
                if node:
                    node.operations.append(m_dollar.group(2).strip())
            continue
        
        # 条件跳转：if condition: jump target
        cond_jump = re.match(r'^\s*if\s+(.+?)\s*:\s*(jump|call)\s+(\w+)', line)
        if cond_jump:
            condition, jtype, target = cond_jump.group(1), cond_jump.group(2), cond_jump.group(3)
            if not any(n.label_name == target for n in graph.nodes.values()):
                graph.add_node(target, 200, 200)
            from_id = next((nid for nid, n in graph.nodes.items() if n.label_name == current_label), None)
            to_id = next((nid for nid, n in graph.nodes.items() if n.label_name == target), None)
            if from_id and to_id:
                graph.add_edge(from_id, to_id, etype=jtype, condition=condition, option_text="")
            continue
        
        # 无条件跳转
        simple_jump = re.match(r'^\s*(jump|call)\s+(\w+)', line)
        if simple_jump:
            jtype, target = simple_jump.group(1), simple_jump.group(2)
            if not any(n.label_name == target for n in graph.nodes.values()):
                graph.add_node(target, 200, 200)
            from_id = next((nid for nid, n in graph.nodes.items() if n.label_name == current_label), None)
            to_id = next((nid for nid, n in graph.nodes.items() if n.label_name == target), None)
            if from_id and to_id:
                graph.add_edge(from_id, to_id, etype=jtype, condition="", option_text="")
            continue
    
    print(f"解析完成：{len(graph.nodes)} 标签，{len(graph.edges)} 条边。")

# ===================== 导出为 Ren'Py =====================
def export_to_renpy(graph, filepath):
    all_vars = collect_all_variables(graph)
    lines = ["# 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）"]
    for var in sorted(all_vars):
        lines.append(f"default {var} = 0")
    lines.append("")
    
    for nid, node in graph.nodes.items():
        control_label = f"{node.label_name}_control"
        lines.append(f"label {control_label}:")
        lines.append(f"    call {node.label_name}")
        for op in node.operations:
            lines.append(f"    $ {op}")
        
        out_edges = [e for e in graph.edges.values() if e.from_id == nid]
        if not out_edges:
            lines.append("    return")
            lines.append("")
            continue
        
        menu_edges = [e for e in out_edges if e.option_text]
        cond_edges = [e for e in out_edges if not e.option_text and e.condition]
        uncond_edges = [e for e in out_edges if not e.option_text and not e.condition]
        
        # 情况 A：只有菜单边
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
        
        # 情况 B：有菜单边 + 其他边
        elif menu_edges:
            if not cond_edges:   # 无真条件边 → 降级为纯菜单
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
                # 条件边 if/elif
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
        
        # 情况 C：纯条件/无条件边（无菜单）
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
                        lines.append(f"    else:")
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

# ===================== 前端界面 =====================
class GraphView:
    def __init__(self, root, graph):
        self.graph = graph
        self.root = root
        self.root.title("剧情树编辑器 - 增强变量分析版")
        self.root.geometry("1400x900")
        
        self.offset_x = 0
        self.offset_y = 0
        self.scale = 1.0
        
        self.selected_node = None
        self.selected_edge = None
        self.drag_data = {"node": None, "start_x": 0, "start_y": 0}
        self.connect_from = None
        self.temp_line = None
        self.canvas_ids = {}    # node_id -> (rect_id, text_id, op_text_id)
        self.edge_ids = {}      # edge_id -> line_id
        
        # 历史管理器
        self.history = HistoryManager(self)
        
        # 创建主框架
        main_frame = tk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 左侧画布
        self.canvas = tk.Canvas(main_frame, bg='#f0f0f0', highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 右侧面板
        right_panel = tk.Frame(main_frame, width=350)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        
        # 分析结果显示区域
        tk.Label(right_panel, text="分析结果", font=("微软雅黑", 12, "bold")).pack(pady=5)
        self.analysis_text = st.ScrolledText(right_panel, width=45, height=5, font=("Consolas", 9))
        self.analysis_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 按钮面板
        btn_frame = tk.Frame(right_panel)
        btn_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(btn_frame, text="导入Ren'Py文件", command=self.import_renpy).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="导出为Ren'Py", command=self.export_renpy).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="保存布局(JSON)", command=self.save_layout).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="加载布局", command=self.load_layout).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="清空图", command=self.clear_graph, bg="#F44336", fg="white").pack(pady=2, fill=tk.X)    
        tk.Button(btn_frame, text="检查逻辑漏洞", command=self.analyze_and_warn, 
                 bg="#4CAF50", fg="white").pack(pady=10, fill=tk.X)
        
        tk.Button(btn_frame, text="显示详细分析", command=self.show_detailed_analysis,
                 bg="#2196F3", fg="white").pack(pady=2, fill=tk.X)
        
        # 帮助信息
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
        
        # 缩放显示
        scale_frame = tk.Frame(right_panel)
        scale_frame.pack(fill=tk.X, pady=5)
        tk.Label(scale_frame, text="缩放：").pack(side=tk.LEFT)
        self.scale_var = tk.StringVar(value="100%")
        tk.Label(scale_frame, textvariable=self.scale_var).pack(side=tk.LEFT)
        
        # 右键菜单
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="添加新章节", command=self.add_node)
        self.menu.add_command(label="设为当前章节", command=self.set_current)
        self.menu.add_command(label="编辑变量操作", command=self.edit_operations)
        self.menu.add_separator()
        self.menu.add_command(label="删除节点", command=self.delete_node)
        self.menu.add_command(label="删除连线", command=self.delete_edge)
        
        # 事件绑定
        self.canvas.bind("<ButtonPress-1>", self.on_left_down)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_up)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<B3-Motion>", self.on_right_drag)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.root.bind("<Delete>", self.on_delete_key)
        self.root.bind("<Control-s>", lambda e: self.save_layout())
        self.root.bind("<Control-o>", lambda e: self.load_layout())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())
        self.root.bind("<Control-a>", lambda e: self.analyze_and_warn())
        
        self.redraw()
    
    # ---------- 坐标转换 ----------
    def world_to_screen(self, wx, wy):
        return (wx + self.offset_x) * self.scale, (wy + self.offset_y) * self.scale
    
    def screen_to_world(self, sx, sy):
        return sx / self.scale - self.offset_x, sy / self.scale - self.offset_y
    
    # ---------- 命中检测 ----------
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
    
    # ---------- 核心绘制----------
    def redraw(self):
        self.canvas.delete("all")
        self.canvas_ids.clear()
        self.edge_ids.clear()

        # ---- 预处理：找出双向边，计算公共垂直方向 ----
        pair_edges = {}
        for eid, edge in self.graph.edges.items():
            a, b = edge.from_id, edge.to_id
            key = (min(a, b), max(a, b))
            pair_edges.setdefault(key, []).append(eid)

        bidirectional_info = {}   # eid -> (bend, perp_x, perp_y)
        for key, eid_list in pair_edges.items():
            if len(eid_list) == 2:
                e1, e2 = eid_list[0], eid_list[1]
                edge1 = self.graph.edges[e1]
                edge2 = self.graph.edges[e2]
                if edge1.from_id == edge2.to_id and edge1.to_id == edge2.from_id:
                    # 公共方向：从小ID节点指向大ID节点
                    a, b = key
                    node_a = self.graph.nodes[a]
                    node_b = self.graph.nodes[b]
                    sax, say = self.world_to_screen(node_a.x, node_a.y)
                    sbx, sby = self.world_to_screen(node_b.x, node_b.y)
                    cdx = sbx - sax
                    cdy = sby - say
                    clength = math.hypot(cdx, cdy) or 1
                    cperp_x = -cdy / clength
                    cperp_y = cdx / clength

                    # 指定偏移符号：a→b 为正，b→a 为负
                    if edge1.from_id == a:
                        bidirectional_info[e1] = (+0.15, cperp_x, cperp_y)
                        bidirectional_info[e2] = (-0.15, cperp_x, cperp_y)
                    else:
                        bidirectional_info[e1] = (-0.15, cperp_x, cperp_y)
                        bidirectional_info[e2] = (+0.15, cperp_x, cperp_y)

        # 按起点分组出边（用于普通多出边弯曲）
        out_edges_dict = {}
        for eid, edge in self.graph.edges.items():
            out_edges_dict.setdefault(edge.from_id, []).append(eid)

        # ---- 绘制连线 ----
        for eid, edge in self.graph.edges.items():
            from_node = self.graph.nodes.get(edge.from_id)
            to_node = self.graph.nodes.get(edge.to_id)
            if not from_node or not to_node:
                continue

            sx1, sy1 = self.world_to_screen(from_node.x, from_node.y)
            sx2, sy2 = self.world_to_screen(to_node.x, to_node.y)

            # 决定弯曲系数和垂直方向
            if eid in bidirectional_info:
                bend, perp_x, perp_y = bidirectional_info[eid]
            else:
                # 普通多出边：按出边序号计算弯曲系数
                outgoing = out_edges_dict.get(edge.from_id, [])
                count = len(outgoing)
                if count <= 1:
                    bend = 0.0
                else:
                    idx = outgoing.index(eid)
                    bend = (idx / (count - 1)) * 0.5 - 0.25  # [-0.25, 0.25]
                # 使用自身方向计算垂直方向
                dx = sx2 - sx1
                dy = sy2 - sy1
                length = math.hypot(dx, dy) or 1
                perp_x = -dy / length
                perp_y = dx / length

            # 计算偏移距离
            dx = sx2 - sx1
            dy = sy2 - sy1
            length = math.hypot(dx, dy) or 1
            dist = length * 0.3          # 偏移幅度
            offset_x = perp_x * bend * dist
            offset_y = perp_y * bend * dist

            # 单一控制点：起终点中点 + 偏移
            ctrl_x = (sx1 + sx2) / 2 + offset_x
            ctrl_y = (sy1 + sy2) / 2 + offset_y

            # 颜色与样式
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

            # 绘制光滑曲线（单控制点贝塞尔）
            line_id = self.canvas.create_line(
                sx1, sy1, ctrl_x, ctrl_y, sx2, sy2,
                smooth=True, splinesteps=32,
                fill=color, width=width, dash=dash
            )
            self.edge_ids[eid] = line_id

            # ---- 中点箭头 ----
            # 计算 t=0.5 处的坐标和切线
            t = 0.5
            t1 = 0.5
            # 二次贝塞尔中点公式：B(t) = (1-t)^2*P0 + 2(1-t)t*P1 + t^2*P2
            mx = t1**2 * sx1 + 2 * t1 * t * ctrl_x + t**2 * sx2
            my = t1**2 * sy1 + 2 * t1 * t * ctrl_y + t**2 * sy2
            # 切线：dB/dt = 2(1-t)(P1-P0) + 2t(P2-P1)
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
            self.canvas.create_polygon(
                tip_x, tip_y, left_x, left_y, right_x, right_y,
                fill=color, outline=color
            )

            # ---- 标签绘制 ----
            display_lines = []
            if edge.option_text:
                display_lines.append(edge.option_text)
            if edge.condition:
                display_lines.append(f"if {edge.condition}")
            if not display_lines:
                continue

            label_x = mx + perp_x * 15   # 稍微远离箭头
            label_y = my + perp_y * 15
            line_height = 20
            total_height = (len(display_lines) - 1) * line_height
            start_y = label_y - total_height / 2
            font = ("微软雅黑", 8, "bold") if eid == self.selected_edge else ("微软雅黑", 8)

            for i, line in enumerate(display_lines):
                y_pos = start_y + i * line_height
                self.canvas.create_text(
                    label_x, y_pos, text=line, fill="darkred",
                    font=font, width=110
                )

        # ---- 绘制节点----
        for nid, node in self.graph.nodes.items():
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
            if node.operations:
                op_count = len(node.operations)
                op_text = f"{op_count} 操作"
                if op_text:
                    op_text_id = self.canvas.create_text(sx, sy + 15, text=op_text,
                                                        font=("微软雅黑", 8), fill="#666")

            self.canvas_ids[nid] = (rect_id, text_id, op_text_id)
    
    # ---------- 视图变换 ----------
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
        self.redraw()
        self.scale_var.set(f"{int(self.scale*100)}%")
   # ---------- 撤销/重做包装 ----------
    def action_with_history(self, action_func):
        """执行修改图的操作，并自动保存历史"""
        self.history.snapshot()
        action_func()
        self.redraw()

    def undo(self):
        self.history.undo()

    def redo(self):
        self.history.redo()

    # ---------- 交互事件 ----------
    def on_left_down(self, event):
        nid = self.node_at(event.x, event.y)
        if nid is not None:
            self.selected_node = nid
            self.selected_edge = None
            if event.state & 0x0001:   # Shift 连线
                self.connect_from = nid
                sx, sy = self.world_to_screen(self.graph.nodes[nid].x, self.graph.nodes[nid].y)
                self.temp_line = self.canvas.create_line(sx, sy, event.x, event.y, 
                                                        fill="red", width=3, arrow=tk.LAST)
            else:
                # 开始拖拽节点，记录当前状态（拖拽结束时坐标已改变，只需记录一次）
                self.history.snapshot()
                self.drag_data["node"] = nid
                self.drag_data["start_x"] = event.x
                self.drag_data["start_y"] = event.y
            self.redraw()
            return
        
        eid = self.edge_at(event.x, event.y)
        if eid is not None:
            self.selected_edge = eid
            self.selected_node = None
            self.redraw()
            return
        
        self.selected_node = None
        self.selected_edge = None
        self.drag_data["node"] = None
        self.redraw()

    def on_left_drag(self, event):
        if self.drag_data["node"] is not None:
            nid = self.drag_data["node"]
            dx = event.x - self.drag_data["start_x"]
            dy = event.y - self.drag_data["start_y"]
            node = self.graph.nodes[nid]
            node.x += dx / self.scale
            node.y += dy / self.scale
            self.drag_data["start_x"] = event.x
            self.drag_data["start_y"] = event.y
            self.redraw()
        elif self.connect_from is not None and self.temp_line:
            sx, sy = self.world_to_screen(self.graph.nodes[self.connect_from].x,
                                         self.graph.nodes[self.connect_from].y)
            self.canvas.coords(self.temp_line, sx, sy, event.x, event.y)

    def on_left_up(self, event):
        if self.drag_data["node"] is not None:
            self.drag_data["node"] = None
            # 节点拖拽结束，已在 on_left_down 中保存快照，无需重复
            return
        
        if self.connect_from is not None:
            target_nid = self.node_at(event.x, event.y)
            if target_nid and target_nid != self.connect_from:
                dlg = tk.Toplevel(self.root)
                dlg.title("选择跳转类型")
                dlg.geometry("300x220")
                dlg.transient(self.root)
                dlg.grab_set()
                # 居中
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
                
                from_id = self.connect_from
                to_id = target_nid
                
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
                    
                    # 使用 action_with_history 包裹添加边操作
                    self.action_with_history(
                        lambda: self.graph.add_edge(from_id, to_id, etype=etype,
                                                    condition=condition, option_text=option_text)
                    )
                    dlg.destroy()
                
                tk.Button(dlg, text="确定", command=on_ok).pack(pady=10)
                # 等待对话框关闭
                self.root.wait_window(dlg)
            
            # 清理临时线
            if self.temp_line:
                self.canvas.delete(self.temp_line)
                self.temp_line = None
            self.connect_from = None
            self.redraw()

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
        self.redraw()
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_right_drag(self, event):
        if not hasattr(self, '_last_pan_x'):
            self._last_pan_x = event.x
            self._last_pan_y = event.y
        self.update_view_transform(dx=event.x - self._last_pan_x,
                                  dy=event.y - self._last_pan_y)
        self._last_pan_x = event.x
        self._last_pan_y = event.y

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
                self.action_with_history(lambda: self.graph.add_node(name, wx, wy))

    def on_delete_key(self, event):
        if self.selected_node:
            self.delete_node()
        elif self.selected_edge:
            self.delete_edge()

    # ---------- 对话框 ----------
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
            self.action_with_history(lambda: [
                setattr(edge, 'type', new_type),
                setattr(edge, 'option_text', new_option),
                setattr(edge, 'condition', new_cond)
            ])
            dlg.destroy()
        
        tk.Button(dlg, text="确定", command=on_ok).grid(row=5, column=1, pady=20)
        dlg.mainloop()

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
        for op in node.operations:
            text_widget.insert(tk.END, op + '\n')
        text_widget.focus_set()
        
        def on_ok():
            lines = text_widget.get("1.0", tk.END).splitlines()
            new_ops = [l.strip() for l in lines if l.strip()]
            self.action_with_history(lambda: setattr(node, 'operations', new_ops))
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

    # ---------- 菜单动作 ----------
    def add_node(self):
        wx, wy = self.screen_to_world(200, 200)
        name = simpledialog.askstring("新建章节", "Label 名称:")
        if name:
            self.action_with_history(lambda: self.graph.add_node(name, wx, wy))

    def set_current(self):
        if self.selected_node:
            target = self.selected_node
            self.action_with_history(lambda: [
                setattr(n, 'is_current', False) for n in self.graph.nodes.values()
            ] + [setattr(self.graph.nodes[target], 'is_current', True)])
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
                target = self.selected_node
                self.action_with_history(lambda: self.graph.remove_node(target))
                self.selected_node = None
        else:
            messagebox.showinfo("提示", "请先选中一个节点")

    def delete_edge(self):
        if self.selected_edge:
            if messagebox.askyesno("删除", "确定删除此连线吗？"):
                target = self.selected_edge
                self.action_with_history(lambda: self.graph.remove_edge(target))
                self.selected_edge = None
        else:
            messagebox.showinfo("提示", "请先选中一个节点")

    def clear_graph(self):
        if messagebox.askyesno("清空图", "将清除所有节点和连线，是否继续？"):
            self.action_with_history(lambda: self.graph.clear())
            self.analysis_text.delete(1.0, tk.END)

    # ---------- 分析功能 ----------
    def analyze_and_warn(self):
        if not self.graph.nodes:
            messagebox.showinfo("分析中止", "图中无节点。")
            return
        start_nid = next((nid for nid, n in self.graph.nodes.items() if n.is_current), None)
        if not start_nid:
            start_nid = list(self.graph.nodes.keys())[0]
        entry_states, warnings = analyze_reachability(self.graph, start_nid)
        result_text = visualize_analysis_results(self.graph, entry_states, warnings)
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(1.0, result_text)
        if warnings:
            messagebox.showwarning("逻辑警告", f"发现 {len(warnings)} 个问题\n详情请查看右侧分析面板")
        else:
            messagebox.showinfo("分析完成", "未发现明显的逻辑漏洞。")

    def show_detailed_analysis(self):
        if not self.graph.nodes:
            return
        start_nid = next((nid for nid, n in self.graph.nodes.items() if n.is_current), None)
        if not start_nid:
            start_nid = list(self.graph.nodes.keys())[0]
        entry_states, warnings = analyze_reachability(self.graph, start_nid)
        
        dlg = tk.Toplevel(self.root)
        dlg.title("详细分析报告")
        dlg.geometry("900x700")
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_reqheight()) // 2
        dlg.geometry(f"+{x}+{y}")
        
        text_widget = st.ScrolledText(dlg, width=110, height=45, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        report = "=" * 80 + "\n"
        report += "详细变量区间分析报告\n"
        report += "=" * 80 + "\n\n"
        all_vars = collect_all_variables(self.graph)
        report += f"检测到的变量 ({len(all_vars)}个): {', '.join(sorted(all_vars))}\n\n"
        
        for nid, node in self.graph.nodes.items():
            report += f"节点: {node.label_name}\n"
            report += "-" * 40 + "\n"
            state = entry_states.get(nid)
            if state and not state.is_bottom():
                report += f"入口状态:\n{state}\n\n"
                current = state.copy()
                if node.operations:
                    report += "操作序列:\n"
                    for op in node.operations:
                        report += f"  $ {op}\n"
                        current = apply_operation(current, op)
                    report += f"\n出口状态:\n{current}\n"
                out_edges = [e for e in self.graph.edges.values() if e.from_id == nid]
                if out_edges:
                    report += "\n出边分析:\n"
                    for edge in out_edges:
                        target = self.graph.nodes[edge.to_id].label_name
                        cond = edge.condition if edge.condition else "无条件"
                        opt = f'"{edge.option_text}" ' if edge.option_text else ""
                        report += f"  → {target} ({opt}{cond})\n"
                        possible = condition_possible(current, edge.condition)
                        report += f"    条件可能: {'是' if possible else '否'}\n"
                        if possible and edge.condition:
                            after_cond = apply_condition(current, edge.condition)
                            if after_cond:
                                report += f"    满足条件后状态: {after_cond}\n"
            else:
                report += "状态: 不可达\n"
            report += "\n" + "=" * 80 + "\n\n"
        
        if warnings:
            report += "\n" + "!" * 40 + "\n"
            report += "警告信息\n"
            report += "!" * 40 + "\n"
            for warning in warnings:
                report += f"• {warning}\n"
        
        text_widget.delete(1.0, tk.END)
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

    # ---------- 文件操作 ----------
    def import_renpy(self):
        file_path = filedialog.askopenfilename(filetypes=[("Ren'Py 文件", "*.rpy")])
        if file_path:
            # 导入前记录历史（导入是整体修改）
            self.action_with_history(lambda: parse_renpy_file(file_path, self.graph))

    def export_renpy(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".rpy", filetypes=[("Ren'Py 文件", "*.rpy")])
        if file_path:
            export_to_renpy(self.graph, file_path)

    def save_layout(self):
        data = {
            "nodes": {nid: {"label": n.label_name, "x": n.x, "y": n.y, "is_current": n.is_current, "operations": n.operations}
                      for nid, n in self.graph.nodes.items()},
            "edges": {eid: {"from": e.from_id, "to": e.to_id, "type": e.type, "condition": e.condition, "option_text": e.option_text}
                      for eid, e in self.graph.edges.items()},
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
            # 加载布局前记录历史
            self.action_with_history(lambda: self._apply_layout(data))

    def _apply_layout(self, data):
        """内部方法：应用布局数据"""
        self.graph.clear()
        for nid_str, ndata in data["nodes"].items():
            nid = int(nid_str)
            node = Node(nid, ndata["label"], ndata["x"], ndata["y"])
            node.is_current = ndata.get("is_current", False)
            node.operations = ndata.get("operations", [])
            self.graph.nodes[nid] = node
        for eid_str, edata in data["edges"].items():
            eid = int(eid_str)
            edge = Edge(eid, edata["from"], edata["to"], edata["type"],
                       edata.get("condition", ""), edata.get("option_text", ""))
            self.graph.edges[eid] = edge
        self.graph.next_node_id = data["next_ids"]["node"]
        self.graph.next_edge_id = data["next_ids"]["edge"]

# ===================== 程序入口 =====================
if __name__ == "__main__":
    root = tk.Tk()
    graph = Graph()
    app = GraphView(root, graph)
    root.mainloop()