# -*- coding: utf-8 -*-
"""
P0 扫描 · 第四轮：顶层块 / 多 label / 撤销 / 行尾注释 / 缩进（Write 写，不经 shell）
================================================================================

先复验上一轮三条（P0-9 menu 标题 / P0-10 elif 展平 / P0-11 return 越界），
再扫新面。判据一律是**字节 diff**：改动必须只动该动的行。

    D:\\Python\\python.exe tools\\p0_scan_round2.py
"""
from __future__ import annotations

import difflib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from storygraph.app.editor import EditorSession          # noqa: E402

MARKER = '# 由剧本表自动生成'


def body(text: str) -> list:
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith(MARKER):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


def run(name: str, src: str, op, expect: str):
    tmp = Path(tempfile.mkdtemp(prefix='sg_r2_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    p = game / 'script.rpy'
    p.write_bytes(src.encode('utf-8'))
    s = EditorSession(project_root=tmp)
    s.load(p)
    try:
        note = str(op(s) or '')
    except Exception as exc:                       # noqa: BLE001
        print('\n### %s\n    [操作异常] %r' % (name, exc))
        return
    try:
        res = s.export(p)
    except Exception as exc:                       # noqa: BLE001
        print('\n### %s\n    [导出异常] %r' % (name, exc))
        return
    after = open(p, encoding='utf-8', newline='').read()
    b1, b2 = body(src), body(after)
    sm = difflib.SequenceMatcher(None, b1, b2, autojunk=False)
    ops = [op_ for op_ in sm.get_opcodes() if op_[0] != 'equal']
    touched = sum(max(i2 - i1, j2 - j1) for _t, i1, i2, j1, j2 in ops)
    if b1 == b2:
        tag = '— 零 diff'
    elif touched <= 2:
        tag = '✅ 只动 %d 行' % touched
    else:
        tag = '⚠ 波及 %d 行' % touched
    print('\n### %s\n    op: %s\n    export ok=%s | %s\n    预期: %s' %
          (name, note or '(ok)', res.get('ok'), tag, expect))
    if res.get('blocking'):
        print('    拒因：%s' % (res.get('blocking') or [''])[0][:110])
    for _t, i1, i2, j1, j2 in ops[:4]:
        print('    %s 原:%r → 新:%r' % (_t, b1[i1:i2][:2], b2[j1:j2][:2]))


def edit(idx, **fields):
    def op(s):
        f = s.fields(idx)
        f.update(fields)
        s.apply(idx, f, cards=s.cards(idx), advanced_open=True,
                stage_loaded=f.get('Stage'))
        return 'edit(%d)=%r' % (idx, fields)
    return op


def del_at(idx, cascade=False):
    def op(s):
        r = s.delete_beat(idx, cascade=cascade)
        return 'del(%d) ok=%s err=%r' % (idx, (r or {}).get('ok'),
                                         (r or {}).get('error', '')[:40])
    return op


def move(a, b):
    def op(s):
        r = s.move_beat(a, b)
        return 'move(%d→%d) ok=%s' % (a, b, (r or {}).get('ok'))
    return op


# ======================================================================
print('=' * 70)
print('A) 复验上一轮三条')
print('=' * 70)
SRC_MENU = ('label start:\n'
            '    menu "标题":\n'
            '        "选项一":\n'
            '            e "1"\n'
            '    return\n')
run('A1 改 menu 标题', SRC_MENU, edit(0, Text='新标题'),
    '只改 menu 头那一行')

SRC_ELIF = ('label start:\n'
            '    if x == 1:\n'
            '        e "一"\n'
            '    elif x == 2:\n'
            '        e "二"\n'
            '    elif x == 3:\n'
            '        e "三"\n'
            '    return\n')
run('A2 删 elif 块里的拍', SRC_ELIF, del_at(1),
    '只少 e "一" 那一行，if/elif 行逐字节不动')

SRC_RET = 'label start:\n    e "一"\n    e "二"\n    return\n'
run('A3 把 return 挪到最前', SRC_RET, move(2, 0),
    '要么拒绝，要么 return 仍在最后（不许整章截断）')
run('A3b 把台词挪到 return 之后', SRC_RET, move(0, 2),
    '要么拒绝，要么 return 仍在最后')

# ======================================================================
print('\n' + '=' * 70)
print('B) 顶层块（define/default/init python/image）+ 结构操作')
print('=' * 70)
SRC_TOP = ('define e = Character("E")\n'
           'default score = 0\n'
           '\n'
           'init python:\n'
           '    def helper():\n'
           '        return 1\n'
           '\n'
           'image bg room = "bg.png"\n'
           '\n'
           'label start:\n'
           '    e "一"\n'
           '    e "二"\n'
           '    return\n')
run('B1 改第一句台词', SRC_TOP, edit(0, Text='改过的'),
    '顶层块一行不动，只改台词')
run('B2 删第一拍（台词）', SRC_TOP, del_at(0),
    '顶层块一行不许丢（define/default/init python/image）')
run('B3 删最后一拍 return', SRC_TOP, del_at(2),
    '只少 return，顶层块与台词都在')
run('B4 移动两拍台词', SRC_TOP, move(0, 1),
    '顶层块不动，台词顺序变')

# ======================================================================
print('\n' + '=' * 70)
print('C) 多 label 同文件')
print('=' * 70)
SRC_MULTI = ('label a:\n'
             '    e "A1"\n'
             '    e "A2"\n'
             '    return\n'
             '\n'
             'label b:\n'
             '    e "B1"\n'
             '    return\n')
run('C1 改 label a 的台词', SRC_MULTI, edit(0, Text='A1改'),
    '只改那一行')
run('C2 删 label a 的最后一句', SRC_MULTI, del_at(1),
    '只少 A2，label b 不动')
run('C3 跨 label 移动（a 的拍挪到 b）', SRC_MULTI, move(0, 3),
    '要么拒绝，要么顺序正确且 label b 内容不变')
run('C4 改 label 名', SRC_MULTI, edit(0, Label='aaa'),
    'label a → label aaa，正文不动')

# ======================================================================
print('\n' + '=' * 70)
print('D) 撤销 / 恢复的字节一致性')
print('=' * 70)


def edit_then_undo(s):
    f = s.fields(0)
    f['Text'] = '改过的'
    s.save(0, f, cards=s.cards(0), advanced_open=True, stage_loaded=f.get('Stage'))
    s.undo(0)
    return 'save 后 undo'


def del_then_restore(s):
    s.delete_beat(0)
    s.restore_last_removed()
    return 'del 后 restore'


def edit_then_undo_twice(s):
    f = s.fields(0)
    f['Text'] = '改过的'
    s.save(0, f, cards=s.cards(0), advanced_open=True, stage_loaded=f.get('Stage'))
    s.undo(0)
    s.undo(0)
    return '两次 undo（单级栈）'


SRC3 = 'label start:\n    e "一"\n    e "二"\n    return\n'
run('D1 改一句 → undo → 导出', SRC3, edit_then_undo,
    '与原文逐字节相同')
run('D2 删一拍 → restore → 导出', SRC3, del_then_restore,
    '与原文逐字节相同')
run('D3 改一句 → undo×2 → 导出', SRC3, edit_then_undo_twice,
    '与原文逐字节相同（单级栈，第二次 undo 应无害）')

# ======================================================================
print('\n' + '=' * 70)
print('E) 行尾注释 / 空行 / 缩进边界')
print('=' * 70)
SRC_CMT = ('label start:\n'
           '    menu "标题":  # 行尾注释\n'
           '        "选项一":  # 也是注释\n'
           '            e "1"\n'
           '    return\n')
run('E1 带行尾注释的 menu 改标题', SRC_CMT, edit(0, Text='新标题'),
    '注释保留在行尾，只改标题')
run('E2 带行尾注释的 menu 改选项', SRC_CMT, edit(1, Text='新选项'),
    '注释保留在行尾，只改选项')
run('E3 改台词（行尾有注释）', 'label start:\n    e "一"  # 备注\n    return\n',
    edit(0, Text='改过的'), '注释保留，只改台词')

SRC_TAB = 'label start:\n\tif x:\n\t\te "一"\n\te "二"\n\treturn\n'
run('E4 tab 缩进：改块内台词', SRC_TAB, edit(0, Text='改过的'),
    '缩进保持 tab，只改那一行')
run('E5 tab 缩进：删块外台词', SRC_TAB, del_at(1),
    '只少那一行')

SRC_NEST = ('label start:\n'
            '    if x:\n'
            '        if y:\n'
            '            e "深"\n'
            '        e "中"\n'
            '    e "浅"\n'
            '    return\n')
run('E6 双重嵌套改最深台词', SRC_NEST, edit(0, Text='改过的'),
    '只改那一行')
run('E7 双重嵌套删中间那句', SRC_NEST, del_at(1),
    '只少那一行，嵌套结构不动')
