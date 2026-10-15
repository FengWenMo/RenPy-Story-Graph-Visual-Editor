"""
金标基线：**"指纹变了"先分清楚是项目变了还是工具变了**
================================================================================

背景（2026-10-15 用户审核提的 P1）：8 条金标全钉在**活的真机项目**上，
于是"工具变了"与"项目变了"在指纹里**长得一模一样** —— 每次红了都要人肉二分，
而且真机上只要有一个既有的反例（`mini_game_flipcard.rpy` 不幂等），
所有金标的说服力都跟着被削一刀。

这一份把**项目侧的输入摘要**（`.rpy` 正文 / 资源清单 / 资源表）记在
`tests/golden_baseline.json`，并在这里断言它和当前项目**对得上**：

  · 这个断言红 ⇒ **项目内容变了**（不是工具回归）→ 逐条确认金标新输出无误后，
    跑 `python tools/golden_baseline.py --save` 重生基线、再刷新金标；
  · 这个断言绿而金标红 ⇒ **项目侧没变**，那就是**工具侧**变化 = 真回归，去查代码。

⚠ 为什么不用"冻结的项目切片"当基线：`scan` / `engine_facts` 那两条金标依赖
**真实的资源文件树**（真机 `game/images/**` 是 8GB 量级），切片冻不住。
所以这里只记摘要、不做切片。
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
BASELINE = ROOT / 'tests' / 'golden_baseline.json'

pytestmark = pytest.mark.skipif(not REAL.is_dir(),
                                reason='真机项目不在本机（换机）')


def test_金标基线与当前项目对得上():
    import golden_baseline as GB

    assert BASELINE.is_file(), (
        '没有基线文件：跑 `python tools/golden_baseline.py --save` 记一份')
    old = json.loads(BASELINE.read_text(encoding='utf-8'))
    cur = GB.snapshot(REAL)
    diff = GB.compare(old, cur)
    assert not diff, (
        '**项目内容变了**（不是工具回归）—— 逐条确认金标新输出是对的之后，\n'
        '跑 `python tools/golden_baseline.py --save` 重生基线、再刷新金标。\n'
        '差异（旧 → 新）：\n'
        + '\n'.join(f'  {k}: {o} → {n}' for k, o, n in diff))
    # 顺带钉住"摘要对同一个项目是确定的"（否则上面那条永远在抖）
    assert GB.snapshot(REAL)['rpy_sha1'] == cur['rpy_sha1']


def test_摘要只描述项目_不含工具代码():
    """⚠ 摘要里不许出现工具仓的东西 —— 否则"项目侧变了"就失去意义。"""
    import golden_baseline as GB

    snap = GB.snapshot(REAL)
    assert set(snap) >= {'rpy_files', 'rpy_sha1', 'assets', 'assets_sha1',
                         'tables', 'tables_sha1', 'git_rev', 'git_dirty'}
    assert str(ROOT) not in json.dumps(snap, ensure_ascii=False), (
        '摘要里出现了工具仓路径 —— 它必须只描述项目')
