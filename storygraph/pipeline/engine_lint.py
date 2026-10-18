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

#: lint 里"这是语法错误"的标志词（与 `engine_collect._PARSE_MARK` 同源，
#: 但这里**只认语法**：语义类（缺图缺角色）要项目上下文，临时工程查不准）。
_PARSE_MARK = ('expected ', 'SyntaxError', 'ParseError', 'is not a keyword',
               'Unexpected indentation', 'unexpected indent',
               'expected an indented block', 'end of line expected',
               'unexpected end of line')

#: `File "game/script.rpy", line 4: expected ':' not found.` 这类报告头
_RE_REPORT = re.compile(r'^(?:File\s+")?(?P<file>[^":]+\.rpy)"?'
                        r'(?:,)?\s*line\s+(?P<line>\d+)\s*:\s*(?P<msg>.*)$')


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

    只把**语法类**报错算进 parse；并且跳过项目自定义语句头（假阳性）。

    判据要"能定位到源码行"：Ren'Py 的格式是

        File "game/script.rpy", line 4: expected ':' not found.
            menu "caption":
                 ^

    报错行之后那两行才是源码片段 —— 用它判断这是不是一句自定义语句。
    """
    ignore = {str(h).strip() for h in (ignore_heads or ()) if str(h).strip()}
    src_lines = str(source_text or '').splitlines()
    lines = str(text or '').splitlines()
    parse_errors, semantic = [], []
    for i, line in enumerate(lines):
        m = _RE_REPORT.match(line.strip())
        if not m:
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
        shown = f'第 {no} 行：{msg}'
        if any(mark in msg for mark in _PARSE_MARK):
            parse_errors.append(shown)
        else:
            semantic.append(shown)
    return parse_errors, semantic


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
