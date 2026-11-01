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

**2026-11-01 收紧（第十七轮技术债）**：上面那条"有没有调用"太松 ——
`set()`、`QColor(fallback)`、`text.setText(body)`、`_ = exc` 都是调用/提到了 `e`，
但**一个字的信息都没留下**。审计（`docs/宽泛except分类审计_2026-11-01.md`）
拿这条判据复算，24 处真正"静默咽 bug"里**只有 1 处会被判红**（`safeguard`
白名单）。所以判据改成：

    **体里必须有一条"记录通道"**（`swallow` / print / logging / 进
    errors·warnings 列表 / 状态字段 / 事件 emit / 用户可见的错误 /
    `raise` / 失败信封 / 把 `e` 拼进给人看的句子）→ 否则红。

改完之后：这 24 处已经全被修掉（一处保留白名单），仓库仍然全绿 ——
也就是说**这条守卫现在锁住了那一轮的成果**，谁再拿 `set()` 糊弄会当场红。
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

# ---------------------------------------------------------------------------
#  "记录通道"名字表 —— 与 `docs/宽泛except分类审计_2026-11-01.md` §1.3 同源，
#  改这里就等于改审计口径，两边必须一起动。
# ---------------------------------------------------------------------------
#: 日志方法名
LOG_METHODS = {'debug', 'info', 'warning', 'warn', 'error', 'exception',
               'critical', 'fatal'}
#: 进"错误通道"的列表名
ERROR_LISTS = {'errors', 'errs', 'failures', 'failure', 'failed', 'blocking',
               'problems', 'bad', 'issues'}
#: 进"警告/备注通道"的列表名
WARN_LISTS = {'warnings', 'warns', 'notes', 'note', 'messages', 'message',
              'diagnostics', 'skipped_files', 'skipped', 'unmatched', 'missing'}
#: 状态字段名（写进去 = 界面上会变，人看得到）
STATUS_NAMES = {'status', 'state', 'last_error', 'last_err', 'ok', 'failed',
                '_status', '_error', 'error_text', '_err', 'error_message',
                '_errtext', 'set_status', '_set_status', '_log', 'log'}
#: 事件 / 通知
EMIT_NAMES = {'_emit', 'emit', '_notify', 'notify_card_notice', 'notify',
              '_notice', 'say', '_append', '_report_swallowed'}
#: 直接弹给人看的错误
USER_ERROR_CALLS = {'QMessageBox.critical', 'QMessageBox.warning',
                    'QMessageBox.information'}


