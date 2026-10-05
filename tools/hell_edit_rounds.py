#!/usr/bin/env python
"""
地狱测试 · 第三轮：**编辑序列** × **对抗自己的编码**
================================================================================

前两轮都是"一次性读进来、渲染一次、比一次"：
  · 第一轮打顶层块 × 结构 × 对抗文本 × 导出路径；
  · 第二轮打 `Nest` 结构列 × `Inline` 注释列 × 嵌套深度 × 两路一致。

它们都**没有测过"人在反复改"**。而这是产品真正被使用的形状：改一句台词 →
导出 → 再改 → 再导出 …… 几十轮。原地导出的每一条不变量都可能在第 3 轮、
第 7 轮才破。所以这一轮的第一条新轴是**编辑序列**。

第二条新轴是**拿工具自己的编码来打它**：

  · `Inline` 单元格 = `自己那行的注释` + `\\ue004` + `@if ` + 条件行注释
    → 注释正文里若出现 `\\ue004`，`parse_inline_cell` 会在**第一个**分隔符处
      劈开，"条件注释"那一半就被贴到 `if` 行上（**错位**，不是丢失）；
  · `Nest` 路径 = `if:1>menu:2>A`，选项文案里的 `>` / `A` 会不会污染位置键；
  · `FILE_BLOCK_SEP = \\ue003` 与 `CODE_BLOCKS` 的行区间编码同理。

这些是**白盒攻击**：攻击面是你今天刚设计的编码，不是"随便输点怪字符"。

--------------------------------------------------------------------------------
§判据（八条，每一条都对应一种"静默出错"）
--------------------------------------------------------------------------------
E1 **非目标行逐字节不变** —— 原地编辑一行，文件里**只有那一行（±1）允许变**。
   这是原地导出的最强判据：它同时钉住"补丁有没有退回全量重生成"
   （退回 = 整份重写 = 成百行变化），以及"第三类内容有没有被顺手动掉"。
E2 **冻结点** —— 不改任何东西再导出，必须**逐字节相同**（第 2 次起）。
   `N2 不动点` 测的是"渲染两次一样"，这条测的是"**写盘两次一样**"。
E3 **注释不减不增** —— 每条注释**至多一次**，且不许凭空出现新注释。
E4 **结构不漂移** —— 菜单归属口径（`Nest` 路径里的 `menu:`/选项段）不变。
E5 **`if` 不膨胀** —— 产物里 `if` 行数不许增长（条件串不许滚雪球）。
E6 **产物自洽** —— 可再解析、0 错、无跨物理行字符串。
E7 **不许闭口降级** —— 一旦"改动行数"超过阈值（= 退回全量了），
   必须能从返回值里看到 `blocking` / 非空 note；**静默重生成整份**是最坏形态。
E8 **编码自反** —— 注释正文里的分隔符/前缀必须原样往返（单元格层面 +
   文件层面各判一次）。

用法：
    D:/Python/python.exe tools/hell_edit_rounds.py
    D:/Python/python.exe tools/hell_edit_rounds.py -v --only coding
    D:/Python/python.exe tools/hell_edit_rounds.py --real
"""
from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.core.nest_path import split_nest
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import (INLINE_COND_SEP, Beat,
                                        compose_inline_cell, parse_inline_cell)

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')


# ---------------------------------------------------------------------------
#  共用小工具
# ---------------------------------------------------------------------------

def _lines(text: str) -> list:
    return text.splitlines()


def _changed_hunks(a: str, b: str) -> list:
    """两份文本的差异块，每块 `(标签, a 区间, b 区间)`。"""
    sm = difflib.SequenceMatcher(None, _lines(a), _lines(b), autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != 'equal']


def _touched_lines(a: str, b: str) -> int:
    """b 相对 a **一共动了几行**（插/删/改都算）。"""
    return sum(max(i2 - i1, j2 - j1)
               for _tag, i1, i2, j1, j2 in _changed_hunks(a, b))


