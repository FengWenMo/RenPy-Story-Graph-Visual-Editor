# -*- coding: utf-8 -*-
"""
探针：保真报告里那两类差异到底长什么样（2026-11-02）。

差异 A：源里台词含 `\\x05`（不可见控制字符），产物里没了。
差异 B：`chapter3_4_4.rpy` 产物多 2 行，多出来的行是「台词 + \\t\\t#注释」。

本脚本只读源文件 + 打印 repr / 十六进制，不写任何东西到用户项目。
跑法：
    D:\\Python\\python.exe tools\\_probe_fidelity_gaps_20261102.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(r'D:\千连万理\RenPyTest\sr_general\game')

CASES = {
    'A': (ROOT / 'chapter' / 'chapter3_3_3.rpy', [77, 165]),
    'A2': (ROOT / 'chapter' / 'chapter4_1_2.rpy', [48]),
    'B': (ROOT / 'chapter' / 'chapter3_4_4.rpy', list(range(80, 100)) + list(range(180, 195))),
}


def show_lines(path: Path, lineno_list: list):
    print(f'\n===== {path.name} =====')
    raw = path.read_bytes()
    print(f'  文件大小 {len(raw)} B；BOM={raw[:3] == b"\xef\xbb\xbf"}；'
          f'CRLF={raw.count(b"\r\n")} LF={raw.count(b"\n")}')
    text = raw.decode('utf-8', 'replace')
    lines = text.split('\n')
    for n in lineno_list:
        if 1 <= n <= len(lines):
            ln = lines[n - 1]
            print(f'  [{n:>4}] {ln!r}')
            bad = [(i, hex(ord(ch))) for i, ch in enumerate(ln)
                   if ord(ch) < 32 and ch not in '\t']
            if bad:
                print(f'         控制字符：{bad}')


def scan_ctrl(path_root: Path):
    """全项目扫：哪些 .rpy 里有控制字符（除 \\t \\n \\r）。"""
    print('\n===== 全项目控制字符扫描 =====')
    total = 0
    hits = []
    for p in sorted(path_root.rglob('*.rpy')):
        raw = p.read_bytes()
        try:
            t = raw.decode('utf-8')
        except UnicodeDecodeError:
            print(f'  ! 非 UTF-8：{p}')
            continue
        found = {}
        for ch in t:
            o = ord(ch)
            if o < 32 and ch not in '\t\n\r':
                found[hex(o)] = found.get(hex(o), 0) + 1
        if found:
            total += sum(found.values())
            hits.append((p, found))
    for p, found in hits:
        print(f'  {p.relative_to(path_root)}  {found}')
    print(f'  合计 {len(hits)} 个文件 / {total} 处')


def main() -> int:
    for key, (path, lines) in CASES.items():
        if not path.exists():
            print(f'跳过（不存在）：{path}')
            continue
        show_lines(path, lines)
    scan_ctrl(ROOT)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
