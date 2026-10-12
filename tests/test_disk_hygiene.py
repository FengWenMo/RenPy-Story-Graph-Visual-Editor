"""
**磁盘卫生守卫**：探针/测试拷了真项目，跑完必须删（2026-10-11 血账）
=========================================================================

## 为什么有这条

本仓出过**两次**同类事故：

  ① 早先：用 `shutil.copytree` 整份拷 8GB 的 `sr_general`（还拷了五次），
     把 C 盘写满到 **0 字节**（`WinError 112`）；
  ② 2026-10-11：`tools/probe_real_outfit_groups.py` 漏了清理，
     `%TEMP%` 里堆了 6 份 3.6GB 的 `sr_general` ⇒ 一共 **46.9GB**
     （清完之后 C 盘从 41.9GB 回到 **88.9GB** 可用）。

两次都不是"工具坏了"，而是**没有一条判据盯着"跑完要收摊"**。
这份就盯它：静态扫 `tools/*.py`，凡是**建了临时目录**的，
必须有对应的删除动作。

## 判据（保守，只报有把握的）

  · 出现 `mkdtemp(` → 这个脚本必须在同一文件里出现
    `rmtree(` / `TemporaryDirectory(` / 或显式 `unlink` 之一；
  · ⚠ 只查工具目录里的**探针/脚本**（`tests/` 由 pytest 的 basetemp 机制兜着，
    而且那里用 `tmp_path` 是标准做法、由 pytest 自己清）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'

#: 建临时目录的写法
_CREATES = re.compile(r'mkdtemp\(|TemporaryDirectory\(')

#: 收摊的写法（有其一即可）
_CLEANS = re.compile(r'rmtree\(|TemporaryDirectory\(|\.unlink\(|addfinalizer\(')

#: ⚠ **真正的危险信号**：拷**真项目**（几 GB 一份）。
#: 判据只盯这个 —— 只建了个小临时目录的老探针有几十个，全报进来就是噪声，
#: 而噪声会让人无视整条守卫（本仓的既有教训）。
_BIG_COPY = re.compile(r'copytree\(', re.M)

#: 真项目的标志（提到它 + 拷 + 不收摊 = 会吃掉几十 GB）
_REAL_PROJECT = re.compile(r'sr_general|RenpyTest|_copy_for_ask')


def leaking_scripts(tools_dir: Path = None) -> list:
    """
    **会留下大拷贝**的脚本 → `[(文件名, 建了几处临时目录)]`。

    判据（三条同时满足才算危险）：
      ① 建了临时目录；② 里面有 `copytree(`；③ 提到真项目（或本仓的轻量拷贝工具）；
      ④ 且**没有任何**删除动作。
    ⚠ 只建小临时目录的老探针**不报**（否则满屏噪声，守卫就废了）。
    """
    tools_dir = Path(tools_dir) if tools_dir else TOOLS
    out = []
    for fp in sorted(tools_dir.glob('*.py')):
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        made = len(_CREATES.findall(text))
        if not made or not _BIG_COPY.search(text) \
                or not _REAL_PROJECT.search(text):
            continue
        if not _CLEANS.search(text):
            out.append((fp.name, made))
    return out


def test_no_probe_leaks_its_temp_copy():
    """
    ⭐ 核心判据：探针建了临时目录就必须收摊 —— 否则 3.6GB × N 堆在 TEMP 里，
    下一次"磁盘写满"就是这么来的。
    """
    leaks = leaking_scripts()
    assert not leaks, ('这些脚本会留下临时拷贝（真项目 3.6GB/份）：'
                       + '；'.join(f'{n}（{c} 处 mkdtemp）' for n, c in leaks)
                       + ' —— 加一行 shutil.rmtree(..., ignore_errors=True)')


def test_the_guard_itself_works(tmp_path):
    """守卫自己也要有判据（不然它坏了没人知道）：
    ① **拷真项目又不收摊** → 报；② 只建小临时目录 → **不报**（别造噪声）；
    ③ 拷了但收摊了 → 不报；④ 用 `TemporaryDirectory` → 不报。"""
    (tmp_path / 'leaky.py').write_text(
        'import shutil, tempfile\n'
        'REAL = "D:/千连万理/RenpyTest/sr_general"\n'
        'p = tempfile.mkdtemp(prefix="x_")\n'
        'shutil.copytree(REAL, p)\n', encoding='utf-8')
    (tmp_path / 'small.py').write_text(
        'import tempfile\np = tempfile.mkdtemp(prefix="y_")\n'
        'open(p + "/a.txt", "w")\n', encoding='utf-8')
    (tmp_path / 'clean.py').write_text(
        'import shutil, tempfile\n'
        'REAL = "sr_general"\np = tempfile.mkdtemp()\n'
        'shutil.copytree(REAL, p)\n'
        'shutil.rmtree(p, ignore_errors=True)\n', encoding='utf-8')
    (tmp_path / 'ctx.py').write_text(
        'import shutil, tempfile\n'
        'with tempfile.TemporaryDirectory() as d:\n'
        '    shutil.copytree("sr_general", d)\n', encoding='utf-8')
    got = {n for n, _c in leaking_scripts(tmp_path)}
    assert got == {'leaky.py'}, got


def test_pytest_basetemps_do_not_accumulate():
    """
    跑完测试后 `.pip-tmp/` 里不该留下**一大堆** basetemp（每份 ~18MB）。
    留几条最近的可以（方便看失败现场），但**不许无限涨**。
    """
    base = ROOT / '.pip-tmp'
    if not base.is_dir():
        return
    dirs = [d for d in base.iterdir() if d.is_dir()]
    total_mb = sum(
        sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
        for d in dirs) / (1024 * 1024)

    # 阈值给得宽（清一次就够）：主要防"几百份堆着"那种
    assert total_mb < 3000, (f'.pip-tmp 下已堆 {total_mb:.0f}MB 临时产物 —— '
                             f'清一下（{len(dirs)} 个目录）')
