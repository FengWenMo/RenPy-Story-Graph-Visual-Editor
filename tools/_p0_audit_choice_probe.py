# -*- coding: utf-8 -*-
"""
P0 审计探针 1：选择点追加路径的字节保真（story/choice_point.py:apply_choice_point）。

攻击设定：用户的选择点文件是 **CRLF + BOM**（Windows 中文项目常见形态，
本仓库自己审计过官方 tutorial 23 个脚本全是 CRLF+BOM）。
人在里面手写过内容，工具只是**追加**一个新选择点。
判据：追加之后，原有每一个字节都应保持原样（只剩末尾多出一段）。
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story.choice_point import (apply_choice_point,
                                           plan_choice_point)

tmp = Path(tempfile.mkdtemp(prefix='sg_p0probe_'))
game = tmp / 'game' / 'script'
game.mkdir(parents=True)

# 章节文件（选择点要挂在某一章上）
ch = game / 'ch01.rpy'
ch.write_text('label ch01:\n    "你好。\\n"\n    return\n', encoding='utf-8')

# 选择点文件：BOM + CRLF，且带一行**手写**注释
choices = ch.with_name('ch01_choices.rpy')
original = 'label ch01_choice_1:\n    menu:\n        "去吧":\n            jump ch01\n'
data = b'\xef\xbb\xbf' + original.replace('\n', '\r\n').encode('utf-8')
choices.write_bytes(data)

plan = plan_choice_point(tmp, ch, 'ch01')
print('plan.ok =', plan.ok, '| path =', Path(plan.path).name,
      '| label =', plan.label)
if not plan.ok:
    print('blocking:', plan.blocking)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0)

res = apply_choice_point(plan, project_root=tmp)
print('apply.ok =', res.get('ok'), res.get('error', ''))

after = choices.read_bytes()
before_lines = data.split(b'\r\n')
after_lines = after.split(b'\r\n')
print('--- 字节级对比 ---')
print('before bytes =', len(data), '| after bytes =', len(after))
print('before CRLF 行数 =', data.count(b'\r\n'),
      '| after CRLF 行数 =', after.count(b'\r\n'))
print('before 含 BOM =', data.startswith(b'\xef\xbb\xbf'),
      '| after 含 BOM =', after.startswith(b'\xef\xbb\xbf'))
print('after 含裸 LF =', after.count(b'\n') - after.count(b'\r\n'))
kept = all(l in after_lines for l in before_lines if l)
print('原有每一行都原样还在 =', kept)
if not kept:
    lost = [l for l in before_lines if l and l not in after_lines]
    print('!! 被改写的原行（前 5 条）:')
    for l in lost[:5]:
        print('   ', l)
print('tmp =', tmp)
shutil.rmtree(tmp, ignore_errors=True)
