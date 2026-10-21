"""
探针：**载体风格**（编码 / 换行 / BOM / 末尾换行）在被工具碰过之后还在不在。

为什么单独探这一面：现有 200+ 个探针全在量"内容"（拍数、行数、句子在不在），
而没有量"字节的载体"。而载体一旦漂了，`errors='replace'` 那一层会把
"读不出来"变成"读成一个替换符"，再写回去就是**永久损坏**。

四个场景，每个都走 CLI 的真实路径（`table rpy2xlsx` → 改一格 → `table xlsx2rpy --source`）：

  A. UTF-8 无 BOM + LF        （基线，应当逐字节）
  B. UTF-8 + BOM + CRLF       （Windows 中文项目常态）
  C. 文件末尾**没有换行**     （手写文件常见）
  D. GBK 编码                 （非 UTF-8：读不出来时工具该**拒写**，而不是替换）

用法：
    D:/Python/python.exe tools/probe_carrier_style.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

#: 一句台词只改一个字，确保走的是"改一行"而不是"零改动"。
BODY = '''\
label ch1:
    jc "第一句台词。"
    jc "第二句台词。"
    return
'''

EDITED = '第二句台词。'


def run(*args):
    p = subprocess.run([PY, str(ROOT / 'cli.py'), *args], cwd=str(ROOT),
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def edit_one_cell(xlsx: Path, old: str, new: str) -> bool:
    """在表里找含 old 的格子，改掉其中 old → new。"""
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx))
    hit = False
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and old in c.value:
                    c.value = c.value.replace(old, new)
                    hit = True
    wb.save(str(xlsx))
    return hit


def one_case(name: str, raw: bytes, old: str, new: str) -> dict:
    """跑一遍往返，返回 {name, ok, ...}。"""
    out = {'name': name}
    with tempfile.TemporaryDirectory(prefix='carrier_') as td:
        work = Path(td)
        src = work / 'ch1.rpy'
        src.write_bytes(raw)
        before = src.read_bytes()

        xl = work / 'ch1.xlsx'
        rc, log = run('table', 'rpy2xlsx', str(src), '--out', str(xl))
        out['rpy2xlsx_rc'] = rc
        if rc != 0 or not xl.exists():
            out['ok'] = False
            out['why'] = 'rpy2xlsx 失败：' + log.strip().splitlines()[-1:][0] if log.strip() else '无输出'
            return out

        if not edit_one_cell(xl, old, new):
            out['ok'] = False
            out['why'] = '表里找不到那句台词，改不了'
            return out

        rc, log = run('table', 'xlsx2rpy', str(xl), '--out', str(src),
                      '--source', str(src), '--no-engine-lint')
        out['xlsx2rpy_rc'] = rc
        after = src.read_bytes()

        out['changed'] = before != after
        out['before_len'] = len(before)
        out['after_len'] = len(after)
        out['ok'] = rc == 0
        # 载体检查
        out['has_bom_before'] = before.startswith(b'\xef\xbb\xbf')
        out['has_bom_after'] = after.startswith(b'\xef\xbb\xbf')
        out['crlf_before'] = before.count(b'\r\n')
        out['crlf_after'] = after.count(b'\r\n')
        out['endswith_nl_before'] = before.endswith(b'\n')
        out['endswith_nl_after'] = after.endswith(b'\n')
        out['replacement_char_after'] = after.count('\ufffd'.encode('utf-8'))
        # 内容检查：新句子在不在、旧句子走了没
        txt_after = after.decode('utf-8', 'replace')
        out['new_present'] = new in txt_after
        out['old_gone'] = old not in txt_after
        # 解码成人类可看
        out['tail'] = repr(after[-60:])
        out['log_tail'] = '\n'.join(
            [ln for ln in log.strip().splitlines() if ln.strip()][-3:])
        return out


def main() -> int:
    body_lf = BODY
    body_crlf = BODY.replace('\n', '\r\n')
    cases = [
        ('A utf8+LF',       body_lf.encode('utf-8'),  '第二句', '改过的一句'),
        ('B utf8+BOM+CRLF', b'\xef\xbb\xbf' + body_crlf.encode('utf-8'),
                            '第二句', '改过的一句'),
        ('C 无末尾换行',     body_lf.rstrip('\n').encode('utf-8'),
                            '第二句', '改过的一句'),
        ('D GBK 编码',       BODY.encode('gbk'),      '第二句', '改过的一句'),
    ]
    rows = []
    for name, raw, old, new in cases:
        try:
            rows.append(one_case(name, raw, old, new))
        except Exception as e:                    # noqa: BLE001 - 探针要跑完
            rows.append({'name': name, 'ok': False, 'why': f'{type(e).__name__}: {e}'})
        print('=' * 70)
        r = rows[-1]
        for k, v in r.items():
            print(f'  {k:24} {v}')
    print('=' * 70)
    # 结论表
    print(f'{"场景":22} {"rc":>4} {"字节变了":>8} {"BOM":>10} {"CRLF":>10} '
          f'{"末尾NL":>8} {"替换符":>6}')
    for r in rows:
        if 'before_len' not in r:
            print(f'{r["name"]:22} {"-":>4} {"-":>8} {"-":>10} {"-":>10} '
                  f'{"-":>8} {"-":>6}  {r.get("why", "")}')
            continue
        bom = f'{r["has_bom_before"]}->{r["has_bom_after"]}'
        crlf = f'{r["crlf_before"]}->{r["crlf_after"]}'
        nl = f'{r["endswith_nl_before"]}->{r["endswith_nl_after"]}'
        print(f'{r["name"]:22} {r["xlsx2rpy_rc"]:>4} {str(r["changed"]):>8} '
              f'{bom:>10} {crlf:>10} {nl:>8} {r["replacement_char_after"]:>6}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
