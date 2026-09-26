import tkinter as tk
from tkinter import simpledialog, messagebox, filedialog
import tkinter.scrolledtext as st
import re
import json
import math
import ast
import copy
import os
import subprocess
import sys
from collections import deque, defaultdict
import bisect
import threading
import queue
import time
import hashlib

# ===================== 数据结构 =====================
class Node:
    def __init__(self, node_id, label_name, x=100, y=100):
        self.id = node_id
        self.label_name = label_name
        self.x = x
        self.y = y
        self.is_current = False
        self.operations = []
        self.file_path = None
        self.line_number = None

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        result.id = self.id
        result.label_name = self.label_name
        result.x = self.x
        result.y = self.y
        result.is_current = self.is_current
        result.operations = copy.deepcopy(self.operations, memo)
        result.file_path = self.file_path
        result.line_number = self.line_number
        return result

class Edge:
    def __init__(self, edge_id, from_id, to_id, etype="jump", condition="", option_text=""):
        self.id = edge_id
        self.from_id = from_id
        self.to_id = to_id
        self.type = etype
        self.condition = condition
        self.option_text = option_text

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        result.id = self.id
        result.from_id = self.from_id
        result.to_id = self.to_id
        result.type = self.type
        result.condition = self.condition
        result.option_text = self.option_text
        return result

class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.next_node_id = 1
        self.next_edge_id = 1

    def copy(self):
        g = Graph()
        g.next_node_id = self.next_node_id
        g.next_edge_id = self.next_edge_id
        for nid, node in self.nodes.items():
            g.nodes[nid] = copy.deepcopy(node)
        for eid, edge in self.edges.items():
            g.edges[eid] = copy.deepcopy(edge)
        return g

    def add_node(self, label_name, x, y, operations=None, file_path=None, line_number=None):
        nid = self.next_node_id
        self.next_node_id += 1
        node = Node(nid, label_name, x, y)
        if operations:
            node.operations = operations
        if file_path:
            node.file_path = file_path
        if line_number:
            node.line_number = line_number
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
    tokens = re.findall(r'\b([a-zA-Z_]\w*)\b', text)
    keywords = {'if','else','elif','and','or','not','True','False','None','jump','call',
               'int','str','bool','float','len','abs','min','max','range','sum'}
    return [t for t in tokens if t not in keywords]

def collect_all_variables(graph):
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
        if self.is_bottom():
            return other.copy()
        if other.is_bottom():
            return self.copy()
        if self.is_top() or other.is_top():
            return ValueRange(-math.inf, math.inf)
        return ValueRange(min(self.lo, other.lo), max(self.hi, other.hi))

    def intersect(self, other):
        if self.is_bottom() or other.is_bottom():
            return ValueRange(1, -1)
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
        self.ranges = {}
        if var_names:
            for v in var_names:
                self.ranges[v] = ValueRange(0, 0)

    def copy(self):
        new_state = VariableState()
        new_state.ranges = {k: v.copy() for k, v in self.ranges.items()}
        return new_state

    def merge(self, other):
        keys = set(self.ranges.keys()) | set(other.ranges.keys())
        new_state = VariableState()
        for k in keys:
            r1 = self.ranges.get(k, ValueRange(-math.inf, math.inf))
            r2 = other.ranges.get(k, ValueRange(-math.inf, math.inf))
            new_state.ranges[k] = r1.join(r2)
        return new_state

    def set_top(self, var_name=None):
        if var_name:
            if var_name in self.ranges:
                self.ranges[var_name] = ValueRange(-math.inf, math.inf)
        else:
            for k in self.ranges:
                self.ranges[k] = ValueRange(-math.inf, math.inf)

    def is_bottom(self):
        if not self.ranges:
            return False
        return all(r.is_bottom() for r in self.ranges.values())

    def __str__(self):
        items = []
        for var, rng in sorted(self.ranges.items()):
            items.append(f"{var}: {rng}")
        return "{" + ", ".join(items) + "}"

class IntervalEvaluator:
    def __init__(self, state):
        self.state = state

    def eval(self, node):
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
            return ValueRange(0, 1)
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

def apply_operation(state, op_line):
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

# 条件分析：改进 NotEq 处理及 Or 分支状态合并
def condition_possible(state, cond_str):
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
            elif isinstance(comp, ast.Name):
                rng1 = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))
                rng2 = state.ranges.get(comp.id, ValueRange(-math.inf, math.inf))
                if rng1.is_const() and rng2.is_const() and rng1.lo == rng2.lo:
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
            # And 条件：依次应用，全部成功才为真
            for v in node.values:
                if not _ast_apply_cond(v, state):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            # Or 条件：至少一个分支成功，合并各分支状态
            original = state.copy()
            # 收集所有子条件中出现的变量名
            involved_vars = set()
            class VarCollector(ast.NodeVisitor):
                def visit_Name(self, node):
                    involved_vars.add(node.id)
                    self.generic_visit(node)
            collector = VarCollector()
            for v in node.values:
                collector.visit(v)

            merged_ranges = {}  # 存储 involved_vars 的合并结果
            any_success = False
            for v in node.values:
                branch_state = original.copy()
                if _ast_apply_cond(v, branch_state):
                    any_success = True
                    for var in involved_vars:
                        if var in branch_state.ranges:
                            rng = branch_state.ranges[var]
                            if var not in merged_ranges:
                                merged_ranges[var] = rng.copy()
                            else:
                                merged_ranges[var] = merged_ranges[var].join(rng)
            if any_success:
                # 更新原状态：仅更新 involved_vars，其余保持不变
                for var in involved_vars:
                    if var in merged_ranges:
                        state.ranges[var] = merged_ranges[var]
                    else:
                        state.ranges[var] = ValueRange(-math.inf, math.inf)
                return True
            else:
                return False
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        # 对 Not 条件，不更新状态，仅返回 True（表示可能满足）
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
            val = int(comp.value)
            if lo == hi == val:
                return False  # 单点且相等，条件为假
            # 排除边界值（若目标值恰为边界）
            if val == lo:
                if lo + 1 <= hi:
                    state.ranges[left_name] = ValueRange(lo + 1, hi)
                else:
                    return False
            elif val == hi:
                if lo <= hi - 1:
                    state.ranges[left_name] = ValueRange(lo, hi - 1)
                else:
                    return False
            # 若 val 在区间内部，保守保持原区间（无法用单区间表示）
            # 若 val 在区间外，无需处理

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