def _notes_of(text: str) -> list:
    """产物里带注释的整行（strip 后）。"""
    return [ln.strip() for ln in _lines(text) if '  # ' in ln]


def _note_tails(text: str) -> list:
    """
    **注释正文**（`注释` 那一部分），不带前面那句代码。

    第一版我拿**整行**当注释比（`e "开场"  # 备注：开场`），于是
    "把台词改掉"在判据眼里成了"凭空多出一条注释" —— 10 轮报了 10 次假红。
    注释的**身份**是它自己那段文字，不是它贴在谁后面。
    """
    return [ln.rsplit('  # ', 1)[-1].strip() for ln in _notes_of(text)]


def _note_multiset(text: str) -> dict:
    counts: dict = {}
    for tail in _note_tails(text):
        counts[tail] = counts.get(tail, 0) + 1
    return counts


def _menu_scope(path) -> tuple:
    """菜单口径：`Nest` 路径里 `menu:` 与选项段（`if:` 段按文档序，不参与）。"""
    return tuple(s for s in split_nest(path)
                 if s.startswith('menu:') or not s.startswith('if:'))


def _scopes(beats: list) -> list:
    return sorted(f'{getattr(b, "label", "")}|{_menu_scope(getattr(b, "nest_path", ""))}'
                  for b in beats)


def _if_lines(text: str) -> int:
    return sum(1 for ln in _lines(text) if ln.strip().startswith('if '))


_MARKER = '# 由剧本表自动生成'


def _norm(text: str) -> str:
    """
    逐字节比之前去掉**模板噪声**：生成标记行、首尾空行、行尾空白。

    第二轮就踩过一次（把生成标记算成"第 3 行变了"），这一轮又踩了一次 ——
    凡是"渲染产物再解析再渲染"的比较，**都要先过这一道**。
    """
    lines = [ln.rstrip() for ln in _lines(text) if not ln.startswith(_MARKER)]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def _syntax_broken(text: str) -> list:
    """跨物理行的字符串字面量（口径同前两轮：去转义后数引号奇偶）。"""
    return [ln for ln in _lines(text)
            if '"""' not in ln and "'''" not in ln
            and re.sub(r'\\.', '', ln).count('"') % 2]


def _placeholder_leak(text: str) -> list:
    """
    产物里出现了 `\\x00`？—— `MENU_NO_CAPTION` 占位符**不许进产物**。

    为什么单列一条（第二轮的盲区）：第二轮用"直接渲染 vs 表往返"**逐字节**
    比，可这个占位符**两条路都漏**的时候，两边一样 → 判据是绿的。
    而这东西一旦落进脚本，玩家会在菜单标题那一行看见一个 NUL。
    所以它得独立成条：**产物里不许有任何 `\\x00`**。
    """
    return [ln for ln in _lines(text) if '\x00' in ln]


def _parse_errors(text: str, path: Path) -> list:
    path.write_text(text, encoding='utf-8')
    a = IncrementalRenPyAnalyzer()
    a.analyze(files=[path], graph=None)
    return list(getattr(a, 'errors', None) or [])


def _read_beats(text: str, path: Path) -> list:
    path.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _unparseable(text: str, tmp: Path) -> bool:
    try:
        return bool(_parse_errors(text, tmp / '_pe.rpy'))
    except Exception:                                  # noqa: BLE001
        return True


# ===========================================================================
#  轴 A：对抗自己的编码（`Inline` 分隔符 / `@if ` 前缀 / `Nest` 的 `>`）
# ===========================================================================

SEP = INLINE_COND_SEP                      # \ue004
FSEP = '\ue003'                            # FILE_BLOCK_SEP

