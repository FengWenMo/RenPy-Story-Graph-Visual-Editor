"""
storygraph.pipeline.engine_lint
==============================
**产物 → 引擎 lint 的闸门**（2026-10-18，审计 P1-9 / 修复顺序第一条）。

问题（审计原话）："构建不跑引擎 lint，所以 P0-1~P0-6 全能顺利『构建成功』" ——
工具自己的一整套 `validate` 验的是"工具内部前后一致"，而"引擎能不能解析
这个产物"从来没进过判据。于是"全绿"和"跑不起来"可以同时成立。

这一层只做一件事：把**生成出来的文本**丢进一个最小 Ren'Py 工程跑 SDK
`lint`，把 **parse 错误**（引擎连语法都读不进去的那类）报成阻塞问题。
语义错误（缺图 / 缺角色）只作为警告 —— 那些要靠项目上下文，临时工程里
本来就查不准。

三条纪律（都是被现实逼出来的）：

  · **没有 SDK 就如实说"没跑"**，绝不假装通过（`LintReport.ran=False`）；
  · **项目自定义语句不算语法错**：临时工程里没有 `renpy.register_statement`，
    引擎会把 `mycue …` 报成 `expected statement.` —— 那是假阳性。
    所以给一份 `ignore_heads`（项目声明的语句名），错误行以它开头就跳过；
  · **默认不参与单测/库调用**：跑一次要起一个 Ren'Py 进程（约 1 秒）。
    只有产品写盘路径（CLI `table xlsx2rpy` / `compile` / 界面导出）显式打开。

用法：

    rep = lint_text(text, ignore_heads={'mycue'})
    if rep.ran and not rep.ok:
        ...  # rep.parse_errors 里有原文行号
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: 引擎**加载 / 编译阶段抛异常**的报错行：
#:
#:     File "game/script.rpy", line 13850: '{' was never closed
#:     File "game/script.rpy", line 3: style property xxx is not known.
#:
#: 出处是引擎的异常渲染（`renpy.error`），**不是** lint 的检查报告 ——
#: 也就是说"脚本根本没读进去"。不管那句话长什么样，产物都加载不了，
#: 一律算**阻塞**（2026-10-18，P1：从前靠 8 个措辞去猜，`'{' was never
#: closed` 猜漏了 → 判成语义、`ok=True`、闸门放行）。
_RE_EXC_LINE = re.compile(
    r'^File\s+"(?P<file>[^"]+)"\s*,\s*line\s+(?P<line>\d+)\s*:\s*(?P<msg>.*)$')

#: 引擎 **lint 检查**自己的报错行（脚本已经读进去了）：
#:
#:     game/script.rpy:12 'bg room' is not an image.
#:
#: 这些是"缺图 / 缺角色 / 跳转目标不存在 / 音频缺失 / 样式建议"——
#: 要项目上下文，临时工程里本来就查不准 ⇒ **不阻塞**，只作为语义提示。
_RE_CHECK_LINE = re.compile(r'^(?P<file>[^:\n]*\.rpy):(?P<line>\d+)\s+'
                            r'(?P<msg>\S.*)$')

#: 引擎在 lint 期间**崩了**（`init python` 抛异常等）：输出里有 traceback 帧，
#: 但没有任何 `File "…", line N:` 形式的报错行。这种产物加载即崩，
#: 同样是阻塞 —— 判据不能只看"有没有那句话"。
#:
#: Ren'Py 的 traceback **不打** Python 那句 `Traceback (most recent call
#: last)`：它自己渲染成一串 `File "game/script.rpy", line 2, in script`
#: —— 注意行号后面是**逗号**（`in <函数>`），与异常报错的冒号是两种形状。
_RE_FRAME = re.compile(r'^File\s+"[^"]+"\s*,\s*line\s+\d+\s*,\s*in\s+\S+')

#: traceback 最后那行异常（`ZeroDivisionError: division by zero`）
_RE_EXC_TAIL = re.compile(
    r'^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Exit))\s*:\s*(?P<msg>.*)$')


@dataclass
class LintReport:
    """一次引擎 lint 的结论。"""

    #: 语法层面**通过**（没跑也算通过，但 `ran` 是 False，调用方必须区分）
    ok: bool = True
    #: 真的跑了引擎吗（找不到 SDK / 超时 = False）
    ran: bool = False
    #: 没跑的原因（`ran=False` 时非空）
    reason: str = ''
    #: 语法错误（阻塞）：`['第 4 行：expected ':' not found.']`
    parse_errors: list = field(default_factory=list)
    #: 语义/程序错误（警告，不阻塞）
    semantic_errors: list = field(default_factory=list)
    #: 引擎原始输出（排查用；可能很长，调用方自行截断）
    raw: str = ''

    def summary(self) -> str:
        if not self.ran:
            return f'引擎 lint 未运行（{self.reason}）'
        if self.ok:
            return '引擎 lint 通过'
        return f'引擎 lint 报 {len(self.parse_errors)} 处语法错误'


def _sdk():
    """找到 SDK 就返回 Path，找不到返回 None（**不抛**：lint 是可选闸门）。"""
    try:
        from storygraph.pipeline.engine_collect import find_sdk
        return find_sdk()
    except Exception as e:                         # noqa: BLE001 - 没有 SDK 很常见
        from storygraph.core.safeguard import swallow
        swallow('pipeline.engine_lint._sdk', e)
        return None


def available() -> bool:
    """这台机器上能不能跑引擎 lint。"""
    return _sdk() is not None


def _normalize_line(text: str) -> str:
    return re.sub(r'\s+', '', str(text or ''))


def _head_of(line: str) -> str:
    s = str(line or '').strip()
    if not s or s.startswith('#'):
        return ''
    return s.split(None, 1)[0].rstrip(':')


def classify(text: str, *, source_text: str = '',
             ignore_heads=()) -> tuple:
    """
    lint 输出 → `(parse_errors, semantic_errors)`。

    判据是**出处**，不是措辞（2026-10-18，P1：闸门 fail-open）。

    引擎只会用两种带行号的格式报事，两者含义完全不同：

      · `File "game/script.rpy", line 12: '{' was never closed`
        —— 加载 / 编译时**抛了异常**，脚本没读进去 ⇒ **阻塞**。
        它可能是 Python 编译错（`'{' was never closed`）、Ren'Py 语句错
        （`expected ':' not found.`）、样式属性不认识 …
        旧写法列了 8 个"像语法错"的词去匹配，`'{' was never closed` 不在
        里面 → 归进 `semantic_errors` → 只 warn、`ok=True`、闸门放行，
        而那份产物 Ren'Py 根本加载不了。**措辞清单永远漏，出处不会漏。**

      · `game/script.rpy:12 'bg room' is not an image.`
        —— 脚本读进去了，这是 lint **检查**的意见（缺图 / 缺角色 /
        跳转目标不存在 / 音频缺失 …）。要项目上下文，临时工程里查不准
        ⇒ 不算阻塞，只报给人看。

    还有第三种：输出里有 traceback、却没有上面任何一条带行号的报错
    （`init python` 把 lint 跑崩了）。那种产物加载即崩，同样阻塞。

    报错行之后的两行是源码片段 —— 用它可以判断"这是不是一句项目声明的
    自定义语句"（`ignore_heads`）：临时工程里没 `renpy.register_statement`，
    引擎会把 `mycue …` 报成 `expected statement.`，那是假阳性。
    """
    ignore = {str(h).strip() for h in (ignore_heads or ()) if str(h).strip()}
    src_lines = str(source_text or '').splitlines()
    lines = str(text or '').splitlines()
    parse_errors, semantic = [], []
    for i, line in enumerate(lines):
        s = line.strip()
        m = _RE_EXC_LINE.match(s)
        if m is None:
            m = _RE_CHECK_LINE.match(s)
            if m is None:
                continue
            semantic.append(f'第 {int(m.group("line"))} 行：'
                            f'{m.group("msg").strip()}')
            continue
        msg = m.group('msg').strip()
        no = int(m.group('line'))
        snippet = ''
        if i + 1 < len(lines):
            snippet = lines[i + 1].strip()
        if not snippet and 0 < no <= len(src_lines):
            snippet = src_lines[no - 1].strip()
        if _head_of(snippet) in ignore:
            continue          # 项目声明的自定义语句 —— 临时工程不认，不算错
        parse_errors.append(f'第 {no} 行：{msg}')
    crash = _load_crash(text)
    if crash and not parse_errors:
        parse_errors.append(crash)
    return parse_errors, semantic


def _load_crash(raw: str) -> str:
    """
    引擎在 lint 期间**崩了**吗？崩了就返回那句异常，否则返回空串。

    为什么要单独判：`init python` 抛异常时引擎**不会**中止 lint —— 它打印
    traceback、继续做语义检查、退出码还是 0（`error_reported` 只在 `report()`
    里置位）。于是"产物一进游戏就崩"会被闸门当成"语法通过"。

    判据是**有没有 traceback 帧**（`File "…", line N, in <函数>`）。这一种
    形状只出现在"引擎自己抛了异常"时，lint 的检查报告永远是
    `game/script.rpy:N msg`（冒号，没有帧）。
    """
    lines = [ln.strip() for ln in str(raw or '').splitlines() if ln.strip()]
    if not any(_RE_FRAME.match(ln) for ln in lines):
        return ''
    for ln in reversed(lines):
        m = _RE_EXC_TAIL.match(ln)
        if m:
            return f'引擎 lint 期间抛异常（产物加载即崩）：{ln}'
    return '引擎 lint 期间抛异常（产物加载即崩，原文见 lint 输出）'


def lint_text(text: str, *, ignore_heads=(), sdk=None,
              timeout: int = 180) -> LintReport:
    """
    把一段 `.rpy` 文本丢进**最小工程**跑引擎 lint。

    最小工程里只有 `game/script.rpy`（就是这段文本）和一份空 `options.rpy`，
    所以语义检查（缺图 / 缺角色）查不准 —— 它们只进 `semantic_errors`，
    不阻塞。**语法**不受影响：语法是引擎自己读文本，不需要项目。
    """
    sdk_path = Path(sdk) if sdk else _sdk()
    if sdk_path is None:
        return LintReport(ok=True, ran=False,
                          reason='找不到 Ren\'Py SDK（用 STORYGRAPH_RENPY_SDK 指定）')
    py = sdk_path / 'lib' / 'py3-windows-x86_64' / 'python.exe'
    if not py.exists():
        # 非 Windows 打包：退到 SDK 下的 `python`/当前解释器
        py = sdk_path / 'lib' / 'python'
        if not py.exists():
            return LintReport(ok=True, ran=False,
                              reason=f'SDK 里找不到引擎 python：{sdk_path}')
    with tempfile.TemporaryDirectory(prefix='sglint_') as td:
        game = Path(td) / 'game'
        game.mkdir(parents=True, exist_ok=True)
        (game / 'script.rpy').write_text(str(text or ''), encoding='utf-8')
        (game / 'options.rpy').write_text('', encoding='utf-8')
        try:
            proc = subprocess.run(
                [str(py), str(sdk_path / 'renpy.py'), td, 'lint',
                 '--error-code'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=timeout,
                cwd=str(sdk_path))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return LintReport(ok=True, ran=False,
                              reason=f'引擎 lint 起不来：{exc}')
        raw = (proc.stdout or '') + (proc.stderr or '')
    parse_errors, semantic = classify(raw, source_text=text,
                                      ignore_heads=ignore_heads)
    # ⚠ 退出码不能当判据：`Could not evaluate 'e'` 这类**语义**错也会让它非 0。
    #   真正的判据是"有没有**语法**错误"。
    return LintReport(ok=not parse_errors, ran=True,
                      parse_errors=parse_errors,
                      semantic_errors=semantic, raw=raw)


def declared_heads_near(path) -> set:
    """
    从产物路径反推项目根，取这个项目**声明过的自定义语句名**。

    为什么要它：临时工程里没有 `renpy.register_statement`，项目自定义语句
    （`mycue …`）会被引擎报成语法错 —— 那是**假阳性**，闸门必须知道
    "这几个头是项目自己教的"。项目根推不出来就返回空集（宁可少豁免）。
    """
    try:
        from storygraph.story.paths import project_root_of
        p = Path(path) if path else None
        root = None
        if p is not None and p.is_dir() and (p / 'game').is_dir():
            root = p
        root = root or (project_root_of(p) if p is not None else None)
        if not root:
            return set()
        from storygraph.story.statement_registry import StatementRegistry
        return set(StatementRegistry.for_project(root).names())
    except Exception:                              # noqa: BLE001 - 豁免清单拿不到不影响判据
        return set()


def lint_project(project_root, *, sdk=None, timeout: int = 300) -> LintReport:
    """直接 lint 一个**完整工程**（项目上下文齐全，语义检查也可信）。"""
    sdk_path = Path(sdk) if sdk else _sdk()
    if sdk_path is None:
        return LintReport(ok=True, ran=False, reason='找不到 Ren\'Py SDK')
    py = sdk_path / 'lib' / 'py3-windows-x86_64' / 'python.exe'
    if not py.exists():
        return LintReport(ok=True, ran=False,
                          reason=f'SDK 里找不到引擎 python：{sdk_path}')
    try:
        proc = subprocess.run(
            [str(py), str(sdk_path / 'renpy.py'), str(project_root), 'lint',
             '--error-code'],
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout, cwd=str(sdk_path))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return LintReport(ok=True, ran=False, reason=f'引擎 lint 起不来：{exc}')
    raw = (proc.stdout or '') + (proc.stderr or '')
    parse_errors, semantic = classify(raw)
    return LintReport(ok=not parse_errors, ran=True,
                      parse_errors=parse_errors,
                      semantic_errors=semantic, raw=raw)


__all__ = ['LintReport', 'available', 'classify', 'declared_heads_near',
           'lint_project', 'lint_text']
