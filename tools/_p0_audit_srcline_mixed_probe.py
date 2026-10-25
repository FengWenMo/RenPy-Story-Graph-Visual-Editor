# -*- coding: utf-8 -*-
"""P1-5 遗漏路径实证：source_line_delete 删源行时混合换行是否仍归一化。

背景：并行修复给 source_style.apply() 加了 source= 逐行还原，
control_route / control_branch / control_takeover / choice_point /
window_nodes / session_export / compile / generation_guard 都传了；
graph/commands（undo/redo）与 source_line_delete 没传。
undo/redo 的 old_text 读时已丢 EOL（read_text 统一 LF），属更深设计限制；
source_line_delete 是**原地改写点**（删剧本行），本可逐行还原却漏了。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.source_line_delete import (
    plan_source_line_delete, apply_source_line_delete)
from storygraph.app.editor import EditorSession

LINES = [
    'label ch01:',
    '    "第一句"',
    '    jump target_a',
    '    "第二句"',
    '    call other',
    '    jump target_b',
    '    return',
]
# 前 4 行 CRLF、后 3 行 LF（混合换行）
raw = b''
for i, line in enumerate(LINES):
    raw += line.encode('utf-8')
    raw += b'\r\n' if i < 4 else b'\n'

tmp = Path(tempfile.mkdtemp(prefix='sg_p15_sld_'))
game = tmp / 'game'
game.mkdir(parents=True)
rpy = game / 'ch01.rpy'
rpy.write_bytes(raw)
print(f'混合换行文件：{len(LINES)} 行，前 4 CRLF 后 3 LF')

# 取 jump target_a 拍的 line_id（走真实加载路径）
session = EditorSession(project_root=tmp)
session.load(rpy)
beat = next((b for b in (session.beats or ())
             if str(getattr(b, 'raw', '') or '').strip() == 'jump target_a'),
            None)
if beat is None:
    print('没找到 jump 拍；session.beats =',
          [str(getattr(b, 'raw', '')) for b in (session.beats or ())])
    sys.exit(1)
line_id = str(getattr(beat, 'line_id', '') or '')
print(f'目标拍 line_id={line_id!r} raw={getattr(beat, "raw", "")!r}')

# 删这一拍（第 3 行，CRLF 行）
plan = plan_source_line_delete(tmp, rpy, line_id)
if not plan.ok:
    print(f'计划失败：{plan.reason} blocking={plan.blocking}')
    sys.exit(1)
print(f'计划 ok：{plan.reason}')

res = apply_source_line_delete(plan)
print(f'apply ok={res["ok"]} error={res["error"]!r}')

after = rpy.read_bytes()
after_eols = []
for chunk in after.splitlines(keepends=True):
    if chunk.endswith(b'\r\n'):
        after_eols.append('CRLF')
    elif chunk.endswith(b'\n'):
        after_eols.append('LF')
print(f'删后各行 EOL：{after_eols}')

# 判据：删掉的是第 3 行（CRLF 行）；剩下的 6 行里
# 原 CRLF 的 3 行（1、2、4 行）+ 原 LF 的 3 行（5、6、7 行）应保持各自 EOL
expected = ['CRLF', 'CRLF', 'CRLF', 'LF', 'LF', 'LF']
ok = after_eols == expected
print(f'期望：{expected}')
print(f'未碰行 EOL 全部原样：{ok}')
if not ok:
    print('★ 缺口确认：source_line_delete 没传 source= → 删一拍把全文换行统一了')

# 顺带：CRLF 单风格文件不应回归（单风格走老路径应仍正确）
raw2 = b''.join((l + '\r\n').encode('utf-8') for l in LINES)
rpy2 = game / 'ch02.rpy'
rpy2.write_bytes(raw2)
session2 = EditorSession(project_root=tmp)
session2.load(rpy2)
beat2 = next((b for b in (session2.beats or ())
              if str(getattr(b, 'raw', '') or '').strip() == 'jump target_a'),
             None)
if beat2 is None:
    print('单风格对照：没找到 jump 拍，跳过')
    sys.exit(0)
plan2 = plan_source_line_delete(
    tmp, rpy2, str(getattr(beat2, 'line_id', '') or ''))
res2 = apply_source_line_delete(plan2)
eols2 = ['CRLF' if c.endswith(b'\r\n') else 'LF'
         for c in rpy2.read_bytes().splitlines(keepends=True)]
print(f'单风格 CRLF 文件删行：ok={res2["ok"]} EOL={set(eols2)}')
