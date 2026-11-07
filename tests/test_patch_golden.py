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

**重录记录**

- 2026-10-24（第二轮 P0）：唯一变化 `script_pomo.rpy [edited]`。
  旧产物里 `jc "…X" (no_skip=True)` 与 `jc "…" (no_skip=True)` **两行都在**
  （带台词后缀的行在补丁里认不出自己是台词 → 新行被插、旧行留下）；
  新产物只剩改过的那一行。这是修 P0-8「SayTail 配不上对」的**正向**变化，
  不是回归 —— 金标按新产物重录。

- 2026-11-02（保真审计）：变化**只落在 5 个文件**上 —— `chapter3_3_3` /
  `chapter3_4_2` / `chapter3_4_3` / `chapter3_4_4` / `chapter4_1_2`，
  正是含 `U+0005` 或真制表符的那 5 章。两类都是修保真带来的**正向**变化：

  1. `U+0005` 不再被 `xml_safe` 剥掉，改成 `\\xNN` 转义往返（写表时转义、
     读表时还原）→ 产物里那个不可见字符回来了；
  2. `atl._strip_comment` 认 `\\` 转义 + `patcher._escaped_same`
     让"真 tab 与 `\\t`"判成同一行 → 产物保留作者的真制表符。

  核对过：`used_patch` 次数没变（285），`reasons` 全空，三个计数
  （`changed_lines` / `stale` / `unlanded`）没动。**只有 `text` 指纹变**。

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
