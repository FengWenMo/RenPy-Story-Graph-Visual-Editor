# -*- coding: utf-8 -*-
"""
`tools/build_fork_sdk.py` —— **一键重建 fork 模拟环境**
======================================================

为什么要有这个脚本（而不是把 SDK 拷进仓库）
--------------------------------------------
地狱测试里有一类关键用例（D4a）需要"**引擎被 fork 过**"：
引擎自己带一条语句，项目里**一个 CDS 都不写**，看工具链能不能察觉。
为了可复现，我们需要一个被改过的 Ren'Py SDK。

但**把 1.4GB 的 SDK 树提交进 git 是错的**：
  · 仓库永久背上这个体积（还会随每次 SDK 升级再翻倍）；
  · 那是**第三方软件**（有自己的 LICENSE），不该混进本项目的历史。

所以版本库里只放**"怎么把它造出来"**：
  · 这个脚本（构建 + 打补丁 + 自检）；
  · `tools/fork_sdk_patch/weather6_dispatch.py`（**补丁本身**，可 review 的一小段代码）。

一条命令重建：
    python tools/build_fork_sdk.py                 # 用默认 SDK，造到 .pip-tmp/fork_sdk_full
    python tools/build_fork_sdk.py --sdk <目录> --out <目录>

⚠ `.pip-tmp/` 在 `.gitignore` 里 —— 造出来的 SDK **不进版本库**，只在本机复跑用。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

#: 补丁片段（可 review 的那一小段代码）——单独放一个文件，改它比改本脚本安全。
PATCH_FILE = HERE / 'fork_sdk_patch' / 'weather6_dispatch.py'

#: 默认 SDK 候选（与 `pipeline.engine_collect` 保持一致）
SDK_CANDIDATES = (
    r'D:/software/renpy-8.4.1-sdk',
    r'C:/renpy',
)
DEFAULT_OUT = ROOT / '.pip-tmp' / 'fork_sdk_full'

#: `renpy/parser.py` 里的锚点：`parse_statement` 拿到 loc 之后、分派之前。
ANCHOR = "    # Store the current location.\n    loc = l.get_location()\n"


def find_sdk(explicit=None) -> Path:
    for c in (explicit, *SDK_CANDIDATES):
        if c and (Path(c) / 'renpy.py').is_file():
            return Path(c)
    raise SystemExit('找不到 Ren\'Py SDK；用 --sdk 指定')


def copy_sdk(src: Path, out: Path) -> None:
    """
    整份拷贝（**必须完整**）。

    ⚠ 踩过的坑：一开始只拷 `renpy.py` + `renpy/` + `lib/`，结果引擎在
    `bootstrap → import_all → backup()` 里崩 —— **残缺的 SDK 跑不起来**。
    """
    if out.exists():
        print(f'· 目标已存在，跳过拷贝：{out}')
        return
    print(f'· 拷贝 SDK：{src} → {out}（约 1.4G，稍等）')
    shutil.copytree(src, out)


def apply_patch(out: Path) -> str:
    """
    把补丁插进 `renpy/parser.py`。**幂等**：已经打过就跳过。

    返回一句话说明结果（给日志用）。
    """
    parser = out / 'renpy' / 'parser.py'
    if not parser.is_file():
        raise SystemExit(f'找不到 {parser} —— 这份 SDK 是不是不全？')
    text = parser.read_text(encoding='utf-8')
    snippet = PATCH_FILE.read_text(encoding='utf-8')
    body = snippet.strip()

    # 幂等**按内容判**（不按首行标记 —— 曾经因为改了标记文字而**重复插入**过一次）
    if body in text:
        return '已打过补丁（跳过）'
    if body.splitlines()[-1] in text and 'weather6' in text:
        return '⚠ 已存在功能等价的补丁（首行标记不同）—— 请人工检查，未重复插入'
    if ANCHOR not in text:
        raise SystemExit(
            f'锚点在 {parser} 里找不到：\n{ANCHOR!r}\n'
            '（SDK 版本可能变了 —— 请重新确认插入点，别硬插）')
    patched = text.replace(ANCHOR, ANCHOR + '\n' + body + '\n', 1)
    parser.write_text(patched, encoding='utf-8')
    return f'已打补丁 → {parser}'


def verify(out: Path) -> bool:
    """自检：补丁语句真的进了分派表（不跑引擎，只查源码）。"""
    parser = out / 'renpy' / 'parser.py'
    return parser.is_file() and 'weather6' in parser.read_text(encoding='utf-8')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='重建 fork 模拟 SDK（地狱测试 D4a 用）')
    ap.add_argument('--sdk', default=None, help='源 SDK 目录')
    ap.add_argument('--out', default=str(DEFAULT_OUT), help='输出目录')
    args = ap.parse_args(argv)

    src = find_sdk(args.sdk)
    out = Path(args.out)
    print(f'源 SDK：{src}\n目标  ：{out}')
    copy_sdk(src, out)
    print('·', apply_patch(out))
    ok = verify(out)
    print(('✓ fork SDK 就绪：' if ok else '✗ 补丁没生效：') + str(out))
    if ok:
        print('  复跑地狱测试：python tools/hell_engine_oracle.py D4a D4b')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
