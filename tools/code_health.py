#!/usr/bin/env python
"""
tools/code_health.py
====================
**代码体检** —— 一次性把"这套代码还有多好读"量出来，别靠感觉。

为什么要有它（2026-10-28）
--------------------------
这个仓库已经 12 万行、4100 多个函数，讨论"简不简洁"时双方都在凭印象。
凭印象的结论没法验收，也没法看"这次改动让它变好还是变坏"。
所以把六个可测的维度固定下来，每次改完跑一遍，比自己拍脑袋可靠：

  ① 注释率      —— 有没有把"为什么"写下来
  ② 长函数      —— 一段逻辑有多长（读的人一次要端多少东西）
  ③ 圈复杂度    —— 分支有多密（近似 mccabe，与 `tools/_complexity.py` 同口径）
  ④ 巨型文件    —— 一个文件装了多少件事
  ⑤ 跨文件重复  —— 同一段代码被抄了几遍（**剥掉注释和字符串再比**，
                    否则报出来的全是模块 docstring，看不见真代码）
  ⑥ 注释考古度  —— 注释里混了多少"日期 / P0 编号 / 实测数字"。
                    这类信息对当事人是无价的，对新人是噪音；
                    比例过高说明注释在替文档干活。

用法
----
    python tools/code_health.py                  # 全部维度，各取头部 15
    python tools/code_health.py --top 40         # 多看几行
    python tools/code_health.py --only funcs     # 只看一维
    python tools/code_health.py --json           # 机器可读（给 CI / 对比用）
    python tools/code_health.py --path storygraph/story   # 只看一个子包

读数怎么算"好"
--------------
阈值取通用工程经验，不是本项目的特例（写在 `THRESHOLDS` 里，改之前先想清楚
为什么这个项目该跟别人不一样）。它们只用来**排序优先级**，
不是"超过就错"的红线 —— 本项目有些 300 行的函数是被真实业务复杂度撑起来的。
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 通用工程经验值。三档：理想 / 可接受 / 需要动手。
THRESHOLDS = {
    'comment_ratio': (0.08, 0.05),      # 注释行 / 代码行：>=8% 好，<5% 偏薄
    'func_lines': (50, 80),             # 函数行数：<=50 好，>80 该写"分几段"
    'complexity': (10, 20),             # 圈复杂度：<=10 好，>20 该拆
    'file_lines': (800, 2000),          # 文件行数：<=800 好，>2000 该分区
    'dup_lines': 8,                     # 连续多少行算"重复块"
}

#: 与 mccabe 同口径：每多一个分支 +1。`BoolOp` 按短路次数算，不是按个数。
BRANCH = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
          ast.ExceptHandler, ast.With, ast.AsyncWith, ast.BoolOp,
          ast.IfExp, ast.comprehension, ast.Match)

#: 注释里的"考古成分"：日期 / 审计编号。看到它们就说明这条注释在讲历史。
_RE_DATE = re.compile(r'20\d\d-\d\d-\d\d')
_RE_AUDIT = re.compile(r'\bP[0-3][-\u2011]?[A-Z]?\d*\b')

#: 这些词打头的行"到哪都一样"，算不进"实质行"（见 `_worth_comparing`）。
_CONTROL_WORDS = {'return', 'pass', 'break', 'continue', 'try', 'else',
                  'finally', 'raise', '(', ')', 'elif', 'except'}


# --------------------------------------------------------------------------
# 一、量规模与注释
# --------------------------------------------------------------------------
def scan_file(path: Path) -> dict:
    """量一个 .py 文件。返回的字典就是后面所有榜单的原料。"""
    text = path.read_text(encoding='utf-8', errors='replace')
    raw_lines = text.split('\n')

    # 注释行 / 代码行：只按"这一行第一个非空字符是不是 #"分，
    # 不去智能区分行尾注释 —— 量级上没差别，简单才不会算错。
    comment_lines = code_lines = 0
    dated = audited = 0
    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith('#'):
            comment_lines += 1
            if _RE_DATE.search(s) or _RE_AUDIT.search(s):
                dated += 1
        else:
            code_lines += 1

    info = {
        'path': str(path.relative_to(ROOT)).replace('\\', '/'),
        'lines': len(raw_lines),
        'code_lines': code_lines,
        'comment_lines': comment_lines,
        'dated_comments': dated,
        'audited_comments': audited,
        'functions': [],
        'public_without_doc': [],
    }

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:               # 语法坏了的文件单独报，别整个脚本崩
        info['error'] = f'{exc.msg} (line {exc.lineno})'
        return info

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        n_lines = body_lines_of(node)
        info['functions'].append({
            'name': node.name,
            'lineno': node.lineno,
            'lines': n_lines,
            'complexity': complexity_of(node),
            'has_doc': bool(ast.get_docstring(node)),
        })
        # 公开函数（不带下划线）+ 够长 + 没 docstring = 最难读的那一类
        if (not node.name.startswith('_') and n_lines >= 15
                and not ast.get_docstring(node)):
            info['public_without_doc'].append((node.name, node.lineno, n_lines))
    return info


def body_lines_of(node) -> int:
    """
    函数的**逻辑行数** —— 不算 docstring。

    为什么要减掉（2026-10-28 实测）：加了一版"分段导读"docstring 之后，
    `patch_export` 从 394 涨到 415，体检榜上反而"变长了"。
    拿含 docstring 的总行数当长度，等于**惩罚写文档的人**，
    最后大家会把"这段在干什么"删掉来让指标好看。所以长度只算代码。

    口径：第一个语句是字符串常量（就是 docstring）时，从它**结束的下一行**
    算到函数结束；否则从 `def` 那行算起。
    """
    end = getattr(node, 'end_lineno', node.lineno) or node.lineno
    body = getattr(node, 'body', None) or []
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(getattr(body[0], 'value', None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        if len(body) == 1:            # 只有 docstring、没有代码
            return 0
        start = (getattr(body[0], 'end_lineno', None) or node.lineno) + 1
        return max(0, end - start + 1)
    return end - node.lineno + 1


def complexity_of(node) -> int:
    """近似圈复杂度：从 1 起，每遇一个分支点 +1。"""
    n = 1
    for sub in ast.walk(node):
        if isinstance(sub, BRANCH):
            if isinstance(sub, ast.BoolOp):
                n += max(0, len(sub.values) - 1)
            else:
                n += 1
    return n


# --------------------------------------------------------------------------
# 二、跨文件重复块
# --------------------------------------------------------------------------
def code_skeleton(path: Path, n: int) -> list[str]:
    """
    把源码压成"只剩骨架"的行序列，用来比重复。

    做法：走 tokenizer，**丢掉注释**（不比注释），把每个**字符串字面量**换成
    `"s"`（不比文案）。这样"两段代码只改了提示语"的复制粘贴也能被抓到 ——
    而这恰恰是最常见的复制方式，也是后来最容易改漏一处的地方。

    docstring 和字符串都被抹成 `"s"`，正好不会因为"文案不同"漏报。
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(
            path.read_text(encoding='utf-8', errors='replace')).readline))
    except (tokenize.TokenError, SyntaxError):
        return []

    lines: dict[int, list[str]] = defaultdict(list)
    skip_types = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                  tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
    for tok in toks:
        if tok.type in skip_types:
            continue
        if tok.type in (tokenize.STRING, getattr(tokenize, 'FSTRING_MIDDLE', -1)):
            text = '"s"'
        else:
            text = tok.string
        lines[tok.start[0]].append(text)

    out: list[str] = []
    real_lineno: list[int] = []
    for k in sorted(lines):
        text = ' '.join(lines[k])
        # **import 行不参与比对**（2026-10-28 实测）：6 个 `window_*.py` mixin
        # 各 import 一遍同样的 PySide6 是真正常的事，把它们并成一个文件没有收益，
        # 反而让"这个 mixin 依赖谁"变隐蔽。所以直接剔掉 —— 榜单只留真代码。
        if text.startswith(('import ', 'from ')):
            continue
        out.append(text)
        real_lineno.append(k + 1)        # tokenize 的行号从 1 起，无需再 +1

    # 滑窗：只比 n 行、且总长够长（太短的行天生重复，报出来没意义）
    blocks = []
    for i in range(len(out) - n + 1):
        chunk = out[i:i + n]
        if not _worth_comparing(chunk):
            continue
        joined = '\n'.join(chunk)
        if len(joined) >= 60 * n // 2:
            blocks.append((joined, real_lineno[i]))
    return blocks


