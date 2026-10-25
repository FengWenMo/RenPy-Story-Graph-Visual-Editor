# -*- coding: utf-8 -*-
"""
P0 审计探针 4：真·非法字节 + BOM 拒导机制确认。

  F' 注释里塞一个**真非法字节**（Latin-1 的 0xE9，不是 UTF-8 编码的 é）。
     判据：导出后这个字节还在不在？被换成 U+FFFD 就是静默改写用户文件。
  G  BOM 文件的 `_source_text_for_guard` 到底交了什么给门禁。
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession

SRC_LINES = [
    '# 文件头注释：必须保住',
    'label ch01:',
    '    scene bg classroom',
    '    show ling happy at left',
    '    ling "第一句"',
    '    "旁白一句"',
    '    ling "第三句"',
    '    return',
]

# —— F'：真非法字节 ——
tmp = Path(tempfile.mkdtemp(prefix='sg_p0probe4_'))
fp = tmp / 'game' / 'ch01.rpy'
fp.parent.mkdir(parents=True)
head = '# 文件头注释：必须保住 '.encode('utf-8') + b'\xe9\n'
raw = head + ('\n'.join(SRC_LINES[1:]) + '\n').encode('utf-8')
fp.write_bytes(raw)
before = fp.read_bytes()
print('=== F\' 注释里一个真非法字节 0xE9 ===')
print('  原文件能当 UTF-8 解码吗：', end='')
try:
    raw.decode('utf-8')
    print('能')
except UnicodeDecodeError:
    print('不能（Ren\'Py 也会报错）')
s = EditorSession(project_root=tmp)
try:
    n = s.load(fp)
    print(f'  load ok，拍到 {n} 张')
except ValueError as exc:
    # 2026-10-25 晚 P1-2 修好之后：非 UTF-8 载入就带坐标报错，不再静默 0 拍
    print(f'  load 明确拒绝（P1-2 已修）：{exc}')
    print(f'  文件字节未动：{fp.read_bytes() == before}')
    shutil.rmtree(tmp, ignore_errors=True)
    tmp = None
if tmp is not None:
    for b in s.beats:
        if '旁白' in str(getattr(b, 'text', '') or ''):
            b.text = '旁白改过了'
    res = s.export(fp)
    print(f'  export ok={res.get("ok")} used_patch={res.get("used_patch")} '
          f'codes={res.get("codes")}')
    after = fp.read_bytes()
    print(f'  字节完全未动：{after == before}')
    print(f'  0xE9 原字节还在吗：{bytes([0xE9]) in after}')
    print(f'  U+FFFD 出现了吗：{b"\xef\xbf\xbd" in after}')
    shutil.rmtree(tmp, ignore_errors=True)

# —— G：BOM 文件的 guard 源文 ——
print()
print('=== G BOM 文件的 _source_text_for_guard ===')
tmp = Path(tempfile.mkdtemp(prefix='sg_p0probe4b_'))
fp = tmp / 'game' / 'ch01.rpy'
fp.parent.mkdir(parents=True)
bom = b'\xef\xbb\xbf' + ('\n'.join(SRC_LINES) + '\n').encode('utf-8')
fp.write_bytes(bom)
s = EditorSession(project_root=tmp)
s.load(fp)
st = s._source_text_for_guard()
print('  guard 拿到的源文首行 repr：', repr((st or '').splitlines()[0]))
print('  首行带 BOM 字符：', (st or '').startswith('\ufeff'))
# 对照：patcher 用的 read_text（剥 BOM）
from storygraph.story.source_style import read_text as _rt
print('  source_style.read_text 首行 repr：', repr(_rt(fp)[0].splitlines()[0]))
shutil.rmtree(tmp, ignore_errors=True)