def analyze_reachability(graph, start_node_id):
    if start_node_id not in graph.nodes:
        return {}, ["起始节点不存在！"]

    all_vars = collect_all_variables(graph)
    entry_states = {}

    for nid in graph.nodes:
        bottom_state = VariableState(all_vars)
        for v in all_vars:
            bottom_state.ranges[v] = ValueRange(1, -1)
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
# ===================== Ren'Py 解析器 =====================
def parse_renpy_file(filepath, graph):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"读取失败: {filepath}, {e}")
        return

    # ---------- 辅助函数 ----------
    def get_indent(line):
        return len(re.match(r'^(\s*)', line).group(1))

    def combine_cond(cond1, cond2):
        """合并两个条件字符串，结果保证是合法 Python 表达式"""
        if not cond1:
            return cond2
        if not cond2:
            return cond1
        return f"({cond1}) and ({cond2})"

    # 第一遍：收集所有 label，检测重复
    label_lines = {}
    for idx, line in enumerate(lines):
        m = re.match(r'^\s*label\s+(\w+)\s*:', line)
        if m:
            lab = m.group(1)
            if lab in label_lines:
                print(f"警告：标签 '{lab}' 在文件 {filepath} 第 {label_lines[lab][0]} 行和第 {idx+1} 行重复定义，将使用第一个定义。")
                continue
            label_lines[lab] = (idx + 1, idx)  # (行号, 索引)
            existing = next((n for n in graph.nodes.values() if n.label_name == lab), None)
            if existing is None:
                nid = graph.add_node(lab, 200, 200)
                existing = graph.nodes[nid]
            existing.file_path = filepath
            existing.line_number = idx + 1

    current_label = None
    label_indent = -1

    def parse_block(start_idx, block_indent, accum_cond, current_label, graph):
        """
        解析一个代码块（缩进大于 block_indent 的行），返回下一个未处理的索引。
        accum_cond: 当前代码路径需要满足的累积条件（字符串，可能为空表示无条件）
        """
        i = start_idx
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                i += 1
                continue
            indent = get_indent(line)
            if indent <= block_indent:
                break  # 退出当前块

            # ---- 处理 if/elif/else 条件链 ----
            if re.match(r'^(\s*)(if|elif|else)\b', line):
                # 处理整个条件链（同一缩进层级的 if/elif/else）
                prev_branch_conds = []  # 存储前面分支的条件（不含 accum_cond）
                # 先处理第一个 if
                if_match = re.match(r'^(\s*)if\s+(.+?)\s*:\s*$', line)
                if if_match:
                    cond = if_match.group(2).strip()
                    prev_branch_conds.append(cond)
                    new_accum = combine_cond(accum_cond, cond)
                    i += 1  # 跳过 if 行
                    # 递归解析 if 子块
                    i = parse_block(i, indent, new_accum, current_label, graph)

                    # 继续处理同一条件链的 elif/else
                    while i < len(lines):
                        line2 = lines[i]
                        stripped2 = line2.strip()
                        if not stripped2 or stripped2.startswith('#'):
                            i += 1
                            continue
                        indent2 = get_indent(line2)
                        if indent2 != indent:
                            break  # 缩进不同，不属于同一条件链

                        elif_match = re.match(r'^(\s*)elif\s+(.+?)\s*:\s*$', line2)
                        if elif_match:
                            elif_cond = elif_match.group(2).strip()
                            neg_prev = " or ".join(f"({c})" for c in prev_branch_conds)
                            # 条件 = accum_cond and (elif_cond) and not (前面条件的析取)
                            combined = f"({accum_cond}) and ({elif_cond}) and not ({neg_prev})" if accum_cond else f"({elif_cond}) and not ({neg_prev})"
                            prev_branch_conds.append(elif_cond)
                            i += 1
                            i = parse_block(i, indent, combined, current_label, graph)
                            continue

                        else_match = re.match(r'^(\s*)else\s*:\s*$', line2)
                        if else_match:
                            neg_prev = " or ".join(f"({c})" for c in prev_branch_conds)
                            combined = f"({accum_cond}) and not ({neg_prev})" if accum_cond else f"not ({neg_prev})"
                            i += 1
                            i = parse_block(i, indent, combined, current_label, graph)
                            # else 是条件链的最后一个，跳出 while 循环
                            break

                        # 不是 elif/else，退出条件链
                        break
                continue  # 条件链处理完毕，继续外层循环

            # ---- 处理 menu 块 ----
            menu_match = re.match(r'^(\s*)menu\s*:', line)
            if menu_match:
                menu_indent = indent
                i += 1  # 跳过 menu 行
                while i < len(lines):
                    mline = lines[i]
                    mstripped = mline.strip()
                    if not mstripped or mstripped.startswith('#'):
                        i += 1
                        continue
                    mindent = get_indent(mline)
                    if mindent <= menu_indent:
                        break  # menu 结束
                    opt_match = re.match(r'^\s*"([^"]*)"(\s+if\s+(.+?))?\s*:', mline)
                    if opt_match:
                        option_text = opt_match.group(1)
                        opt_cond = opt_match.group(3).strip() if opt_match.group(3) else ""
                        # 菜单选项条件：累加 accum_cond 和 opt_cond
                        option_accum = combine_cond(accum_cond, opt_cond) if opt_cond else accum_cond
                        i += 1  # 跳过选项行
                        # 解析选项块内跳转
                        i = parse_block(i, mindent, option_accum, current_label, graph)
                    else:
                        # 非选项行，忽略
                        i += 1
                continue

            # ---- 处理 jump/call ----
            jump_match = re.match(r'^\s*(jump|call)\s+(\w+)', line)
            if jump_match:
                jtype, target = jump_match.group(1), jump_match.group(2)
                if not any(n.label_name == target for n in graph.nodes.values()):
                    graph.add_node(target, 200, 200)
                from_id = next((nid for nid, n in graph.nodes.items() if n.label_name == current_label), None)
                to_id = next((nid for nid, n in graph.nodes.items() if n.label_name == target), None)
                if from_id and to_id:
                    graph.add_edge(from_id, to_id, etype=jtype,
                                   condition=accum_cond if accum_cond else "",
                                   option_text="")
                i += 1
                continue

            # ---- 处理 $ 变量操作 ----
            m_dollar = re.match(r'^(\s*)\$\s+(.+)', line)
            if m_dollar:
                # 仅当 accum_cond 为空时（无条件块）才加入节点操作，避免条件分支内的赋值被无条件执行
                if not accum_cond:
                    node = next((n for n in graph.nodes.values() if n.label_name == current_label), None)
                    if node:
                        node.operations.append(m_dollar.group(2).strip())
                i += 1
                continue

            # ---- 处理 return ----
            return_match = re.match(r'^\s*return\s*$', line)
            if return_match:
                print(f"警告：在标签 '{current_label}' 中遇到 return 语句，当前版本忽略。")
                i += 1
                continue

            # 其他语句（如对话、pass 等）忽略
            i += 1
        return i

    # ---------- 主解析循环 ----------
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue

        # 检测 label 行
        lm = re.match(r'^(\s*)label\s+(\w+)\s*:', line)
        if lm:
            current_label = lm.group(2)
            label_indent = get_indent(line)
            i += 1
            # 解析该 label 的主体（缩进大于 label_indent 的行）
            i = parse_block(i, label_indent, "", current_label, graph)
            continue

        # 非 label 行且当前无 label（文件开头可能有注释等），忽略
        i += 1

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
        # 先执行操作
        for op in node.operations:
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

