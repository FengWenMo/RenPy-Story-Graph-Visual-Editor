# utils.py
import ast


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


def collect_all_variables(graph):
    vars_set = set()
    for node in graph.nodes.values():
        for op in node.operations:
            vars_set.update(extract_variables(op))
    for edge in graph.edges.values():
        if edge.condition:
            vars_set.update(extract_variables(edge.condition))
    return vars_set


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