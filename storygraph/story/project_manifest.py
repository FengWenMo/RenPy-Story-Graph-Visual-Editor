"""
项目清单（`storygraph.rules.json`）的**最底层读取**：在哪、读出来、读坏了怎么办。

为什么单独一个模块（2026-11-01，拆环）：清单是**好几层都要读**的东西 ——
`project_conventions`（识别约定）、`statement_registry`（声明过的语句）、
`host_hooks`（信任边界）。原来它住在 `project_conventions` 里，于是
`host_hooks → project_conventions` 与 `project_conventions → host_hooks`
互引成环、`statement_registry → project_conventions` 也是一条环比。
读取本身只依赖 `json` + `pathlib`，**下沉成叶子模块**两头都往下看，环就断了。

⚠ `project_conventions` 继续 re-export `find_manifest` / `load_manifest` /
`MANIFEST_NAMES`：那是既有调用方（`mcp_surface` / `dialog_learn` / 测试）
一直用的入口，搬家的目的是**解环**，不是改调用面。
"""

from __future__ import annotations

import json
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 清单文件名（两处都找：项目根、`game/` 下）
MANIFEST_NAMES = ('storygraph.rules.json',)


def find_manifest(project_root) -> object:
    """项目根 / `game/` 下的 `storygraph.rules.json`（没有返回 None）。"""
    root = Path(project_root)
    for base in (root, root / 'game'):
        for name in MANIFEST_NAMES:
            cand = base / name
            try:
                if cand.is_file():
                    return cand
            except OSError as e:                # noqa: BLE001
                swallow('story.project_manifest.find_manifest', e)
    return None


def load_manifest(project_root) -> tuple:
    """
    读清单 → `(data, path, warnings)`。

    坏 JSON **不抛异常**（工具不该因为一个手写错的文件就打不开项目），
    而是记一条警告并当作"没有清单"。
    """
    path = find_manifest(project_root)
    warnings = []
    if path is None:
        return {}, '', warnings
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('顶层必须是对象')
        return data, str(path), warnings
    except Exception as e:                      # noqa: BLE001 - 手写文件容错
        swallow('story.project_manifest.load_manifest', e)
        warnings.append(f'{path.name} 读不出来（{e}）—— 已忽略，'
                        f'按自动识别走')
        return {}, str(path), warnings


__all__ = ['MANIFEST_NAMES', 'find_manifest', 'load_manifest']