# ===================== 自动布局（优化版） =====================
def layout_graph(graph, start_id=None, layer_gap=180, node_gap=60, max_iter=10):
    if not graph.nodes:
        return

    # 构建邻接表，排除自环
    adj = {nid: [] for nid in graph.nodes}
    undirected_neighbors = {nid: set() for nid in graph.nodes}
    edges = []
    self_loops = []  # 记录自环节点
    for eid, edge in graph.edges.items():
        u, v = edge.from_id, edge.to_id
        if u == v:
            self_loops.append((u, v))
            continue
        adj[u].append(v)
        undirected_neighbors[u].add(v)
        undirected_neighbors[v].add(u)
        edges.append((u, v, eid))

    # 环破除
    def break_cycles(adj):
        """
        使用 Kahn 算法结合贪心选择反转边，以尽量少地破坏原图方向。
        输入：adj 为邻接表（字典，键为节点，值为出边目标列表）
        返回：需要反转的边集合（元素为 (u, v) 表示反转 u -> v 为 v -> u）
        """
        # 初始化入度
        indeg = {u: 0 for u in adj}
        for u in adj:
            for v in adj[u]:
                indeg[v] = indeg.get(v, 0) + 1

        # 存储反转的边
        reversed_edges = set()
        # 待处理队列（入度为0的节点）
        q = deque([u for u in indeg if indeg[u] == 0])
        processed = set()

        while q:
            u = q.popleft()
            processed.add(u)
            for v in adj.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)

        # 如果还有节点未处理，说明存在环
        remaining = [u for u in adj if u not in processed]
        while remaining:
            # 选择一个节点打破环（优先选择出度最小的节点）
            u = min(remaining, key=lambda x: len(adj.get(x, [])))
            # 选择 u 的一条出边（优先选择指向剩余节点的边）
            candidates = [v for v in adj.get(u, []) if v in remaining]
            if not candidates:
                # 如果 u 没有出边指向剩余节点，则任选一条出边
                candidates = adj.get(u, [])
            v = candidates[0] if candidates else None
            if v is None:
                # 无法处理，跳出（理论上不会发生）
                break
            # 反转边 u->v 为 v->u
            reversed_edges.add((u, v))
            # 更新邻接表和入度（实际反转）
            adj[u].remove(v)
            adj.setdefault(v, []).append(u)
            indeg[v] += 1
            indeg[u] -= 1
            if indeg[u] == 0:
                q.append(u)
            # 继续处理队列
            while q:
                node = q.popleft()
                processed.add(node)
                for nb in adj.get(node, []):
                    indeg[nb] -= 1
                    if indeg[nb] == 0:
                        q.append(nb)
            remaining = [x for x in adj if x not in processed]

        return reversed_edges

    reversed_edges = break_cycles(adj)

    # 构建 DAG
    dag_adj = {nid: set() for nid in graph.nodes}
    for u, v, _ in edges:
        if (u, v) in reversed_edges:
            dag_adj[v].add(u)
        else:
            dag_adj[u].add(v)

    # 确保 DAG
    def ensure_dag(dag_adj, nodes):
        while True:
            indeg = {u: 0 for u in nodes}
            for u in nodes:
                for v in dag_adj[u]:
                    indeg[v] += 1
            q = deque([u for u in nodes if indeg[u] == 0])
            processed = set()
            while q:
                u = q.popleft()
                processed.add(u)
                for v in dag_adj[u]:
                    indeg[v] -= 1
                    if indeg[v] == 0:
                        q.append(v)
            if len(processed) == len(nodes):
                break
            remaining = [u for u in nodes if u not in processed]
            cycle_edge = None
            for u in remaining:
                for v in dag_adj[u]:
                    if v in remaining:
                        cycle_edge = (u, v)
                        break
                if cycle_edge:
                    break
            if not cycle_edge:
                break
            u, v = cycle_edge
            dag_adj[u].discard(v)
            dag_adj[v].add(u)
        return dag_adj

    dag_adj = ensure_dag(dag_adj, list(graph.nodes))

    # 层级分配
    indeg = {u: 0 for u in graph.nodes}
    for u in graph.nodes:
        for v in dag_adj[u]:
            indeg[v] += 1
    # 指定 start_id 时，将其入度置0，但保留其他源节点
    if start_id is not None and start_id in indeg:
        indeg[start_id] = 0

    # 收集所有入度为0的节点作为初始层
    sources = [u for u in graph.nodes if indeg[u] == 0]
    q = deque(sources)
    layers = {u: 0 for u in sources}
    indeg2 = indeg.copy()
    while q:
        u = q.popleft()
        for v in dag_adj[u]:
            layers[v] = max(layers.get(v, 0), layers[u] + 1)
            indeg2[v] -= 1
            if indeg2[v] == 0:
                q.append(v)

    max_layer = max(layers.values()) if layers else 0
    for nid in graph.nodes:
        if nid not in layers:
            layers[nid] = max_layer + 1
            max_layer = max(max_layer, layers[nid])

    # 上移优化
    preds = {u: set() for u in graph.nodes}
    for u, v, _ in edges:
        if (u, v) in reversed_edges:
            preds[u].add(v)
        else:
            preds[v].add(u)

    for _ in range(2):
        changed = False
        for u in sorted(graph.nodes, key=lambda x: layers[x]):
            if not preds[u]:
                new_layer = 0
            else:
                max_pred_layer = max(layers[p] for p in preds[u])
                new_layer = max_pred_layer + 1
            if new_layer < layers[u]:
                if all(layers[v] > new_layer for v in dag_adj[u]):
                    layers[u] = new_layer
                    changed = True
        if not changed:
            break

    max_layer = max(layers.values()) if layers else 0

    # 虚拟节点插入
    virtual_info = {}
    layer_nodes_ext = {lv: [] for lv in range(max_layer + 1)}
    for nid, lv in layers.items():
        layer_nodes_ext[lv].append(nid)

    edge_segments = []
    for u, v, eid in edges:
        lu, lv = layers[u], layers[v]
        if lu == lv:
            continue
        low_node, high_node = (u, v) if lu < lv else (v, u)
        low_layer, high_layer = sorted((lu, lv))
        if high_layer - low_layer == 1:
            edge_segments.append((low_node, high_node, eid))
        else:
            prev = low_node
            for layer in range(low_layer + 1, high_layer):
                vid = f"__virt_{eid}_{layer}"
                virtual_info[vid] = eid
                layer_nodes_ext[layer].append(vid)
                edge_segments.append((prev, vid, eid))
                prev = vid
            edge_segments.append((prev, high_node, eid))

    # 初始顺序（包含虚拟节点）
    order_ext = {lv: [] for lv in range(max_layer + 1)}
    # BFS顺序
    visited_bfs = set()
    bfs_queue = deque(sources)
    while bfs_queue:
        u = bfs_queue.popleft()
        if u in visited_bfs:
            continue
        visited_bfs.add(u)
        order_ext[layers[u]].append(u)
        for v in dag_adj[u]:
            if v not in visited_bfs:
                bfs_queue.append(v)
    # 补充未访问节点
    for nid, lv in layers.items():
        if nid not in visited_bfs:
            order_ext[lv].append(nid)
    # 补充虚拟节点
    for lv in range(max_layer + 1):
        for nid in layer_nodes_ext[lv]:
            if nid not in order_ext[lv]:
                order_ext[lv].append(nid)
    # 排序
    for lv in order_ext:
        order_ext[lv].sort(key=lambda x: str(x))

    # 交叉计数
    def count_crossings(order):
        total = 0
        for lv in range(max_layer):
            left = order.get(lv, [])
            right = order.get(lv + 1, [])
            if not left or not right:
                continue
            pos_left = {nid: idx for idx, nid in enumerate(left)}
            pos_right = {nid: idx for idx, nid in enumerate(right)}
            groups = defaultdict(list)
            for a, b, _ in edge_segments:
                if a in pos_left and b in pos_right:
                    groups[pos_left[a]].append(pos_right[b])
                elif b in pos_left and a in pos_right:
                    groups[pos_left[b]].append(pos_right[a])
            sorted_seen = []
            inv = 0
            for li in sorted(groups.keys()):
                rights = groups[li]
                for r in rights:
                    pos = bisect.bisect_right(sorted_seen, r)
                    inv += len(sorted_seen) - pos
                for r in rights:
                    bisect.insort(sorted_seen, r)
            total += inv
        return total

    # 重心排序
    ext_neighbors = defaultdict(set)
    for a, b, _ in edge_segments:
        ext_neighbors[a].add(b)
        ext_neighbors[b].add(a)

    def is_virtual(node_id):
        return isinstance(node_id, str) and node_id.startswith("__virt_")

    def barycenter_once(order):
        new_order = {lv: list(order[lv]) for lv in order}
        # 向下
        for lv in range(1, max_layer + 1):
            prev = new_order.get(lv - 1, [])
            if not prev:
                continue
            pos_prev = {nid: idx for idx, nid in enumerate(prev)}
            bary = {}
            for nid in new_order.get(lv, []):
                neighbors = [nb for nb in ext_neighbors[nid] if nb in pos_prev]
                if neighbors:
                    weights = [0.5 if is_virtual(nb) else 1.0 for nb in neighbors]
                    total_weight = sum(weights)
                    weighted_vals = sorted(pos_prev[nb] * w for nb, w in zip(neighbors, weights))
                    if total_weight == 0:
                        bary[nid] = len(prev) / 2
                    else:
                        cum = 0
                        target = total_weight / 2
                        for val, nb, w in sorted(zip(weighted_vals, neighbors, weights)):
                            cum += w
                            if cum >= target:
                                bary[nid] = val / w if w != 0 else val
                                break
                        else:
                            bary[nid] = weighted_vals[-1] / weights[-1] if weights[-1] != 0 else weighted_vals[-1]
                else:
                    bary[nid] = len(prev) / 2
            new_order[lv] = sorted(new_order[lv],
                                   key=lambda n: (bary.get(n, len(prev)/2), str(n)))
        # 向上
        for lv in range(max_layer - 1, -1, -1):
            next_layer = new_order.get(lv + 1, [])
            if not next_layer:
                continue
            pos_next = {nid: idx for idx, nid in enumerate(next_layer)}
            bary = {}
            for nid in new_order.get(lv, []):
                neighbors = [nb for nb in ext_neighbors[nid] if nb in pos_next]
                if neighbors:
                    weights = [0.5 if is_virtual(nb) else 1.0 for nb in neighbors]
                    total_weight = sum(weights)
                    weighted_vals = sorted(pos_next[nb] * w for nb, w in zip(neighbors, weights))
                    if total_weight == 0:
                        bary[nid] = len(next_layer) / 2
                    else:
                        cum = 0
                        target = total_weight / 2
                        for val, nb, w in sorted(zip(weighted_vals, neighbors, weights)):
                            cum += w
                            if cum >= target:
                                bary[nid] = val / w if w != 0 else val
                                break
                        else:
                            bary[nid] = weighted_vals[-1] / weights[-1] if weights[-1] != 0 else weighted_vals[-1]
                else:
                    bary[nid] = len(next_layer) / 2
            new_order[lv] = sorted(new_order[lv],
                                   key=lambda n: (bary.get(n, len(next_layer)/2), str(n)))
        return new_order

    for _ in range(max_iter):
        new_order = barycenter_once(order_ext)
        changed = any(order_ext[lv] != new_order.get(lv, []) for lv in order_ext)
        order_ext = new_order
        if not changed:
            break

    # 全局交换优化
    best_cross = count_crossings(order_ext)
    for _ in range(2):
        improved_global = False
        for lv in range(max_layer + 1):
            layer = order_ext[lv]
            if len(layer) < 2:
                continue
            local_improved = True
            while local_improved:
                local_improved = False
                for i in range(len(layer) - 1):
                    layer[i], layer[i+1] = layer[i+1], layer[i]
                    new_cross = count_crossings(order_ext)
                    if new_cross < best_cross:
                        best_cross = new_cross
                        local_improved = True
                        improved_global = True
                        break
                    else:
                        layer[i], layer[i+1] = layer[i+1], layer[i]
                if local_improved:
                    pass
            if improved_global:
                break
        if not improved_global:
            break

    # 坐标分配
    coords = {}
    for lv, layer in order_ext.items():
        total_height = (len(layer) - 1) * node_gap
        start_y = -total_height / 2
        x = lv * layer_gap
        for i, nid in enumerate(layer):
            y = start_y + i * node_gap
            if nid in graph.nodes:
                coords[nid] = (x, y)

    for nid in graph.nodes:
        if nid not in coords:
            coords[nid] = (max_layer + 1, 0)

    # 处理自环：给自环节点一个额外偏移
    for u, v in self_loops:
        if u in coords:
            coords[u] = (coords[u][0], coords[u][1] + node_gap * 0.5)

    for nid, (x, y) in coords.items():
        graph.nodes[nid].x = x
        graph.nodes[nid].y = y

