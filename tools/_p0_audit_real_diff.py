# -*- coding: utf-8 -*-
"""
P0 审计探针 5b：用 difflib 重新裁决真机往返（修正探针 5 的行位移假象）。

真机原文（只读） vs 临时工程里导出后的文件 —— 逐字节语义 diff。
判据：
  · 阶段一（没改任何东西）：差异只能是「生成标记 2 行」或**零差异**
  · 阶段二（改了一句台词）：差异只能是「标记 2 行 + 那 1 句」
  其它任何差异都是 P0。
"""
import difflib
import sys
from pathlib import Path

SRC_ROOT = Path(r'D:\千连万理\RenPyTest\sr_general')
TMP = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if TMP is None:
    cands = sorted(Path(r'C:\Users\28749\AppData\Local\Temp').glob('sg_p0_real_*'),
                   key=lambda p: p.stat().st_mtime)
    TMP = cands[-1]
print(f'分析临时工程：{TMP}')

MARK = '# 由剧本表自动生成，请勿在此文件手工改动结构'
EDIT = '审计探针改写的这一句。'


def decode(b):
    return b.decode('utf-8', 'replace')


def true_diff(before: bytes, after: bytes):
    """返回 (opcodes 摘要, 是否只有标记/编辑行)。"""
    bl = decode(before).splitlines()
    al = decode(after).splitlines()
    sm = difflib.SequenceMatcher(a=bl, b=al, autojunk=False)
    ops = []
    benign = True
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        old = bl[i1:i2]
        new = al[j1:j2]
        # 只允许：插入标记行（1~2 行，且内容是标记/空行）
        only_marker = (not old and all(
            (ln.strip() == '' or ln.strip() == MARK) for ln in new))
        # 只允许：把某一行替换成含 EDIT 的行（一对一）
        only_edit = (len(old) == len(new) == 1
                     and EDIT in new[0])
        if only_marker:
            ops.append(f'+标记@{j1 + 1}')
        elif only_edit:
            ops.append(f'~改句@{j1 + 1}')
        else:
            benign = False
            ops.append(f'!!{tag} 旧[{i1 + 1}-{i2}] 新[{j1 + 1}-{j2}] '
                       f'{old[:2]} -> {new[:2]}')
    return ops, benign


bad = 0
checked = 0
for f in sorted(TMP.rglob('*.rpy')):
    rel = f.relative_to(TMP)
    orig = SRC_ROOT / rel
    if not orig.is_file():
        continue
    before = orig.read_bytes()
    after = f.read_bytes()
    if before == after:
        continue
    checked += 1
    ops, benign = true_diff(before, after)
    flag = '✓' if benign else '✗'
    if not benign:
        bad += 1
    print(f'{flag} {rel}  差异={ops if len(ops) < 8 else str(len(ops)) + " 处"}')
    if not benign:
        for o in ops[:6]:
            print(f'     {o}')

print(f'\n共 {checked} 个文件与真机原文不同；其中**非良性差异 {bad} 个**')