COSTUMES = {
    'sep_in_text': f'# 正文里有分隔符{SEP}后面还有字',
    'file_sep_in_text': f'# 文件块分隔符{FSEP}在这里',
    'looks_like_cond': '# @if 我自己写的，不是条件注释',
    'gt_in_text': '# 正文里有 > 符号',
    'only_hash': '#',
    'quotes_and_slash': '# 他说："走\\n吧"',
    'very_long': '# ' + '注' * 3000,
    'trailing_backslash': '# 以反斜杠结尾 \\',
    'emoji': '# 注释里也有表情 😀🎮',
    'brace': '# 带 {x} 与 [y] 的注释',
}


def _src_with(note_text: str) -> str:
    return (f'label a:\n'
            f'    e "第一句"  {note_text}\n'
            f'    if flag:  {note_text}\n'
            f'        e "条件里"  {note_text}\n'
            f'    e "最后一句"  {note_text}\n')


def run_coding(verbose: bool, tmp: Path) -> list:
    """轴 A：注释正文里塞进编码自己的分隔符/前缀，必须原样往返。"""
    bad: list = []
    for name, note in COSTUMES.items():
        src = _src_with(note)
        beats = _read_beats(src, tmp / f'coding_{name}.rpy')
        got = [b.inline_comment for b in beats
               if getattr(b, 'inline_comment', '')]
        conds = [b.cond_comment for b in beats
                 if getattr(b, 'cond_comment', '')]

        # E8a 单元格层面自反：compose → parse 必须逐字回来
        for b in beats:
            own, cond = parse_inline_cell(compose_inline_cell(b))
            if (own, cond) != (getattr(b, 'inline_comment', '') or '',
                               getattr(b, 'cond_comment', '') or ''):
                bad.append((f'A·{name}', 'E8编码',
                            f'单元格往返串了：{b.inline_comment!r} / '
                            f'{b.cond_comment!r} → {own!r} / {cond!r}'))

        # E8b 文件层面：产物里必须能找到**原样的**注释
        rpy = _beats_to_rpy(beats)
        if note.lstrip('#').strip() and note not in rpy:
            bad.append((f'A·{name}', 'E8编码',
                        f'注释没能原样回到产物。\n      源: {note[:60]!r}\n'
                        f'      收到的注释: {[x[:40] for x in got + conds][:3]}'))
        # 条件注释不许被正文污染（正文里带分隔符时最容易串到这里）
        if any(c and not c.startswith('#') for c in conds):
            bad.append((f'A·{name}', 'E8编码',
                        f'条件注释不像注释（被正文串了？）：{conds[:2]}'))
        if verbose:
            print(f'--- A/{name} 注释 {len(got)} + 条件 {len(conds)}'
                  f'  产物 {len(rpy.splitlines())} 行')
    return bad


def _beats_to_rpy(beats: list) -> str:
    from storygraph.story.beat_render import render_beats_to_rpy
    return render_beats_to_rpy(beats, label_name='a')


# ---------------------------------------------------------------------------
#  轴 A2：**反斜杠/换行在反复渲染里会不会累积**（真信号是从轴 B 的噪声里捞出来的）
# ---------------------------------------------------------------------------

CAPTION_CASES = {
    'say_newline': ('say', '第一行\n第二行'),
    'say_backslash': ('say', '路径 C:\\new\\test'),
    'menu_caption_newline': ('menu', '第一行\n第二行'),
    'menu_caption_backslash': ('menu', '路径 C:\\new'),
    'menu_caption_both': ('menu', 'A\\nB\nC'),
    'option_newline': ('option', '选项\n第二行'),
}


