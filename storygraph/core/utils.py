# utils.py
import ast

from storygraph.core.statement import strip_marker

from storygraph.core.safeguard import swallow

# 排除：内建函数名与语言关键字
# （与旧正则版的 keywords 集合语义一致，仅补齐常用内建）
_BUILTINS_WHITELIST = frozenset({
    'abs', 'min', 'max', 'len', 'int', 'str', 'bool', 'float',
    'range', 'sum', 'any', 'all', 'round', 'sorted',
    'list', 'dict', 'set', 'tuple',
    'True', 'False', 'None',
})

_KEYWORDS = frozenset({
    'if', 'else', 'elif', 'and', 'or', 'not',
    'jump', 'call', 'return', 'pass',
})

#: **引擎 / 标准库的模块名**：它们可以被"读"，但绝不能当成剧情变量去
#: `default x = 0`。
#:
#: 2026-10-05 审计"双 IR / 两套生成器"时抓到的**真 bug**：控制流导出对
#: 每个收集到的名字都写一行 `default <名字> = 0`，而收集走的是
#: `extract_variables`（只认 AST 里的 Name），于是真实项目导出产物里有：
#:
#:     default renpy = 0        ← 把引擎模块遮蔽成 int！
#:     default persistent = 0
#:     default config = 0
#:     default time = 0
#:
#: `default renpy = 0` 之后，脚本里所有 `renpy.transition(...)` 都会
#: 变成"int 没有 transition 属性" —— 整章直接跑不起来。
_ENGINE_MODULES = frozenset({
    'renpy', 'persistent', 'config', 'store', 'gui', 'style', 'ui', 'audio',
    'preferences', '_preferences', 'defaults', 'nvl', 'build', 'iap',
    'achievement', 'layeredimage', 'main_menu', 'renpy_',
    # 常用标准库（项目里 `import random` / `import time` 之后就会出现在
    # 表达式里，同样不该被 default）
    'random', 'time', 'math', 'sys', 'os', 're', 'json', 'copy',
    'collections', 'itertools', 'functools', 'datetime', 'string',
})


class _VariableCollector(ast.NodeVisitor):
    """
    收集表达式 / 语句中的所有变量名。

    规则：
      · 收集 ast.Name（Load / Store / Del 均收）
      · 不收集 ast.Call 的 func —— 那是函数名，不是变量
      · 不收集内建函数名与关键字
      · 属性访问只收最内层 Name（`a.b.c` → `a`）
      · 关键字参数名（`f(x=1)` 里的 `x`）不收集
    """

    def __init__(self):
        self.names: list = []

    def visit_Name(self, node: ast.Name):
        if node.id in _BUILTINS_WHITELIST or node.id in _KEYWORDS:
            return
        self.names.append(node.id)

    def visit_Call(self, node: ast.Call):
        # 只访问参数，不访问 func（函数名不是变量）
        for arg in node.args:
            self.visit(arg)
        for kw in node.keywords:
            self.visit(kw.value)


def extract_variables(text):
    """
    从表达式或语句文本中提取变量名列表（保持首次出现顺序）。

    无法解析的文本返回空列表。

    语义要点：
      · 属性访问只收最内层：`a.b.c` → ['a']
      · 函数名不收：`abs(x)` → ['x']
      · 关键字参数名不收：`f(x=1)` → []
    """
    if not text or not text.strip():
        return []

    stripped = text.strip()
    tree = None
    for mode in ('eval', 'exec'):
        try:
            tree = ast.parse(stripped, mode=mode)
            break
        except SyntaxError:
            continue

    if tree is None:
        return []

    collector = _VariableCollector()
    collector.visit(tree)

    seen = set()
    result = []
    for name in collector.names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def node_code_texts(node) -> list:
    """
    节点上的代码文本（**结构化真源优先**）。

    迁移（2026-10-05）：`Node` 现在存 `list[Statement]`；这里只取"代码类"
    语句的文本。老对象（只有 `operations` 的鸭子类型）照样能喂进来。
    """
    stmts = getattr(node, 'statements', None)
    if stmts is None:
        return list(getattr(node, 'operations', []) or [])
    from storygraph.core.statement import is_code_statement, operation_source
    return [operation_source(s) for s in stmts if is_code_statement(s)]


#: 旧名（模块内部一直这么叫）
_node_code_texts = node_code_texts


def collect_all_variables(graph):
    vars_set = set()
    for node in graph.nodes.values():
        for op in _node_code_texts(node):
            vars_set.update(extract_variables(strip_marker(op)))
    for edge in graph.edges.values():
        if edge.condition:
            vars_set.update(extract_variables(edge.condition))
    return vars_set


