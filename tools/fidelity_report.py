#!/usr/bin/env python
"""
逐字节保真报告：把"没动的行一个字节都不变"从一句承诺变成**能读的清单**
=====================================================================

与 `audit_byte_exact.py` 的分工（两份都留着，别合并）：

  · `audit_byte_exact` —— 冒烟用。只报**第一处**差异（"第 2 行不同"），
    快，但审不了：一处空行错位会把后面所有差异挡住。
  · 本工具 —— 交证据用。给**全部**差异，并把差异分成两档：

        identical  逐字节相同
        layout     只有排版差异（空行 / 行尾空格 / 换行符 / 末尾换行）—— 内容没丢
        content    行级内容不同 —— 这时才需要人看

    为什么必须分档：`source_style` / 全量重生成本来就会重排空白（P1-1 的原话：
    "顶层语句之间各插一个空行、行尾空格删除"）。把排版差异和内容差异混成一个
    "有差异"的数字，等于让真 bug 淹在噪音里 —— 2026-10-18 那次审计就是这么漏的。

行级判据（docs/路线图_下一步执行.md 步骤 1 要求的那条）：
    丢了几行（源有、产物没有）/ 多了几行（产物有、源没有）/ 改了几行（同位置不同）。
    用 `difflib.SequenceMatcher` 的 opcodes 算，**行号都保留**，能直接定位。

跑法：
    python tools/fidelity_report.py --project tests/fixtures/fidelity --verbose
    python tools/fidelity_report.py --project D:\\千连万理\\renpytest\\sr_general
    python tools/fidelity_report.py --project demo --md .pip-tmp/report.md

退出码：0 = 全部 identical（给了 --allow-layout 时 layout 也算过）；1 = 有 content / error；2 = 用法错。
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit_byte_exact import strip_banner  # noqa: E402  （判据只留一份）

#: 不算章节的目录：引擎产物、缓存、存档、翻译
_SKIP_DIRS = {'build', 'cache', 'saves', 'log', '.git', '__pycache__', 'tl'}

IDENTICAL, LAYOUT, CONTENT, ERROR = 'identical', 'layout', 'content', 'error'


@dataclass
class FileReport:
    """一个文件的体检结果。字段都是"人能直接读"的，不是内部表示。"""

    rel: str
    status: str = ERROR
    used_patch: bool = False
    note: str = ''
    #: 源有、产物没有：[(源行号, 行), …]
    missing: list = field(default_factory=list)
    #: 产物有、源没有：[(产物行号, 行), …]
    extra: list = field(default_factory=list)
    #: 同一位置内容不同：[(源行号, 源行, 产物行), …]
    changed: list = field(default_factory=list)
    #: 完整差异（unified diff，全部 hunk）
    unified: str = ''

    @property
    def ok(self) -> bool:
        return self.status == IDENTICAL


def _line_of(text: str) -> list:
    return text.splitlines()


def _layout_key(lines: list) -> list:
    """排版归一：去行尾空白、丢空行。只剩内容时两边应当完全相等。"""
    return [ln.rstrip() for ln in lines if ln.strip()]


def classify(src_text: str, out_text: str) -> tuple:
    """
    判定 + 行级细节。返回 `(status, missing, extra, changed, unified)`。

    ⚠ `layout` 用的归一**只去空白**，不去注释、不去大小写 —— 那不是"排版"，
    那是内容变了，必须报出来。

    BOM 单独归一：它属于"载体"（和 CRLF 同类），丢了是字节变了、内容没变。
    真机有 5 个 BOM 文件，补丁目前会原样保住；这里归一是为了让判定口径
    在"将来某天 BOM 没保住"时仍然说人话（layout，而不是 content）。
    """
    raw_equal = src_text == out_text
    src_text = src_text.removeprefix('\ufeff')
    out_text = out_text.removeprefix('\ufeff')
    if raw_equal:
        return IDENTICAL, [], [], [], ''
    if src_text == out_text:
        # 只有 BOM 不同：字节变了、内容没变 → 载体档，不是 identical（那是谎话）
        return LAYOUT, [], [], [], ''
    src_lines, out_lines = _line_of(src_text), _line_of(out_text)
    if _layout_key(src_lines) == _layout_key(out_lines):
        # ⚠ 空行可能不是排版：`python:` 块里的 `"""…"""` / `'''…'''` 里，
        # 空行是**字符串内容**。这种文件里"只差空行"必须当内容报出来，
        # 否则一次静默的字符串改字会被判成"只是排版"—— 那是数据损坏。
        if any(q in src_text or q in out_text for q in ('"""', "'''")):
            pass                              # 落到下面的 content 分支
        else:
            return LAYOUT, [], [], [], ''

    matcher = difflib.SequenceMatcher(None, src_lines, out_lines, autojunk=False)
    missing, extra, changed = [], [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'delete':
            missing.extend((i + 1, src_lines[i]) for i in range(i1, i2))
        elif tag == 'insert':
            extra.extend((j + 1, out_lines[j]) for j in range(j1, j2))
        elif tag == 'replace':
            pair = min(i2 - i1, j2 - j1)
            for k in range(pair):
                changed.append((i1 + k + 1, src_lines[i1 + k], out_lines[j1 + k]))
            missing.extend((i + 1, src_lines[i]) for i in range(i1 + pair, i2))
            extra.extend((j + 1, out_lines[j]) for j in range(j1 + pair, j2))

    unified = '\n'.join(difflib.unified_diff(
        src_lines, out_lines, 'source', 'product', lineterm='', n=2))
    return CONTENT, missing, extra, changed, unified


def _iter_rpy(base: Path, pattern: str = '*.rpy'):
    for path in sorted(base.rglob(pattern)):
        if any(part in _SKIP_DIRS for part in path.relative_to(base).parts[:-1]):
            continue
        if path.name.startswith('.'):
            continue
        yield path


def roundtrip(src: Path, workdir: Path, index: int) -> FileReport:
    """
    走**生产那条路**：`.rpy → 表 → 带 --source 写回`。
    不用 `render_beats_to_rpy` 直连 —— 那样测的是渲染器，不是用户按的那颗按钮。
    """
    from storygraph.pipeline.compile import compile_table
    from storygraph.story.script_table import open_rpy_as_table

    rel = src.name
    xlsx = workdir / f'{index:04d}.xlsx'
    out = workdir / f'{index:04d}.rpy'
    rep = FileReport(rel=rel)
    try:
        open_rpy_as_table(str(src), str(xlsx))
        _p, _s, res = compile_table(
            str(xlsx), rpy_path=out, write_sheet=False, force=True,
            source_path=str(src), skip_empty=True, engine_lint=False)
        rep.used_patch = bool(getattr(res, 'used_patch', False))
        rep.note = str(getattr(res, 'patch_note', '') or '')
        if not out.exists():
            rep.status = ERROR
            rep.note = rep.note or '没有产物（门禁拦下或空表）'
            return rep
        src_body, _ = strip_banner(src.read_bytes())
        out_body, _ = strip_banner(out.read_bytes())
        src_text = src_body.decode('utf-8', 'replace')
        out_text = out_body.decode('utf-8', 'replace')
        status, missing, extra, changed, unified = classify(src_text, out_text)
        rep.status = status
        rep.missing, rep.extra, rep.changed = missing, extra, changed
        rep.unified = unified
    except Exception as exc:                  # noqa: BLE001 - 报告要跑完全部文件
        rep.status = ERROR
        rep.note = f'{type(exc).__name__}: {exc}'
        rep.unified = traceback.format_exc(limit=3)
    return rep


def build_reports(base: Path, pattern: str, limit: int = 0,
                  verbose: bool = False) -> list:
    files = list(_iter_rpy(base, pattern))
    if limit:
        files = files[:limit]
    reports = []
    workdir = Path(tempfile.mkdtemp(prefix='sgfid_'))
    try:
        for i, src in enumerate(files, start=1):
            rep = roundtrip(src, workdir, i)
            rep.rel = str(src.relative_to(base))
            reports.append(rep)
            # 默认只报**有差异**的：真机 55 个全绿时，55 行 ✓ 只会把
            # 真正要看的那一行淹掉（--verbose 才逐条打）。
            if rep.status == IDENTICAL:
                if verbose:
                    print(f'  ✓ {rep.rel}')
            elif rep.status == LAYOUT:
                print(f'  ~ {rep.rel}（只有排版差异）')
            elif rep.status == CONTENT:
                print(f'  ✗ {rep.rel}')
            else:
                print(f'  ! {rep.rel}：{rep.note}')
            if rep.status == CONTENT and not rep.used_patch:
                print(f'     （退回全量：{rep.note or "未说明"}）')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    return reports


def _counts(reports: list) -> dict:
    out = {IDENTICAL: 0, LAYOUT: 0, CONTENT: 0, ERROR: 0}
    for rep in reports:
        out[rep.status] = out.get(rep.status, 0) + 1
    return out


def _summary_line(reports: list) -> str:
    n = _counts(reports)
    return (f'文件 {len(reports)} · 逐字节相同 {n[IDENTICAL]} · 排版差异 {n[LAYOUT]}'
            f' · 内容不同 {n[CONTENT]} · 出错 {n[ERROR]}')


def _detail(rep: FileReport, *, limit: int = 6) -> list:
    """一个文件的人话细节（差异太多时截断，但**计数不截断**）。"""
    lines = []
    if rep.status == IDENTICAL:
        return lines
    lines.append(f'  {"~" if rep.status == LAYOUT else "✗"} {rep.rel}'
                 f'（{"增量" if rep.used_patch else "退回全量"}'
                 + (f'：{rep.note}' if rep.note else '') + '）')
    if rep.status == LAYOUT:
        lines.append('     排版差异：空行 / 行尾空格 / 换行符 —— 内容一行没丢')
        return lines
    lines.append(f'     丢 {len(rep.missing)} 行 / 多 {len(rep.extra)} 行 '
                 f'/ 改 {len(rep.changed)} 行')
    for line_no, text in rep.missing[:limit]:
        lines.append(f'     源第 {line_no} 行没了：{text.strip()[:100]}')
    for line_no, text in rep.extra[:limit]:
        lines.append(f'     产物第 {line_no} 行是多的：{text.strip()[:100]}')
    for line_no, old, new in rep.changed[:limit]:
        lines.append(f'     源第 {line_no} 行被改：{old.strip()[:60]!r} → {new.strip()[:60]!r}')
    if len(rep.missing) + len(rep.extra) + len(rep.changed) > limit:
        lines.append(f'     …（共 {len(rep.missing) + len(rep.extra) + len(rep.changed)} 处）')
    return lines


def render_markdown(reports: list, base: Path) -> str:
    n = _counts(reports)
    out = [f'# 逐字节保真报告', '', f'`{base}`', '',
           f'- {_summary_line(reports)}',
           f'- 增量补丁 {sum(1 for r in reports if r.used_patch)} / {len(reports)}',
           '']
    bad = [r for r in reports if r.status != IDENTICAL]
    if not bad:
        out.append('**全部逐字节相同** —— 没改的行一个字节都没动。')
        return '\n'.join(out)
    out += ['| 文件 | 判定 | 走的路 | 丢 | 多 | 改 |', '|---|---|---|---|---|---|']
    for rep in bad:
        out.append(f'| `{rep.rel}` | {rep.status} | '
                   f'{"增量" if rep.used_patch else "全量"} | {len(rep.missing)} | '
                   f'{len(rep.extra)} | {len(rep.changed)} |')
    for rep in bad:
        out += ['', f'## `{rep.rel}`', '']
        if rep.note:
            out.append(f'- 补丁说明：{rep.note}')
        if rep.status == LAYOUT:
            out.append('- **只有空白差异**（空行 / 行尾空格 / 换行符 / BOM）—— 内容一行没丢')
        else:
            out.append(f'- 丢 {len(rep.missing)} / 多 {len(rep.extra)} / 改 {len(rep.changed)}')
        if rep.unified:
            out += ['', '```diff', rep.unified, '```']
    return '\n'.join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='逐字节保真报告')
    ap.add_argument('--project', required=True,
                    help='项目根（含 game/）或任意装了 .rpy 的目录（如 tests/fixtures/fidelity）')
    ap.add_argument('--pattern', default='*.rpy')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verbose', action='store_true', help='每个文件都打一行')
    ap.add_argument('--allow-layout', action='store_true',
                    help='排版差异不算失败（默认算：承诺是"逐字节"）')
    ap.add_argument('--md', default='', help='把报告写成 markdown')
    ap.add_argument('--json', default='', help='把报告写成 json')
    args = ap.parse_args(argv)

    root = Path(args.project)
    base = root / 'game' if (root / 'game').is_dir() else root
    if not base.is_dir():
        print(f'找不到目录：{base}', file=sys.stderr)
        return 2

    reports = build_reports(base, args.pattern, args.limit, verbose=args.verbose)
    if not reports:
        print(f'{base} 下没有匹配 {args.pattern} 的文件', file=sys.stderr)
        return 2

    print()
    print('=' * 72)
    print(f'{base}')
    print(_summary_line(reports))
    patched = sum(1 for r in reports if r.used_patch)
    print(f'（其中 {patched} 个走的是增量补丁；{len(reports) - patched} 个退回全量）')
    bad = [r for r in reports if r.status != IDENTICAL]
    if bad:
        print()
        print('有差异的文件：')
        for rep in bad:
            print('\n'.join(_detail(rep)))
            if args.verbose and rep.unified:
                print('     完整差异：')
                for line in rep.unified.splitlines():
                    print(f'       {line}')

    if args.md:
        Path(args.md).write_text(render_markdown(reports, base), encoding='utf-8')
        print(f'\n报告：{args.md}')
    if args.json:
        payload = [r.__dict__ for r in reports]
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        print(f'JSON：{args.json}')

    failed = [r for r in reports if r.status in (CONTENT, ERROR)]
    layout = [r for r in reports if r.status == LAYOUT]
    if failed or (layout and not args.allow_layout):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
