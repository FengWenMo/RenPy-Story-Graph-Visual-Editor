#!/usr/bin/env python
"""
地狱测试 · 第四轮：**多文件耦合**（轴 A）→ **变宽压力**（轴 B）
================================================================================

前三轮都在**一个文件里**打转：
  一 顶层块 × 结构 × 对抗文本 × 导出路径
  二 结构位 × 行尾注释 × 嵌套深度
  三 编辑序列 × 对抗自己的编码

可真实项目是 **40 个文件互相 `jump`/`call`**：`location` 跳 `map`、`chapter1_2`
调 `mini_game1`、`credits` 被多处 `call`。单文件的每一条不变量都成立，
**不代表跨文件成立** —— 导出甲文件可能悄悄改掉乙文件里的东西（或者更常见：
改掉"甲文件里指向乙的那一跳"）。

--------------------------------------------------------------------------------
§轴 A · 多文件耦合（七条判据）
--------------------------------------------------------------------------------
A1 **只动该动的**：原地导出甲文件，乙/丙/定义文件**逐字节不变**（含 mtime 目录）。
A2 **label 全局守恒**：导出前后，**整库** label 集合相等（不多不少）。
A3 **跳转目标不悬空**：每个 `jump X` / `call X` 的 `X` 都要在整库 label 集合里。
   ⚠ 基线对照：原来就悬空的**不算**新增（真实项目里就有 —— 那是游戏自己的事）。
A4 **导出顺序无关**：逐文件导出（任意顺序）之后，整库状态与"一次导出全部"一致。
A5 **重名 label 必须被拦**：两个文件定义同名 label，产物要落进 `<项目根>/game/`
   → 必须 block（`LABEL_COLLISION`）。`force` 也**不许**越这条。
A6 **`from` 子句原样保留**：`call helper from _call_helper_1` 不许被吃掉
   （丢了它 Ren'Py 的回溯/存档点会错位）。
A7 **循环跳转不炸**：a→b→a 的环不许让导出/构建无限递归或爆栈。

§轴 B · 变宽压力（单人规模，四档）
--------------------------------------------------------------------------------
单 label 200 拍 / 60 个选项 / 300 条注释 / 深度 6。
判据只有两条：**正确性照旧**（结构守恒 + 注释不丢不多 + 逐字节不动点）
+ **时间在预算内**（超时算失败：那说明某处是 O(n²) 或反复全量重解析）。
"变宽"与"变深"是两种不同的坏法：深了会猜错块边界，宽了会撞位置键、
把 O(n) 的循环写成 O(n²)（第一轮真实项目就吃过 90 秒冷缓存的亏）。

用法：
    D:/Python/python.exe tools/hell_cross_file.py            # A + B + 真实项目
    D:/Python/python.exe tools/hell_cross_file.py --only A -v
    D:/Python/python.exe tools/hell_cross_file.py --real     # 只跑真实项目
    D:/Python/python.exe tools/hell_cross_file.py --only B --scale 2
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.discover import discover_scripts
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')
NOTE = '# X:'
_MARKER = '# 由剧本表自动生成'


# ---------------------------------------------------------------------------
#  共用
# ---------------------------------------------------------------------------

def _label_re() -> re.Pattern:
    return re.compile(r'^\s*label\s+([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:')


def _labels_in(text: str) -> set:
    return {m.group(1) for ln in text.splitlines()
            if (m := _label_re().match(ln))}


def _jump_targets(text: str) -> set:
    """
    `jump X` / `call X` 的目标（**排掉** `call screen` / `expression` /
    `call X from Y` 的 from 部分）。
    """
    out = set()
    for ln in text.splitlines():
        s = ln.strip()
        m = re.match(r'^(jump|call)\s+([A-Za-z_]\w*)', s)
        if not m:
            continue
        verb, target = m.group(1), m.group(2)
        if verb == 'call' and target in ('screen', 'expression'):
            continue
        out.add(target)
    return out


def _lines(text: str) -> list:
    return text.splitlines()


def _norm(text: str) -> str:
    lines = [ln.rstrip() for ln in _lines(text) if not ln.startswith(_MARKER)]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def _touched(a: str, b: str) -> int:
    import difflib
    sm = difflib.SequenceMatcher(None, _lines(a), _lines(b), autojunk=False)
    return sum(max(i2 - i1, j2 - j1)
               for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal')


def _note_tails(text: str) -> list:
    """注释**正文**（身份是那段文字，不是它贴在谁后面 —— 三轮的教训）。"""
    return [ln.rsplit('  # ', 1)[-1].strip()
            for ln in _lines(text) if '  # ' in ln]


def _multiset(items) -> dict:
    counts: dict = {}
    for it in items:
        counts[it] = counts.get(it, 0) + 1
    return counts


def _hunks(a: str, b: str, limit: int = 3) -> str:
    """把差异块连**内容**打出来（只看行号没法判断是缺陷还是等价改写）。"""
    import difflib
    sm = difflib.SequenceMatcher(None, _lines(a), _lines(b), autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        old = _lines(a)[i1:i2]
        new = _lines(b)[j1:j2]
        out.append(f'    [{tag}] A[{i1}:{i2}] → B[{j1}:{j2}]\n'
                   f'       A: {old[:2]}\n       B: {new[:2]}')
        if len(out) >= limit:
            break
    return '\n'.join(out)


def _lost_strings(a: str, b: str, skip: str = '') -> list:
    """
    产物在**改动行**上丢掉了哪些"带引号的文本"？返回丢掉的片段。

    比"动了几行"强得多的一条通用判据：它不关心一行动了几次，只问
    "**旧行里有、新行里没有**的引号内容" —— 任何形式的截断（双字符串 say、
    被吃掉的尾巴、条件行覆盖）都会在这里现形。
    `skip` = 我们**故意**改掉的那段文本，它本来就不该出现在产物里。
    """
    import difflib
    old_lines, new_lines = _lines(a), _lines(b)
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    out: list = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag not in ('replace', 'delete'):
            continue
        old_seg, new_seg = old_lines[i1:i2], new_lines[j1:j2]
        for k, ln in enumerate(old_seg):
            # ⚠ 按**位置**配对，跳过"我们故意改的那一对"：
            #   第一版只跳过"旧行里含新文本"的行 —— 而旧行里当然没有新文本，
            #   于是**被改的那一行的旧台词**也被当成"丢失"，真实项目 10 个文件
            #   全假红。判据要跳过的是**新侧**带标记的那一对。
            counterpart = new_seg[k] if k < len(new_seg) else ''
            if skip and skip in counterpart:
                continue
            for s in re.findall(r'"([^"\n]{2,})"', ln):
                if s not in '\n'.join(new_seg):
                    out.append(s)
    return out


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _beats_of(path: Path) -> list:
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _session(path: Path, project_root: Path):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=project_root)
    s.load(path)
    return s


# ===========================================================================
#  轴 A · 多文件耦合
# ===========================================================================

MF_SRC = {
    'a.rpy': '''label a_start:
    e "甲：开场"  # X:a1
    if ready:
        call helper_hub from _call_helper_hub
    jump b_start
''',
    'b.rpy': '''label b_start:
    e "乙：接着"  # X:b1
    menu:
        "去丙":  # X:b2
            jump c_start
        "留在乙":
            call helper_hub
            jump b_start
''',
    'c.rpy': '''init python:
    C_READY = True

transform slide_in:
    xalign 1.0
    linear 0.3 xalign 0.5

label c_start:
    e "丙：收尾"  # X:c1
    jump a_start
''',
    'hub.rpy': '''label helper_hub:
    e "公共：枢纽"  # X:h1
    return
''',
}


def _make_project(base: Path, files: dict) -> Path:
    game = base / 'game'
    game.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (game / name).write_text(body, encoding='utf-8')
    return game


def _snapshot(game: Path) -> dict:
    return {p.name: _read(p) for p in sorted(game.rglob('*.rpy'))}


def run_cross_file(verbose: bool, tmp: Path) -> list:
    bad: list = []
    proj = tmp / 'mf'
    game = _make_project(proj, MF_SRC)
    before = _snapshot(game)
    all_labels_before = set().union(*(_labels_in(t) for t in before.values()))
    targets_before = set().union(*(_jump_targets(t) for t in before.values()))
    dangling_before = targets_before - all_labels_before

    # ---- A3 基线：合成项目本来不该有悬空 ----
    if dangling_before:
        bad.append(('A3 跳转目标', 'E基线',
                    f'合成项目一开始就有悬空跳转：{sorted(dangling_before)}'))
    else:
        print('   · 基线：0 处悬空跳转')

    # ---- A5 重名 label 必须被拦（先做，别污染后面的文件）----
    clash = dict(MF_SRC)
    clash['dup.rpy'] = 'label b_start:\n    e "我是重名的乙"\n'
    game_dup = _make_project(tmp / 'mf_dup', clash)
    s = _session(game_dup / 'dup.rpy', tmp / 'mf_dup')
    res = s.export(game_dup / 'dup.rpy', force=True)
    if res.get('ok'):
        bad.append(('A5 重名', 'E拦下',
                    '两个文件定义同名 label，产物还要落进 game/ —— 竟然写出去了'))
    else:
        codes = [str(c) for c in (res.get('codes') or [])]
        if not any('COLLISION' in c for c in codes):
            bad.append(('A5 重名', 'E拦下',
                        f'拦下了但理由不对（不是 LABEL_COLLISION）：{codes}'))
        else:
            print(f'   · A5 重名 label 被拦下（{codes}）')

    # ---- A1/A2/A6 逐个文件原地导出：跨文件不许受伤 ----
    #
    # ⚠ **先预热**：第一次导出会补生成标记行与末尾 `return`（那是工具的正常
    #    归一，不是缺陷）。不预热的话，这两行会被算进"只改一句台词却动了 4 行"，
    #    而 A4 的冻结点也会因此假红 —— 判据校准清单第 4 条（滤模板噪声）
    #    在这里又咬了一口。预热后基线=工具已产出的形态，与现实一致。
    work = tmp / 'mf_work'
    game_work = _make_project(work, MF_SRC)
    for name in MF_SRC:
        f = game_work / name
        _session(f, work).export(f, force=True)
    cur = _snapshot(game_work)
    labels_base = set().union(*(_labels_in(t) for t in cur.values()))
    tgt_base = set().union(*(_jump_targets(t) for t in cur.values()))
    dangling_before = tgt_base - labels_base
    if dangling_before:
        bad.append(('A3 跳转目标', 'E基线',
                    f'预热后就有悬空跳转：{sorted(dangling_before)}'))

    for name in ('a.rpy', 'b.rpy', 'c.rpy', 'hub.rpy'):
        f = game_work / name
        s = _session(f, work)
        beats = s.beats
        t = next((b for b in beats if (b.text or '').strip()), None)
        if t is None:
            continue
        old_line = f'"{t.text}"'
        t.text = f'改过-{name}'
        res = s.export(f, force=True)
        if not res.get('ok'):
            bad.append((f'A1 {name}', 'E导出',
                        f'导出被拒：{res.get("blocking") or res.get("error")}'))
            continue
        now = _snapshot(game_work)

        # A1 别的文件必须逐字节不变
        for other, body in cur.items():
            if other == name:
                continue
            if now.get(other) != body:
                bad.append((f'A1 {name}', 'E逐字节',
                            f'导出 {name} 却动了 {other}（'
                            f'{_touched(body, now.get(other, ""))} 行）'))

        # A1 自己：只许动那一两行
        touched = _touched(cur[name], now[name])
        if touched > 3:
            bad.append((f'A1 {name}', 'E逐字节',
                        f'只改一句台词却动了 {touched} 行：\n'
                        + _hunks(cur[name], now[name])))
        if f'改过-{name}' not in now[name]:
            bad.append((f'A1 {name}', 'E逐字节', '改的那句话没进产物'))
        if now[name].count(old_line) > cur[name].count(old_line):
            bad.append((f'A1 {name}', 'E逐字节', f'旧台词被复制了：{old_line}'))

        # A2 整库 label 守恒
        all_after = set().union(*(_labels_in(t2) for t2 in now.values()))
        if all_after != labels_base:
            bad.append((f'A2 {name}', 'E守恒',
                        f'整库 label 变了：多 {sorted(all_after - labels_base)} '
                        f'/ 少 {sorted(labels_base - all_after)}'))

        # A3 不许**新增**悬空
        tgt = set().union(*(_jump_targets(t2) for t2 in now.values()))
        dangling = tgt - set().union(*(_labels_in(t2) for t2 in now.values()))
        if dangling - dangling_before:
            bad.append((f'A3 {name}', 'E悬空',
                        f'导出后出现新的悬空跳转：{sorted(dangling - dangling_before)}'))

        # A6 `from` 子句
        if 'from _call_helper_hub' not in now['a.rpy']:
            bad.append((f'A6 {name}', 'Efrom',
                        '`call helper_hub from _call_helper_hub` 的 from 子句没了'))

        cur = now
        if verbose:
            print(f'   · 原地导出 {name}: 动了 {touched} 行')

    # ---- A4 顺序无关：整库再跑一轮必须逐字节稳定（全库冻结点）----
    for name in ('hub.rpy', 'c.rpy', 'b.rpy', 'a.rpy'):     # 故意换个顺序
        f = game_work / name
        s = _session(f, work)
        s.export(f, force=True)
    after_round2 = _snapshot(game_work)
    for name in cur:
        if after_round2[name] != cur[name]:
            bad.append(('A4 顺序无关', 'E不动点',
                        f'{name} 在"不改任何东西再导出"之后又变了 '
                        f'（{_touched(cur[name], after_round2[name])} 行）：\n'
                        + _hunks(cur[name], after_round2[name])))

    # ---- A7 循环跳转：构建整库不许爆 ----
    try:
        proc = subprocess.run(
            [sys.executable, '-m', 'storygraph.cli', 'build', str(work),
             '--out', str(tmp / 'mf_build'), '--no-strict'],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        out = (proc.stdout or '') + (proc.stderr or '')
        built = sorted((tmp / 'mf_build').rglob('*.rpy'))
        if proc.returncode not in (0, 1):
            bad.append(('A7 循环', 'E构建', f'build 退出码 {proc.returncode}：{out[-400:]}'))
        elif not built:
            bad.append(('A7 循环', 'E构建', f'build 没产出任何 .rpy：{out[-300:]}'))
        else:
            merged = '\n'.join(_read(p) for p in built)
            lost = labels_base - _labels_in(merged)
            if lost:
                bad.append(('A7 循环', 'E守恒',
                            f'合并构建产物少了 label：{sorted(lost)}'))
            print(f'   · A7 整库构建：{len(built)} 个产物，'
                  f'label {len(_labels_in(merged))}/{len(labels_base)}')
        if verbose:
            print(out[-600:])
    except subprocess.TimeoutExpired:
        bad.append(('A7 循环', 'E构建', 'build 超过 180 秒（环跳让构建炸了？）'))

    # ---- A8 `"角色名" "台词"`（双字符串 say）不许被截断 ----
    #
    # 真缺陷（2026-10-05 实测，真实项目 `character_gallery.rpy` 2 处）：
    # 解析层把 `"系统" "你已解锁…"` 记成 **`speaker=''` + `text='系统'`** ——
    # 第一个字符串被当成**旁白正文**，第二个（真正的台词）**丢了**。
    # 于是只要**这一章动过任何一处**，那两行就会被改写成残缺的 `"系统"`。
    # 触发面比"改那一句"大得多：改同一章里**任何一行**都会。写在这里当回归钉。
    ds = tmp / 'mf_ds'
    SYS_SRC = ('label sys_note:\n'
               '    e "开头一句"\n'
               '    $ persistent.sys_ready = True\n'
               '    "系统" "这是一句系统提示"\n'
               '    return\n')
    _make_project(ds, {
        'sys.rpy': SYS_SRC,
        'other.rpy': 'label other:\n    e "别的文件少动我"\n',
    })

    # ① 解析层：台词正文必须留在模型里
    sb = _beats_of(ds / 'game' / 'sys.rpy')
    if not any('这是一句系统提示' in str(getattr(b, 'text', '') or '')
               for b in sb):
        bad.append(('A8 双字符串 say', 'E解析',
                    f'模型里没有那句台词正文。拍分别是：'
                    f'{[(b.kind, b.speaker, b.text) for b in sb]}'))

    # ② 文件层：改**同一章里的另一行**（`e "开头一句"`），
    #    这一句的台词正文不许被改写掉 —— 这才是真实的触发面。
    f = ds / 'game' / 'sys.rpy'
    s2 = _session(f, ds)
    head = next(b for b in s2.beats if '开头一句' in str(b.text or ''))
    head.text = '开头改过了'
    res2 = s2.export(f, force=True)
    now_sys = _read(f)
    if '这是一句系统提示' not in now_sys:
        bad.append(('A8 双字符串 say', 'E丢台词',
                    f'只改了同一章的开头一句（export={res2.get("note")!r}），'
                    f'那句系统台词的**正文没了**：\n'
                    + _hunks(SYS_SRC, now_sys)))

    return bad


# ===========================================================================
#  轴 B · 变宽压力（单人规模）
# ===========================================================================

def _wide_src(beats: int, options: int, notes: int, depth: int) -> str:
    """造一份"很宽"的章节：多拍 + 超多选项 + 密集注释。"""
    ind = '    '
    lines = ['label wide:']
    # 深度：交替嵌套
    kinds = ['if' if i % 2 == 0 else 'menu' for i in range(depth)]
    pad = ind
    for i, k in enumerate(kinds):
        if k == 'if':
            lines.append(f'{pad}if f{i}:')
        else:
            lines.append(f'{pad}menu:')
            lines.append(f'{pad}{ind}"深选项{i}":')
            pad += ind
        pad += ind
    # 宽度：选项
    lines.append(pad + 'menu:  ' + NOTE + 'wide_menu')
    body_pad = pad + ind + ind
    for i in range(1, options + 1):
        lines.append(f'{pad}{ind}"选项{i}":  {NOTE}o{i}')
        lines.append(f'{body_pad}e "选项{i}的台词"')
    # 多拍 + 密集注释（**每行只写一个 `#`** —— 第一版写成
    # `# 章节备注0  # X:b0`，第一个 `#` 才是注释正文，第二段只是行内文字，
    # 于是"300 条只贴回 0 条"是我的判据在骗自己）
    for i in range(beats):
        note = f'  # 章节备注{i}' if i < notes else ''
        lines.append(f'{ind}e "第{i}句"{note}')
    return '\n'.join(lines) + '\n'


def run_wide(scale: float, tmp: Path, verbose: bool) -> list:
    bad: list = []
    n_beats = int(200 * scale)
    n_opts = int(60 * scale)
    n_notes = int(300 * scale)
    depth = 6

    t0 = time.perf_counter()
    src = _wide_src(n_beats, n_opts, n_notes, depth)
    d = tmp / 'wide'
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'wide.rpy'
    f.write_text(src, encoding='utf-8')

    beats = _beats_of(f)
    t_parse = time.perf_counter() - t0

    # 位置键：60 个选项 → A..Z 之后必须是 A27..，不许撞键。
    # 判据 = **不同的位置键数量 ≥ 选项数**（少一个就说明两处撞了同一个键）。
    paths = [getattr(b, 'nest_path', '') for b in beats]
    keys = {seg for p in paths for seg in str(p or '').split('>')
            if seg and not seg.startswith(('if:', 'menu:'))}
    if n_opts and len(keys) < n_opts:
        bad.append(('B 位置键', 'E撞键',
                    f'{n_opts} 个选项只数出 {len(keys)} 个不同的位置键'))

    # 注释：每一条都必须**恰好一次**（宽表下最容易翻倍或整批丢）
    #
    # ⚠ 别用 `文本.count('章节备注1')` —— `章节备注1` 是 `章节备注10` 的**子串**，
    #   于是 300 条全被报成"翻倍"（这一条判据我改了三版）。
    #   正确做法：先把注释**正文**精确抽出来，再比多重集。
    r1 = render_beats_to_rpy(beats)
    t_render = time.perf_counter() - t0
    src_notes = re.findall(r'# (章节备注\d+)', src)
    got = _multiset([t for t in _note_tails(r1) if t.startswith('章节备注')])
    missing = [n for n in src_notes if n not in got]
    dup_notes = [k for k, v in got.items() if v > 1]
    invented = [k for k in got if k not in set(src_notes)]
    if missing:
        bad.append(('B 注释', 'E丢失',
                    f'{len(src_notes)} 条里丢了 {len(missing)} 条'
                    f'（例：{missing[:3]}）'))
    if dup_notes:
        bad.append(('B 注释', 'E翻倍', f'例：{dup_notes[:3]}'))
    if invented:
        bad.append(('B 注释', 'E凭空', f'例：{invented[:3]}'))

    # 逐字节不动点
    g = d / 'again.rpy'
    g.write_text(r1, encoding='utf-8')
    r2 = render_beats_to_rpy(_beats_of(g))
    if _norm(r2) != _norm(r1):
        import difflib
        sm = difflib.SequenceMatcher(None, _lines(_norm(r1)), _lines(_norm(r2)))
        first = next((op for op in sm.get_opcodes() if op[0] != 'equal'), None)
        bad.append(('B 不动点', 'E逐字节', f'再渲染变了：{first}'))

    # Excel 两路一致（宽表最容易在这里塌）
    xl = d / 'wide.xlsx'
    t_excel0 = time.perf_counter()
    export_beats_to_excel(beats, xl)
    r3 = render_beats_to_rpy(import_beats_from_excel(xl))
    t_excel = time.perf_counter() - t_excel0
    if _norm(r3) != _norm(r1):
        bad.append(('B 两路', 'E一致', '表格往返后的产物与直接渲染不同'))
    leaked = [ln for ln in _lines(r3) if '\x00' in ln]
    if leaked:
        bad.append(('B 占位符', 'E泄漏', f'{leaked[:1]}'))

    total = time.perf_counter() - t0
    print(f'   · {n_beats} 拍 / {n_opts} 选项 / 深度 {depth}：'
          f'解析 {t_parse:.1f}s 渲染 {t_render - t_parse:.1f}s '
          f'Excel往返 {t_excel:.1f}s 合计 {total:.1f}s')
    if total > 120:
        bad.append(('B 性能', 'E超时',
                    f'合计 {total:.1f}s（变宽不该这么慢：怀疑 O(n²)）'))
    return bad


# ===========================================================================
#  真实项目：跨文件
# ===========================================================================

def run_real(verbose: bool, tmp: Path, limit: int = 0,
             max_touched: int = 4) -> tuple:
    game = REAL_PROJECT / 'game'
    if not game.exists():
        print(f'⚠ 没有真实项目（{REAL_PROJECT}）')
        return [], {}
    files = [p for p in discover_scripts(REAL_PROJECT) if p.suffix == '.rpy']
    if limit:
        files = files[:limit]
    print(f'真实项目跨文件：{len(files)} 个 .rpy（discover_scripts）')

    bad: list = []
    texts = {p: _read(p) for p in files}
    labels0 = set().union(*(_labels_in(t) for t in texts.values()))
    targets0 = set().union(*(_jump_targets(t) for t in texts.values()))
    dangling0 = targets0 - labels0
    print(f'   label {len(labels0)} 个 / 跳转目标 {len(targets0)} 个 / '
          f'本来就悬空 {len(dangling0)} 个'
          + (f'（{sorted(dangling0)[:6]}）' if dangling0 else ''))

    work = tmp / 'real_mf'
    work.mkdir(parents=True, exist_ok=True)
    stat = {'edited': 0, 'max_touched': 0, 'degraded': 0}

    for src in files:
        dst = work / src.name
        shutil.copyfile(src, dst)
        original = _read(dst)
        s = _session(dst, REAL_PROJECT)
        t = next((b for b in s.beats
                  if (b.text or '').strip()
                  and not (b.text or '').startswith('\x00')), None)
        if t is None:
            continue
        t.text = 'Y跨文件改过Y'
        res = s.export(dst, force=True)
        if not res.get('ok'):
            bad.append((src.name, 'E导出',
                        f'被拒：{res.get("blocking") or res.get("error")}'))
            continue
        now = _read(dst)
        touched = _touched(original, now)
        stat['max_touched'] = max(stat['max_touched'], touched)
        stat['edited'] += 1
        if touched > 5:
            stat['degraded'] += 1
            print(f'   · {src.name}: 降级退回全量（{touched} 行）—— '
                  f'{str(res.get("note"))[:60]}')
            continue
        # 单文件：只许动那一两行
        if touched > max_touched:
            bad.append((src.name, 'E1逐字节',
                        f'只改一句台词却动了 {touched} 行（上限 {max_touched}）：\n'
                        + _hunks(original, now)))
        # 通用判据：改动行上不许**丢引号文本**（截断/吃尾巴都会在这里现形）
        lost = _lost_strings(original, now, skip='Y跨文件改过Y')
        if lost:
            bad.append((src.name, 'E1丢文本',
                        f'改动行上丢了这些文本：{lost[:3]}\n'
                        + _hunks(original, now)))
        # 整库 label / 跳转目标守恒（把这一份换掉，其余按原文算）
        labels1 = (labels0 - _labels_in(original)) | _labels_in(now)
        if labels1 != labels0:
            bad.append((src.name, 'E2守恒',
                        f'label 变了：多 {sorted(labels1 - labels0)} '
                        f'/ 少 {sorted(labels0 - labels1)}'))
        targets1 = (targets0 - _jump_targets(original)) | _jump_targets(now)
        dangling1 = targets1 - labels1
        if dangling1 - dangling0:
            bad.append((src.name, 'E3悬空',
                        f'出现新的悬空跳转：{sorted(dangling1 - dangling0)[:4]}'))
        # 注释不许新增
        added = [k for k in _multiset(_note_tails(now))
                 if _multiset(_note_tails(now))[k]
                 > _multiset(_note_tails(original)).get(k, 0)]
        if added:
            bad.append((src.name, 'E注释', f'注释比原文多了：{added[:2]}'))
        if verbose:
            print(f'   · {src.name}: 动了 {touched} 行')

    print(f'   改过 {stat["edited"]} 个 / 单次最多动 {stat["max_touched"]} 行 / '
          f'退回全量 {stat["degraded"]} 个')
    return bad, stat


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--real', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--max-touched', type=int, default=4,
                    help='原地编辑允许动的行数上限（默认 4）')
    ap.add_argument('--scale', type=float, default=1.0,
                    help='变宽压力的规模倍数（单人默认 1.0）')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='hell_mf_'))
    fails = 0
    try:
        groups = []
        if not args.real:
            groups.append(('A 多文件耦合',
                           lambda: run_cross_file(args.verbose, tmp)))
            groups.append(('B 变宽压力',
                           lambda: run_wide(args.scale, tmp, args.verbose)))
        for title, fn in groups:
            if args.only and args.only not in title:
                continue
            print(f'===== {title} =====')
            bad = fn()
            for name, judge, msg in bad:
                print(f'[FAIL] {name} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] {title}（{len(bad)} 处）\n')
            fails += len(bad)

        if not args.only or '真实' in args.only:
            bad, _st = run_real(args.verbose, tmp, args.limit,
                                args.max_touched)
            for name, judge, msg in bad[:20]:
                print(f'[FAIL] {name} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] 真实项目跨文件（{len(bad)} 处）')
            fails += len(bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'\n失败合计：{fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
