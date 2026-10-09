"""
金标守卫 · 解析结果指纹（M4 拆 `renpy_parser._walk` 的护栏）
============================================================

`_walk` 是"源码 → `ParseResult`"的分发中枢（角色/台词/label/transform/image/
菜单/label 集合/翻译单元…）。拆它时漏掉任何一类，**下游全都会少东西**，
而且多半是**静默**的 —— 所以金标覆盖**整份 ParseResult**：

  · 真机 53 个 `.rpy`；通用递归归一化（dataclass→dict、dict/set 排序、Path 相对化）
    后逐文件 sha256 → `tests/golden_parse_fingerprint.txt`。
  · 报差异时指出"**解析结果变了：xxx.rpy**"。

⚠ 真机项目不在时 skip；`tests/test_cst_hell.py` 等是同一件事的单元级入口。
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
GOLDEN = ROOT / 'tests' / 'golden_parse_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_parse_result_fingerprint_matches_the_golden():
    import probe_parse_fingerprint as probe

    lines = GOLDEN.read_text(encoding='utf-8').split()
    want = lines[0] if lines else ''
    assert want, '金标文件是空的'
    fp, per_file = probe.fingerprint(REAL)
    if fp != want:
        want_files = dict(zip(lines[1::2], lines[2::2]))
        changed = sorted(k for k, v in per_file.items()
                         if want_files.get(k) != v)
        assert False, ('解析结果变了（你在做纯重构：这就是回归）—— 这些文件：'
                       f'{changed}\n（`_walk` 少收/多收一类，下游会静默跟着变）')


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_parse_fingerprint_is_deterministic():
    import probe_parse_fingerprint as probe

    assert probe.fingerprint(REAL)[0] == probe.fingerprint(REAL)[0]


def test_norm_handles_dataclasses_dicts_and_sets():
    """归一化本身要稳：dataclass / dict 顺序 / set 顺序都不该影响指纹。"""
    import probe_parse_fingerprint as probe

    from storygraph.parsing.renpy_parser import ParseResult

    a = ParseResult()
    a.menu_labels = {'b', 'a'}
    b = ParseResult()
    b.menu_labels = {'a', 'b'}
    assert json.dumps(probe.norm(a), sort_keys=True) == \
        json.dumps(probe.norm(b), sort_keys=True)
