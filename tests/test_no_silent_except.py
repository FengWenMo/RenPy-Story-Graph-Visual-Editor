"""
P3-13 守卫：**不许有"什么都不说"的 `except Exception`**
=====================================================

用户 2026-10-05 拿审计报告来问：

> 错误处理靠压制：321 个 except 里 99 个 body 只有 pass，
> 全项目 196 处 `# noqa: BLE001`

数字属实。审计报告的建议是"把 `except` 收窄成具体异常" ——
**这一版没照做**，理由写在 `storygraph/core/safeguard.py` 的开头：

  · 那些位置**确实需要兜住一切**（清理、Qt 控件已销毁、可选缓存写不进去、
    第三方表格式千奇百怪）—— 收窄成 `except ValueError` 只会让真正的
    异常逃到上层，变成"整个界面崩一下"，比现在更糟；
  · 真正的毛病不是"宽"，而是**一出事什么都没有**：
    用户看到"功能没反应"，我们连一行痕迹都没有。

所以处理方式是**保留宽捕获、补上痕迹**：`except Exception as e:` +
`swallow('<模块>.<函数>', e)`。`swallow` 写进一个有界环形缓冲，
`report()` 给控制台 / 体检 / 测试用。

本文件守的就是这条规则，判据与 `tools/_audit_silent_except.py` 同源：

    **一个 `except Exception` 体，既没用异常对象、也没有任何调用**
    → "什么都不说" → 红。

为什么是"没有调用"而不是"没有 `swallow`"：`self._status(f'…{e}')`、
`logging.warning(...)`、`_emit(ENGINE_ERROR, ...)` 都是**说了**
（用户/开发者看得到、或者进了事件流），不该被判违规。
只认 `swallow` 会把它们全红掉，那这个守卫就没人愿意留。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / 'storygraph'

#: **故意**允许"什么都不说"的地方。每条都要写清为什么。
#: 这个集合的终点是**空**（现在是空的）。
ALLOWED: dict = {
    ('core.safeguard', 'swallow'): (
        '`swallow` **自己**的兜底。它就在错误处理路径上 —— '
        '记录失败时再调一次自己，万一又失败就是**无限递归**。'
        '所以这里只能是空的 `pass`，是全项目唯一允许的。'
        '（`test_safeguard_itself_is_the_only_allowed_pass` 另外钉住'
        '"只有一个、且旁边写了理由"。）'),
}

#: 宽捕获的类型（只查这两种；`except ValueError:` 那种窄的不管）
WIDE = ('Exception', 'BaseException')


def _iter_py():
    return sorted(PKG.rglob('*.py'))


def _module_of(p: Path) -> str:
    return p.relative_to(PKG).with_suffix('').as_posix().replace('/', '.')


def _enclosing(p: Path, src: str, line_no: int) -> str:
    tree = ast.parse(src)
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= line_no <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else '<module>'


def _is_silent(node: ast.ExceptHandler) -> bool:
    """
    体**既没用异常对象、也没有任何调用** → 什么都不说。

    两处细节都是被实测逼出来的：

      · 只看 `node.body`（**不能** `ast.walk(node)`）——
        那会把 `node.type`（`Exception` 这个名字）也算进来；
      · f-string 里的 `{e}` 要能认出来（`_status(f'失败：{e}')` 是说了的），
        所以按 `'{e}'` / `'e.'` / `' e'` / `'(e'` / `', e'` 找一圈。
    """
    body_src = '\n'.join(ast.unparse(st) for st in node.body)
    if any(isinstance(n, ast.Call) for st in node.body for n in ast.walk(st)):
        return False
    if node.name and (f'{{{node.name}}}' in body_src
                      or f'{node.name}.' in body_src
                      or f' {node.name}' in body_src
                      or f'({node.name}' in body_src
                      or f', {node.name}' in body_src):
        return False
    return True


def _find_silent() -> list:
    bad = []
    for p in _iter_py():
        src = p.read_text(encoding='utf-8')
        try:
            tree = ast.parse(src)
        except SyntaxError as e:          # 解析不了本身就是错
            bad.append((p, 0, '<unparsable>', str(e)))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            if ast.unparse(node.type) not in WIDE:
                continue
            if not _is_silent(node):
                continue
            key = (_module_of(p), _enclosing(p, src, node.lineno))
            if key in ALLOWED:
                continue
            body = '; '.join(ast.unparse(st) for st in node.body)[:60]
            bad.append((p, node.lineno, key[1], body))
    return bad


class TestNoSilentWideExcept:

    def test_no_handler_swallows_without_a_trace(self):
        """
        宽捕获**必须留下痕迹**：用 `e`，或者至少调一次什么。

        这一条是这次改动的**净收益**：改之前 88 处是真的什么都不说
        （`except Exception: return None` / `continue` / `x = None`），
        现在每一处都进了 `swallow` 的环形缓冲 ——
        出事时 `safeguard.report()` 能看到"哪一块、多少次、最后一条是什么"。
        """
        bad = _find_silent()
        assert not bad, (
            '这些 `except Exception` 体里既没用异常对象、也没有任何调用，'
            '出事时不会有任何痕迹：\n'
            + '\n'.join(f'  {p.relative_to(PKG)}:{ln}  {fn}()  {body!r}'
                        for p, ln, fn, body in bad[:25])
            + f'\n（共 {len(bad)} 处）\n\n'
            '改法：体首加 `swallow(\'<模块>.<函数>\', e)`（见 '
            'storygraph/core/safeguard.py）。确实是"故意什么都不说"的，'
            '写进本文件的 ALLOWED 并说明理由。')

    def test_safeguard_itself_is_the_only_allowed_pass(self):
        """
        `core/safeguard.py` 里那个 `except: pass` 是**唯一**允许的空实现
        （`swallow` 自己就在错误处理路径上，再调自己会无限递归）。
        它必须带说明注释 —— 不然下一个人会照着它抄。
        """
        p = PKG / 'core' / 'safeguard.py'
        src = p.read_text(encoding='utf-8')
        tree = ast.parse(src)
        passes = [n for n in ast.walk(tree)
                  if isinstance(n, ast.ExceptHandler)
                  and all(isinstance(st, ast.Pass) for st in n.body)]
        assert len(passes) == 1, (
            f'safeguard.py 里"体全是 pass"的 except 有 {len(passes)} 个，'
            f'应该恰好 1 个（swallow 的兜底）')
        # 附近要有解释
        lines = src.splitlines()
        lo = passes[0].lineno
        around = '\n'.join(lines[max(0, lo - 3):lo + 4])
        assert '递归' in around or '错误处理路径' in around, (
            f'那个 pass 旁边没写为什么，别人会照着抄：\n{around}')

    def test_the_count_is_what_we_think_it_is(self):
        """
        总数抽查：宽捕获数量不该**减少**（减少说明有人把 `except` 收窄了 ——
        那是审计报告建议、但我们**刻意没做**的改动，得有人来解释）。
        """
        total = 0
        for p in _iter_py():
            tree = ast.parse(p.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.ExceptHandler) and node.type
                        and ast.unparse(node.type) in WIDE):
                    total += 1
        assert total >= 250, (
            f'全项目宽捕获只剩 {total} 处（2026-10-05 是 258）—— '
            f'如果是有意把某些收窄成具体异常，请更新这条下限并在 '
            f'docs/问题清单与优化路线_2026-10-03.md 里记一笔')

    def test_swallow_import_is_present_where_used(self):
        """
        用了 `swallow` 的文件必须**真的 import 了**它。

        这条抓的是一次真实事故：批处理脚本给某文件插了 `swallow(...)`
        但没插 import（那个文件原本没有 `safeguard` 的 import），
        `ast.parse` **照样通过**（名字解析是运行期的事），
        只有跑到那条 except 才 `NameError`。
        """
        missing = []
        for p in _iter_py():
            src = p.read_text(encoding='utf-8')
            if 'safeguard' in src and p.name == 'safeguard.py':
                continue                      # 它自己就是定义处
            tree = ast.parse(src)
            calls = any(
                isinstance(n, ast.Call)
                and ((isinstance(n.func, ast.Name) and n.func.id == 'swallow')
                     or (isinstance(n.func, ast.Attribute)
                         and n.func.attr == 'swallow'))
                for n in ast.walk(tree))
            if not calls:
                continue
            imported = any(
                (isinstance(n, ast.ImportFrom) and n.module
                 and n.module.endswith('safeguard')
                 and any(a.name == 'swallow' for a in n.names))
                or (isinstance(n, ast.Import)
                    and any(a.name.endswith('safeguard') for a in n.names))
                for n in ast.walk(tree))
            if not imported:
                missing.append(str(p.relative_to(PKG)))
        assert not missing, (
            f'这些文件调了 `swallow` 却没有 import 它（跑到那条 except 才 '
            f'NameError）：{missing}')


class TestSafeguardIsWired:

    def test_report_is_importable_from_the_package(self):
        """`safeguard` 是 core 层公开工具，别被挪走或改名。"""
        from storygraph.core.safeguard import (clear, contexts, recent,  # noqa
                                              report, swallow, total)
        assert callable(swallow) and callable(report) and callable(total)
        _ = (clear, contexts, recent)

    def test_a_swallowed_error_shows_up_in_the_report(self):
        """端到端：吞一次 → `report()` 里能看到它（这才是"有痕迹"的意义）。"""
        from storygraph.core import safeguard

        safeguard.clear()
        try:
            raise ValueError('演示用的失败')
        except Exception as e:                # noqa: BLE001
            safeguard.swallow('tests.demo.context', e)
        text = safeguard.report()
        assert 'tests.demo.context' in text, text
        assert 'ValueError' in text, text
        safeguard.clear()

    @pytest.mark.parametrize('bad', [None, '字符串', 123, object()])
    def test_swallow_never_raises_even_with_garbage(self, bad):
        """`swallow` 自己绝不许抛 —— 它就在错误处理路径上。"""
        from storygraph.core import safeguard
        safeguard.swallow('tests.garbage', bad)
        safeguard.clear()
