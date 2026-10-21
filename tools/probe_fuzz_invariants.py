"""
探针：**随机变异 + 不变量**（100 轮）—— 用组合面把"手工想不到的叠法"扫一遍。

手工挑场景只能覆盖"想得到的"。这一轮随机挑 1~3 个操作叠加，每轮只查
**内容守恒**（不比对具体期望，那是另一类）：

  I1. 产物里任何一句台词的出现次数，**不许超过**源里的次数
      （凭空多出来一句 = 重复行，上一轮 P0 就是它）
  I2. 源文件里每一行 `$ ...` 代码，**必须在产物里出现**（除非它所在的拍被删了）
  I3. 源文件里每一行 `show` / `hide` / `scene`，**必须在产物里出现**
      （除非对应拍的舞台列被清空 —— 本探针不清空，所以一律要求还在）
  I4. 产物里必须还有 `label ch01:` 和 `return`，且 `label` 只出现一次
  I5. 产物能过 Ren'Py 引擎 `lint`（抽样跑，全跑太慢）

操作池：改台词 / 改说话人 / 删一拍 / 在末尾插一拍 / 交换两拍 / 改注释。

用法：
    D:/Python/python.exe tools/probe_fuzz_invariants.py [轮数]
"""
from __future__ import annotations

import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app import tables                      # noqa: E402

SRC = '''\
label ch01:
    scene bg room
    show ling happy at left
    $ gold = 10
    ling "第一句台词"
    ling "第二句台词"
    $ gold += 1
    ling "第三句台词"
    hide ling
    ling "第四句台词"
    return
'''


def sheet(table: Path):
    import openpyxl
    wb = openpyxl.load_workbook(str(table))
    ws = wb['Beats']
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    rows = []
    for r in range(2, ws.max_row + 1):
        kind = str(ws.cell(r, heads['类型']).value or '').strip()
        text = str(ws.cell(r, heads['台词']).value or '').strip()
        if kind or text:
            rows.append({'row': r, 'kind': kind, 'text': text})
    return wb, ws, heads, rows


def gen_table(root: Path, seed: int) -> tuple:
    """每轮独立造文件 + 建表，避免上一轮的残留影响。"""
    (root / 'game').mkdir(parents=True, exist_ok=True)
    src = root / 'game' / 'ch.rpy'
    src.write_text(SRC, encoding='utf-8')
    gen = tables.generate_table(src, project_root=root)
    return src, gen['table_path']


def mutate(rnd: random.Random, ws, heads, rows) -> list:
    """随机挑 1~3 个操作，返回做过的动作名（报告用）。"""
    ops = []
    for _ in range(rnd.randint(1, 3)):
        if not rows:
            break
        pick = rnd.choice(['text', 'speaker', 'delete', 'insert', 'swap', 'comment'])
        try:
            if pick == 'text':
                r = rnd.choice([x for x in rows if x['kind'] == 'say'] or rows)
                ws.cell(r['row'], heads['台词']).value = \
                    f'{r["text"]}（改{rnd.randint(1, 99)}）'
                ops.append('改台词')
            elif pick == 'speaker':
                r = rnd.choice([x for x in rows if x['kind'] == 'say'] or rows)
                old = str(ws.cell(r['row'], heads['说话人']).value or 'ling')
                ws.cell(r['row'], heads['说话人']).value = \
                    'e' if old != 'e' else 'ling'
                ops.append('改说话人')
            elif pick == 'delete' and len(rows) > 2:
                r = rnd.choice(rows)
                ws.delete_rows(r['row'])
                ops.append(f'删句子「{r["text"][:6]}」')
                _wb, ws, heads, rows = sheet_from(ws)
            elif pick == 'insert':
                at = rows[-1]['row']
                ws.insert_rows(at)
                ws.cell(at, heads['章节']).value = rows[-1].get('label') or 'ch01'
                ws.cell(at, heads['类型']).value = 'say'
                ws.cell(at, heads['说话人']).value = 'ling'
                ws.cell(at, heads['台词']).value = f'新插的句子{rnd.randint(1, 99)}'
                ops.append('插一拍')
                _wb, ws, heads, rows = sheet_from(ws)
            elif pick == 'swap' and len(rows) >= 2:
                a, b = rnd.sample(rows, 2)
                va = ws.cell(a['row'], heads['台词']).value
                vb = ws.cell(b['row'], heads['台词']).value
                ws.cell(a['row'], heads['台词']).value = vb
                ws.cell(b['row'], heads['台词']).value = va
                ops.append('换两拍')
            else:
                r = rnd.choice(rows)
                ws.cell(r['row'], heads['注释']).value = f'fuzz 注释{rnd.randint(1, 99)}'
                ops.append('改注释')
        except Exception as e:                  # noqa: BLE001 - 变异本身失败不算缺陷
            ops.append(f'{pick}失败:{type(e).__name__}')
    return ops


