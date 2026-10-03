#!/usr/bin/env python
"""
P3-13：把"什么都不说"的 `except Exception` 补上 `swallow(...)`。

**为什么整段重写成"文本锚定"**（第一版栽了三次，都记在下面）
------------------------------------------------------------
三次失败全部来自"用 `ast` 的行号去改文本"：

  1. `ExceptHandler.lineno` **不是** `except` 关键字那一行 ——
     实测它指向 **handler 体的第一行**（`renpy_parser.py`：
     `lineno=3008` 是 `lines = fp.read_text(...)`，`except` 在 3009）。
     于是"给 except 行补 `as e`"改到了 `try` 体里，产物成
     `try: … as e` —— **语法错误**。
  2. 插 import 时用"最后一个 `^(from|import) …$` 行"，会插进
     **多行 import 的括号里**（4 个文件语法错误）。
  3. `except Exception:   # noqa` 冒号后有注释，"按 `:` 结尾"的断言直接崩。

结论：**这类批量改写只认文本**，`ast` 只用来**判定**（哪些 handler 是
"什么都不说"的），不用来定位行。所以本脚本：

  · 逐行找 `^\\s*except (+Exception|BaseException)\\b…:`；
  · 用 `ast` 建一个 `{体首行 → 是否静默}` 的映射来**判定**；
  · 插入/改写全部按**行索引**做，改完 `ast.parse` 自检。

判据（与 `tools/_audit_silent_except.py` 同源）：体**既没用异常对象、
也没有任何调用** → "什么都不说"。

**只加痕迹、不改行为**：体首插 `swallow('<模块>.<函数>', e)`。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'storygraph'

#: 故意不动的**文件**（+ 为什么）。
#:
#: `core/safeguard.py` 是 `swallow` 自己的家：
#:   · 它唯一的 `except: pass` 是**防递归**的（再调 `swallow` 会无限递归）；
#:   · 它也不需要 import 自己。
#:
#: 第一版用"整行签名"当键 —— 行内空格一变就对不上，结果既给它插了
#: `swallow(...)`（**无限递归**）、又插了 `from storygraph.core.safeguard
#: import swallow`（`ImportError`）。改成**按文件**跳过，不可能再对不上。
SKIP_FILES = {'core/safeguard.py'}

EXCEPT_RE = re.compile(
    r'^(?P<pad>\s*)except\s+(?P<typ>Exception|BaseException)'
    r'(?:\s+as\s+(?P<var>\w+))?\s*:')
IMPORT_RE = re.compile(r'^from storygraph\.core\.safeguard import ([^\n]+)$',
                       re.M)
SAFEGUARD_IMPORT = 'from storygraph.core.safeguard import swallow'


def _silent_body_lines(src: str) -> set:
    """`ast`：返回"体是静默的"那些 handler 的**体首行号**集合。"""
    out = set()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        if ast.unparse(node.type) not in ('Exception', 'BaseException'):
            continue
        body_src = '\n'.join(ast.unparse(st) for st in node.body)
        if any(isinstance(n, ast.Call)
               for st in node.body for n in ast.walk(st)):
            continue                      # 有调用 → 说了什么
        if node.name and (f'{{{node.name}}}' in body_src
                          or f'{node.name}.' in body_src
                          or f' {node.name}' in body_src
                          or f'({node.name}' in body_src
                          or f', {node.name}' in body_src):
            continue                      # 用了异常对象 → 说了什么
        if node.body:
            out.add(node.body[0].lineno)
    return out


def _is_silent_at(lines: list, silent_starts: set, except_idx: int) -> bool:
    """
    `lines[except_idx]` 是 `except ...:` —— 它的体是不是"什么都不说"？

    **容错匹配是必须的**：`ast` 报的 `body[0].lineno` 在这个 Python 构建上
    **差一行**。实测 `renpy_parser.py` 那个 handler：

        lines[3007] = 'try:'                    （0 基）
        lines[3008] = 'except Exception:'        ← except_idx
        lines[3009] = 'continue'                 ← 真正的体首行

        ast: handler.lineno = 3008（= `try` 那一行）
             body[0].lineno = 3009（= **`except` 那一行**！）
             body[0] 是 `Continue`

    也就是说 `Continue` 的 `lineno` 被报成了 `except` 自己那一行。
    只按"体首行精确匹配"会漏掉它（39 vs 88 就是这么来的）。
    所以：找工作 `except` 之后的**第一条非空非注释行**，
    它本身或它下一行命中都算。
    """
    j = except_idx + 1
    while j < len(lines) and (not lines[j].strip()
                              or lines[j].lstrip().startswith('#')):
        j += 1
    # j = 真正的体首行（0 基）→ 1 基是 j+1；再容一行给上面那个偏差
    return (j + 1) in silent_starts or (j + 2) in silent_starts


#: 需要"向下找体首行"时最多跳几个空行/注释行
LOOKAHEAD = 6


def _module_of(rel: str) -> str:
    return rel[:-3].replace('/', '.')


def _enclosing_func(src: str, line_no: int) -> str:
    """`line_no`（1 基）落在哪个函数里（取最内层）。"""
    tree = ast.parse(src)
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= line_no <= end:
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else '<module>'


def _import_insert_index(src: str) -> int:
    """
    import 块结束处的**行号（1 基）**。

    用 `ast` 只算"最后一个顶层 import 的结束行" —— 这是**可靠**的用法
    （`Import.end_lineno` 就是那一行），不像 `ExceptHandler.lineno` 那样反直觉。

    ⚠ **必须传"原来的源码文本"**，不能传"已经被改过的 `lines`"：
    第一版传的是 `lines`（插入 `swallow` 之后的状态），它重新 `join` 起来
    以后续行反斜杠会出问题 —— `_debug = str(...) not in \\` 那一行
    直接解析失败（`safeguard.py` 里就有这种续行）。
    """
    tree = ast.parse(src)
    last = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = max(last, node.end_lineno or node.lineno)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        else:
            break
    return last


def _tidy_except_alignment(text: str) -> str:
    """
    把注释对齐到**同一层级其他 except 行**的注释列。

    为什么要这一手：插入 `as e` 之后注释往右顶了一截，同一个模块里出现

        except Exception:                 # noqa: BLE001
        except Exception as e:            # noqa: BLE001   ← 参差

    不影响功能（也不影响 flake8：`noqa` 的位置无所谓），但 200 处参差
    全是 diff 噪声。**判据按缩进分组**取众数 —— 同一个文件里卡片页的
    handler 缩进 4、嵌套里的缩进 12，用全局众数会把其中一组拉歪。
    """
    lines = text.splitlines(keepends=True)

    def split_comment(ln: str):
        body = ln.rstrip('\n')
        # 只在"没有引号"的情况下按 `#` 切（这些行都是 except 子句，简单可靠）
        i = body.find('#')
        if i < 0:
            return None
        code = body[:i]
        return code, body[i:], ('\n' if ln.endswith('\n') else '')

    # 按"缩进层级"收集注释起始列
    groups = {}
    for ln in lines:
        if not ln.lstrip().startswith('except'):
            continue
        got = split_comment(ln)
        if not got:
            continue
        code, comment, _nl = got
        key = len(code) - len(code.lstrip())
        groups.setdefault(key, []).append(len(code.rstrip()))

    best = {}
    for key, cols in groups.items():
        best[key] = max(set(cols), key=cols.count)

    out = []
    for ln in lines:
        if not ln.lstrip().startswith('except'):
            out.append(ln)
            continue
        got = split_comment(ln)
        if not got:
            out.append(ln)
            continue
        code, comment, nl = got
        code = code.rstrip()
        key = len(code) - len(code.lstrip())
        pad = ' ' * max(1, best.get(key, 1) - len(code) + 2)
        out.append(f'{code}{pad}{comment}{nl}')
    return ''.join(out)


def process(p: Path, apply: bool) -> tuple:
    """返回 (改动数, 跳过数, 报错列表)。"""
    rel = p.relative_to(PKG).as_posix()
    if rel in SKIP_FILES:
        return 0, 0, []
    src = p.read_text(encoding='utf-8')
    lines = src.splitlines(keepends=True)
    try:
        silent_starts = _silent_body_lines(src)
    except SyntaxError as e:
        return 0, 0, [f'{rel}: 解析不了 {e}']

    todo = []
    for i, raw in enumerate(lines):
        m = EXCEPT_RE.match(raw)
        if not m:
            continue
        # 找 handler 体首行：从下一行起跳过空行/注释行
        if not _is_silent_at(lines, silent_starts, i):
            continue                      # 不静默 → 已经有痕迹了
        j = i + 1
        while j < len(lines) and (not lines[j].strip()
                                  or lines[j].lstrip().startswith('#')):
            j += 1
        if j >= len(lines):
            continue
        todo.append((i, j, m))

    if not apply or not todo:
        return len(todo), 0, []

    # —— 从后往前改，行号不漂 ——
    for i, j, m in sorted(todo, key=lambda t: -t[0]):
        pad = ' ' * (len(lines[i]) - len(lines[i].lstrip()) + 4)
        ctx = f'{_module_of(rel)}.{_enclosing_func(src, i + 1)}'
        need_as_e = m.group('var') is None
        if need_as_e:
            raw = lines[i].rstrip('\n')
            nl = '\n' if lines[i].endswith('\n') else ''
            head, sep, tail = raw.partition(':')
            assert sep, f'{rel}:{i+1} 没有冒号：{raw!r}'
            lines[i] = f'{head.rstrip()} as e:{tail}{nl}'
        # `except: pass` → 只留 swallow（不留死 pass）
        if lines[j].strip() == 'pass':
            lines[j] = ''
        lines.insert(j, f"{pad}swallow('{ctx}', e)\n")

    text = ''.join(lines)
    if rel == 'core/safeguard.py':
        pass                              # ← 它就是 swallow 的家，不给自己加 import
    elif not IMPORT_RE.search(text):
        # 注意：用**原始** `src` 算插入点（改过的 lines 重新 join 会破坏续行）
        idx = _import_insert_index(src)
        ins = (SAFEGUARD_IMPORT + '\n') if idx == 0 else \
            ('\n' + SAFEGUARD_IMPORT + '\n')
        lines.insert(idx, ins)
        text = ''.join(lines)
    elif 'swallow' not in IMPORT_RE.search(text).group(1):
        m2 = IMPORT_RE.search(text)
        text = (text[:m2.start(1)] + 'swallow, ' + m2.group(1)
                + text[m2.end(1):])

    try:
        ast.parse(text)
    except SyntaxError as e:
        return 0, 0, [f'{rel}: 改完不可解析 {e}']
    p.write_text(text, encoding='utf-8')
    return len(todo), 0, []


def main(apply: bool) -> int:
    total = skipped = 0
    errors = []
    for p in sorted(PKG.rglob('*.py')):
        n, s, errs = process(p, apply)
        if n:
            print(f'  {p.relative_to(PKG)}  {n} 处')
        total += n
        skipped += s
        errors += errs
    print(f'\n{"已改" if apply else "待改"} {total} 处')
    for e in errors:
        print('**', e)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(apply='--apply' in sys.argv))