def _worth_comparing(chunk: list[str]) -> bool:
    """
    这段窗口值不值得拿去比重复？**不值得报的不报**。

    挡掉两类噪音：

      · **纯 docstring / 纯标点** —— 剥字符串后只剩 `"s"`，
        那说明"两个模块的开头说明长得像"，不是代码重复；
      · **纯控制词** —— `return` / `try :` / `else :` / `pass` 这些行
        在哪个函数里都长一样，凑够 8 行能凑出一堆"重复"，
        但它们抄不成函数（抄了也没人看得懂）。
    """
    real = [ln for ln in chunk if ln and ln != '"s"']
    if len(real) < max(3, len(chunk) // 2):
        return False
    meaty = [ln for ln in real if ln.split(' ')[0] not in _CONTROL_WORDS]
    return len(meaty) >= 3


def find_duplicates(files: list[Path], n: int) -> list[dict]:
    """跨文件找重复块；同一个文件内部重复也报（那更该抽函数）。"""
    seen: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for p in files:
        rel = str(p.relative_to(ROOT)).replace('\\', '/')
        for chunk, idx in code_skeleton(p, n):
            seen[chunk].append((rel, idx))

    dups = []
    for chunk, locs in seen.items():
        where = {loc[0] for loc in locs}
        if len(locs) < 2:
            continue
        # 同一个文件里连续窗口滑过去会重复报同一段 → 只留第一个位置
        firsts = []
        last = None
        for rel, idx in locs:
            if last is None or rel != last[0] or idx - last[1] > n:
                firsts.append((rel, idx))
            last = (rel, idx)
        if len(firsts) < 2:
            continue
        dups.append({
            'copies': len(firsts),
            'files': len(where),
            'where': [f'{rel}:{idx}' for rel, idx in firsts[:6]],
            'preview': chunk.split('\n')[0][:90],
        })
    dups.sort(key=lambda d: (-d['files'], -d['copies']))
    return _collapse_overlapping(dups, n)


def _collapse_overlapping(dups: list[dict], n: int) -> list[dict]:
    """
    把"同一段重复"只报一次。

    8 行窗口是逐行滑动的：一段真重复 12 行，会产出 5 个互相重叠的 8 行块，
    排行榜上就是同样的代码刷 5 条。这里按"出现位置相距不超过 n 行"判定为同一段，
    只留最靠前的那条。**这一步纯属报告层去噪，不影响判定。**
    """
    shown: dict[str, list[int]] = defaultdict(list)
    kept = []
    for d in dups:
        first = d['where'][0]
        rel, _, line = first.rpartition(':')
        line_no = int(line)
        if any(abs(line_no - prev) <= n for prev in shown.get(rel, ())):
            continue
        kept.append(d)
        for where in d['where']:
            r, _, ln = where.rpartition(':')
            shown[r].append(int(ln))
    return kept


# --------------------------------------------------------------------------
# 三、汇总与打印
# --------------------------------------------------------------------------
def collect(paths: list[Path]) -> dict:
    files = sorted(paths)
    infos = [scan_file(p) for p in files]
    good = [i for i in infos if 'error' not in i]

    code = sum(i['code_lines'] for i in good)
    comment = sum(i['comment_lines'] for i in good)
    dated = sum(i['dated_comments'] for i in good)

    funcs = [dict(f, file=i['path']) for i in good for f in i['functions']]
    long_funcs = sorted([f for f in funcs
                         if f['lines'] >= THRESHOLDS['func_lines'][1]],
                        key=lambda f: -f['lines'])
    hot_funcs = sorted([f for f in funcs
                        if f['complexity'] > THRESHOLDS['complexity'][1]],
                       key=lambda f: -f['complexity'])
    big_files = sorted([(i['lines'], i['path']) for i in good],
                       key=lambda t: -t[0])
    thin_files = sorted([(i['comment_lines'] / i['code_lines'], i['path'],
                          i['code_lines'])
                         for i in good if i['code_lines'] >= 100],
                        key=lambda t: t[0])
    no_doc = sorted([(i['path'], name, line, n)
                     for i in good for name, line, n in i['public_without_doc']],
                    key=lambda t: -t[3])

    return {
        'files': len(good),
        'errors': [(i['path'], i['error']) for i in infos if 'error' in i],
        'lines': sum(i['lines'] for i in good),
        'code_lines': code,
        'comment_lines': comment,
        'comment_ratio': comment / max(code, 1),
        'dated_ratio': dated / max(comment, 1),
        'functions': len(funcs),
        'long_funcs': long_funcs,
        'hot_funcs': hot_funcs,
        'big_files': big_files,
        'thin_files': thin_files,
        'no_doc': no_doc,
        'dups': find_duplicates(files, THRESHOLDS['dup_lines']),
        'docstring_ratio': (sum(1 for f in funcs if f['has_doc'])
                            / max(len(funcs), 1)),
    }


def verdict(value: float, good: float, ok: float, higher_is_better=False) -> str:
    """给一个数贴上"好 / 还行 / 该动手"的标签，只看方向，不做总分。"""
    if higher_is_better:
        return '好' if value >= good else ('还行' if value >= ok else '偏薄')
    return '好' if value <= good else ('还行' if value <= ok else '该动手')


#: `--only` 认的六个名字 → 打印顺序。写死在这里，是为了让 `--only` 不是个假旋钮：
#: 报一个不认识的名字就直接告诉用户有哪些（见 `main`），而不是默默全打。
SECTIONS = ('ratio', 'funcs', 'cc', 'files', 'dups', 'docs')


def print_report(r: dict, top: int, only: set = frozenset(SECTIONS)) -> None:
    p = print
    p('=' * 78)
    p('代码体检 · storygraph')
    p('=' * 78)
    p(f"文件 {r['files']} 个｜总行 {r['lines']:,}｜代码 {r['code_lines']:,}"
      f"｜注释 {r['comment_lines']:,}｜函数 {r['functions']:,}")
    p('')

    if 'ratio' in only:
        p('① 注释率                       '
          f"{r['comment_ratio']:.1%}   [{verdict(r['comment_ratio'], 0.08, 0.05, True)}]")
        p(f"   其中有 docstring 的函数      {r['docstring_ratio']:.1%}")
        p(f"   注释里带日期/审计编号        {r['dated_ratio']:.1%}"
          '   ← 越高说明注释在替文档干活')
        p('')
    if 'funcs' in only:
        # 标签的第二档取"总数的 2%"：181/4153 说明"每 50 个函数就有一个超长"，
        # 比一个拍脑袋的绝对数（比如"超过 20 个就该动手"）更有意义。
        p(f"② 长函数（>{THRESHOLDS['func_lines'][1]} 行）        "
          f"{len(r['long_funcs'])} / {r['functions']} 个"
          f"   [{verdict(len(r['long_funcs']), 0, r['functions'] * 0.02)}]")
        for f in r['long_funcs'][:top]:
            p(f"   {f['lines']:5d} 行  {f['file']}:{f['lineno']}  {f['name']}()")
        p('')
    if 'cc' in only:
        p(f"③ 圈复杂度 >{THRESHOLDS['complexity'][1]}            "
          f"{len(r['hot_funcs'])} 个")
        for f in r['hot_funcs'][:top]:
            p(f"   cc={f['complexity']:4d}  {f['file']}:{f['lineno']}  {f['name']}()"
              f"  ({f['lines']} 行)")
        p('')
    if 'files' in only:
        p(f"④ 巨型文件（>{THRESHOLDS['file_lines'][1]} 行）      "
          f"{sum(1 for n, _ in r['big_files'] if n > THRESHOLDS['file_lines'][1])} 个")
        for n, path in r['big_files'][:top]:
            p(f"   {n:5d} 行  {path}")
        p('')
    if 'dups' in only:
        p(f"⑤ 跨文件重复块（连续 {THRESHOLDS['dup_lines']} 行、剥掉注释与字符串）"
          f"  {len(r['dups'])} 组")
        for d in r['dups'][:top]:
            p(f"   x{d['copies']} 份 / {d['files']} 个文件   {d['preview']}")
            for w in d['where']:
                p(f"        {w}")
        p('')
    if 'docs' in only:
        p(f"⑥ 公开函数 ≥15 行却没有 docstring   {len(r['no_doc'])} 个")
        for path, name, line, n in r['no_doc'][:top]:
            p(f"   {n:4d} 行  {path}:{line}  {name}()")
        if r['thin_files']:
            p('')
            p('  注释最薄的模块（代码行 ≥100）：')
            for ratio, path, c in r['thin_files'][:5]:
                p(f"   {ratio:5.1%}   {path}  ({c} 行代码)")
    if r['errors']:
        p('')
        p('⚠ 解析失败（这些文件没进统计）：')
        for path, err in r['errors']:
            p(f"   {path}  {err}")
    p('')
    p('=' * 78)
    p('结论怎么用：长函数/高复杂度的**排行**比总数有用 —— 修最上面那几条，')
    p('看得见的收益才出现；单纯压总数会把精力花在没人读的地方。')
    p('=' * 78)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='代码体检：简洁度与可读性的六个可测维度')
    ap.add_argument('--path', default='storygraph', help='要体检的目录（默认 storygraph）')
    ap.add_argument('--top', type=int, default=15, help='每个榜单打印多少条')
    ap.add_argument('--only', default='', help='funcs / cc / files / dups / docs')
    ap.add_argument('--json', action='store_true', help='输出 JSON（给 CI 或前后对比）')
    args = ap.parse_args(argv)

    base = ROOT / args.path
    if not base.exists():
        print(f'没有这个目录：{base}')
        return 2
    files = [p for p in base.rglob('*.py') if '__pycache__' not in p.parts]
    if not files:
        print(f'{base} 里没有 .py')
        return 2

    result = collect(files)
    if args.json:
        slim = dict(result)
        slim['long_funcs'] = result['long_funcs'][:args.top]
        slim['hot_funcs'] = result['hot_funcs'][:args.top]
        slim['dups'] = result['dups'][:args.top]
        slim['no_doc'] = result['no_doc'][:args.top]
        slim['big_files'] = [{'lines': n, 'path': p} for n, p in result['big_files'][:args.top]]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return 0

    print_report(result, args.top, _parse_only(args.only))
    return 0


def _parse_only(raw: str) -> set:
    """
    解析 `--only`。**写错名字要当场说出来**，不能默默当"全打" ——
    否则用户以为自己筛过了，其实看的是全量（假旋钮的一种）。
    """
    wanted = {p.strip().lower() for p in (raw or '').split(',') if p.strip()}
    if not wanted:
        return set(SECTIONS)
    unknown = wanted - set(SECTIONS)
    if unknown:
        raise SystemExit(
            f'--only 不认识 {sorted(unknown)}；可用：{", ".join(SECTIONS)}')
    return wanted


if __name__ == '__main__':
    # Windows 控制台默认 GBK，中文表格会炸；显式切 UTF-8 再画表。
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    raise SystemExit(main())
