# -*- coding: utf-8 -*-
"""
P0 审计探针 2：敌对文件形态下的「载入 → 改一拍 → 原地导出」循环。

攻击形态（都是 Windows 中文项目里真实存在的）：
  A. CRLF 文件        —— 只有改过的那行能变，其余 \r\n 必须原样
  B. 混合换行文件    —— 不许把内容改坏（风格统一是既定行为，但语义行不能动）
  C. 无末尾换行文件  —— 不许擅自补/删字节
  D. GBK 编码文件    —— 不许把用户的字节静默变成乱码
  E. BOM 文件        —— BOM 必须在且只在开头

判据：除"用户真改过的那一行"外，原文每一个字节都必须原样留在盘上。
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


def make_project(tmp: Path):
    game = tmp / 'game'
    game.mkdir(parents=True, exist_ok=True)
    return game


def run_case(name: str, raw: bytes, edit: bool = True):
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0probe2_'))
    game = make_project(tmp)
    fp = game / 'ch01.rpy'
    fp.write_bytes(raw)
    before = fp.read_bytes()

    print(f'=== {name} ===')
    s = EditorSession(project_root=tmp)
    try:
        n = s.load(fp)
    except Exception as e:                       # noqa: BLE001
        print(f'  load 抛异常：{type(e).__name__}: {e}')
        print(f'  文件是否被改动：{fp.read_bytes() != before}')
        shutil.rmtree(tmp, ignore_errors=True)
        return
    print(f'  load ok，拍到 {n} 张')
    if edit and s.beats:
        # 改第三张拍的正文（"旁白一句" → "旁白改过了"）
        target = None
        for b in s.beats:
            if '旁白' in str(getattr(b, 'text', '') or ''):
                target = b
                break
        if target is not None:
            target.text = '旁白改过了'
            try:
                res = s.export(fp)
            except Exception as e:               # noqa: BLE001
                print(f'  export 抛异常：{type(e).__name__}: {e}')
                shutil.rmtree(tmp, ignore_errors=True)
                return
            print(f'  export ok={res.get("ok")} '
                  f'used_patch={res.get("used_patch")} '
                  f'codes={res.get("codes")}')
            if not res.get('ok'):
                print(f'  blocking={res.get("blocking")}')
        else:
            print('  （没找到旁白拍，跳过导出）')
    after = fp.read_bytes()
    same = after == before
    print(f'  文件字节是否完全未动：{same}')
    if not same:
        bl = before.splitlines()
        al = after.splitlines()
        crlf_n = bytes([13, 10])
        print(f'  行数 {len(bl)} → {len(al)}；'
              f'CRLF {before.count(crlf_n)} → {after.count(crlf_n)}')
        diff = [i for i, (x, y) in enumerate(zip(bl, al)) if x != y]
        print(f'  内容不同的行号（0 基，前 10）：{diff[:10]}')
    shutil.rmtree(tmp, ignore_errors=True)


crlf = ('\r\n'.join(SRC_LINES) + '\r\n').encode('utf-8')
lf = ('\n'.join(SRC_LINES) + '\n').encode('utf-8')
bom_crlf = b'\xef\xbb\xbf' + crlf
mixed = ('\n'.join(SRC_LINES[:5]) + '\r\n'
         + '\r\n'.join(SRC_LINES[5:]) + '\r\n').encode('utf-8')
no_tail = ('\n'.join(SRC_LINES)).encode('utf-8')          # 末尾无换行
gbk = ('\n'.join(SRC_LINES) + '\n').encode('gbk')

run_case('A. CRLF', crlf)
run_case('B. BOM + CRLF', bom_crlf)
run_case('C. 混合换行', mixed)
run_case('D. 无末尾换行', no_tail)
run_case('E. GBK 编码', gbk, edit=True)