# ===================== 命令模式 =====================
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
        # 清除选中
        self.graph_view.selected_node = None
        self.graph_view.selected_edge = None
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        self.graph_view.graph.nodes[self.node_id] = self.removed_node
        for edge in self.removed_edges:
            self.graph_view.graph.edges[edge.id] = edge
        self.graph_view.on_graph_structure_changed()

class AddEdgeCommand(Command):
    def __init__(self, graph_view, from_id, to_id, etype, condition, option_text):
        self.graph_view = graph_view
        self.from_id = from_id
        self.to_id = to_id
        self.etype = etype
        self.condition = condition
        self.option_text = option_text
        self.edge_id = None

    def execute(self):
        self.edge_id = self.graph_view.graph.add_edge(
            self.from_id, self.to_id, self.etype, self.condition, self.option_text
        )
        self.graph_view.on_graph_structure_changed()

    def undo(self):
        if self.edge_id is not None:
            self.graph_view.graph.remove_edge(self.edge_id)
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

class UpdateNodeOperationsCommand(Command):
    def __init__(self, graph_view, node_id, new_ops):
        self.graph_view = graph_view
        self.node_id = node_id
        self.new_ops = list(new_ops)
        self.old_ops = None

    def execute(self):
        self.old_ops = list(self.graph_view.graph.nodes[self.node_id].operations)
        self.graph_view.graph.nodes[self.node_id].operations = list(self.new_ops)
        self.graph_view.on_graph_content_changed(self.node_id)

    def undo(self):
        self.graph_view.graph.nodes[self.node_id].operations = list(self.old_ops)
        self.graph_view.on_graph_content_changed(self.node_id)