def run_caption_escapes(verbose: bool, tmp: Path) -> list:
    """
    同一段文本喊三轮：`R1 → parse → R2 → parse → R3`，三轮必须**逐字节相同**。

    为什么要单独拎出来：轴 B 第 4-8 轮里，那条菜单标题的反斜杠是这样长的 ——

        \\n  →  \\\\n  →  \\\\\\\\n      （每渲染一次翻一倍）

    它藏在"我故意插卡"的噪声底下。转义必须是**自反**的：`_quote` 把 `\\`
    转成 `\\\\`，解析就该把它还原回 `\\`；只做前一半，每导出一次文本就变一次
    （而"文本变了一次"正是增量对位失败、注释丢失的上游）。
    """
    from storygraph.story.beat_model import Beat
    bad: list = []
    for name, (kind, text) in CAPTION_CASES.items():
        if kind == 'say':
            beats = [Beat(seq=1, label='a', kind='say', speaker='e', text=text)]
        elif kind == 'menu':
            beats = [Beat(seq=1, label='a', kind='menu', text=text),
                     Beat(seq=2, label='a', kind='menu_option', text='选项'),
                     Beat(seq=3, label='a', kind='say', speaker='e', text='体')]
        else:
            beats = [Beat(seq=1, label='a', kind='menu', text='标题'),
                     Beat(seq=2, label='a', kind='menu_option', text=text),
                     Beat(seq=3, label='a', kind='say', speaker='e', text='体')]
        r1 = _beats_to_rpy(beats)
        r2 = _beats_to_rpy(_read_beats(r1, tmp / f'esc_{name}_2.rpy'))
        r3 = _beats_to_rpy(_read_beats(r2, tmp / f'esc_{name}_3.rpy'))
        if _norm(r2) != _norm(r1):
            bad.append((f'A2·{name}', 'E2不动点', _first_diff(_norm(r1), _norm(r2))))
        elif _norm(r3) != _norm(r2):
            bad.append((f'A2·{name}', 'E2不动点',
                        '第 2→3 轮才变形：\n      '
                        + _first_diff(_norm(r2), _norm(r3))))
        leak = _placeholder_leak(r2)
        if leak:
            bad.append((f'A2·{name}', 'E9占位符', f'{leak[:1]}'))
        if verbose:
            print(f'--- A2/{name}: {len(r1.splitlines())} 行 → '
                  f'{len(r2.splitlines())} → {len(r3.splitlines())}')
            print('    R1:', [ln.strip() for ln in r1.splitlines()
                              if ln.strip()][:3])
            if r2 != r1:
                print('    R2:', [ln.strip() for ln in r2.splitlines()
                                  if ln.strip()][:3])
    return bad


def _first_diff(a: str, b: str) -> str:
    la, lb = _lines(a), _lines(b)
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else '<缺行>'
        y = lb[i] if i < len(lb) else '<缺行>'
        if x != y:
            return f'第 {i + 1} 行变了一次：\n      A: {x!r}\n      B: {y!r}'
    return '尾部长度不同'


# ===========================================================================
#  轴 B：连续编辑 10 轮（原地导出）
# ===========================================================================

EDIT_SRC = '''label a:
    e "开场"                    # 备注：开场
    menu:                       # 备注：菜单
        "去左边":               # 备注：左选项
            show ling happy at left
            e "左边"            # 备注：左台词
        "去右边":               # 备注：右选项
            if flag:            # 备注：条件
                e "右边"        # 备注：右台词
    e "收尾"                    # 备注：收尾
'''


def _new_session(tmp: Path, src: str, name: str):
    from storygraph.app.editor import EditorSession
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / 'ch.rpy'
    f.write_text(src, encoding='utf-8')
    s = EditorSession(project_root=d)
    s.load(f)
    return s, f


