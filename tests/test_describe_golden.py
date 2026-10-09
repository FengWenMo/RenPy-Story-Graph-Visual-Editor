"""
金标守卫 · `describe_statements` 结果指纹（M4 拆它的护栏）
==========================================================

它是"IDE 提示 / 文档 / 能力覆盖"那一面的数据源：**少报一条声明、把 builtin 记成
未知**，都不会让人当场看见。所以金标覆盖**四种调用**：

  · 真机（`cli` 那条 / `build` 那条带 statements）：8 个头 / 7 builtin / 1 shape-guess；
  · **合成项目**（`synthetic` / `synthetic_build`）：**有声明** —— 才压得到
    declared / builtin / shape-guess 三条路（真机一个自定义声明都没有）。

真机 + 合成，两条路都跑，`project` 字段归一化后算 sha256。
`tests/golden_describe_fingerprint.txt`（`tests/test_describe.py` 是单元级入口）。

⚠ 真机项目不在时 skip。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
GOLDEN = ROOT / 'tests' / 'golden_describe_fingerprint.txt'


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_describe_result_matches_the_golden():
    import probe_describe_fingerprint as probe

    want = GOLDEN.read_text(encoding='utf-8').split('\n', 1)[0].strip()
    assert want, '金标文件是空的'
    fp, _text, cases = probe.fingerprint(REAL)
    if fp != want:
        import json
        import difflib
        old = json.loads(
            GOLDEN.read_text(encoding='utf-8').split('\n', 1)[1] or '{}')
        diff = list(difflib.unified_diff(
            json.dumps(old, ensure_ascii=False, sort_keys=True,
                       indent=1).splitlines(),
            json.dumps(cases, ensure_ascii=False, sort_keys=True,
                       indent=1).splitlines(),
            lineterm='', n=0))
        assert False, ('describe 的结果变了（你在做纯重构：这就是回归）\n'
                       + '\n'.join(x[:160] for x in diff[:30]))


@pytest.mark.skipif(not REAL.is_dir(), reason='真机项目不在本机（换机）')
def test_describe_fingerprint_is_deterministic():
    import probe_describe_fingerprint as probe

    assert probe.fingerprint(REAL)[0] == probe.fingerprint(REAL)[0]
