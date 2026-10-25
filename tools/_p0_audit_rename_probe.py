# -*- coding: utf-8 -*-
"""
P0 审计探针 6（第二轮）：资产改名补丁的两个攻击面。

攻击一（TOCTOU）：plan → **用户/程序员在别处改了文件** → apply。
  判据：并发改进去的那些行，落盘后还在不在。
  对照：本项目其它落盘点（control_route / source_line_delete / takeover）
  全部"写前复读全文，对不上就拒写"——改名补丁是唯一不核对的。

攻击二（过度匹配）：把图片 id `bg` 改名成 `bg_new`，而项目里同时有
  `$ bg = 1`（Python 变量）、`# bg 注释`、`"bg 音乐"`（字符串）。
  判据：这些**不是资源引用**的行会不会被一起改写。
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_extensions import (apply_rename_patch,
                                                plan_rename_patch,
                                                rollback_rename_patch)

SRC = '''label ch01:
    scene bg classroom
    show bg
    $ bg = 1
    # bg 这是注释
    play music "bg 音乐.mp3"
    "台词"
    return
'''

tmp = Path(tempfile.mkdtemp(prefix='sg_p0_rename_'))
fp = tmp / 'game' / 'ch01.rpy'
fp.parent.mkdir(parents=True)
fp.write_text(SRC, encoding='utf-8')

print('=== 攻击二：过度匹配（先看预览会改哪些行）===')
plan = plan_rename_patch('bg', 'bg_new', [fp])
for f in plan['files']:
    for no, old, new in f['changed']:
        print(f'  L{no}: {old!r}')
        print(f'     -> {new!r}')
res = apply_rename_patch(plan, confirm=True, backup=True)
print('  apply ok =', res.get('ok'), '| 备份 =', [Path(b).name for b in res['backup_files']])
after = fp.read_text(encoding='utf-8')
print('  落盘后全文：')
for ln in after.splitlines():
    print(f'    {ln!r}')
code_ok = '$ bg_new = 1' in after
comment_kept = '# bg 这是注释' in after
music_kept = '"bg 音乐.mp3"' in after
print(f'  Python 变量被一起改（把用户代码改坏）：{code_ok}')
print(f'  注释被一起改：{not comment_kept}')
print(f'  字符串被一起改：{not music_kept}')
# 回滚验证
rb = rollback_rename_patch(res)
print('  回滚后与原文一致：', fp.read_text(encoding='utf-8') == SRC,
      '| restored =', rb.get('restored'))

print()
print('=== 攻击一：TOCTOU（计划之后文件被改过）===')
fp.write_text(SRC, encoding='utf-8')
plan = plan_rename_patch('bg', 'bg_new', [fp])
# 模拟：用户在看 diff 的时候，程序员在文件里加了一行**
concurrent = SRC.replace('    "台词"', '    "程序员刚加的一句"')
fp.write_text(concurrent, encoding='utf-8')
print('  计划后盘上内容已变成（含程序员新加行）:',
      '程序员刚加的一句' in fp.read_text(encoding='utf-8'))
res = apply_rename_patch(plan, confirm=True, backup=True)
after = fp.read_text(encoding='utf-8')
print('  apply 返回 ok =', res.get('ok'))
print('  ★ 程序员刚加的那句还在吗：', '程序员刚加的一句' in after)
print('  ★ 落盘内容是基于**过期读取**的旧全文：',
      'bg_new' in after and '程序员刚加的一句' not in after)
bak = res['backup_files'][0] if res['backup_files'] else ''
if bak:
    print('  （程序员那一句只在备份里活着：',
          '程序员刚加的一句' in Path(bak).read_text(encoding='utf-8'), '）')
print('  临时工程：', tmp)
shutil.rmtree(tmp, ignore_errors=True)
