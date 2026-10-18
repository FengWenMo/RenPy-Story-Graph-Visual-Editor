"""
金标守卫 · 校验问题清单指纹（M4 拆 `validate_level1` 的护栏）
=============================================================

校验产出是"给人看的结论"：拆它时**多报一条假警、少报一条真错**都算行为变了
（而且少报一条错误很容易**没人发现**）。所以金标覆盖两种输入下的**完整问题清单**：

  · 带资源表（真机 `game/asset_tables`）—— 走完整分支；
  · 不带资源表 —— 走**降级路径**（是另一批分支，必须一起钉住）。

真机基线（2026-10-09）：1795 拍；带表 198 条（E103 16 / W111 149 / W112 2 / W113 31）、
不带表 31 条（W113）。

2026-10-18 重录：**1823 拍**（`show …:` 的 ATL 块不再被并进舞台拍，而是整块
逐字保真 → 多 28 张携带拍）。问题清单只变了一条：`splashscreen` 里
`image 'white' 未登记` 的 where 由 `.0002` 变成 `.0003`（多出的那张拍在它前面），
条数与 code 分布都没动 —— 这正是"结构位变了、结论没变"。

⚠ 真机项目不在时 skip；`tests/test_cell_control_chars.py` 等是同一件事的单元级入口。
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
GOLDEN = ROOT / 'tests' / 'golden_validate_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_validate_level1_report_matches_the_golden():
    import probe_validate_fingerprint as probe

    body = GOLDEN.read_text(encoding='utf-8').split('\n', 1)
    want = body[0].strip()
    assert want, '金标文件是空的'
    fp, text, _counts, _n = probe.fingerprint(REAL)
    if fp != want:
        old = json.loads(body[1]) if len(body) > 1 else {}
        new = json.loads(text)
        lines = []
        for tag in sorted(set(old) | set(new)):
            a, b = old.get(tag) or [], new.get(tag) or []
            if a != b:
                lines.append(f'  {tag}: {len(a)} -> {len(b)}')
                for x in sorted(set(a) - set(b))[:5]:
                    lines.append(f'    少了: {x[:110]}')
                for x in sorted(set(b) - set(a))[:5]:
                    lines.append(f'    多了: {x[:110]}')
        assert False, ('校验报告变了（你在做纯重构：这就是回归）\n'
                       + '\n'.join(lines))


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_validate_fingerprint_is_deterministic():
    import probe_validate_fingerprint as probe

    assert probe.fingerprint(REAL)[0] == probe.fingerprint(REAL)[0]