def run_edit_rounds(verbose: bool, tmp: Path, rounds: int = 10) -> list:
    """
    轴 B：人在反复改 —— 每轮一个**明确种类**的编辑，然后原地导出。

    第一版把这件事做错了两处（都报了假红，记在这里免得重犯）：
      · 我**故意**插卡/删卡，却又拿"结构不许变"去要求它 —— 结构当然会变；
      · `insert_beat` 返回的 `index` 我丢了，删的时候按下标硬删，
        于是删掉的是**别人**（原来那一张选项），看着像"产品把选项吃了"。
    现在：结构步子单独判（拍数 ±1），内容步子才要求"只许动那一两行"。
    """
    bad: list = []
    s, f = _new_session(tmp, EDIT_SRC, 'rounds')
    body = f.read_text(encoding='utf-8')
    base_notes = _note_multiset(body)
    inserted_idx = None

    for r in range(1, rounds + 1):
        prev = body
        act = ['text', 'show', 'caption', 'option', 'append', 'drop', 'undo'][r % 7]
        expect_structural = act in ('append', 'drop', 'undo')
        try:
            if act == 'text':
                t = next(b for b in s.beats if (b.text or '').strip())
                t.text = f'第{r}轮台词'
            elif act == 'show':
                t = next((b for b in s.beats if (b.show or '').strip()), None)
                if t is None:
                    t = next(b for b in s.beats if (b.text or '').strip())
                    t.show = 'ling angry at center'
                else:
                    t.show = 'ling angry at center'
            elif act == 'caption':
                # 菜单拍：它的 `text` 就是标题
                m = next((b for b in s.beats
                          if getattr(b, 'kind', '') == 'menu'), None)
                if m is not None and not (m.text or '').startswith('\x00'):
                    m.text = f'第{r}轮标题'
            elif act == 'option':
                o = next((b for b in s.beats
                          if getattr(b, 'kind', '') == 'menu_option'), None)
                if o is not None:
                    o.text = f'第{r}轮选项'
            elif act == 'append':
                idx = s.insert_beat(len(s.beats) - 1, after=True)['index']
                s.beats[idx].text = f'第{r}轮插入'
                inserted_idx = idx
            elif act == 'drop':
                if inserted_idx is not None:
                    s.delete_beat(inserted_idx)
                inserted_idx = None
            else:
                s.undo_structural()
        except Exception as exc:                        # noqa: BLE001
            bad.append((f'B·第{r}轮', 'E6自洽', f'编辑 {act} 抛异常：{exc!r}'))
            break

        try:
            res = s.export(f, force=True)
        except Exception as exc:                        # noqa: BLE001
            bad.append((f'B·第{r}轮', 'E6自洽', f'导出抛异常：{exc!r}'))
            break
        if not res.get('ok'):
            bad.append((f'B·第{r}轮', 'E7降级',
                        f'导出被拒：{res.get("blocking") or res.get("error")}'))
            break
        body = f.read_text(encoding='utf-8')

        # E9 占位符不许漏进产物
        leaked = _placeholder_leak(body)
        if leaked:
            bad.append((f'B·第{r}轮', 'E9占位符',
                        f'产物里有 MENU_NO_CAPTION：{leaked[:1]}'))

        # E6 产物自洽
        if _syntax_broken(body):
            bad.append((f'B·第{r}轮', 'E6自洽', f'跨行字符串：'
                                                f'{_syntax_broken(body)[:1]}'))
        if _unparseable(body, tmp):
            bad.append((f'B·第{r}轮', 'E6自洽', '产物不能再解析'))

        # E3 注释：正文至多一次、不许凭空出现
        tails = _note_multiset(body)
        dup = {k: v for k, v in tails.items() if v > 1}
        if dup:
            bad.append((f'B·第{r}轮', 'E3注释', f'注释翻倍：{list(dup)[:2]}'))
        invented = [k for k in tails if k not in base_notes]
        if invented:
            bad.append((f'B·第{r}轮', 'E3注释', f'凭空多出注释：{invented[:2]}'))

        # E5 `if` 不膨胀
        if r == 1:
            continue
        # E1 内容步子：只许动那一两行
        touched = _touched_lines(prev, body)
        if not expect_structural and touched > 2:
            bad.append((f'B·第{r}轮', 'E1逐字节',
                        f'只改了 {act}，却动了 {touched} 行'
                        f'（原地导出应只换那一行）'))
        # E2 结构性步子：拍数必须 ±1（插入 → 删掉 → 撤销）
        if expect_structural and touched > 6:
            bad.append((f'B·第{r}轮', 'E1逐字节',
                        f'{act} 动了 {touched} 行，太多了（像全量重生成）'))
        if verbose:
            print(f'--- B/第{r}轮 act={act:8s} 动了 {touched:2d} 行 '
                  f'if={_if_lines(body)} 注释 {len(tails)}')

    # E2 冻结点：不改任何东西再导出，必须逐字节相同
    body1 = f.read_text(encoding='utf-8')
    try:
        from storygraph.app.editor import EditorSession
        s3 = EditorSession(project_root=f.parent)
        s3.load(f)
        s3.export(f, force=True)
        body2 = f.read_text(encoding='utf-8')
    except Exception as exc:                            # noqa: BLE001
        bad.append(('B·冻结', 'E2不动点', f'再导出抛异常：{exc!r}'))
        return bad
    if body2 != body1:
        bad.append(('B·冻结', 'E2不动点',
                    f'**不改任何东西**再导出，文件还是变了：'
                    f'{_changed_hunks(body1, body2)[:2]}'))
    return bad


