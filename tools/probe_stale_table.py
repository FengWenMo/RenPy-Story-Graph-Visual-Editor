"""
探针：**表过期**（程序员在编辑器外改了 .rpy）之后，策划再导表会发生什么。

这是工具最核心的承诺之一：「程序敲代码 → 自动反映到表格」。
但真实节奏是「表先导出 → 程序又改了三行 → 策划拿旧表再导一次」。
这时表的行号已经对不上源码了。三种可能结果，只有第一种是可接受的：

  A. 按内容认出来 → 改对那一句（自愈）
  B. 明确拒绝（"表与源码不同步，请重读"）→ 可接受
  C. **改错行 / 插到错位置** → 静默毁数据 = P0

同时探"重复台词"：两句一模一样的台词，改第二句会不会改到第一句。

用法：
    D:/Python/python.exe tools/probe_stale_table.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: 四句台词，句子互相可区分。
BODY = '''\
label ch1:
    jc "第一句。"
    jc "第二句。"
    jc "第三句。"
    jc "第四句。"
    return
'''


def run(*args):
    p = subprocess.run([PY, str(ROOT / 'cli.py'), *args], cwd=str(ROOT),
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def set_cell(xlsx: Path, old: str, new: str, occurrence: int = 0) -> int:
    """把表里第 occurrence 个等于 old 的台词格改成 new（按行号先后）。"""
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx))
    hits = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.strip() == old:
                    if hits == occurrence:
                        c.value = new
                        wb.save(str(xlsx))
                        return 1
                    hits += 1
    wb.save(str(xlsx))
    return 0


def show(path: Path, title: str):
    print(f'  -- {title}')
    for ln in path.read_text(encoding='utf-8').splitlines():
        print(f'     | {ln}')


def case_stale_insert(work: Path) -> None:
    """程序员在中间插了 3 行代码，策划拿旧表改最后一句。"""
    print('\n' + '=' * 72)
    print('场景 1：表导出后，程序员在 .rpy 中间插了 3 行；策划改「第四句」→「改过」')
    src = work / 'stale.rpy'
    src.write_text(BODY, encoding='utf-8')
    xl = work / 'stale.xlsx'
    rc, _ = run('table', 'rpy2xlsx', str(src), '--out', str(xl))
    assert rc == 0, 'rpy2xlsx 失败'

    # 程序员的改动：在「第二句」后面插 3 行（含一句新台词）
    txt = src.read_text(encoding='utf-8')
    txt = txt.replace('    jc "第二句。"\n',
                      '    jc "第二句。"\n'
                      '    pause 0.5\n'
                      '    jc "程序员新加的一句。"\n'
                      '    $ flag = True\n')
    src.write_text(txt, encoding='utf-8')
    show(src, '程序员改过之后的源码（表里没有这 3 行）')

    hit = set_cell(xl, '第四句。', '改过。')
    print(f'  表里改到 {hit} 个格子')

    out = work / 'stale.out.rpy'
    rc, log = run('table', 'xlsx2rpy', str(xl), '--out', str(out),
                  '--source', str(src), '--no-engine-lint')
    print(f'  退出码 = {rc}')
    for ln in [x for x in log.strip().splitlines() if x.strip()][-6:]:
        print(f'     {ln}')
    if out.exists():
        show(out, '产物')
        t = out.read_text(encoding='utf-8')
        print(f'  「第四句。」还在吗 : {"第四句。" in t}')
        print(f'  「改过。」在吗     : {"改过。" in t}')
        print(f'  程序员的 pause 还在: {"pause 0.5" in t}')
        print(f'  程序员的 $ flag    : {"$ flag = True" in t}')
        print(f'  程序员新台词还在   : {"程序员新加的一句。" in t}')
    else:
        print('  产物没生成')


def case_dup(work: Path) -> None:
    """两句一模一样的台词，改第二句。"""
    print('\n' + '=' * 72)
    print('场景 2：两句一模一样的台词（同一 label 内），改**第二句**')
    src = work / 'dup.rpy'
    src.write_text('label ch1:\n'
                   '    jc "嗯。"\n'
                   '    jc "中间一句。"\n'
                   '    jc "嗯。"\n'
                   '    return\n', encoding='utf-8')
    xl = work / 'dup.xlsx'
    rc, _ = run('table', 'rpy2xlsx', str(src), '--out', str(xl))
    assert rc == 0

    # 第二个「嗯。」（occurrence=1）
    hit = set_cell(xl, '嗯。', '第二句嗯改过。', occurrence=1)
    print(f'  表里改到 {hit} 个格子（期望 1）')

    out = work / 'dup.out.rpy'
    rc, log = run('table', 'xlsx2rpy', str(xl), '--out', str(out),
                  '--source', str(src), '--no-engine-lint')
    print(f'  退出码 = {rc}')
    for ln in [x for x in log.strip().splitlines() if x.strip()][-4:]:
        print(f'     {ln}')
    if out.exists():
        show(out, '产物')
        lines = out.read_text(encoding='utf-8').splitlines()
        first = [i for i, l in enumerate(lines) if '嗯。' in l]
        print(f'  剩下几句「嗯。」: {len(first)}（期望 1，且在第一句的位置）')
        first_ok = any(l.strip() == 'jc "嗯。"' for l in lines)
        print(f'  第一句还是原样   : {first_ok}')


def case_delete_dup(work: Path) -> None:
    """两句一样的台词，删掉第二句。"""
    print('\n' + '=' * 72)
    print('场景 3：两句一模一样的台词，**删掉第二句**（表里删那一行）')
    src = work / 'dup2.rpy'
    src.write_text('label ch1:\n'
                   '    jc "嗯。"\n'
                   '    jc "中间一句。"\n'
                   '    jc "嗯。"\n'
                   '    return\n', encoding='utf-8')
    xl = work / 'dup2.xlsx'
    rc, _ = run('table', 'rpy2xlsx', str(src), '--out', str(xl))
    assert rc == 0

    import openpyxl
    wb = openpyxl.load_workbook(str(xl))
    ws = wb['Beats'] if 'Beats' in wb.sheetnames else wb.worksheets[0]
    hdr = [c.value for c in ws[1]]
    # 表头是"台词"（老版本叫"台词 Text"）—— 两种都认。
    ti = next((hdr.index(k) for k in ('台词 Text', '台词') if k in hdr), None)
    if ti is None:
        print('  找不到台词列：', hdr)
        return
    targets = []
    for r in range(2, ws.max_row + 1):
        v = ws.cell(r, ti + 1).value
        if isinstance(v, str) and v.strip() == '嗯。':
            targets.append(r)
    print(f'  「嗯。」所在行：{targets}')
    if len(targets) >= 2:
        ws.delete_rows(targets[1])
        wb.save(str(xl))
        print(f'  已删表格第 {targets[1]} 行')
    out = work / 'dup2.out.rpy'
    rc, log = run('table', 'xlsx2rpy', str(xl), '--out', str(out),
                  '--source', str(src), '--no-engine-lint')
    print(f'  退出码 = {rc}')
    for ln in [x for x in log.strip().splitlines() if x.strip()][-4:]:
        print(f'     {ln}')
    if out.exists():
        show(out, '产物')
        print(f'  还剩几句「嗯。」: {out.read_text(encoding="utf-8").count("嗯。")}（期望 1）')


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='stale_') as td:
        work = Path(td)
        case_stale_insert(work)
        case_dup(work)
        case_delete_dup(work)
    return 0


if __name__ == '__main__':
    sys.exit(main())