class _DeclaredCollector(ast.NodeVisitor):
    """
    只收**被赋值的名字**（`Store` 目标），不是"出现过的名字"。

    与 `_VariableCollector` 的区别就是 `default` 该写谁：
      · `love += 1`          → love
      · `x = f(y)`           → x（y 只是被读）
      · `persistent.a = 1`   → 不要（属性赋值，不是 store 变量）
      · `for i in range(3):` → 不要 i（循环临时变量，每章 default 一个 i 没意义）
      · `renpy.transition(x)`→ 不要 renpy（那是模块）
    """

    def __init__(self):
        self.names: list = []
        self._loop_targets: set = set()

    def _add(self, node):
        if isinstance(node, ast.Name):
            name = node.id
            if name in _BUILTINS_WHITELIST or name in _ENGINE_MODULES:
                return
            if name.startswith('_'):
                return                    # 私有/引擎内部名
            if name not in self.names:
                self.names.append(name)
        # 元组解包 `a, b = f()`：两个都是新变量 ✓
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._add(elt)

    def visit_Assign(self, node):
        for t in node.targets:
            self._add(t)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._add(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._add(node.target)
        self.generic_visit(node)

    def visit_For(self, node):
        for n in ast.walk(node.target):
            if isinstance(n, ast.Name):
                self._loop_targets.add(n.id)
        self.generic_visit(node)

    def visit_comprehension(self, node):
        for n in ast.walk(node.target):
            if isinstance(n, ast.Name):
                self._loop_targets.add(n.id)
        self.generic_visit(node)

    def visit_Name(self, node):
        # 兜底：`with open() as f:` / `except E as e:` 这类绑定
        return


def declared_variables(ops=(), conditions=(), extra_ops=()) -> list:
    """
    一批 python op / 条件 → **可以安全 `default` 的剧情变量**（保持首次出现顺序）。

    只认"被赋值的名字"，并且把引擎/标准库模块名与循环临时变量剔掉 ——
    这就是 `default renpy = 0` / `default i = 0` 那类产物的修法
    （见 `_ENGINE_MODULES` 的说明）。
    """
    collector = _DeclaredCollector()
    texts = [strip_marker(op) for op in list(ops or ()) + list(extra_ops or ())]
    texts += [str(c or '') for c in (conditions or ())]
    for text in texts:
        text = str(text or '').strip()
        if not text:
            continue
        tree = None
        for mode in ('exec', 'eval'):
            try:
                tree = ast.parse(text, mode=mode)
                break
            except SyntaxError:
                continue
        if tree is None:
            continue
        try:
            collector.visit(tree)
        except Exception as e:                 # noqa: BLE001 - 半个 AST 也不该炸导出
            swallow('core.utils.declared_variables', e)
            continue
    return [n for n in collector.names if n not in collector._loop_targets]


def declared_variables_of_graph(graph) -> list:
    """整张图里**该 `default` 的变量**（`graph` → `declared_variables`）。"""
    ops, conds = [], []
    for node in graph.nodes.values():
        ops.extend(_node_code_texts(node))
    for edge in graph.edges.values():
        if getattr(edge, 'condition', ''):
            conds.append(edge.condition)
    return declared_variables(ops=ops, conditions=conds)


# ===================== 回归测试 =====================

def _run_extract_variables_tests():
    """
    手动运行：python -c "from utils import _run_extract_variables_tests as t; t()"
    或 `python utils.py`
    """
    # (输入, 应返回的集合)
    cases = [
        ('x = 10',                              {'x'}),
        ('x += 5',                              {'x'}),
        ('score = love + intelligence * 2',     {'score', 'love', 'intelligence'}),
        ('result = abs(value) + min(a, b)',     {'result', 'value', 'a', 'b'}),
        ('is_unlocked = love > 5 and money >= 100',
                                                {'is_unlocked', 'love', 'money'}),
        ('a.b.c',                               {'a'}),
        ('math.sqrt(x)',                        {'x'}),
        ('f(x=1)',                              set()),
        ('x = f(y) + g(z)',                     {'x', 'y', 'z'}),
        ('arr[i] = arr[j] + 1',                 {'arr', 'i', 'j'}),
        ('',                                    set()),
        ('   ',                                 set()),
        ('### 无效语法 ###',                     set()),
        ('x == 5',                              {'x'}),
    ]

    failures = []
    for text, expected in cases:
        try:
            got = set(extract_variables(text))
        except Exception as e:
            failures.append(f"{text!r}: 异常 {e}")
            continue
        if got != expected:
            failures.append(f"{text!r}: got {got}, want {expected}")

    if failures:
        print("测试失败：")
        for f in failures:
            print(f"  - {f}")
        return False

    print(f"全部 {len(cases)} 个用例通过。")
    return True


if __name__ == '__main__':
    _run_extract_variables_tests()