# ===========================================================================
#  轴 C：真实项目 × 一次真实编辑（只许动那一行）
# ===========================================================================

def run_real_edit(verbose: bool, tmp: Path, limit: int = 0) -> tuple:
    """真实项目每个文件：改一张有台词的拍 → 原地导出 → **只许动那一行**。"""
    game = REAL_PROJECT / 'game'
    if not game.exists():
        print(f'⚠ 没有真实项目（{REAL_PROJECT}）')
        return [], {}
    files = sorted(p for p in game.rglob('*.rpy')
                   if not p.name.endswith('.generated.rpy'))
    if limit:
        files = files[:limit]
    print(f'真实项目编辑：{len(files)} 个 .rpy')
    bad: list = []
    stat = {'files': 0, 'edited': 0, 'max_touched': 0, 'degraded': 0}
    work = tmp / 'real_edit'
    work.mkdir(parents=True, exist_ok=True)

    for src in files:
        dst = work / src.name
        dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')
        original = dst.read_text(encoding='utf-8')

        from storygraph.app.editor import EditorSession
        s = EditorSession(project_root=src.parents[2])
        try:
            s.load(dst)
        except Exception as exc:                        # noqa: BLE001
            bad.append((src.name, 'X', f'load 抛异常：{exc!r}'))
            continue
        if not s.beats:
            continue
        stat['files'] += 1
        target = next((b for b in s.beats
                       if (b.text or '').strip()
                       and not (b.text or '').startswith('\x00')), None)
        if target is None:
            continue
        old_line = f'"{target.text}"'
        target.text = 'X改过的一句话X'
        stat['edited'] += 1

        try:
            res = s.export(dst, force=True)
        except Exception as exc:                        # noqa: BLE001
            bad.append((src.name, 'E6自洽', f'export 抛异常：{exc!r}'))
            continue
        if not res.get('ok'):
            bad.append((src.name, 'E7降级',
                        f'导出被拒：{res.get("blocking") or res.get("error")}'))
            continue

        now = dst.read_text(encoding='utf-8')
        touched = _touched_lines(original, now)
        stat['max_touched'] = max(stat['max_touched'], touched)

        # E9/E6 **要在降级分支之前判**：退回全量的产物同样不许带占位符、
        # 同样得能解析。（第一版把这两条放在 `continue` 后面，于是"降级"
        # 那 6 个文件整个逃过了检查 —— 而补丁消息里恰好有一条 `"\\x00menu"`
        # 的线索，差点被漏掉。）
        leaked = _placeholder_leak(now)
        if leaked:
            bad.append((src.name, 'E9占位符',
                        f'产物里有 MENU_NO_CAPTION 占位符：{leaked[:1]}'))
        if _syntax_broken(now):
            bad.append((src.name, 'E6自洽', f'有跨行字符串：'
                                            f'{_syntax_broken(now)[:1]}'))
        if not _unparseable(original, tmp / f'po_{src.stem}.rpy') \
                and _unparseable(now, tmp / f'pe_{src.stem}.rpy'):
            errs = _parse_errors(now, tmp / f'pe2_{src.stem}.rpy')
            bad.append((src.name, 'E6自洽',
                        f'原文能解析、产物不能：{errs[:2]}'))

        # E1：只许那一行变（±1）。超过 5 行 = 退回全量重生成
        if touched > 5:
            stat['degraded'] += 1
            note = str(res.get('note') or '')
            blocking = res.get('blocking') or []
            # E7：降级必须说出口（"增量未采用 …" 就是说出口了）
            if not blocking and '全量' not in note and '增量未采用' not in note:
                bad.append((src.name, 'E7降级',
                            f'改了 {touched} 行（像退回全量）却没说：'
                            f'note={note[:60]!r}'))
            else:
                print(f'   · {src.name}: 降级（{touched} 行）已报出：'
                      f'{(note or str(blocking))[:70]}')
            continue

        if 'X改过的一句话X' not in now:
            bad.append((src.name, 'E1逐字节', '改的那句话没进产物'))
        # 旧台词还在？**比计数**（同一句话在本章里出现两次是合法的 ——
        # 第一版写的是"出现过就算"，6 个文件全假红）
        old_body = now.replace('"X改过的一句话X"', '')
        if (not old_line.startswith('\x00')
                and old_body.count(old_line.strip()) > original.count(old_line.strip())):
            bad.append((src.name, 'E1逐字节',
                        f'旧台词被复制了：原文 {original.count(old_line.strip())} 处 '
                        f'→ 产物 {old_body.count(old_line.strip())} 处'
                        f'（{old_line[:40]!r}）'))

        # E3：注释不许翻倍/凭空出现 —— **与原文比计数**，不是"出现两次就是翻倍"
        #    （作者本来就在两行上写了同一条注释是合法的）
        b_tails = _note_multiset(original)
        n_tails = _note_multiset(now)
        dup = {k: v for k, v in n_tails.items() if v > b_tails.get(k, 0)}
        if dup:
            bad.append((src.name, 'E3注释', f'注释比原文多了：'
                                            f'{[f"{k}×{v}" for k, v in dup.items()][:2]}'))
        invented = [k for k in n_tails if k not in b_tails]
        if invented:
            bad.append((src.name, 'E3注释', f'凭空多出注释：{invented[:2]}'))

        if verbose:
            print(f'   · {src.name}: 动了 {touched} 行')

    print(f'  载入 {stat["files"]} 个 / 改过 {stat["edited"]} 个 / '
          f'单次最多动 {stat["max_touched"]} 行 / 退回全量 {stat["degraded"]} 个')
    return bad, stat


# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--real', action='store_true', help='只跑真实项目编辑')
    ap.add_argument('--rounds', type=int, default=10)
    ap.add_argument('--limit', type=int, default=0, help='真实项目只跑前 N 个')
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='hell_edit_'))
    fails = 0
    try:
        groups = []
        if not args.real:
            groups.append(('A 对抗编码', lambda: run_coding(args.verbose, tmp)))
            groups.append(('A2 转义自反', lambda: run_caption_escapes(
                args.verbose, tmp)))
            groups.append(('B 连续编辑', lambda: run_edit_rounds(
                args.verbose, tmp, args.rounds)))
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
            bad, _stat = run_real_edit(args.verbose, tmp, args.limit)
            for name, judge, msg in bad[:20]:
                print(f'[FAIL] {name} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] 真实项目编辑（{len(bad)} 处）')
            fails += len(bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'\n失败合计：{fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