class UpdateEdgeCommand(Command):
    def __init__(self, graph_view, edge_id, etype=None, condition=None, option_text=None):
        self.graph_view = graph_view
        self.edge_id = edge_id
        self.new_etype = etype
        self.new_condition = condition
        self.new_option_text = option_text
        self.old_etype = None
        self.old_condition = None
        self.old_option_text = None

    def execute(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        self.old_etype = edge.type
        self.old_condition = edge.condition
        self.old_option_text = edge.option_text
        if self.new_etype is not None:
            edge.type = self.new_etype
        if self.new_condition is not None:
            edge.condition = self.new_condition
        if self.new_option_text is not None:
            edge.option_text = self.new_option_text
        self.graph_view.on_graph_content_changed(edge.from_id)

    def undo(self):
        edge = self.graph_view.graph.edges[self.edge_id]
        edge.type = self.old_etype
        edge.condition = self.old_condition
        edge.option_text = self.old_option_text
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
            messagebox.showinfo("提示", "没有可撤销的操作")
            return
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)

    def redo(self):
        if not self.redo_stack:
            messagebox.showinfo("提示", "没有可重做的操作")
            return
        command = self.redo_stack.pop()
        command.execute()
        self.undo_stack.append(command)

# ===================== GraphView 前端 =====================
class GraphView:
    def __init__(self, root, graph):
        self.graph = graph
        self.root = root
        self.root.title("剧情树编辑器-织言片语")
        self.root.geometry("1400x900")

        self.offset_x = 0
        self.offset_y = 0
        self.scale = 1.0

        self.selected_node = None
        self.selected_edge = None
        self.drag_data = {"node": None, "start_x": 0, "start_y": 0, "old_x": 0, "old_y": 0}
        self.connect_from = None
        self.temp_line = None

        # 画布元素ID存储
        self.canvas_ids = {}       # nid -> (rect_id, text_id, op_text_id)
        self.edge_ids = {}         # eid -> line_id
        self.edge_arrow_ids = {}   # eid -> arrow_polygon_id
        self.edge_label_ids = {}   # eid -> list of text ids

        # 控制点缓存
        self.compute_edge_control_cache = {}

        # 异步任务
        self.result_queue = queue.Queue()
        self.busy_count = 0  # 任务计数
        self.lock = threading.Lock()  # 保护 busy_count

        # 分析缓存
        self.analysis_cache = None
        self.graph_hash = None
        self.variables_cache = None

        self.history = HistoryManager(self)

        self.setup_ui()
        self.redraw_all()
        self.root.after(100, self.process_queue)

    # ---------- UI构建 ----------
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

        tk.Button(btn_frame, text="导入Ren'Py文件", command=self.import_renpy).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="导出为Ren'Py", command=self.export_renpy).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="保存布局(JSON)", command=self.save_layout).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="加载布局", command=self.load_layout).pack(pady=2, fill=tk.X)
        tk.Button(btn_frame, text="清空图", command=self.clear_graph, bg="#F44336", fg="white").pack(pady=2, fill=tk.X)
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

        # 右键菜单
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="添加新章节", command=self.add_node)
        self.menu.add_command(label="设为当前章节", command=self.set_current)
        self.menu.add_command(label="编辑变量操作", command=self.edit_operations)
        self.menu.add_command(label="在编辑器中打开", command=self.open_label_in_editor, state="disabled")
        self.menu.add_separator()
        self.menu.add_command(label="自动布局", command=self.auto_layout)
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

    # ---------- 坐标转换 ----------
    def world_to_screen(self, wx, wy):
        return (wx + self.offset_x) * self.scale, (wy + self.offset_y) * self.scale

    def screen_to_world(self, sx, sy):
        return sx / self.scale - self.offset_x, sy / self.scale - self.offset_y

    # ---------- 绘图函数 ----------
    def redraw_all(self):
        """全量重绘"""
        self.canvas.delete("all")
        self.canvas_ids.clear()
        self.edge_ids.clear()
        self.edge_arrow_ids.clear()
        self.edge_label_ids.clear()
        self.compute_edge_control_cache.clear()

        # 画边
        for eid, edge in self.graph.edges.items():
            self.draw_edge(eid)
        # 画节点
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
        if node.operations:
            op_text_id = self.canvas.create_text(sx, sy + 15, text=f"{len(node.operations)} 操作",
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

        # 箭头
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

        # 标签
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
        # 检测双向边
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
        """更新所有节点和边的位置，不删除重建（用于平移和缩放）"""
        # 更新节点
        for nid, node in self.graph.nodes.items():
            if nid in self.canvas_ids:
                sx, sy = self.world_to_screen(node.x, node.y)
                w, h = 120, 60
                rect_id, text_id, op_text_id = self.canvas_ids[nid]
                self.canvas.coords(rect_id, sx-w/2, sy-h/2, sx+w/2, sy+h/2)
                self.canvas.coords(text_id, sx, sy-10)
                if op_text_id:
                    self.canvas.coords(op_text_id, sx, sy+15)
        # 更新边
        for eid, edge in self.graph.edges.items():
            if eid in self.edge_ids:
                self.update_edge_position(eid)
        # 控制点缓存可能因坐标变化而失效，但保留，因为缓存键包含坐标，新坐标会生成新键
        # 为防止无限膨胀，限制缓存大小
        if len(self.compute_edge_control_cache) > 1000:
            # 简单清理：删除最早的一半
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
        # 使用增量更新而不是全量重绘
        self.update_all_positions()
        self.scale_var.set(f"{int(self.scale*100)}%")

    # ---------- 局部更新 ----------
    def update_node_position(self, nid):
        """更新节点位置及其关联边"""
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

        # 清除相关控制点缓存
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
                self.redraw_all()  # 数量变化，只能全量重绘
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
        # 先重置所有
        for nid in self.graph.nodes:
            self.update_node_color(nid)
        for eid in self.graph.edges:
            self.update_edge_color(eid)
        # 再高亮选中
        if self.selected_node:
            self.update_node_color(self.selected_node)
        if self.selected_edge:
            self.update_edge_color(self.selected_edge)

    # ---------- 缓存失效 ----------
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
            if node.operations:
                op_text = f"{len(node.operations)} 操作"
            else:
                op_text = ""
            rect_id, text_id, op_text_id = self.canvas_ids[nid]
            if op_text_id:
                self.canvas.itemconfig(op_text_id, text=op_text)
            else:
                sx, sy = self.world_to_screen(node.x, node.y)
                op_text_id = self.canvas.create_text(sx, sy+15, text=op_text,
                                                    font=("微软雅黑", 8), fill="#666")
                self.canvas_ids[nid] = (rect_id, text_id, op_text_id)
        else:
            # 可能影响多条边（例如边属性改变），更新所有边
            for eid in self.graph.edges:
                self.update_edge_position(eid)
        self.update_selection_highlight()

    def on_current_changed(self):
        self.update_current_highlight()

    # ---------- 事件处理 ----------
    def on_left_down(self, event):
        nid = self.node_at(event.x, event.y)
        if nid is not None:
            self.selected_node = nid
            self.selected_edge = None
            if event.state & 0x0001:  # Shift
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
            return

        eid = self.edge_at(event.x, event.y)
        if eid is not None:
            self.selected_edge = eid
            self.selected_node = None
            self.update_selection_highlight()
            return

        self.selected_node = None
        self.selected_edge = None
        self.drag_data["node"] = None
        self.update_selection_highlight()

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
            self.update_node_position(nid)
        elif self.connect_from is not None and self.temp_line:
            sx, sy = self.world_to_screen(self.graph.nodes[self.connect_from].x,
                                         self.graph.nodes[self.connect_from].y)
            self.canvas.coords(self.temp_line, sx, sy, event.x, event.y)

    def on_left_up(self, event):
        if self.drag_data["node"] is not None:
            nid = self.drag_data["node"]
            old_x = self.drag_data["old_x"]
            old_y = self.drag_data["old_y"]
            new_x = self.graph.nodes[nid].x
            new_y = self.graph.nodes[nid].y
            if old_x != new_x or old_y != new_y:
                cmd = MoveNodeCommand(self, nid, old_x, old_y, new_x, new_y)
                # 注意：节点位置已经更新，但我们需要通过命令系统记录，这里可直接执行命令
                # 但命令的execute会再次设置位置，值相同，没问题
                self.history.execute_command(cmd)
            self.drag_data["node"] = None
            return

        if self.connect_from is not None:
            target_nid = self.node_at(event.x, event.y)
            if target_nid and target_nid != self.connect_from:
                from_id = self.connect_from
                to_id = target_nid
                # 弹出对话框
                self.show_edge_dialog(from_id, to_id)
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
            if node.file_path and node.line_number:
                self.menu.entryconfig("在编辑器中打开", state="normal")
            else:
                self.menu.entryconfig("在编辑器中打开", state="disabled")
        else:
            self.menu.entryconfig("在编辑器中打开", state="disabled")

        # 初始化右键平移
        self._last_pan_x = event.x
        self._last_pan_y = event.y

        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_right_drag(self, event):
        if not hasattr(self, '_last_pan_x'):
            self._last_pan_x = event.x
            self._last_pan_y = event.y
        dx = event.x - self._last_pan_x
        dy = event.y - self._last_pan_y
        self.offset_x += dx / self.scale
        self.offset_y += dy / self.scale
        self._last_pan_x = event.x
        self._last_pan_y = event.y
        self.update_all_positions()
        self.scale_var.set(f"{int(self.scale*100)}%")

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
        for op in node.operations:
            text_widget.insert(tk.END, op + '\n')
        text_widget.focus_set()

        def on_ok():
            lines = text_widget.get("1.0", tk.END).splitlines()
            new_ops = [l.strip() for l in lines if l.strip()]
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

    # ---------- 菜单动作 ----------
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
        start_nid = next((nid for nid, n in graph_copy.nodes.items() if n.is_current), None)
        if not start_nid:
            start_nid = list(graph_copy.nodes.keys())[0]
        def worker():
            try:
                layout_graph(graph_copy, start_id=start_nid)
                self.result_queue.put(("layout", graph_copy))
            except Exception as e:
                self.result_queue.put(("error", str(e)))
            finally:
                self.result_queue.put(("busy_end",))
        threading.Thread(target=worker, daemon=True).start()

    # ---------- 分析功能 ----------
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
        start_nid = next((nid for nid, n in graph_copy.nodes.items() if n.is_current), None)
        if not start_nid:
            start_nid = list(graph_copy.nodes.keys())[0]
        def worker():
            try:
                entry_states, warnings = analyze_reachability(graph_copy, start_nid)
                result_text = visualize_analysis_results(graph_copy, entry_states, warnings)
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
        start_nid = next((nid for nid, n in graph_copy.nodes.items() if n.is_current), None)
        if not start_nid:
            start_nid = list(graph_copy.nodes.keys())[0]
        def worker():
            try:
                entry_states, warnings = analyze_reachability(graph_copy, start_nid)
                report = self._build_detailed_report(graph_copy, entry_states, warnings)
                self.result_queue.put(("detailed_report", report))
            except Exception as e:
                self.result_queue.put(("error", str(e)))
            finally:
                self.result_queue.put(("busy_end",))
        threading.Thread(target=worker, daemon=True).start()

    def _build_detailed_report(self, graph, entry_states, warnings):
        report = "=" * 80 + "\n"
        report += "详细变量区间分析报告\n"
        report += "=" * 80 + "\n\n"
        all_vars = collect_all_variables(graph)
        report += f"检测到的变量 ({len(all_vars)}个): {', '.join(sorted(all_vars))}\n\n"
        for nid, node in graph.nodes.items():
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
        return report

    # ---------- 文件操作 ----------
    def import_renpy(self):
        file_path = filedialog.askopenfilename(filetypes=[("Ren'Py 文件", "*.rpy")])
        if file_path:
            cmd = ImportRenPyCommand(self, file_path)
            self.history.execute_command(cmd)

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

    # ---------- 异步队列处理 ----------
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

    # ---------- 撤销/重做 ----------
    def undo(self):
        self.history.undo()

    def redo(self):
        self.history.redo()

# ===================== 外部编辑器打开 =====================
def open_file_at_line(file_path, line_number):
    if not os.path.exists(file_path):
        messagebox.showerror("错误", f"文件不存在：{file_path}")
        return
    commands = []
    if sys.platform == "win32":
        commands.append(["code", "-g", f"{file_path}:{line_number}"])
        commands.append(["notepad++", f"-n{line_number}", file_path])
        commands.append(["subl", f"{file_path}:{line_number}"])
    elif sys.platform == "darwin":
        commands.append(["code", "-g", f"{file_path}:{line_number}"])
        commands.append(["subl", f"{file_path}:{line_number}"])
        commands.append(["open", "-a", "TextEdit", "--args", "-l", str(line_number), file_path])
    else:
        commands.append(["code", "-g", f"{file_path}:{line_number}"])
        commands.append(["subl", f"{file_path}:{line_number}"])
        commands.append(["xdg-open", file_path])
    for cmd in commands:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    try:
        if sys.platform == "win32":
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])
    except Exception as e:
        messagebox.showerror("错误", f"无法打开文件：{e}")

# ===================== 程序入口 =====================
if __name__ == "__main__":
    root = tk.Tk()
    graph = Graph()
    app = GraphView(root, graph)
    root.mainloop()