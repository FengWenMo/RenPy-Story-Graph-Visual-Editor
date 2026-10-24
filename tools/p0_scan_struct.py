# -*- coding: utf-8 -*-
"""
P0 扫描 · **结构操作**（删 / 插 / 移动 / 块内编辑）会不会改坏脚本
================================================================

只诊断、不修复。每个用例都走 `load → 操作 → export(原地)`，然后看
**字节 diff** —— 改动必须局限在"该动的那一两行"，多动一行就算可疑（记 P0 候选）。

    D:\\Python\\python.exe tools\\p0_scan_struct.py
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


def diff(a: str, b: str) -> str:
    return ''.join(difflib.unified_diff(body(a), body(b), '原文', '导出', n=1))


def run(name: str, src: str, op, expect_note: str):
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    s = EditorSession(project_root=tmp)
    s.load(path)
    note = ''
    try:
        note = str(op(s) or '')
    except Exception as exc:                       # noqa: BLE001
        print('\n### %s\n    [操作抛异常] %r' % (name, exc))
        return
    try:
        res = s.export(path)
    except Exception as exc:                       # noqa: BLE001
        print('\n### %s\n    [导出抛异常] %r' % (name, exc))
        return
    after = open(path, encoding='utf-8', newline='').read()
    d = diff(src, after)
    tag = '✅ 无 diff' if not body(after) == body(src) else '✅ 无 diff'
    if d:
        tag = '⚠ 有 diff'
    print('\n### %s\n    op: %s | export: %s\n    预期: %s' %
          (name, note or '(ok)', (res.get('note') or '')[:60], expect_note))
    if d:
        print('    ' + d.replace('\n', '\n    '))
    else:
        print('    ' + tag)


# ------------------------------------------------------------------
# 删除
# ------------------------------------------------------------------
def del_at(idx, cascade=False):
    def op(s):
        r = s.delete_beat(idx, cascade=cascade)
        return 'delete(%d) -> %r' % (idx, r if isinstance(r, dict) else str(r)[:80])
    return op


SRC_PLAIN = ('label start:\n'
             '    e "一"\n'
             '    e "二"\n'
             '    e "三"\n'
             '    return\n')

SRC_IF = ('label start:\n'
          '    if flag:\n'
          '        e "分支里的台词"\n'
          '        e "分支里的第二句"\n'
          '    e "块外"\n'
          '    return\n')

SRC_MENU = ('label start:\n'
            '    menu:\n'
            '        "选项一":\n'
            '            e "选了 1"\n'
            '        "选项二":\n'
            '            e "选了 2"\n'
            '    return\n')

SRC_MIXED = ('label start:\n'
             '    scene bg room\n'
             '    show e happy at left\n'
             '    play music "a.ogg"\n'
             '    voice "v.ogg"\n'
             '    hide e\n'
             '    e "台词"\n'
             '    return\n')

SRC_CALL = ('label start:\n'
            '    call side from _call_side\n'
            '    e "回来了"\n'
            '    return\n')

SRC_JUMP = ('label start:\n'
            '    jump other\n'
            '\n'
            'label other:\n'
            '    e "在这"\n'
            '    return\n')

run('删中间台词拍', SRC_PLAIN, del_at(1),
    '只少一行 e "二"')
run('删第一拍（台词）', SRC_PLAIN, del_at(0),
    '只少一行 e "一"')
run('删 if 块内台词', SRC_IF, del_at(1),
    '只少一行"分支里的台词"，if 结构不动')
run('删 if 头（级联）', SRC_IF, del_at(0, cascade=True),
    'if 块整体消失（含分支两行），块外与 return 不动')
run('删 menu 选项拍', SRC_MENU, del_at(1),
    '少一个选项（选项一 + 它的体），menu 结构完好')
run('删 call 拍', SRC_CALL, del_at(0),
    '只少 call 那一行')
run('删 jump 拍', SRC_JUMP, del_at(0),
    '只少 jump 那一行（label other 还在）')
run('删台词拍（混合拍，含舞台）', SRC_MIXED, del_at(6),
    '只少 e "台词"，舞台/音频/退场一行不动')
run('删 scene 拍', SRC_MIXED, del_at(0),
    '只少 scene 那行')
run('删 show 拍', SRC_MIXED, del_at(1),
    '只少 show 那行')
run('删 play 拍', SRC_MIXED, del_at(2),
    '只少 play 那行')
run('删 voice 拍', SRC_MIXED, del_at(3),
    '只少 voice 那行')
run('删 hide 拍', SRC_MIXED, del_at(4),
    '只少 hide 那行')

# ------------------------------------------------------------------
# 插入
# ------------------------------------------------------------------
def ins_at(idx, after=True):
    def op(s):
        r = s.insert_beat(idx, after=after)
        return 'insert(%d,%s) -> %r' % (idx, after, r if isinstance(r, dict) else str(r)[:80])
    return op


def ins_flow(idx, flow_type='jump', target='other', after=True):
    def op(s):
        r = s.insert_flow_beat(idx, after=after, flow_type=flow_type,
                               target=target)
        return 'insert_flow -> %r' % (r if isinstance(r, dict) else str(r)[:80])
    return op


run('插空拍到中间', SRC_PLAIN, ins_at(1),
    '在 e "二" 之后多一行（空台词/占位），其它不动')
run('插空拍到 if 块内', SRC_IF, ins_at(1),
    '只在块内多一行')
run('插 jump 拍（章末前）', SRC_PLAIN, ins_flow(2),
    '在 e "三" 后多一行 jump other')
run('插 call 拍（带 from）', SRC_CALL, ins_flow(0, 'call', 'side2'),
    '多一行 call side2')

# ------------------------------------------------------------------
# 移动
# ------------------------------------------------------------------
def move(src_idx, dst_idx):
    def op(s):
        r = s.move_beat(src_idx, dst_idx)
        return 'move(%d->%d) -> %r' % (src_idx, dst_idx,
                                       r if isinstance(r, dict) else str(r)[:80])
    return op


run('移动：第 0 拍挪到最后', SRC_PLAIN, move(0, 2),
    '三句台词顺序变成 二/三/一，行数不变')
run('移动：台词挪进 if 块？', SRC_IF, move(3, 1),
    '要么拒绝，要么保持行数与缩进正确')

# ------------------------------------------------------------------
# 编辑（块内 / 控制流 / 条件）
# ------------------------------------------------------------------
def edit_at(idx, **fields):
    def op(s):
        f = s.fields(idx)
        f.update(fields)
        s.apply(idx, f, cards=s.cards(idx), advanced_open=True,
                stage_loaded=f.get('Stage'))
        return 'edit(%d)=%r' % (idx, fields)
    return op


run('改 if 里的台词', SRC_IF, edit_at(1, Text='改过的台词'),
    '只改那一行')
run('改 menu 选项一的正文', SRC_MENU, edit_at(1, Text='新选项'),
    '只改选项那一行（含冒号结构）')
run('改 menu 选项二的正文', SRC_MENU, edit_at(2, Text='新选项二'),
    '只改第二选项行')
run('改 call 的目标', SRC_CALL, edit_at(0, FlowTarget='other_side'),
    '只改 call 那一行')
run('改 jump 的目标', SRC_JUMP, edit_at(0, FlowTarget='third'),
    '只改 jump 那一行')
run('改 if 条件', SRC_IF, edit_at(0, condition='flag and other'),
    '只改 if 行')
run('把 call 改成 jump', SRC_CALL, edit_at(0, FlowType='jump', FlowTarget='other'),
    '只改那一行')
run('清空 scene', SRC_MIXED, edit_at(0, Scene=''),
    'scene 行消失（或改成空拍），别的不动')
run('改 show 的位置', SRC_MIXED, edit_at(1, Position='right'),
    '只改 show 行')
run('给台词加说话人', SRC_PLAIN, edit_at(0, Speaker='m'),
    '只改那一行（e -> m）')
