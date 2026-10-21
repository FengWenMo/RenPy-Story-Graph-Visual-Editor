"""
探针：**改顺序**类操作 —— 策划在 Excel 里最常做的十件事，逐件对账。

前两轮探的是"改一个字"（内容），这一轮探**位置**：
  把两行上下换一下、把一拍挪到末尾、删一拍同时插一拍、跨 label 改、
  在 if / menu / for 里面改。

位置类操作最容易出 P0，因为 `SequenceMatcher` 把"换位置"看成
"一插一删"，而"删台词"是它唯一不敢做的事 —— 两边一对不上就可能
插进去 + 留着旧的（重复），或者干脆按全量重排。

每个场景独立造文件，判据是**产物里的台词序列**（不是行数、不是回执）：
  · 顺序对了 = PASS；
  · 顺序不对 / 少句 / 多句 = FAIL（并打印实际序列）。

用法：
    D:/Python/python.exe tools/probe_order_ops.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: 四个 label / 两句一组，句子可区分。
BASE = '''\
label ch01:
    e "A1"
    e "A2"
    e "A3"
    e "A4"
    return
'''

#: 带分支与菜单的章节。
STRUCT = '''\
label ch02:
    e "S1"
    if flag:
        e "S2 在分支里"
    else:
        e "S3 在另一支里"
    e "S4"
    menu:
        "选左":
            e "S5 左"
        "选右":
            e "S6 右"
    e "S7"
    return
'''

#: 两个 label 的文件。
TWO = '''\
label ch03a:
    e "B1"
    e "B2"
    return

label ch03b:
    e "C1"
    e "C2"
    return
'''


def run(*args):
    p = subprocess.run([PY, str(ROOT / 'cli.py'), *args], cwd=str(ROOT),
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def say_texts(path: Path) -> list:
    """产物里所有台词正文（按出现顺序）。"""
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(str(path))))
    out = []
    for b in beats:
        if str(getattr(b, 'kind', '')) in ('say', 'narration', 'menu_option'):
            t = str(getattr(b, 'text', '') or '').strip()
            if t:
                out.append(t)
    return out


def beats_sheet(table: Path):
    import openpyxl
    wb = openpyxl.load_workbook(str(table))
    ws = wb['Beats']
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    rows = []
    for r in range(2, ws.max_row + 1):
        kind = str(ws.cell(r, heads['类型']).value or '').strip()
        text = str(ws.cell(r, heads['台词']).value or '').strip()
        if not kind and not text:
            continue
        rows.append({'row': r, 'kind': kind, 'text': text,
                     'label': str(ws.cell(r, heads['章节']).value or '').strip()})
    return wb, ws, heads, rows


def one(name: str, body: str, mutate, expect: list, work: Path) -> dict:
    """
    跑一个场景：建表 → `mutate(ws, heads, rows)` → 导出 → 对比台词序列。
    """
    from storygraph.app import tables
    tag = ''.join(sorted(set(name)))[:6] + str(abs(hash(name)) % 9999)
    root = work / f'p{tag}'
    (root / 'game').mkdir(parents=True, exist_ok=True)
    src = root / 'game' / 'ch.rpy'
    src.write_text(body, encoding='utf-8')

    gen = tables.generate_table(src, project_root=root)
    if not gen.get('ok'):
        return {'name': name, 'stage': '建表', 'verdict': 'FAIL',
                'note': str(gen.get('error'))[:80]}
    table = gen['table_path']
    wb, ws, heads, rows = beats_sheet(table)
    note = mutate(ws, heads, rows)
    wb.save(str(table))

    res = tables.export_table(table, project_root=root, source_rpy=src)
    done = tables.apply_export(res, project_root=root)
    d = {'name': name, 'note': note or '', 'written': bool(done.get('written')),
         'blocking': (res.get('blocking') or [])[:1],
         'patch': bool(res.get('used_patch')), 'patch_note': (res.get('note') or '')[:60]}
    if not done.get('written'):
        # 拒写是**合法**结论之一（安全方向）；只要源文件没被改就行
        d['got'] = say_texts(src)
        d['verdict'] = 'PASS(拒写)' if d['got'] == _orig_texts(body) else 'FAIL(拒写但源变了)'
        return d
    d['got'] = say_texts(src)
    d['verdict'] = 'PASS' if d['got'] == expect else 'FAIL'
    return d


def _orig_texts(body: str) -> list:
    """原文里的台词序列（不经过任何工具，纯文本扫）。"""
    import re
    out = []
    for ln in body.splitlines():
        m = re.match(r'\s*(?:\w+\s+)?"([^"]*)"\s*$', ln)
        if m:
            out.append(m.group(1))
        m2 = re.match(r'\s*"([^"]*)":\s*$', ln)      # 选项行
        if m2:
            out.append(m2.group(1))
    return out


# ----------------------------------------------------------------------
#  十个变换
# ----------------------------------------------------------------------

def m_swap_adjacent(ws, h, rows):
    a, b = rows[0]['row'], rows[1]['row']
    va = ws.cell(a, h['台词']).value
    vb = ws.cell(b, h['台词']).value
    ws.cell(a, h['台词']).value = vb
    ws.cell(b, h['台词']).value = va
    return 'A1↔A2'


def m_swap_far(ws, h, rows):
    a, b = rows[0]['row'], rows[3]['row']
    va = ws.cell(a, h['台词']).value
    vb = ws.cell(b, h['台词']).value
    ws.cell(a, h['台词']).value = vb
    ws.cell(b, h['台词']).value = va
    return 'A1↔A4（隔两个）'


def m_move_first_to_end(ws, h, rows):
    """把第一拍挪到最后（删第一行 + 末尾插一行）。"""
    first = rows[0]
    last_row = rows[-1]['row']
    vals = {k: ws.cell(first['row'], h[k]).value if k in h else None
            for k in ('章节', '类型', '说话人', '台词')}
    ws.insert_rows(last_row + 1)
    for k, v in vals.items():
        if k in h:
            ws.cell(last_row + 1, h[k]).value = v
    ws.delete_rows(first['row'])
    return 'A1 挪到末尾'


def m_replace_one(ws, h, rows):
    """删第 2 拍、在同位置插一句新的（"换掉这一段"）。"""
    r = rows[1]['row']
    ws.cell(r, h['台词']).value = 'A2 换成了新的'
    return '第 2 拍换成新句'


def m_delete_and_insert(ws, h, rows):
    """删第 2 拍，并在第 4 拍之前插一拍。"""
    ws.delete_rows(rows[1]['row'])
    _, ws2, h2, rows2 = beats_sheet_again(ws)
    at = rows2[-1]['row']
    ws.insert_rows(at)
    ws.cell(at, h['章节']).value = rows2[-1]['label']
    ws.cell(at, h['类型']).value = 'say'
    ws.cell(at, h['说话人']).value = 'e'
    ws.cell(at, h['台词']).value = 'A新 插在末尾之前'
    return '删第 2 拍 + 插一拍'


def beats_sheet_again(ws):
    heads = {str(c.value).strip(): i + 1 for i, c in enumerate(ws[1]) if c.value}
    rows = []
    for r in range(2, ws.max_row + 1):
        kind = str(ws.cell(r, heads['类型']).value or '').strip()
        text = str(ws.cell(r, heads['台词']).value or '').strip()
        if not kind and not text:
            continue
        rows.append({'row': r, 'kind': kind, 'text': text,
                     'label': str(ws.cell(r, heads['章节']).value or '').strip()})
    return None, ws, heads, rows


def m_edit_in_branch(ws, h, rows):
    for r in rows:
        if r['text'] == 'S2 在分支里':
            ws.cell(r['row'], h['台词']).value = 'S2 改过（分支里）'
            return 'if 分支里改一句'
    return None


def m_edit_in_menu(ws, h, rows):
    for r in rows:
        if r['text'] == 'S5 左':
            ws.cell(r['row'], h['台词']).value = 'S5 改过（选项体里）'
            return 'menu 选项体里改一句'
    return None


def m_edit_in_other_label(ws, h, rows):
    for r in rows:
        if r['text'] == 'C2':
            ws.cell(r['row'], h['台词']).value = 'C2 改过（第二个 label）'
            return '第二个 label 里改一句'
    return None


def m_delete_in_branch(ws, h, rows):
    for r in rows:
        if r['text'] == 'S2 在分支里':
            ws.delete_rows(r['row'])
            return '删 if 分支里唯一的一句'
    return None


def main() -> int:
    cases = [
        ('相邻两句换位置', BASE, m_swap_adjacent,
         ['A2', 'A1', 'A3', 'A4']),
        ('隔两句换位置', BASE, m_swap_far,
         ['A4', 'A2', 'A3', 'A1']),
        ('第一拍挪到末尾', BASE, m_move_first_to_end,
         ['A2', 'A3', 'A4', 'A1']),
        ('换掉中间那句', BASE, m_replace_one,
         ['A1', 'A2 换成了新的', 'A3', 'A4']),
        ('删一拍+插一拍', BASE, m_delete_and_insert,
         ['A1', 'A3', 'A新 插在末尾之前', 'A4']),
        ('if 分支里改一句', STRUCT, m_edit_in_branch,
         ['S1', 'S2 改过（分支里）', 'S3 在另一支里', 'S4',
          '选左', 'S5 左', '选右', 'S6 右', 'S7']),
        ('menu 选项体里改一句', STRUCT, m_edit_in_menu,
         ['S1', 'S2 在分支里', 'S3 在另一支里', 'S4',
          '选左', 'S5 改过（选项体里）', '选右', 'S6 右', 'S7']),
        ('删 if 分支唯一一句', STRUCT, m_delete_in_branch,
         ['S1', 'S3 在另一支里', 'S4', '选左', 'S5 左', '选右', 'S6 右', 'S7']),
        ('第二个 label 里改一句', TWO, m_edit_in_other_label,
         ['B1', 'B2', 'C1', 'C2 改过（第二个 label）']),
    ]
    with tempfile.TemporaryDirectory(prefix='order_') as td:
        work = Path(td)
        out = []
        for name, body, fn, expect in cases:
            try:
                r = one(name, body, fn, expect, work)
            except Exception as e:              # noqa: BLE001 - 探针要跑完
                r = {'name': name, 'verdict': f'抛异常 {type(e).__name__}',
                     'note': str(e)[:80]}
            out.append(r)
            print('=' * 76)
            print(f'[{name}]  {r.get("note", "")}')
            print(f'  判定   : {r["verdict"]}')
            print(f'  写盘   : {r.get("written")}  增量: {r.get("patch")}'
                  f'  {r.get("patch_note", "")}')
            if r.get('blocking'):
                print(f'  拦下   : {str(r["blocking"][0])[:110]}')
            if 'got' in r:
                print(f'  实际   : {r["got"]}')
        print('\n' + '=' * 76)
        print(f'{"场景":22} {"结果":10} 说明')
        for r in out:
            print(f'{r["name"]:22} {r["verdict"]:10} {r.get("note", "")[:40]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
