#!/usr/bin/env python
"""
地狱测试 · 第六轮：**多引擎导出**（从没测过的那条路）
================================================================================

前五轮全在打 `.rpy` 这条路。而产品目标是"**后续可能改用 Unity / 更复杂的引擎，
所以编辑器要能多语言复用**" —— 那条路是 `pipeline.engine_exporters`：
`build_neutral_ir`（中立 IR）→ unity / godot / json / csv / renpy 清单。

**它从来没被地狱测试碰过。** 而它的失法是另一种味道：不是"脚本坏了"，
而是"换引擎之后东西少了、而且没人告诉你"。

判据（M1~M9）：

  M1 **文本守恒** —— 源里每句台词都要在 IR 的 `lines[].text` 里
     （用"引号文本守恒"这条通用判据，不预设缺陷形状）；
  M2 **菜单选项不丢** —— 源里 N 个选项，IR 里 `kind=='menu_option'` 的行数要 ≥ N；
  M3 **条件不丢** —— 源里每个 `if` 分支，IR 里都要有带非空 `condition` 的行；
  M4 **跳转可接线** —— 每个 `jump X` / `call X` 的目标名都要在 IR 里找得到。
     这条是"换引擎"的命门：目标名丢了，Unity 侧就接不上线，而且**看不出少了什么**；
  M5 **结构位**（**只报告**）—— IR 里没有 `nest` 之类的结构位，嵌套层级无法重建。
     这是能力缺口（不是当前缺陷），但它是"换引擎"最先撞上的墙，所以必须量出来；
  M6 **幂等** —— 同一份拍连导两次，产物逐字节相同；
  M7 **对抗文本** —— emoji / 引号 / 换行 / 反斜杠经 JSON 往返后**逐字相等**；
  M8 **多文件不互相覆盖**（**重点攻击**）—— 40 章导到同一个目录会怎样？
  M9 **五个引擎都跑得通**且产物非空。

用法：
    D:/Python/python.exe tools/hell_multi_engine.py
    D:/Python/python.exe tools/hell_multi_engine.py -v
    D:/Python/python.exe tools/hell_multi_engine.py --real
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.pipeline.engine_exporters import (SUPPORTED_ENGINES,
                                                  build_neutral_ir, export_ir)
from storygraph.story.beat_build import beats_from_parse_result

REAL_PROJECT = Path(r'D:\千连万理\RenPyTest\sr_general')

SRC = '''label t:
    "系统" "字面量说话人的一句"  # M:note1
    e "普通台词：引号 \\" 与反斜杠\\\\ 与 emoji 😀"
    if flag:
        show ling happy at left
        e "条件里的一句"  # M:note2
    menu:
        "选项一":  # M:note3
            e "选项一的台词"
        "选项二":
            call helper_hub
            jump elsewhere
    e "收尾"

label helper_hub:
    e "被 call 的"
    return
'''

SCRIPT = {'t.rpy': SRC,
          'u.rpy': ('label elsewhere:\n    e "另一章的台词"\n    jump t\n')}

ALL_FIELDS = ('text', 'raw', 'code', 'stage', 'comment', 'condition',
              'scene', 'music', 'sound', 'at', 'with', 'actor',
              'speaker', 'label')


def _beats(text: str, path: Path) -> list:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[path], graph=None)
    return beats_from_parse_result(pr)


def _quoted(t: str) -> list:
    return [s for s in re.findall(r'"((?:[^"\\]|\\.)*)"', t) if s.strip()]


def _jump_key(ln: dict) -> str:
    return str(ln.get('line_id') or ln.get('lineId') or '')


def _jump_check(beats, ir_lines) -> list:
    """
    每一跳的**目标**有没有跟着那一行进产物？返回 `[(目标, 原因), …]`。

    ⚠ 判据要害（第一版错在这里）：**不能**在整份产物里搜那个名字 ——
    目标名常常**恰好等于某个 label 名**，而 `label` 字段本来就在产物里，
    于是搜得到、假阴性（真实项目上"跳转 0 丢失"就是这么来的）。
    正确做法：按 `line_id` 对齐到**承载那一跳的那一行**，再看那一行的字段里
    有没有目标名。适配层丢字段这件事只有这样才能量准。
    """
    by_id = {}
    for ln in ir_lines:
        if isinstance(ln, dict):
            by_id.setdefault(_jump_key(ln), []).append(ln)
    missing = []
    for b in beats or ():
        raw = str(getattr(b, 'raw', '') or '')
        targets = _jump_targets(raw)
        if not targets:
            continue
        rows = by_id.get(str(getattr(b, 'line_id', '') or '')) or []
        if not rows:
            missing.append((sorted(targets), '产物里没有这一行'))
            continue
        own = _all_strings(rows)
        for t in sorted(targets):
            if not _has(own, t):
                missing.append((t, '这一行的字段里没有目标'))
    return missing


def _all_strings(obj, out=None) -> list:
    """
    递归收集 IR 里**所有字符串值**。

    ⚠ 别拿 `json.dumps(ir)` 的**文本**去比对（第一版就是那么写的）：
    JSON 会把 `"` 写成 `\\"`、把 `\\` 写成 `\\\\`，于是**带引号或反斜杠的台词
    全被报成"丢失"** —— 五个引擎一起假红，真实项目也报了一条
    `c:\\admin_script.py`。比"值"，不比"序列化后的文本"。
    """
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _all_strings(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _all_strings(v, out)
    elif obj is not None and not isinstance(obj, (int, float, bool)):
        out.append(str(obj))
    return out


def _has(strings, needle: str) -> bool:
    return any(s == needle or needle in s for s in strings)


def _say_texts(beats) -> list:
    """
    模型里**真正的台词**（say / narration 的 `text`）。

    ⚠ 别拿"整文件里的所有引号文本"来判（第一版就是那么写的）：`define` /
    `screen` / 图片路径 / 样式表里的字符串**本来就不该进 IR**（它们是第三类），
    于是真实项目一个文件就报 200+ 处"丢失" —— 判据错，不是缺陷。
    """
    out = []
    for b in beats or ():
        if str(getattr(b, 'kind', '') or '') not in ('say', 'narration'):
            continue
        txt = str(getattr(b, 'text', '') or '')
        if txt.strip() and txt != '\x00menu':
            out.append(txt)
    return out


def _jump_targets(t: str) -> set:
    out = set()
    for ln in t.splitlines():
        m = re.match(r'^\s*(jump|call)\s+([A-Za-z_]\w*)', ln)
        if not m:
            continue
        if m.group(1) == 'call' and m.group(2) in ('screen', 'expression'):
            continue
        out.add(m.group(2))
    return out


def _ir_blob(ir: dict) -> str:
    """IR 里**所有**字段拼成一个串 —— 用于"某个名字到底有没有出现在 IR 里"。"""
    return json.dumps(ir, ensure_ascii=False)


def run_engines(tmp: Path, verbose: bool) -> list:
    bad: list = []
    d = tmp / 'me'
    f = d / 't.rpy'
    beats = _beats(SRC, f)
    src_text = sys.intern(SRC)

    want_quotes = _say_texts(beats)      # 只判台词（模型口径）
    want_targets = _jump_targets(SRC)
    n_opts = len(re.findall(r'^\s{8}"[^"]+":', SRC, re.M))
    n_ifs = len(re.findall(r'^\s{4}if ', SRC, re.M))

    for engine in SUPPORTED_ENGINES:
        out = tmp / 'me_out' / engine
        try:
            files = export_ir(engine, beats, out,
                              project=str(d), title='t')
        except Exception as exc:                       # noqa: BLE001
            bad.append((f'M9 {engine}', 'E异常', f'导出抛异常：{exc!r}'))
            continue
        if not files or not all(Path(p).exists() for p in files):
            bad.append((f'M9 {engine}', 'E产物', f'没产出文件：{files}'))
            continue

        ir_path = None
        for p in files:
            if Path(p).name in ('story_ir.json', 'UnityDialogueData.json',
                                'GodotDialogueData.json'):
                ir_path = Path(p)
        if ir_path is None:
            if verbose:
                print(f'   · {engine}: {[Path(p).name for p in files]}')
            continue
        try:
            ir = json.loads(ir_path.read_text(encoding='utf-8'))
        except Exception as exc:                       # noqa: BLE001
            bad.append((f'M1 {engine}', 'EJSON', f'IR 读不回来：{exc!r}'))
            continue

        blob = _ir_blob(ir)
        strings = _all_strings(ir)          # 比"值"，不比序列化文本
        lines = ir.get('lines') or ir.get('dialogue') or []
        if verbose:
            print(f'   · {engine}: 顶层键 {sorted(ir.keys())}')
            if lines:
                print(f'     首行键 {sorted(lines[0].keys())}')
            print(f'     目录 {[p.name for p in Path(ir_path).parent.iterdir()]}')
            kinds: dict = {}
            for ln in lines:
                kinds[str(ln.get('kind'))] = kinds.get(str(ln.get('kind')), 0) + 1
            print(f'     kind 分布 {kinds}')
            nested = [k for ln in lines for k in ln
                      if isinstance(ln.get(k), (list, dict))
                      and k not in ('actors', 'poses', 'stage')]
            print(f'     嵌套键 {sorted(set(nested))[:6]}')

        # M1 文本守恒（比解析后的值）
        miss = [q for q in want_quotes if not _has(strings, q)]
        if miss:
            bad.append((f'M1 {engine}', 'E丢文本',
                        f'IR 里找不到这些台词文本：{miss[:3]}'))

        # M2 行类型字段 + 菜单选项
        #
        # 拆两条（第一版混在一起，把 godot 报成"丢选项"）：
        #   M2a 行类型字段 `kind` 在不在 —— godot 适配**整个丢掉了它**，
        #       于是 11 行全是"没有类型的行"，消费方分不出这是台词还是选项；
        #   M2b `kind` 在的前提下，menu_option 的数量不许少。
        has_kind = any('kind' in ln for ln in lines)
        if not has_kind:
            bad.append((f'M2a {engine}', 'E丢行类型',
                        f'IR 里**没有任何行带 `kind`** —— 消费方分不出哪一行是'
                        f'台词、哪一行是菜单选项、哪一行是跳转'
                        f'（{len(lines)} 行）'))
        else:
            got_opts = sum(1 for ln in lines
                           if str(ln.get('kind') or '') == 'menu_option')
            if got_opts < n_opts:
                bad.append((f'M2b {engine}', 'E丢选项',
                            f'源里 {n_opts} 个选项，IR 里只有 {got_opts} 个'))

        # M3 条件
        got_cond = sum(1 for ln in lines
                       if str(ln.get('condition') or '').strip())
        if got_cond < n_ifs:
            bad.append((f'M3 {engine}', 'E丢条件',
                        f'源里 {n_ifs} 个 if 分支，IR 里只有 {got_cond} 行带条件'))

        # M4 跳转可接线（按 line_id 对齐到承载那一跳的行）
        missed = _jump_check(beats, lines)
        if missed:
            bad.append((f'M4 {engine}', 'E接线断',
                        f'这些跳转目标没有跟着那一行进产物（换引擎后接不上线）：'
                        f'{missed[:4]}'))

        # M5 结构位（只报告）
        has_nest = any(k for ln in lines
                       for k in ln if 'nest' in str(k).lower())
        if not has_nest:
            print(f'   · [能力缺口] {engine}: IR 里没有结构位字段 —— '
                  f'嵌套层级/选项归属无法重建')

        if verbose:
            for t in sorted(want_targets):
                where = [(b.kind, k) for b in beats for k in ALL_FIELDS
                         if t in str(getattr(b, k, '') or '')]
                print(f'     目标 {t!r} 在模型的字段：{where[:4]}')

    # M6 幂等
    a = tmp / 'me_idem_a'
    b = tmp / 'me_idem_b'
    export_ir('json', beats, a, project=str(d), title='t')
    export_ir('json', beats, b, project=str(d), title='t')
    ta = (a / 'story_ir.json').read_text(encoding='utf-8')
    tb = (b / 'story_ir.json').read_text(encoding='utf-8')
    if ta != tb:
        bad.append(('M6 幂等', 'E不动点', '同一份拍连导两次，JSON 不同'))

    # M7 对抗文本经 JSON 往返
    ir = json.loads(ta)
    strings = _all_strings(ir)
    for q in want_quotes:
        if not _has(strings, q):
            bad.append(('M7 对抗文本', 'E变形',
                        f'{q!r} 经 JSON 往返后变形（值层面就不等了）'))
            break
    return bad


def run_multi_file(tmp: Path, verbose: bool) -> list:
    """
    M8：**多章导到同一个目录会怎样？**（UI 是"选一个目录 → 导出当前这一章"）

    `export_ir` 写的是**固定文件名**（`story_ir.json` / `UnityDialogueData.json`…），
    所以答案大概率是"后一章覆盖前一章，而且日志说导出成功"。
    这条要量出来 —— 40 章的项目里，用户点四次就会丢掉三章。
    """
    bad: list = []
    proj = tmp / 'mf_me' / 'game'
    proj.mkdir(parents=True, exist_ok=True)
    for name, body in SCRIPT.items():
        (proj / name).write_text(body, encoding='utf-8')
    out = tmp / 'mf_me_out'

    total_quotes: set = set()
    for name, body in SCRIPT.items():
        beats = _beats(body, tmp / 'mf_me_cache' / name)
        total_quotes |= set(_quoted(body))
        export_ir('json', beats, out, project=str(tmp / 'mf_me'), title=name)

    blob = (out / 'story_ir.json').read_text(encoding='utf-8')
    if 'story_ir.json' in [p.name for p in out.iterdir()]:
        # 只有一个文件 —— 数一下它带了几章的内容
        got = json.loads(blob)
        labels = {str(ln.get('label') or '') for ln in (got.get('lines') or [])}
        if len(labels) < 2:
            bad.append(('M8 多文件', 'E互相覆盖',
                        f'两章导到同一目录后只剩 {sorted(labels)} 一章的内容 —— '
                        f'后导的**静默覆盖**了先导的（文件名是固定的 '
                        f'`story_ir.json`），而且每次都会报"导出成功"'))
            print(f'   · [真缺陷] 两章 → 同一目录：只剩 {sorted(labels)}')
        else:
            print(f'   · 两章 → 同一目录：IR 里有 {sorted(labels)}')
    if verbose:
        print(f'   目录内容：{sorted(p.name for p in out.iterdir())}')
    return bad


def run_real(tmp: Path, limit: int = 0) -> tuple:
    game = REAL_PROJECT / 'game'
    if not game.exists():
        print(f'⚠ 没有真实项目（{REAL_PROJECT}）')
        return [], {}
    from storygraph.parsing.discover import discover_scripts
    files = [p for p in discover_scripts(REAL_PROJECT) if p.suffix == '.rpy']
    if limit:
        files = files[:limit]
    print(f'真实项目多引擎：{len(files)} 个 .rpy')
    bad: list = []
    stat = {'ok': 0, 'empty': 0, 'quote_lost': 0, 'jump_lost': 0,
            'files_jump': set(), 'godot_no_kind': 0, 'unity_no_jump': 0}
    work = tmp / 'real_me'
    work.mkdir(parents=True, exist_ok=True)

    # 三种引擎都过一遍：`json` 是中立 IR（好的那条路），`unity`/`godot` 是
    # **适配层**（合成用例已发现它俩丢跳转目标 / 丢 `kind`）——
    # 真实文件上量一遍，才知道那是"边角形态"还是"整库都中"。
    for src in files:
        text = src.read_text(encoding='utf-8')
        beats = _beats(text, work / f'pp_{src.stem}.rpy')
        if not beats:
            continue
        for engine in ('json', 'unity', 'godot'):
            out = work / f'{src.stem}_{engine}'
            try:
                files_out = export_ir(engine, beats, out,
                                      project=str(REAL_PROJECT), title=src.stem)
            except Exception as exc:                   # noqa: BLE001
                bad.append((f'{src.name}·{engine}', 'M9', f'抛异常：{exc!r}'))
                continue
            if not files_out:
                stat['empty'] += 1
                continue
            stat['ok'] += 1
            ir_name = {'json': 'story_ir.json',
                       'unity': 'UnityDialogueData.json',
                       'godot': 'GodotDialogueData.json'}[engine]
            path = out / ir_name
            if not path.exists():
                bad.append((f'{src.name}·{engine}', 'M9', f'没产出 {ir_name}'))
                continue
            ir = json.loads(path.read_text(encoding='utf-8'))
            strings = _all_strings(ir)
            lines = ir.get('lines') or ir.get('dialogue') or []
            miss = [q for q in _say_texts(beats) if not _has(strings, q)]
            if miss:
                stat['quote_lost'] += 1
                bad.append((f'{src.name}·{engine}', 'M1丢文本',
                            f'{len(miss)} 处台词不在产物里（例：{miss[:2]}）'))
            missed = _jump_check(beats, lines)
            if missed:
                stat['jump_lost'] += 1
                stat['files_jump'].add(f'{src.name}·{engine}')
                if engine == 'unity':
                    stat['unity_no_jump'] += 1
                bad.append((f'{src.name}·{engine}', 'M4接线断',
                            f'{len(missed)} 跳的目标没跟着进产物'
                            f'（例：{missed[:2]}）'))
            if engine == 'godot' and not any('kind' in ln for ln in lines):
                stat['godot_no_kind'] += 1
    print(f'   导出 {stat["ok"]} 次 / 丢文本 {stat["quote_lost"]} 次 / '
          f'跳转目标丢失 {stat["jump_lost"]} 次')
    print(f'   godot 产物里没有 `kind` 的文件 {stat["godot_no_kind"]} 个 / '
          f'unity 丢跳转 {stat["unity_no_jump"]} 个')
    return bad, stat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--only', default='')
    ap.add_argument('--real', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='hell_me_'))
    fails = 0
    try:
        groups = []
        if not args.real:
            groups.append(('M 五引擎', lambda: run_engines(tmp, args.verbose)))
            groups.append(('M8 多文件', lambda: run_multi_file(tmp, args.verbose)))
        for title, fn in groups:
            if args.only and args.only not in title:
                continue
            print(f'===== {title} =====')
            bad = fn()
            for nm, judge, msg in bad:
                print(f'[FAIL] {nm} · {judge}: {msg}')
            print(f'[{"FAIL" if bad else " ok "}] {title}（{len(bad)} 处）\n')
            fails += len(bad)

        if not args.only or '真实' in args.only:
            bad, _st = run_real(tmp, args.limit)
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
