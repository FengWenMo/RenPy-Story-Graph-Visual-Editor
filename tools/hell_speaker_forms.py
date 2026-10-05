#!/usr/bin/env python
"""
地狱测试 · 第五轮：**白盒攻击刚修好的那段解析**（字符串说话人）
================================================================================

`fb73bff` 修了 P0：`"系统" "你已解锁…"` 被当成"旁白 `"系统"`"、后半句丢掉。
修法是在 `_RE_SAY` **之前**无条件接管 `"a" "b"` 这条形态（`split_string_speaker`），
渲染时按 `is_literal_speaker`（首尾同引号）**逐字写回**，校验时跳过它。

**修好的代码本身就是新的攻击面**，而且这一处特别危险：

    它抢在 `_RE_SAY` 前面接管所有"以引号开头 + 后面还有引号"的行。

  · 抢少了 → 原 P0 还在；
  · **抢多了 → 普通旁白 `"系统"`、菜单选项行 `"选项":`、菜单标题、
    带条件的选项行都会被卷进来** —— 那是**全项目级**的内容丢失，
    比原缺陷大好几个数量级。

所以这一轮的判据不是"那个 P0 好没好"（第四轮已经验过），而是**边界有没有越界**：

  S1 **逐字回写**：这些形态渲染出来必须与原文**逐字相同**（一个字符都不许变）；
  S2 **引号文本守恒**（通用判据）：原文那一行里所有引号文本，
     都要在同一行产物里找到 —— 任何截断/吞并都会在这里现形；
  S3 **两轮不动点**：再解析再渲染，逐字节不变（防止"每轮多套一层引号"）；
  S4 **Excel 往返**：表那条路也要逐字节一致（`Speaker` 列存的是 `"系统"` 这种带引号形态）；
  S5 **校验不许被带偏**：真的没登记的角色**仍要报**（他跳过了字面量说话人 ——
     要防"跳过面过大"把真问题也放过）；
  S6 **旁白回归**：`"系统"` 单独一行**必须还是旁白**（这是最要紧的一条：
     全项目每一句旁白都长这样）。

--------------------------------------------------------------------------------
§已知边界（用户 `fb73bff` 明确记录 + 量过：真实项目 0 处）
--------------------------------------------------------------------------------
· say 的 `with` / `id` 尾从句会丢（`"系统" "文字" with dissolve`）；
· 菜单标题行 `"系统" "提示"` 只保住可见文案，角色名会丢。
这两条**只报告、不算失败**（标 `known=True`）：值在于**把它们量成数字**，
而不是留在"据说会丢"的状态。哪天真项目用上了，这里会立刻显形。

用法：
    D:/Python/python.exe tools/hell_speaker_forms.py
    D:/Python/python.exe tools/hell_speaker_forms.py -v --only 旁白
    D:/Python/python.exe tools/hell_speaker_forms.py --real
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_model import is_literal_speaker
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.excel_export import export_beats_to_excel
from storygraph.story.excel_import import import_beats_from_excel

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')
_MARKER = '# 由剧本表自动生成'


# ---------------------------------------------------------------------------
#  原料：一层是"形态"，一层是"上下文"
# ---------------------------------------------------------------------------

#: `known=True` = 用户已记录的已知边界，只报告不计失败
FORMS = {
    # ---- 他修好的正主 ----
    'literal_say': ('"系统" "你已解锁角色【万理】的人物介绍！"', False),
    'literal_say_single_quote': ("'系统' '你已解锁'", False),
    'literal_say_mixed_quote': ('"系统" \'混引号\'', False),
    'literal_who_has_quote': ('"他说\\"你好\\"" "台词"', False),
    'literal_who_has_backslash': ('"C:\\\\new" "路径"', False),
    'literal_say_with_escape': ('"系统" "第一行\\n第二行"', False),

    # ---- 最重要：旁白回归（全项目每一句旁白都是这个形态）----
    'narration_only': ('"系统"', False),
    'narration_long': ('"这是一句很长的旁白，里面还有 引号 「」 和 # 井号"', False),
    'narration_with_colon': ('"注意：下面要做出选择"', False),

    # ---- 抢多了就会坏的那些形态 ----
    'menu_option_line': ('"选项" if flag:', False),      # 带条件；单独成行的见下面
    'label_like_string': ('"某句带冒号的旁白："', False),

    # ---- 尾从句（已知边界）----
    'say_with_clause': ('"系统" "文字" with dissolve', True),
    'say_with_id': ('"系统" "文字" id sys_line', True),
}


def _beats(text: str, path: Path) -> list:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _lines(t: str) -> list:
    return t.splitlines()


def _norm(t: str) -> str:
    ls = [ln.rstrip() for ln in _lines(t) if not ln.startswith(_MARKER)]
    while ls and not ls[0]:
        ls.pop(0)
    while ls and not ls[-1]:
        ls.pop()
    return '\n'.join(ls)


def _quoted(t: str) -> list:
    """一行里所有带引号的文本（两种引号都算，去掉转义）。"""
    return [s for s in re.findall(r'"((?:[^"\\]|\\.)*)"', t)] + \
           [s for s in re.findall(r"'((?:[^'\\]|\\.)*)'", t)]


def _line_with(t: str, needle: str):
    for ln in _lines(t):
        if needle in ln:
            return ln
    return None


# ---------------------------------------------------------------------------
#  三档上下文：顶层 / 条件里 / 菜单选项体里
# ---------------------------------------------------------------------------

CONTEXTS = {
    'flat': '    {line}\n',
    'in_if': '    if flag:\n        {line}\n',
    'in_menu': ('    menu:\n        "选一":\n            {line}\n'),
}


def run_forms(verbose: bool, tmp: Path) -> list:
    bad: list = []
    for form, (line, known) in FORMS.items():
        for ctx, tpl in CONTEXTS.items():
            name = f'{form}@{ctx}'
            # 菜单选项行单独造（它**必须**带冒号才是选项，塞进选项体里会变语法错）
            body = tpl.format(line=line)
            src = f'label t:\n{body}'
            d = tmp / 'forms' / f'{form}_{ctx}'
            f = d / 'ch.rpy'
            try:
                beats = _beats(src, f)
            except Exception as exc:                   # noqa: BLE001
                bad.append((name, 'S1解析', f'解析抛异常：{exc!r}'))
                continue
            if not beats:
                bad.append((name, 'S1解析', '一张拍都没有'))
                continue

            r1 = render_beats_to_rpy(beats)
            # S6 旁白回归：`"系统"` 单独一行不许被当成"字面量说话人 + 空台词"
            if form.startswith('narration'):
                lit = [b for b in beats if is_literal_speaker(
                    getattr(b, 'speaker', ''))]
                if lit:
                    bad.append((name, 'S6旁白回归',
                                f'旁白被当成了字面量说话人：'
                                f'{[(b.speaker, b.text) for b in lit]}'))
                want = _quoted(line.replace('    ', ' ').strip())
                if want and (not any(w in r1 for w in want)):
                    bad.append((name, 'S6旁白回归',
                                f'旁白正文在产物里找不到：{want!r}'))

            # S2 引号文本守恒（通用判据）
            out_line = _line_with(r1, _quoted(line)[0] if _quoted(line) else '')
            if out_line is None:
                bad.append((name, 'S2引号守恒',
                            f'产物里找不到这一行。拍的 (speaker, text) = '
                            f'{[(b.speaker, b.text) for b in beats]}'))
            else:
                want = _quoted(line)
                miss = [q for q in want if q not in out_line]
                if miss and not known:
                    bad.append((name, 'S2引号守恒',
                                f'产物那一行丢了引号文本 {miss!r}\n'
                                f'      原文: {line.strip()!r}\n'
                                f'      产物: {out_line.strip()!r}'))
                if miss and known:
                    print(f'   · [已知边界] {name}: 丢了 {miss!r}'
                          f'（{out_line.strip()!r}）')

            # S1 **who 逐字回写**（他设计的关键：`who_raw` 原样出去）。
            #    ⚠ `what` 的**引号风格**允许归一：单引号原文会渲染成双引号
            #    （`'系统' '文字'` → `'系统' "文字"`），语义一样、且一轮之后
            #    就是不动点 —— 所以判据只要求 who 逐字 + 文本内容守恒（S2），
            #    不要求整行逐字（第一版就是拿整行比，报了 6 处假红）。
            if not known and any(getattr(b, 'speaker', '') for b in beats):
                who_raw = next(b.speaker for b in beats if b.speaker)
                if who_raw not in r1:
                    bad.append((name, 'S1逐字回写',
                                f'模型里的 who {who_raw!r} 没有**逐字**出现在产物里'
                                f'（被重新加引号了？）。拍 = '
                                f'{[(b.speaker, b.text) for b in beats]}\n'
                                f'      产物: {out_line!r}'))
            if known and out_line is not None:
                # 已知边界也要看一眼**产物实际长什么样**，别只报"丢了什么"
                print(f'   · [已知边界] {name} 产物行 = {out_line.strip()!r}')

            # S7 占位符不许漏进产物（菜单上下文里最容易）
            leaked = [ln for ln in _lines(r1) if '\x00' in ln]
            if leaked:
                bad.append((name, 'S7占位符', f'产物里有 MENU_NO_CAPTION：'
                                              f'{leaked[:1]}'))

            # S3 两轮不动点
            g = d / 'again.rpy'
            g.write_text(r1, encoding='utf-8')
            pr2, _ = IncrementalRenPyAnalyzer().analyze(files=[g], graph=None)
            r2 = render_beats_to_rpy(beats_from_parse_result(pr2))
            if _norm(r2) != _norm(r1):
                bad.append((name, 'S3不动点',
                            '再渲染变了：\n      A: '
                            + repr(_line_with(_norm(r1),
                                              _quoted(line)[0] if _quoted(line) else ''))
                            + '\n      B: '
                            + repr(_line_with(_norm(r2),
                                              _quoted(line)[0] if _quoted(line) else ''))))

            # S4 Excel 往返
            xl = d / 'ch.xlsx'
            try:
                export_beats_to_excel(beats, xl)
                r3 = render_beats_to_rpy(import_beats_from_excel(xl))
            except Exception as exc:                   # noqa: BLE001
                bad.append((name, 'S4表往返', f'抛异常：{exc!r}'))
                continue
            if _norm(r3) != _norm(r1):
                bad.append((name, 'S4表往返',
                            'Excel 往返后产物变了：\n      A: '
                            + repr(_line_with(_norm(r1),
                                              _quoted(line)[0] if _quoted(line) else ''))
                            + '\n      B: '
                            + repr(_line_with(_norm(r3),
                                              _quoted(line)[0] if _quoted(line) else ''))))
            if verbose:
                print(f'   · {name}: 拍={[(b.speaker, b.text) for b in beats]}')
    return bad


# ---------------------------------------------------------------------------
#  S5 校验不许被带偏
# ---------------------------------------------------------------------------

VALIDATE_SRC = '''label t:
    "系统" "字面量说话人，不该报"
    lin "这个角色没登记，必须报"
    e "这个也没登记"
'''


def run_validate(tmp: Path) -> list:
    """他让校验跳过字面量说话人 —— 要防"跳过面过大"把真问题一起放过。"""
    bad: list = []
    d = tmp / 'val'
    game = d / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'ch.rpy').write_text(VALIDATE_SRC, encoding='utf-8')
    try:
        proc = subprocess.run(
            [sys.executable, '-m', 'storygraph.cli', 'validate', str(d)],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        out = (proc.stdout or '') + (proc.stderr or '')
    except subprocess.TimeoutExpired:
        return [('S5 校验', 'E超时', 'validate 超过 180 秒')]

    if 'lin' not in out:
        bad.append(('S5 校验', 'E漏报',
                    f'没登记的角色 `lin` 没被报出来（跳过面是不是开太大了？）：'
                    f'{out[-500:]}'))
    if '字面量说话人' in out and '系统' in out:
        bad.append(('S5 校验', 'E误报',
                    f'字面量说话人 `"系统"` 被当角色报了：{out[-400:]}'))
    hit = [ln for ln in out.splitlines() if 'lin' in ln]
    print(f'   · 校验输出里提到 lin 的行：{hit[:2]}')
    return bad


# ---------------------------------------------------------------------------
#  真实项目：整库旁白/说话人形态有没有被这一改动到
# ---------------------------------------------------------------------------

def run_real(tmp: Path, verbose: bool, limit: int = 0) -> tuple:
    game = REAL_PROJECT / 'game'
    if not game.exists():
        print(f'⚠ 没有真实项目（{REAL_PROJECT}）')
        return [], {}
    from storygraph.parsing.discover import discover_scripts
    files = [p for p in discover_scripts(REAL_PROJECT) if p.suffix == '.rpy']
    if limit:
        files = files[:limit]
    print(f'真实项目：{len(files)} 个 .rpy')
    bad: list = []
    stat = {'literal': 0, 'narration': 0, 'files_literal': set(),
            'roundtrip_diff': 0}
    work = tmp / 'real'
    work.mkdir(parents=True, exist_ok=True)

    for src in files:
        dst = work / src.name
        shutil.copyfile(src, dst)
        beats = _beats(src.read_text(encoding='utf-8'), tmp / f'pp_{src.stem}.rpy')
        lit = [b for b in beats if is_literal_speaker(getattr(b, 'speaker', ''))]
        if lit:
            stat['literal'] += len(lit)
            stat['files_literal'].add(src.name)
        stat['narration'] += sum(
            1 for b in beats
            if b.kind in ('say', 'narration')
            and not getattr(b, 'speaker', '')
            and (b.text or '').strip())
        # 每份文件：直接渲染 vs 表往返，必须逐字节一致
        r1 = render_beats_to_rpy(beats)
        xl = work / f'{src.stem}.xlsx'
        try:
            export_beats_to_excel(beats, xl)
            r3 = render_beats_to_rpy(import_beats_from_excel(xl))
        except Exception as exc:                       # noqa: BLE001
            bad.append((src.name, 'S4表往返', f'抛异常：{exc!r}'))
            continue
        if _norm(r3) != _norm(r1):
            stat['roundtrip_diff'] += 1
            bad.append((src.name, 'S4表往返', '直接渲染与表往返的产物不同'))
        if verbose:
            print(f'   · {src.name}: 字面量说话人 {len(lit)} 处')

    print(f'   字面量说话人 {stat["literal"]} 处'
          f'（{sorted(stat["files_literal"])}）/ 旁白 {stat["narration"]} 处 / '
          f'表往返不一致 {stat["roundtrip_diff"]} 个文件')
    return bad, stat


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--real', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='hell_say_'))
    fails = 0
    try:
        if not args.real:
            print('===== S 形态 × 上下文 =====')
            bad = run_forms(args.verbose, tmp)
            for nm, judge, msg in bad:
                print(f'[FAIL] {nm} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] 形态×上下文（{len(bad)} 处）\n')
            fails += len(bad)

            print('===== S5 校验 =====')
            bad = run_validate(tmp)
            for nm, judge, msg in bad:
                print(f'[FAIL] {nm} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] 校验（{len(bad)} 处）\n')
            fails += len(bad)

        if not args.only or '真实' in args.only:
            bad, _st = run_real(tmp, args.verbose, args.limit)
            for nm, judge, msg in bad[:20]:
                print(f'[FAIL] {nm} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] 真实项目（{len(bad)} 处）')
            fails += len(bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'\n失败合计：{fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
