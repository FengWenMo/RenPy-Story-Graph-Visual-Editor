# analysis.py
import math
import ast
from models import Graph
from utils import extract_variables, collect_all_variables


# ===================== D1/D2 辅助函数 =====================

def _flip_cmp_op(op):
    """把 a <op> b 翻转为 b <op'> a 时使用的反向运算符。"""
    if isinstance(op, ast.Lt):
        return ast.Gt()
    if isinstance(op, ast.LtE):
        return ast.GtE()
    if isinstance(op, ast.Gt):
        return ast.Lt()
    if isinstance(op, ast.GtE):
        return ast.LtE()
    return op  # Eq / NotEq 对称


def _negate_cmp_op(op):
    """逻辑否定下的比较运算符（D2）。"""
    if isinstance(op, ast.Eq):
        return ast.NotEq()
    if isinstance(op, ast.NotEq):
        return ast.Eq()
    if isinstance(op, ast.Lt):
        return ast.GtE()
    if isinstance(op, ast.LtE):
        return ast.Gt()
    if isinstance(op, ast.Gt):
        return ast.LtE()
    if isinstance(op, ast.GtE):
        return ast.Lt()
    return op


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
        # D7: 任一端或乘数含无穷时，可能产生 inf * 0 = nan 或 nan 比较，
        # 保守返回 TOP，避免污染后续区间运算
        if math.isinf(delta) or math.isinf(self.lo) or math.isinf(self.hi):
            return ValueRange(-math.inf, math.inf)
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
        # D6: bool 是 int 的子类，必须先判 bool，否则第二个分支是死代码
        if isinstance(val, bool):
            return ValueRange(int(val), int(val))
        if isinstance(val, (int, float)):
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
            return ValueRange(1, 1) if operand == ValueRange(0, 0) else ValueRange(0, 1)
        return ValueRange(-math.inf, math.inf)

    def _eval_binop(self, node):
        left = self.eval(node.left)
        right = self.eval(node.right)
        if left.is_top() or right.is_top() or left.is_bottom() or right.is_bottom():
            return ValueRange(-math.inf, math.inf)

        lo1, hi1 = left.lo, left.hi
        lo2, hi2 = right.lo, right.hi

        # D5: 半无限区间参与运算可能产生 nan（如 inf - inf、inf * 0），
        # 保守返回 TOP，不参与运算
        if any(math.isinf(v) for v in (lo1, hi1, lo2, hi2)):
            return ValueRange(-math.inf, math.inf)

        if isinstance(node.op, ast.Add):
            return ValueRange(lo1 + lo2, hi1 + hi2)
        if isinstance(node.op, ast.Sub):
            return ValueRange(lo1 - hi2, hi1 - lo2)
        if isinstance(node.op, ast.Mult):
            candidates = [lo1 * lo2, lo1 * hi2, hi1 * lo2, hi1 * hi2]
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
                # D8: 原 else 分支内部重复同一条件，恒为假，已删除
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
    # 链式比较暂不处理（如 0 < x < 10）
    if len(node.ops) != 1:
        return True

    left_ast = node.left
    op = node.ops[0]
    right_ast = node.comparators[0]

    # D1: 归一化到 Name 在左。Constant < Name 翻转为 Name > Constant
    if isinstance(right_ast, ast.Name) and not isinstance(left_ast, ast.Name):
        left_ast, right_ast = right_ast, left_ast
        op = _flip_cmp_op(op)

    if not isinstance(left_ast, ast.Name):
        return True

    left_name = left_ast.id
    rng = state.ranges.get(left_name, ValueRange(-math.inf, math.inf))

    if isinstance(op, ast.Eq):
        if isinstance(right_ast, ast.Constant) and isinstance(right_ast.value, (int, float)):
            val = int(right_ast.value)
            if rng.is_bottom() or rng.is_top():
                return True
            if rng.lo > val or rng.hi < val:
                return False
        elif isinstance(right_ast, ast.Name):
            rng2 = state.ranges.get(right_ast.id, ValueRange(-math.inf, math.inf))
            if rng.is_bottom() or rng.is_top() or rng2.is_bottom() or rng2.is_top():
                return True
            if rng.hi < rng2.lo or rng.lo > rng2.hi:
                return False

    elif isinstance(op, ast.NotEq):
        if isinstance(right_ast, ast.Constant) and isinstance(right_ast.value, (int, float)):
            val = int(right_ast.value)
            if rng.lo == rng.hi == val:
                return False
        elif isinstance(right_ast, ast.Name):
            rng2 = state.ranges.get(right_ast.id, ValueRange(-math.inf, math.inf))
            if rng.is_const() and rng2.is_const() and rng.lo == rng2.lo:
                return False

    elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        if isinstance(right_ast, ast.Constant) and isinstance(right_ast.value, (int, float)):
            val = int(right_ast.value)
            if rng.is_bottom() or rng.is_top():
                return True
            if isinstance(op, ast.Lt) and rng.lo >= val:
                return False
            if isinstance(op, ast.LtE) and rng.lo > val:
                return False
            if isinstance(op, ast.Gt) and rng.hi <= val:
                return False
            if isinstance(op, ast.GtE) and rng.hi < val:
                return False
        elif isinstance(right_ast, ast.Name):
            rng2 = state.ranges.get(right_ast.id, ValueRange(-math.inf, math.inf))
            if rng.is_bottom() or rng.is_top() or rng2.is_bottom() or rng2.is_top():
                return True
            if isinstance(op, ast.Lt) and rng.lo >= rng2.hi:
                return False
            if isinstance(op, ast.LtE) and rng.lo > rng2.hi:
                return False
            if isinstance(op, ast.Gt) and rng.hi <= rng2.lo:
                return False
            if isinstance(op, ast.GtE) and rng.hi < rng2.lo:
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


