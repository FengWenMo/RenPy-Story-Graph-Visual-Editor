# -*- coding: utf-8 -*-
"""
P0 扫描 · 第四轮续：自定义语句 / 多行 python / extend·nvl / 删空 label
=====================================================================
"""
from __future__ import annotations

import difflib
import io
import json
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


def run(name, src, op, expect, rules=None):
    tmp = Path(tempfile.mkdtemp(prefix='sg_r4_'))
    g = tmp / 'game'
    g.mkdir(parents=True)
    p = g / 'script.rpy'
    p.write_bytes(src.encode('utf-8'))
    if rules:
        (tmp / 'storygraph.rules.json').write_text(
            json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    s = EditorSession(project_root=tmp)
    s.load(p)
    try:
        note = str(op(s) or '')
    except Exception as exc:                       # noqa: BLE001
        print('\n### %s\n    [操作异常] %r' % (name, exc))
        return
    res = s.export(p)
    after = open(p, encoding='utf-8', newline='').read()
    b1, b2 = body(src), body(after)
    sm = difflib.SequenceMatcher(None, b1, b2, autojunk=False)
    ops = [o for o in sm.get_opcodes() if o[0] != 'equal']
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


# ---------------------------------------------------------------- 自定义语句
RULES = {
    'statements': {
        'mycue': {
            'line': 'mycue intensity int =0 layer str =camera',
        }
    }
}
SRC_SPEC = ('label start:\n'
            '    mycue intensity=3 layer=camera\n'
            '    e "台词"\n'
            '    return\n')
run('S1 声明过的自定义语句：原地往返', SRC_SPEC, lambda s: None,
    '逐字节相同', rules=RULES)
run('S2 声明过的语句：改参数（代码格）', SRC_SPEC,
    edit(0, CodeBlock='mycue intensity=5 layer=camera'),
    '只改那一行', rules=RULES)
run('S3 声明过的语句：改台词', SRC_SPEC, edit(1, Text='改过的'),
    '只改台词行', rules=RULES)

# ---------------------------------------------------------------- 多行 python
SRC_PY = ('label start:\n'
          '    python:\n'
          '        x = 1\n'
          '        y = 2\n'
          '        if x:\n'
          '            z = 3\n'
          '    e "台词"\n'
          '    return\n')
run('P1 多行 python 块：原地往返', SRC_PY, lambda s: None, '逐字节相同')
run('P2 多行 python 块：改后面台词', SRC_PY, edit(1, Text='改过的'),
    '只改台词，python 块一字不动')
run('P3 多行 python 块：改代码格', SRC_PY,
    edit(0, CodeBlock='python:\n    x = 10\n    y = 2\n    if x:\n        z = 3'),
    '只改 x=1 那一行')

# ---------------------------------------------------------------- extend / nvl
SRC_EXT = ('label start:\n'
           '    e "第一句"\n'
           '    extend "接着说"\n'
           '    nvl clear\n'
           '    n "nvl 旁白"\n'
           '    window hide\n'
           '    e "再来"\n'
           '    return\n')
run('E1 extend/nvl/window：原地往返', SRC_EXT, lambda s: None, '逐字节相同')
run('E2 改 extend 的正文', SRC_EXT, edit(1, Text='接着说改'),
    '只改 extend 那一行')
run('E3 改 nvl 旁白', SRC_EXT, edit(3, Text='nvl 改'),
    '只改那一行')

# ---------------------------------------------------------------- 删空 label
SRC_LAB = ('label only:\n'
           '    e "唯一一句"\n'
           '    return\n'
           '\n'
           'label other:\n'
           '    e "另一句"\n'
           '    return\n')
run('L1 删唯一内容拍（留 return）', SRC_LAB, del_at(0),
    'only 里只剩 return，other 不动')
run('L2 删光 only 的两拍（级联）', SRC_LAB, del_at(0, cascade=True),
    '要么拒绝，要么 only 变成空 label（补 pass），other 不动')

# ---------------------------------------------------------------- 同拍多语句
SRC_MIX = ('label start:\n'
           '    scene bg room\n'
           '    show e happy at left\n'
           '    play music "a.ogg"\n'
           '    e "台词"\n'
           '    return\n')
run('M1 多语句拍：改台词', SRC_MIX, edit(0, Text='改过的'),
    '只改台词那一行，舞台/音频不动')
run('M2 多语句拍：清空背景', SRC_MIX, edit(0, Scene=''),
    '只少 scene 行')
run('M3 多语句拍：改立绘位置', SRC_MIX, edit(0, Position='right'),
    '只改 show 行')

# ---------------------------------------------------------------- voice 行
SRC_V = ('label start:\n'
         '    voice "v1.ogg"\n'
         '    e "台词"\n'
         '    voice "v2.ogg"\n'
         '    e "第二句"\n'
         '    return\n')
run('V1 改第二句台词', SRC_V, edit(1, Text='第二句改'),
    '只改那一行，两条 voice 不动')
run('V2 改 Voice 字段', SRC_V, edit(0, Voice='w1.ogg'),
    '只改第一条 voice 行')
