"""
金标守卫 · 渲染产物指纹（M4 拆 `render_beat_to_rpy` 的护栏）
=============================================================

渲染是**真源**：拆它时改动一个字节就是改剧情。所以先把真机 53 个 `.rpy` 的
渲染产物指纹录下来（`tests/golden_render_fingerprint.txt`），拆的过程中每步都跑。

指纹 = 每个文件 `render_beats_to_rpy(beats_from_parse_result(pr))` 的文本按
**文件名**喂 sha256，再整体汇总。与 `tools/audit_idempotence.py` 用**同一个调用**。

⚠ 真机项目不在（换机）时 **skip** —— 但**其他判据照跑**：
`tools/audit_idempotence.py` 与 `tests/test_fidelity_bytewise.py` 是同一件事的
另外两个入口，金标只多一层"逐文件定位"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
GOLDEN = ROOT / 'tests' / 'golden_render_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_render_fingerprint_matches_the_golden():
    import probe_render_fingerprint as probe

    want = GOLDEN.read_text(encoding='utf-8').split()
    assert want, '金标文件是空的'
    fp, per_file, _count = probe.fingerprint(REAL)
    if fp != want[0]:
        want_files = dict(zip(want[1::2], want[2::2]))
        changed = sorted(k for k, v in per_file.items()
                         if want_files.get(k) != v)
        assert False, (
            '渲染产物变了 —— 这些文件的输出与金标不一致：'
            f'{changed}\n（你在拆 `render_beat_to_rpy`：这就是回归）')
