# -*- coding: utf-8 -*-
"""
P0 审计探针 3：真实 diff + 单字节损坏场景。

接探针 2 的疑点：
  1. A/C/D 三例"所有行都不同"到底是内容变了还是只有换行变了？
  2. B 例（BOM+首行注释）被 OPAQUE_LOST 拒导 —— 确认误报机制。
  3. **单字节损坏**：一个能跑的 UTF-8 文件，注释里混进一个非法字节
     （Latin-1 的 é 直接塞进 UTF-8 流）—— 导出后这个字节会被静默
     替换成 U+FFFD 吗？（用户文件被静默改写的判定场景）
"""
import difflib
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


def cycle(name, raw, edit_text='旁白改过了'):
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0probe3_'))
    fp = tmp / 'game' / 'ch01.rpy'
    fp.parent.mkdir(parents=True)
    fp.write_bytes(raw)
    before = fp.read_bytes()
    print(f'=== {name} ===')
    s = EditorSession(project_root=tmp)
    try:
        s.load(fp)
    except Exception as e:                        # noqa: BLE001
        print(f'  load 抛异常：{type(e).__name__}: {e}')
        shutil.rmtree(tmp, ignore_errors=True)
        return
    for b in s.beats:
        if '旁白' in str(getattr(b, 'text', '') or ''):
            b.text = edit_text
    try:
        res = s.export(fp)
    except Exception as e:                        # noqa: BLE001
        print(f'  export 抛异常：{type(e).__name__}: {e}')
        shutil.rmtree(tmp, ignore_errors=True)
        return
    print(f'  export ok={res.get("ok")} used_patch={res.get("used_patch")} '
          f'codes={res.get("codes")}')
    after = fp.read_bytes()
    if after == before:
        print('  字节完全未动')
    else:
        b_txt = before.decode('utf-8', 'replace').splitlines()
        a_txt = after.decode('utf-8', 'replace').splitlines()
        real = [(i, x, y) for i, (x, y) in enumerate(zip(b_txt, a_txt))
                if x != y]
        print(f'  真实内容不同的行：{len(real)} 行')
        for i, x, y in real[:6]:
            print(f'    L{i + 1}: {x!r} -> {y!r}')
        print(f'  末尾字节 before={before[-6:]!r} after={after[-6:]!r}')
        print(f'  裸 LF 数 before={before.count(bytes([10])) - before.count(bytes([13, 10]))}'
              f' after={after.count(bytes([10])) - after.count(bytes([13, 10]))}')
    shutil.rmtree(tmp, ignore_errors=True)


lf = ('\n'.join(SRC_LINES) + '\n').encode('utf-8')
crlf = ('\r\n'.join(SRC_LINES) + '\r\n').encode('utf-8')
bom_crlf = b'\xef\xbb\xbf' + crlf
no_tail = ('\n'.join(SRC_LINES)).encode('utf-8')

cycle('A. CRLF', crlf)
cycle('B. BOM+CRLF（首行是注释）', bom_crlf)
cycle('D. 无末尾换行', no_tail)

# —— 单字节损坏：注释里混一个非法字节 ——
bad = lf.replace('# 文件头注释：必须保住'.encode('utf-8'),
                 '# 文件头注释：必须保住 \xe9'.encode('utf-8'))
cycle('F. UTF-8 文件 + 注释里一个非法字节 0xE9', bad)
