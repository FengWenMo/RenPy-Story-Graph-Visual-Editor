"""
缩略图路径解析（2026-11-01 从 `story/excel_export.py` 搬出）
================================================================

`resolve_thumb_source` 回答的是"**资源表里写的 Path，磁盘上到底是哪个文件**"，
不是"怎么导出 excel"。它原先住在 `story/excel_export.py` 里，于是 `assets` 包
（`asset_registry` / `asset_registry_scan`）为了对账要先
`import story.excel_export` —— `assets` 是被 `story` 依赖的底座，反过来 import
上层，就是 29 模块环里的**两条反馈边**（2 + 4 处使用）。

搬到这里之后方向理顺：

    assets.asset_registry ──→ assets.thumb_paths        （同层，向上没有）
    story.excel_export    ──→ assets.thumb_paths        （上层用下层）
    app.thumbs / ui_qt    ──→ story.excel_export        （经 re-export，旧路不变）

⚠ 只搬不改语义、逐字照搬；`story.excel_export` 继续 re-export
`resolve_thumb_source` / `_resolve_thumb_source`。
"""

from __future__ import annotations

from pathlib import Path

#: 支持的扩展名：webp/动图之类 Pillow 不一定能读，读不了就跳过。
#: **公开名**（没有下划线）：门面 `story/script_table.py` 要转发它，
#: 而 `tests/test_package_layering.py` 明令禁止跨包取私有名。
THUMB_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')


def resolve_thumb_source(path, project_root=None):
    """
    资源表里的 Path → 磁盘上真实存在的图。

    路径基准不统一（`sr_general/game/images/x.png` / `game/images/x.png` /
    `images/x.png`），所以逐个基准试；都找不到就放弃（不猜）。
    """
    text = str(path or '').strip().replace('\\', '/')
    if not text or not text.lower().endswith(THUMB_EXTS):
        return None
    p = Path(text)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        # 资源表 Path 的基准五花八门：`sr_general/game/images/x.png`、
        # `game/images/x.png`、`images/x.png`、`x.png` 都出现过。逐个试，
        # 以**文件真的存在**为准——这是唯一可靠的判据。
        parts = [q for q in text.split('/') if q not in ('', '.')]
        bases = [Path.cwd()]
        if project_root:
            root = Path(project_root)
            bases = [root, root.parent, root / 'game'] + bases
        for base in bases:
            candidates.append(base / text)
            if len(parts) > 1:
                candidates.append(base / '/'.join(parts[1:]))
                candidates.append(base / 'game' / '/'.join(parts[1:]))
            candidates.append(base / 'game' / text)
    seen = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None
