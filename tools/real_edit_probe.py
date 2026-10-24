# -*- coding: utf-8 -*-
"""临时（Write 写）：真机章节上各类编辑的落盘情况。跑完删。"""
import sys, io, os, shutil, tempfile, difflib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from storygraph.app.editor import EditorSession

MARKER = '# 由剧本表自动生成'
REAL = Path(r'D:\千连万理\RenpyTest\sr_general\game\pomodorro\script_pomo.rpy')


def body(t):
    out, skip = [], False
    for ln in t.splitlines():
        if ln.startswith(MARKER):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


def run(name, op):
    t = Path(tempfile.mkdtemp(prefix='sg_rf_'))
    g = t / 'game'
    g.mkdir(parents=True)
    dst = g / REAL.name
    shutil.copyfile(REAL, dst)
    s = EditorSession(project_root=t)
    n = s.load(dst)
    before = dst.read_bytes()
    try:
        note = op(s)
    except Exception as exc:                     # noqa: BLE001
        print('%-26s [操作异常] %r' % (name, exc))
        return
    res = s.export(dst)
    after = dst.read_bytes()
    b1 = body(before.decode('utf-8', 'replace'))
    b2 = body(after.decode('utf-8', 'replace'))
    sm = difflib.SequenceMatcher(None, b1, b2, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != 'equal']
    touched = sum(max(i2 - i1, j2 - j1) for _t, i1, i2, j1, j2 in ops)
    if b1 == b2:
        tag = '❌ 零 diff（没落盘）'
    elif touched <= 2:
        tag = '✅ 只动 %d 行' % touched
    else:
        tag = '⚠ 波及 %d 行' % touched
    print('%-26s %s | ok=%s' % (name, tag, res.get('ok')))
    if res.get('blocking'):
        print('%-26s   拒因：%s' % ('', (res.get('blocking') or [''])[0][:110]))
    if b1 != b2:
        for _t, i1, i2, j1, j2 in ops[:3]:
            print('%-26s   %s 原:%r 新:%r' % ('', _t, b1[i1:i2][:2], b2[j1:j2][:2]))


def edit(idx, field, value):
    def op(s):
        fl = s.fields(idx)
        fl[field] = value
        s.apply(idx, fl, cards=s.cards(idx), advanced_open=True,
                stage_loaded=fl.get('Stage'))
        return 'edit(%d,%s)' % (idx, field)
    return op


# 列出拍类型分布（只读一次）
t = Path(tempfile.mkdtemp(prefix='sg_rf0_'))
g = t / 'game'; g.mkdir(parents=True)
dst = g / REAL.name
shutil.copyfile(REAL, dst)
s0 = EditorSession(project_root=t)
s0.load(dst)
print('真机 %s 拍数=%d' % (REAL.name, len(s0.beats)))
for i, b in enumerate(s0.beats):
    if b.kind in ('menu', 'menu_option') or (b.text and i < 30):
        print('  [%d] %-13r %r' % (i, b.kind, (b.text or b.raw or '')[:34]))
print()
run('改 if 内台词(idx24)', edit(24, 'Text', '时间到了X'))
run('改普通台词(idx25)', edit(25, 'Text', '您可能需要休息了X'))
run('改 menu 标题(idx15)', edit(15, 'Text', '是时候X'))
run('改 menu 选项(idx16)', edit(16, 'Text', '开始X'))
run('改选项体台词(idx17)', edit(17, 'Text', 'X'))


def del_at(idx):
    def op(s):
        r = s.delete_beat(idx)
        return 'del(%d)=%s' % (idx, (r or {}).get('ok'))
    return op


def ins_at(idx):
    def op(s):
        r = s.insert_beat(idx)
        return 'ins(%d)=%s' % (idx, (r or {}).get('ok'))
    return op


run('删一拍(idx25)', del_at(25))
run('插一拍(idx25)', ins_at(25))


def move(a, b):
    def op(s):
        r = s.move_beat(a, b)
        return 'move(%d,%d)=%s' % (a, b, (r or {}).get('ok'))
    return op


run('移动(idx25→28)', move(25, 28))