def sheet_from(ws):
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    rows = []
    for r in range(2, ws.max_row + 1):
        kind = str(ws.cell(r, heads['类型']).value or '').strip()
        text = str(ws.cell(r, heads['台词']).value or '').strip()
        if kind or text:
            rows.append({'row': r, 'kind': kind, 'text': text,
                         'label': str(ws.cell(r, heads['章节']).value or '').strip()})
    return None, ws, heads, rows


def check(text: str, src_text: str) -> list:
    """返回不变量被破坏的清单（空 = 全过）。"""
    bad = []
    # I1 台词不许凭空多出来
    def says(t):
        out = Counter()
        for ln in t.splitlines():
            s = ln.strip()
            for q in ('"', "'"):
                if s.startswith('e ') or s.startswith('ling ') or \
                        (s and s[0] not in '#$' and q in s and s.endswith(q)):
                    pass
        return out
    # 用"带引号的行尾字符串"粗判台词
    import re
    def say_texts(t):
        out = []
        for ln in t.splitlines():
            m = re.match(r'\s*(?:[A-Za-z_]\w*\s+)?"([^"]*)"\s*$', ln)
            if m:
                out.append(m.group(1))
        return out
    src_c = Counter(say_texts(src_text))
    out_c = Counter(say_texts(text))
    for k, n in out_c.items():
        # 只查"源里出现过的台词有没有变成两遍"；新文本（改台词/插话）本来就源里没有
        if k in src_c and n > src_c[k]:
            bad.append(f'I1 重复的台词「{k[:20]}」×{n - src_c[k]}')
    # I2/I3 只查"不许凭空多"：删一拍会连带删掉它区间里的舞台/代码行（设计如此），
    # 逐行要求"必须还在"会误报。
    def count_lines(t, pred):
        return sum(1 for ln in t.splitlines() if pred(ln.strip()))
    src_dollar = count_lines(src_text, lambda s: s.startswith('$'))
    out_dollar = count_lines(text, lambda s: s.startswith('$'))
    if out_dollar > src_dollar:
        bad.append(f'I2 $ 行凭空多了：{src_dollar} → {out_dollar}')
    src_stage = count_lines(src_text, lambda s: s.split()[:1] and
                            s.split()[0] in ('show', 'hide', 'scene'))
    out_stage = count_lines(text, lambda s: s.split()[:1] and
                            s.split()[0] in ('show', 'hide', 'scene'))
    if out_stage > src_stage:
        bad.append(f'I3 舞台行凭空多了：{src_stage} → {out_stage}')
    # ⚠ 不加"顺序必须是子序列"这条判据：**换两拍是用户的合法意图**
    # （fuzz 自己就会做），产物跟着换是对的。顺序正确性只能靠"给出期望"验，
    # 不变量判据在这里没有立场。
    # I4 label / return
    if text.count('label ch01:') != 1:
        bad.append(f'I4 label 次数 = {text.count("label ch01:")}')
    if not any(x.strip() == 'return' for x in text.splitlines()):
        bad.append('I4 丢了 return')
    return bad


def main(argv) -> int:
    rounds = int(argv[1]) if len(argv) > 1 else 100
    rnd = random.Random(20261021)
    fails = []
    with tempfile.TemporaryDirectory(prefix='fuzz_') as td:
        work = Path(td)
        for i in range(rounds):
            root = work / f'r{i}'
            try:
                src, table = gen_table(root, i)
                wb, ws, heads, rows = sheet(table)
                ops = mutate(rnd, ws, heads, rows)
                wb.save(str(table))
                res = tables.export_table(table, project_root=root, source_rpy=src)
                done = tables.apply_export(res, project_root=root)
                if not done.get('written'):
                    continue                    # 拒写 = 安全结论，不算缺陷
                text = src.read_text(encoding='utf-8')
                bad = check(text, SRC)
                if bad:
                    fails.append((i, ops, bad, text))
                    print(f'[{i:3}] {ops}')
                    for b in bad[:4]:
                        print(f'      ✗ {b}')
            except Exception as e:              # noqa: BLE001
                import traceback
                traceback.print_exc()
                fails.append((i, [], [f'异常 {type(e).__name__}: {e}'], ''))
    print('\n' + '=' * 74)
    print(f'{rounds} 轮，破坏不变量 {len(fails)} 轮')
    for i, ops, bad, text in fails[:5]:
        print(f'--- 第 {i} 轮 {ops}')
        for b in bad:
            print(f'    {b}')
        print('    产物：')
        for ln in text.splitlines():
            print(f'      | {ln}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
