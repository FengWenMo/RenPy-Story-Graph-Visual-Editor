"""
金标守卫 · `scan_assets` 结果指纹（M4 拆它的护栏）
==================================================

`scan_assets` 决定"**什么是项目资源**"—— 对账、补登、删行、候选表全建在它上面。
拆它时改一条归类规则就等于改整张表，所以先录指纹
（`tests/golden_scan_fingerprint.txt`）。

覆盖四种组合（**嵌套副本排除**是其中最要紧的一档：真机上带 walk 时
images 3282 → 1657，正好把另一份拷贝排掉）：
  · `include_ui=True/False` × 带/不带 `FileWalkIndex`

⚠ 真机项目不在时 skip；`tests/test_asset_categorize.py` 是同一件事的单元级入口。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
GOLDEN = ROOT / 'tests' / 'golden_scan_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_scan_assets_result_matches_the_golden():
    import probe_scan_fingerprint as probe

    want = GOLDEN.read_text(encoding='utf-8').split('\n', 1)[0].strip()
    fp, _text, counts = probe.fingerprint(REAL)
    if fp != want:
        old = json.loads(
            GOLDEN.read_text(encoding='utf-8').split('\n', 1)[1] or '{}')
        lines = []
        _fp2, text2, _c2 = probe.fingerprint(REAL)
        new = json.loads(text2)
        for tag in sorted(set(old) | set(new)):
            for kind in ('images', 'music', 'sound', 'voices'):
                a = (old.get(tag) or {}).get(kind) or []
                b = (new.get(tag) or {}).get(kind) or []
                if a != b:
                    lines.append(f'  {tag}.{kind}: {len(a)} -> {len(b)}'
                                 f' 少{sorted(set(a) - set(b))[:3]}'
                                 f' 多{sorted(set(b) - set(a))[:3]}')
        assert False, ('scan_assets 的归类结果变了（你在做纯重构：这就是回归）\n'
                       + '\n'.join(lines))


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_scan_fingerprint_is_deterministic():
    """指纹必须可重复 —— 否则上面那条会随机红绿（上两轮踩过）。"""
    import probe_scan_fingerprint as probe

    a = hashlib.sha256(probe.fingerprint(REAL)[0].encode()).hexdigest()
    b = hashlib.sha256(probe.fingerprint(REAL)[0].encode()).hexdigest()
    assert a == b
