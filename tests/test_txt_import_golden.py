"""
金标守卫 · 纯文字导入产物指纹（S1/S2 护栏）
============================================

`tools/probe_txt_import_fingerprint.py` 对真机 5 章定稿跑一遍，记录每章
**产物的 sha256** + 行型统计 + 未映射清单（`tests/golden_txt_import_fingerprint.txt`）。

为什么不能只靠 `test_txt_import_hell` 的"往返逐字节"：那条只证明"产物与我们自己的
渲染器一致"，**测不出"映射规则变了但产物仍自洽"** —— 例如 `JC` 从 `jc` 改成别的
变量：往返照样一致，剧情里说话人却换了人。

⚠ 定稿 txt 不在本机时 skip。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

TXT_DIR = Path(r'D:\千连万理\txt--renpy脚本转换工具')
GOLDEN = ROOT / 'tests' / 'golden_txt_import_fingerprint.txt'
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')


@pytest.mark.skipif(not TXT_DIR.is_dir(), reason='定稿 txt 不在本机')
def test_txt_import_products_match_the_golden():
    import probe_txt_import_fingerprint as probe

    body = GOLDEN.read_text(encoding='utf-8').split('\n', 1)
    want = body[0].strip()
    assert want, '金标文件是空的'
    fp, _text, cases = probe.fingerprint(TXT_DIR)
    if fp != want:
        old = json.loads(body[1]) if len(body) > 1 else {}
        lines = []
        for name in sorted(set(old) | set(cases)):
            a, b = old.get(name) or {}, cases.get(name) or {}
            if a != b:
                lines.append(f'  {name}:')
                for k in sorted(set(a) | set(b)):
                    if a.get(k) != b.get(k):
                        lines.append(f'     {k}: {a.get(k)} -> {b.get(k)}')
        assert False, ('纯文字导入的产物变了（你在改转换规则：这就是回归）\n'
                       + '\n'.join(lines[:30]))


@pytest.mark.skipif(not TXT_DIR.is_dir(), reason='定稿 txt 不在本机')
def test_txt_import_fingerprint_is_deterministic():
    import probe_txt_import_fingerprint as probe

    assert probe.fingerprint(TXT_DIR)[0] == probe.fingerprint(TXT_DIR)[0]