def _call_text(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except Exception:                     # pragma: no cover - ast 不该在这里炸
        return '<call>'


def _last_seg(text: str) -> str:
    return text.rsplit('.', 1)[-1]


def _mentions_exc_in_message(src: str, exc: str) -> bool:
    """
    异常对象**被拼进了一句给人看的话**（不是"出现过"）。

    这一条是 2026-11-01 收紧的重点：旧判据"体里出现 `e`"里，
    `_ = exc` 这种"让守卫闭嘴"的写法也算 —— 它一个字都没留下。
    现在只认三种真会出现在输出里的形态：f-string、`str(e)` / `repr(e)`、
    `'…%s' % e`。
    """
    if not exc:
        return False
    return (f'{{{exc}}}' in src or f'{{{exc}!' in src or f'{{{exc}.' in src
            or f'str({exc})' in src or f'repr({exc})' in src
            or f'% {exc}' in src or f'%({exc}' in src)


def _has_failure_envelope(st: ast.stmt) -> bool:
    """`return {'ok': False, 'error': …}` / `return Foo(error=…)` 这类失败信封。"""
    if not isinstance(st, ast.Return) or st.value is None:
        return False
    return _is_failure_payload(st.value)


#: 失败信封里会出现的关键字
FAIL_KEYS = frozenset({'ok', 'error', 'errors', 'reason', 'failed', 'blocking'})


def _is_failure_payload(node) -> bool:
    """一个表达式是不是"失败信封"的形状（dict 键 / 构造器关键字）。"""
    if isinstance(node, ast.Dict):
        return any(isinstance(k, ast.Constant) and isinstance(k.value, str)
                   and k.value in FAIL_KEYS for k in node.keys)
    if isinstance(node, ast.Call):
        return any(k.arg in FAIL_KEYS for k in node.keywords if k.arg)
    return False


def _has_trace(node: ast.ExceptHandler, swallow_names=('swallow',)) -> bool:
    """
    这个 handler 体里**有没有一条记录通道**。

    只看 `node.body`（不能 `ast.walk(node)` —— 那会把 `node.type` 里
    `Exception` 这个名字也算进来）。

    刻意**不**算数的东西（都是实测抓到的"假痕迹"）：

      · `pass` / `continue` / `return None`；
      · `_ = exc`（把异常对象赋给一个没人用的名字）；
      · `set()` / `QColor(fallback)` / `text.setText(body)` 这类
        "有调用、但调用不是记录" 的语句；
      · `x = None` 这种"什么都没改"的赋值。

    `swallow_names` 是**这个文件里 `swallow` 的本地名**（很多文件写
    `from …safeguard import swallow as _sw`，只认字面 `swallow` 会漏）。
    """
    body = node.body
    exc = node.name or ''
    body_src = '\n'.join(ast.unparse(st) for st in body)
    calls = [n for st in body for n in ast.walk(st)
             if isinstance(n, ast.Call)]
    texts = [_call_text(c) for c in calls]
    names = set(swallow_names) | {'swallow'}

    # ① 上抛（含 `raise ... from e`）—— 已经交给上面的调用方了
    if any(isinstance(n, ast.Raise) for st in body for n in ast.walk(st)):
        return True

    # ② swallow（含 `_sw` / `_swallow` 这类别名）
    if any(_last_seg(t) in names for t in texts):
        return True

    # ③ print / traceback / stderr
    if any(t == 'print' or 'print_exc' in t for t in texts):
        return True
    if any(t.endswith('.write') and 'stderr' in t for t in texts):
        return True

    # ④ logging（`logging.warning(...)` / `logger.exception(...)`）
    if any(_last_seg(t) in LOG_METHODS and '.' in t for t in texts):
        return True

    # ⑤ 进 errors / warnings 这类列表（`errors.append(...)`）
    for c in calls:
        if not isinstance(c.func, ast.Attribute):
            continue
        if c.func.attr not in ('append', 'extend', 'add', 'insert', 'update'):
            continue
        holder = c.func.value
        name = (holder.attr if isinstance(holder, ast.Attribute) else
                holder.id if isinstance(holder, ast.Name) else '')
        if (name or '').lower() in (ERROR_LISTS | WARN_LISTS):
            return True

    # ⑥ 状态字段 / `self._status(...)` / `self._log(...)`
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in tgts:
                    nm = (t.attr if isinstance(t, ast.Attribute) else
                          t.id if isinstance(t, ast.Name) else '')
                    if (nm or '').lower() in STATUS_NAMES:
                        return True
    if any(_last_seg(t) in STATUS_NAMES for t in texts):
        return True

    # ⑦ 事件 / 通知
    if any(_last_seg(t) in EMIT_NAMES for t in texts):
        return True

    # ⑧ 直接弹给人看 / 统一失败出口
    if any(t in USER_ERROR_CALLS or _last_seg(t) == '_fail' for t in texts):
        return True

    # ⑨ 失败信封（`return {'ok': False…}`，或把失败 dict 交给 `_send(...)`）
    if any(_has_failure_envelope(st) for st in body):
        return True
    if any(isinstance(a, ast.Dict) and _is_failure_payload(a)
           for st in body for n in ast.walk(st)
           if isinstance(n, ast.Call) for a in n.args):
        return True

    # ⑩ 把异常拼进一句给人看的话
    if _mentions_exc_in_message(body_src, exc):
        return True

    return False


def _is_silent(node: ast.ExceptHandler, swallow_names=('swallow',)) -> bool:
    """体里没有任何记录通道 → 什么都不说。"""
    return not _has_trace(node, swallow_names)


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


def _swallow_aliases(tree) -> set:
    """这个文件里 `swallow` 的**本地名**（`import swallow as _sw` → `_sw`）。"""
    out = {'swallow'}
    for n in ast.walk(tree):
        if (isinstance(n, ast.ImportFrom) and n.module
                and n.module.endswith('safeguard')):
            for a in n.names:
                if a.name == 'swallow':
                    out.add(a.asname or a.name)
    return out


def _find_silent() -> list:
    bad = []
    for p in _iter_py():
        src = p.read_text(encoding='utf-8')
        try:
            tree = ast.parse(src)
        except SyntaxError as e:          # 解析不了本身就是错
            bad.append((p, 0, '<unparsable>', str(e)))
            continue
        aliases = _swallow_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            if ast.unparse(node.type) not in WIDE:
                continue
            if not _is_silent(node, aliases):
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
        宽捕获**必须留下一条记录通道**（判据见文件开头与 `_has_trace`）。

        这一条是这次改动的**净收益**：改之前 88 处是真的什么都不说
        （`except Exception: return None` / `continue` / `x = None`），
        现在每一处都进了 `swallow` 的环形缓冲 ——
        出事时 `safeguard.report()` 能看到"哪一块、多少次、最后一条是什么"。
        """
        bad = _find_silent()
        assert not bad, (
            '这些 `except Exception` 体里没有任何记录通道，'
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
            f'docs/archive/问题清单与优化路线_2026-10-03.md 里记一笔')

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


class TestTheGuardBites:
    """
    **守卫自己也要被守卫**（2026-11-01 收紧时补的）。

    收紧判据最大的风险不是"误报"，是"改着改着它什么都不抓了"。
    这里用剥出来的小片段钉住两个方向：
    假痕迹必须红，真记录必须不红。
    """

    #: 这些体**一个字的痕迹都没有** → 必须判红
    SILENT = [
        # `pass` 与它的几个同义写法
        'except Exception:\n    pass\n',
        'except Exception:\n    return None\n',
        'except Exception:\n    return []\n',
        'except Exception:\n    continue\n',
        # ↓ 这四种正是老守卫（"有没有 Call / 有没有 e"）放过去的
        'except Exception as exc:\n    _ = exc\n',
        'except Exception:\n    return set()\n',
        'except Exception:\n    return QColor(fallback)\n',
        'except Exception as e:\n    text.setText(body)\n',
        'except Exception as e:\n    x = None\n',
    ]

    #: 这些体都有记录通道 → 不许判红
    TRACED = [
        "except Exception as e:\n    swallow('m.f', e)\n",
        "except Exception as e:\n    _sw('m.f', e)\n",       # swallow 别名
        "except Exception as e:\n    print(e)\n",
        "except Exception as e:\n    logging.warning('读不到', exc_info=e)\n",
        'except Exception:\n    raise\n',
        'except Exception as e:\n    raise RuntimeError from e\n',
        "except Exception as e:\n    errors.append(str(e))\n",
        "except Exception as e:\n    self._status(f'失败：{e}')\n",
        "except Exception as e:\n    self._log(str(e))\n",
        "except Exception as e:\n    return {'ok': False, 'error': str(e)}\n",
        "except Exception as e:\n    return Foo(error=str(e))\n",
        "except Exception as e:\n    ok, msg = (False, f'出错了（{e.__class__.__name__}）')\n",
    ]

    @staticmethod
    def _handler(src: str) -> ast.ExceptHandler:
        # `except` 不能单独成句 —— 前面垫一个 `try: pass` 才解析得了
        tree = ast.parse('try:\n    pass\n' + src)
        return next(n for n in ast.walk(tree)
                    if isinstance(n, ast.ExceptHandler))

    @pytest.mark.parametrize('src', SILENT)
    def test_a_body_without_any_record_is_silent(self, src):
        assert _is_silent(self._handler(src), {'swallow', '_sw'}), src

    @pytest.mark.parametrize('src', TRACED)
    def test_a_body_with_a_record_channel_is_not_silent(self, src):
        assert not _is_silent(self._handler(src), {'swallow', '_sw'}), src

    def test_the_guard_is_not_vacuous(self):
        """
        反向保险：如果 `_has_trace` 被改成"永远 True"，上一条不会红 ——
        所以这里直接断言仓库里**确实有**至少 1000 个能判出痕迹的 handler
        （2026-11-01 实测：1960 个 handler 里 1232 个是宽捕获）。
        """
        traced = 0
        for p in _iter_py():
            tree = ast.parse(p.read_text(encoding='utf-8'))
            aliases = _swallow_aliases(tree)
            for node in ast.walk(tree):
                if (isinstance(node, ast.ExceptHandler)
                        and node.type is not None
                        and ast.unparse(node.type) in WIDE
                        and not _is_silent(node, aliases)):
                    traced += 1
        assert traced > 1000, f'只有 {traced} 个宽捕获被判为"有痕迹"，判据可能坏了'


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
