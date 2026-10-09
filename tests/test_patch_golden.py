"""
金标守卫 · 增量补丁产物指纹（M4 拆 `patcher.py` 的护栏）
======================================================

补丁是**写用户 `.rpy`** 的那条路，也是全仓最贵的一类缺陷的来源（"半对"、
静默丢第三类内容）。所以金标覆盖**三条路径**，逐文件记账：

  ① `direct`    ：拍 → `render_beats_to_rpy` → `merge_incrementally(source_path=…)`
  ② `via_excel` ：拍 → 过表 → `compile_beats` → 增量   ← 生产主路径
  ③ `edited`    ：② 之后再**改几条拍**（每 7 条改一次文案）→ 增量 ← 压配对逻辑

指纹 = 每文件 × 每路径的 `(used_patch, 产物 sha256, 计数, reasons)`。
真机基线（2026-10-09）：53 文件、三条路径合计 **used_patch 66** 次。

⚠ 真机项目不在时 skip；`tools/hell_edit_rounds.py` 是同一件事的另一个入口
（它跑"安全拒绝/退回全量"那一类终态），本金标管的是**逐条产物**。
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
GOLDEN = ROOT / 'tests' / 'golden_patch_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_patch_result_matches_the_golden():
    import probe_patch_fingerprint as probe

    body = GOLDEN.read_text(encoding='utf-8').split('\n', 1)
    want = body[0].strip()
    assert want, '金标文件是空的'
    fp, _text, per_file = probe.fingerprint(REAL)
    if fp != want:
        old = json.loads(body[1]) if len(body) > 1 else {}
        lines = []
        for name in sorted(set(old) | set(per_file)):
            a, b = old.get(name) or {}, per_file.get(name) or {}
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    lines.append(f'  {name} [{k}]')
                    lines.append(f'    旧: {a.get(k)}')
                    lines.append(f'    新: {b.get(k)}')
        assert False, ('增量补丁的产物变了（你在做纯重构：这就是回归）\n'
                       + '\n'.join(lines[:40]))


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_patch_fingerprint_is_deterministic():
    """指纹必须可重复 —— 否则上面那条会随机红绿。"""
    import probe_patch_fingerprint as probe

    a = probe.fingerprint(REAL)[0]
    b = probe.fingerprint(REAL)[0]
    assert a == b