def _ast_apply_cond(node, state, polarity=True):
    if isinstance(node, ast.BoolOp):
        if not polarity:
            # D2: 未做 De Morgan 化，否定下的 And/Or 保守不施加约束
            return True
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not _ast_apply_cond(v, state, True):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            original = state.copy()
            involved_vars = set()

            class VarCollector(ast.NodeVisitor):
                def visit_Name(self, node):
                    involved_vars.add(node.id)
                    self.generic_visit(node)

            collector = VarCollector()
            for v in node.values:
                collector.visit(v)

            merged_ranges = {}
            any_success = False
            for v in node.values:
                branch_state = original.copy()
                if _ast_apply_cond(v, branch_state, True):
                    any_success = True
                    for var in involved_vars:
                        if var in branch_state.ranges:
                            rng = branch_state.ranges[var]
                            if var not in merged_ranges:
                                merged_ranges[var] = rng.copy()
                            else:
                                merged_ranges[var] = merged_ranges[var].join(rng)
            if any_success:
                for var in involved_vars:
                    if var in merged_ranges:
                        state.ranges[var] = merged_ranges[var]
                    else:
                        state.ranges[var] = ValueRange(-math.inf, math.inf)
                return True
            else:
                return False

    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        # D2: 翻转极性递归处理操作数
        return _ast_apply_cond(node.operand, state, not polarity)

    elif isinstance(node, ast.Compare):
        return _ast_apply_compare(node, state, polarity)

    elif isinstance(node, ast.Constant):
        if polarity:
            return bool(node.value)
        else:
            return not bool(node.value)

    return True


def _ast_apply_compare(node, state, polarity=True):
    if len(node.ops) != 1:
        return True

    left_ast = node.left
    op = node.ops[0]
    right_ast = node.comparators[0]

    # D1: 归一化到 Name 在左
    if isinstance(right_ast, ast.Name) and not isinstance(left_ast, ast.Name):
        left_ast, right_ast = right_ast, left_ast
        op = _flip_cmp_op(op)

    # D2: 否定极性翻转运算符
    if not polarity:
        op = _negate_cmp_op(op)

    if not isinstance(left_ast, ast.Name):
        return True

    left_name = left_ast.id
    if left_name not in state.ranges:
        return True
    rng = state.ranges[left_name]
    if rng.is_top() or rng.is_bottom():
        return True
    lo, hi = rng.lo, rng.hi

    if (isinstance(op, ast.Eq) and isinstance(right_ast, ast.Constant)
            and isinstance(right_ast.value, (int, float))):
        val = int(right_ast.value)
        if lo > val or hi < val:
            return False
        state.ranges[left_name] = ValueRange(val, val)

    elif (isinstance(op, ast.NotEq) and isinstance(right_ast, ast.Constant)
            and isinstance(right_ast.value, (int, float))):
        val = int(right_ast.value)
        if lo == hi == val:
            return False
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

    elif (isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
            and isinstance(right_ast, ast.Constant)
            and isinstance(right_ast.value, (int, float))):
        val = int(right_ast.value)
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

        # 用出口状态判断，和主循环里的传播口径一致
        current = instate.copy()
        for op in node.operations:
            current = apply_operation(current, op)

        has_possible = False
        for edge in out_edges:
            if condition_possible(current, edge.condition):
                has_possible = True
                break

        if not has_possible:
            warnings.append(
                f"⚠️ 下落风险：{node.label_name} 的所有分支条件在当前变量范围内不可能满足"
            )

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