# -*- coding: utf-8 -*-
"""
P0 扫描 · 第二轮：高级区字段 / 控制流 / 移动的写回是否真的落到字节
====================================================================

只诊断不修。判据：`load → apply → export(原地)` 之后，改动必须出现在产物里，
而且**只动该动的行**。零 diff = "改了没用"（静默失败），多动行 = 改坏。

    D:\\Python\\python.exe tools\\p0_scan_fields.py
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


def body(text):
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


def run(name, src, op, expect):
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0b_'))
    g = tmp / 'game'; g.mkdir(parents=True)
    p = g / 'script.rpy'; p.write_bytes(src.encode('utf-8'))
    s = EditorSession(project_root=tmp); s.load(p)
    try:
        note = str(op(s) or '')
    except Exception as exc:                          # noqa: BLE001
        print('\n### %s\n    [操作异常] %r' % (name, exc))
        return
    res = s.export(p)
    after = open(p, encoding='utf-8', newline='').read()
    d = ''.join(difflib.unified_diff(body(src), body(after), '原文', '导出', n=1))
    zero = not d
    tag = '❌ 零 diff（改了没用）' if zero else '⚠ 有 diff'
    print('\n### %s\n    op: %s\n    export ok=%s | %s\n    预期: %s' %
          (name, note or '(ok)', res.get('ok'), tag, expect))
    if d:
        print('    ' + d.replace('\n', '\n    '))


def edit(idx, **fields):
    def op(s):
        f = s.fields(idx)
        f.update(fields)
        s.apply(idx, f, cards=s.cards(idx), advanced_open=True,
                stage_loaded=f.get('Stage'))
        return 'edit(%d)=%r' % (idx, fields)
    return op


def edit_ui_style(idx, **fields):
    """模拟 UI：只交回 collect_fields 会交的键（advanced 展开）。"""
    def op(s):
        f = s.fields(idx)
        f.update(fields)
        # UI 的 _flow_code_synced：CodeBlock 等于原控制流行时跟着改
        if 'FlowTarget' in fields and 'CodeBlock' in f:
            from storygraph.story.flow import parse_flow, render_flow_line
            flow = parse_flow(s.beats[idx])
            if flow and str(f.get('CodeBlock') or '').strip() == flow.get('line'):
                f['CodeBlock'] = render_flow_line(
                    fields.get('FlowType', flow['type']),
                    fields['FlowTarget'],
                    fields.get('FlowArgs', flow.get('args') or ''),
                    fields.get('FlowFrom', flow.get('from_clause') or ''))
        s.apply(idx, f, cards=s.cards(idx), advanced_open=True,
                stage_loaded=f.get('Stage'))
        return 'ui_edit(%d)=%r' % (idx, fields)
    return op


SRC_SAY = 'label start:\n    e "台词"\n    e "第二句"\n    return\n'
SRC_MENU = ('label start:\n    menu:\n        "选项一":\n            e "选了 1"\n'
            '        "选项二":\n            e "选了 2"\n    return\n')
SRC_CALL = 'label start:\n    call side from _call_side\n    e "回来了"\n    return\n'
SRC_JUMP = 'label start:\n    jump other\n\nlabel other:\n    e "在这"\n    return\n'
SRC_COMMENT = 'label start:\n    # 旧注释\n    e "台词"\n    return\n'

print('======== 高级区字段 ========')
run('改 Label（第一拍）', SRC_SAY, edit(0, Label='new_name'),
    'label 行改名，正文不动')
run('改 Kind 为 narration', SRC_SAY, edit(0, Kind='narration'),
    '那一行变成旁白（无说话人）')
run('给台词加 SayTail', SRC_SAY, edit(0, SayTail='(id="l1")'),
    '台词行尾多 (id="l1")')
run('改 MenuName', SRC_MENU, edit(0, MenuName='my_menu'),
    'menu 头变成 menu my_menu:')
run('改 MenuArgs', SRC_MENU, edit(0, MenuArgs='(x=1)'),
    'menu 头带参数')
run('多行注释', SRC_COMMENT, edit(0, Comment='第一行\n第二行'),
    '注释变两行')

print('\n======== 控制流（API 直连 vs UI 风格）========')
run('[API] 改 call 目标', SRC_CALL, edit(0, FlowTarget='other_side'),
    'call 行改目标（这一条应红：已知 raw 被 CodeBlock 降级）')
run('[UI] 改 call 目标', SRC_CALL, edit_ui_style(0, FlowTarget='other_side'),
    '同上，但按 UI 的 _flow_code_synced 先同步代码格')
run('[API] call → jump', SRC_CALL, edit(0, FlowType='jump', FlowTarget='other'),
    '那一行变成 jump other')
run('[UI] jump 改目标', SRC_JUMP, edit_ui_style(0, FlowTarget='third'),
    'jump third')
run('[API] 改 call 参数', SRC_CALL, edit(0, FlowArgs='(a=1)'),
    'call side(a=1) from …')

print('\n======== 移动 / 撤销 ========')
def move_op(a, b):
    def op(s):
        r = s.move_beat(a, b)
        return 'move(%d->%d)=%s' % (a, b, (r or {}).get('ok'))
    return op


def undo_op(idx=0):
    def op(s):
        r = s.undo(idx)
        return 'undo(%d) -> %r' % (idx, getattr(r, 'text', r))
    return op


run('移动：第0拍挪到最后', SRC_SAY, move_op(0, 2),
    '顺序变成 二/三/一（这是已知红：补丁不认重排）')

def edit_then_undo(s):
    f = s.fields(0); f['Text'] = '改过的'
    s.apply(0, f, cards=s.cards(0), advanced_open=True, stage_loaded=f.get('Stage'))
    s.save(0, f, cards=s.cards(0), advanced_open=True, stage_loaded=f.get('Stage'))
    s.undo(0)
    return '改一句再撤销'


run('改一句 → 撤销 → 导出', SRC_SAY, edit_then_undo,
    '撤销后与原文逐字节相同')

def del_then_restore(s):
    s.delete_beat(0)
    s.restore_last_removed()
    return '删一拍再恢复'


run('删一拍 → 恢复 → 导出', SRC_SAY, del_then_restore,
    '恢复后与原文逐字节相同')
