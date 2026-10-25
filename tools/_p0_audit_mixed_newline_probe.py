# -*- coding: utf-8 -*-
"""混合换行文件的导出保真探针。

`patch_export` 的做法是 `original.splitlines()` → 替换 → 整体写回。
`splitlines()` **丢掉每行自己的换行符**，于是混合换行文件
（一部分行 CRLF、一部分 LF）导出后必然被归一成一种 ——
用户没碰的行也被静默改写。

判据：
  · 建一个 8 行文件：前 4 行 CRLF、后 4 行 LF（混合）；
  · 只改第 6 行（一句台词），原地导出；
  · 逐字节比：除了生成标记 2 行 + 改的那 1 行，
    **其余行的换行符必须原样**（CRLF 还是 CRLF、LF 还是 LF）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from storygraph.app.editor import EditorSession  # noqa: E402

SRC_LINES = [
    'label ch01:',
    '    scene bg classroom',
    '    show ling happy at left',
    '    ling "第一句"',
    '    "旁白一句"',
    '    ling "第三句"',
    '    "第四句"',
    '    return',
]


def build(path: Path):
    """前 4 行 CRLF、后 4 行 LF。"""
    out = bytearray()
    for i, ln in enumerate(SRC_LINES):
        out += ln.encode('utf-8')
        out += b'\r\n' if i < 4 else b'\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def split_keep_eol(data: bytes):
    """按字节拆成 (行内容, 行结束符) 列表（保留每行自己的换行符）。"""
    rows, cur, i = [], bytearray(), 0
    while i < len(data):
        b = data[i:i + 1]
        cur += b
        if b == b'\n':
            rows.append((bytes(cur[:-1]), b'\n'))
            cur = bytearray()
        elif b == b'\r':
            if data[i + 1:i + 2] == b'\n':
                rows.append((bytes(cur[:-1]), b'\r\n'))
                cur = bytearray()
                i += 1
            else:
                rows.append((bytes(cur[:-1]), b'\r'))
                cur = bytearray()
        i += 1
    if cur:
        rows.append((bytes(cur), b''))
    return rows


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sg_mixed_nl_'))
    fp = tmp / 'game' / 'ch01.rpy'
    build(fp)
    before = split_keep_eol(fp.read_bytes())
    print('== 混合换行文件（前 4 行 CRLF，后 4 行 LF）==')
    print('  各行换行符：', [eol for _c, eol in before])

    s = EditorSession(project_root=tmp)
    n = s.load(fp)
    print(f'  load ok，拍到 {n} 张')
    for b in s.beats:
        if '第三句' in str(getattr(b, 'text', '') or ''):
            b.text = '第三句改过了'
    res = s.export(fp)
    print(f'  export ok={res.get("ok")} used_patch={res.get("used_patch")}')
    after = split_keep_eol(fp.read_bytes())
    print('  导后各行换行符：', [eol for _c, eol in after])

    # —— 逐行裁决：原文的 8 行，导后必须还能找到，且换行符原样 ——
    problems = []
    for idx, (content, eol) in enumerate(before):
        if '第三句' in content.decode('utf-8', 'replace'):
            continue                      # 这一行是用户改的，本来就该变
        hit = [e for c, e in after if c == content]
        if not hit:
            problems.append(f'第 {idx} 行内容没了：{content[:30]!r}')
        elif eol not in hit:
            problems.append(f'第 {idx} 行换行符被改：{eol!r} → '
                            f'{hit[0]!r}（{content[:30]!r}）')
    print()
    if problems:
        print(f'命中 {len(problems)} 条保真问题：')
        for p in problems:
            print('  -', p)
    else:
        print('未编辑的行换行符全部原样。')
    # 被改的那一行换成什么换行（informational）
    for c, e in after:
        if '第三句改过了'.encode('utf-8') in c:
            print(f'  （改过的行用：{e!r}）')
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
