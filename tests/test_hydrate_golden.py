"""
金标守卫 · hydrate 结果指纹（M4 拆 `beat_build._hydrate_stage` 的护栏）
======================================================================

`_hydrate_stage` 把「舞台（高级）」列的 DSL **并回结构化字段**，并把"原生真迹"
记在动态属性上（`_stage_native_show/hide/scene`）。它出错的形态是**静默**的：
某一类 op 没并回去 → 渲染少一行；并重了 → 产物多一行。

金标 = **整批拍的可序列化快照**：`to_row()`（所有列）+ 那几个动态属性 + 舞台字段。
真机基线（2026-10-09）：53 文件 / **1795 拍**。

⚠ 真机项目不在时 skip；渲染金标（`test_render_golden.py`）是下游那一层，
本判据管的是"并回来的东西对不对"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
GOLDEN = ROOT / 'tests' / 'golden_hydrate_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_hydrate_result_matches_the_golden():
    import probe_hydrate_fingerprint as probe

    lines = GOLDEN.read_text(encoding='utf-8').split()
    want = lines[0] if lines else ''
    assert want, '金标文件是空的'
    fp, _text, _per_file, per_hash = probe.fingerprint(REAL)
    if fp != want:
        want_files = dict(zip(lines[1::2], lines[2::2]))
        changed = sorted(k for k, v in per_hash.items()
                         if want_files.get(k) != v)
        assert False, ('hydrate 的结果变了（你在做纯重构：这就是回归）—— '
                       f'这些文件：{changed}\n（`_hydrate_stage` 少并/多并一类 op，'
                       '渲染会跟着静默变）')


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_hydrate_fingerprint_is_deterministic():
    import probe_hydrate_fingerprint as probe

    assert probe.fingerprint(REAL)[0] == probe.fingerprint(REAL)[0]
