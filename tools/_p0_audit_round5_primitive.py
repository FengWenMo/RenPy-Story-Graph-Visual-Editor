"""P0 审计·第五轮·第一层：载体原语 `source_style.apply/read_text` 的字节保真。

只用临时目录，不碰任何用户项目。判据一律"构造期望字节"，不用同索引 diff。

跑法： python tools/_p0_audit_round5_primitive.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.story.source_style import (BOM, SourceStyle, detect,
                                           read_text)

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((ok, name, detail))
    print(f'{PASS if ok else FAIL} {name}' + (f'\n       {detail}' if detail else ''))


def run_case(label, raw: bytes, edit_lines, note=''):
    """把 `raw` 写成文件，读→改指定行→apply→写盘，返回新字节。"""
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_r5_'))
    try:
        p = tmp / 'ch.rpy'
        p.write_bytes(raw)
        text, style = read_text(p)
        lines = text.split('\n')
        for idx, new in edit_lines:
            lines[idx] = new
        out = style.apply('\n'.join(lines), source=p)
        p.write_bytes(out.encode('utf-8'))
        return p.read_bytes(), tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def eol_vector(b: bytes):
    """每行（以 \\n 切）的换行符形态 —— 只认 CRLF / LF。"""
    out = []
    i = 0
    for chunk in b.split(b'\n')[:-1]:
        out.append('CRLF' if chunk.endswith(b'\r') else 'LF')
    return out


def main():
    # ---------- 1. 单一 CRLF：只改第 2 行，其余行必须逐字节不动 ----------
    raw = (b'# \xe5\xa4\xb4\r\n'
           b'label ch1:\r\n'
           b'    "a"\r\n'
           b'    "b"\r\n'
           b'    return\r\n')
    got, _ = run_case('crlf', raw, [(3, '    "B"')])
    check('1 单一 CRLF 改一行：换行向量不变',
          eol_vector(got) == eol_vector(raw), f'{eol_vector(got)}')
    check('1b 单一 CRLF：未碰行逐字节相同',
          got.split(b'\n')[:2] == raw.split(b'\n')[:2])

    # ---------- 2. 混合换行 ----------
    raw = (b'a\r\nb\r\nc\r\nd\n e\nf\n')
    got, _ = run_case('mixed', raw, [(2, 'C')])
    check('2 混合换行：未碰行 EOL 全原样',
          eol_vector(got) == eol_vector(raw), f'{eol_vector(got)} vs {eol_vector(raw)}')

    # ---------- 3. BOM ----------
    raw = BOM.encode() + b'label ch1:\r\n    "a"\r\n'
    got, _ = run_case('bom', raw, [(1, '    "b"')])
    check('3 BOM 只出现一次且在开头',
          got.count(B'\xef\xbb\xbf') == 1 and got.startswith(b'\xef\xbb\xbf'),
          f'count={got.count(B"\xef\xbb\xbf")}')

    # ---------- 4. 末尾无换行 ----------
    raw = b'label ch1:\n    "a"'
    got, _ = run_case('noeol', raw, [(1, '    "b"')])
    check('4 末尾无换行：写回后仍然无换行', not got.endswith(b'\n'),
          repr(got[-12:]))

    # ---------- 5. 行内出现 U+2028 / NEL / \x0b / \x0c ----------
    # 这些是 Python str.splitlines 的"行边界"，但 Ren'Py 只认 \n。
    for name, ch in [('U+2028', '\u2028'), ('NEL U+0085', '\x85'),
                     ('VT \\x0b', '\x0b'), ('FF \\x0c', '\x0c')]:
        raw = ('label ch1:\n    "hi' + ch + 'there"\n    "b"\n').encode('utf-8')
        got, _ = run_case(name, raw, [(2, '    "B"')])
        check(f'5 行内 {name}：字节一个不动（只该改第 3 行）',
              got == raw.replace(b'"b"', b'"B"'),
              f'\n       got={got!r}\n       exp={raw.replace(b"\"b\"", b"\"B\"")!r}')

    # ---------- 6. 非 UTF-8 字节：read_text 宽松， apply(source=) 必须拒写 ----------
    raw = b'label ch1:\n    "a"\n    # \xe9\xff bad bytes\n    "b"\n'
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_r5b_'))
    try:
        p = tmp / 'ch.rpy'
        p.write_bytes(raw)
        text, style = read_text(p)
        bad = text.count('\ufffd')
        try:
            style.apply(text, source=p)
            raised = ''
        except Exception as exc:                # noqa: BLE001
            raised = type(exc).__name__
        check('6 非 UTF-8：read_text 有损、apply(source=) 拒写（不腐蚀）',
              bad > 0 and raised == 'SourceNotUtf8',
              f'U+FFFD×{bad}；抛={raised or "无（仍会腐蚀）"}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- 7. detect() 对"50/50 混合"判什么 ----------
    raw = b'a\r\nb\r\nc\nd\n'
    check('7 detect 在 50/50 混合文件上报 LF（逐行还原兜住，仅记录）',
          detect_bytes(raw).newline == '\n', detect_bytes(raw).describe())

    ok_n = sum(1 for ok, _, _ in results if ok)
    print(f'\n汇总：{ok_n}/{len(results)} 通过')
    return 0 if ok_n == len(results) else 1


def detect_bytes(raw: bytes):
    tmp = Path(tempfile.mkdtemp(prefix='sg_p0_r5c_'))
    try:
        p = tmp / 'x.rpy'
        p.write_bytes(raw)
        return detect(p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
