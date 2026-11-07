"""
金标守卫 · `sync_asset_tables` 的行为指纹（M4 重构的护栏）
==========================================================

M4 要把 `sync_asset_tables`（圈复杂 **133**、339 行）拆成阶段函数。这类改动
**只许动结构、不许动行为** —— 所以先做一条"同输入 → 同输出"的金标：

  · 合成一个确定性项目，把四个现场一次摆进去：
    ❶ 文件没了 → 删行（占位行除外）  ❷ 指向嵌套副本 → 改指
    ❸ 改指后同文件重复 → 合并          ❹ 未登记 → 补登（+ 记侧车）
  · 跑 `sync_asset_tables`，把**所有资源表逐格** + **返回的 dict** 归一化后
    （临时路径 → `<BASE>`、set repr 排序）算 sha256。

`tests/golden_sync_fingerprint.txt` 是重构**之前**录下来的指纹；指纹变了就说明
行为变了 —— 要么是 bug，要么是有意改动（那就要连带更新金标并在提交信息里说清）。

⚠ 归一化不是可选项：路径、set repr 的顺序都会让指纹随机漂移（都实测踩过）。

## 金标为什么不等于"从前那一份"

2026-10-25（《不安全因素》第二轮 P1-6）`sync_asset_tables` 的**返回体多两个键**：
`unjudged` / `unjudged_by_kind`（"walk 扫不到、又不该删"的行数）。这是**有意**
的行为改动，所以金标跟着更新；更新前逐条核过：把这两个键摘掉算出的指纹与旧金标
**一字不差**（`94579ad7…`），表内容与 `removed` 计数都没变 —— 也就是说变的只是
"多了两个统计字段"，不是"删错了行"。

2026-11-07 `sync_asset_tables` 的返回体**又多一个 `error`**（"这次没敢动手"的原因；
`_path_column_guard` 用它把"表头被删、于是没有一行有真实文件"说出来，不再静默
`ok=True`）。同一条规矩核过：把 `error` 键摘掉算出的指纹与旧金标
**一字不差**（`c325188f…`），所以金标更新成 `a52368bd…` —— 变的只是这个字段，
表内容 / added / removed / relinked / deduped 全部没动。
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import probe_sync_fingerprint as probe                    # noqa: E402

from storygraph.assets.asset_registry import (             # noqa: E402
    FileWalkIndex, sync_asset_tables)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'tests' / 'golden_sync_fingerprint.txt'


def _fingerprint() -> str:
    base = Path(tempfile.mkdtemp(prefix='sync_golden_'))
    try:
        root = probe.build_project(base)
        res = sync_asset_tables(root, walk=FileWalkIndex(root))
        tf = probe.table_fingerprint(root / 'game' / 'asset_tables', base)
        rf = probe.result_fingerprint(res, base)
        return hashlib.sha256((tf + rf).encode()).hexdigest()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_sync_asset_tables_behavior_matches_the_golden():
    want = GOLDEN.read_text(encoding='utf-8').strip()
    got = _fingerprint()
    assert got == want, (
        'sync_asset_tables 的行为变了（表内容或返回值指纹不一致）。\n'
        f'  期望（金标）：{want}\n  实际        ：{got}\n'
        '若这是**有意的**行为改动：确认无误后把 '
        '`tests/golden_sync_fingerprint.txt` 更新成新指纹，并在提交信息里写清"为什么变"。\n'
        '若你在做纯重构（M4）：**这就是回归** —— 回到上一次提交对比。')


def test_fingerprint_is_deterministic():
    """指纹本身必须可重复 —— 否则上面那条判据会随机红绿（实测踩过两次）。"""
    assert _fingerprint() == _fingerprint()
