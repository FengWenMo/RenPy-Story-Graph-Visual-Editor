"""
storygraph.parsing.discover
===========================
**发现项目里该被构建的 .rpy**（章节脚本清单）。

为什么它住在这里（2026-09-28 下移）
------------------------------------
它原先写在 `pipeline/build.py` 里，但**生成门禁**（`story/generation_guard.py`
的 `occupied_labels`，用来判"生成的 label 是否与项目其他 .rpy 重名"）也需要
它——于是 story 层反过来 import pipeline 层，形成 `story ↔ pipeline` 的环，
只能靠函数内延迟 import 压着。

把"扫出 .rpy 清单"这件事下移到 parsing 层后，依赖方向恢复单向：
`parsing ← story ← pipeline`。`pipeline/build.py` 仍然 re-export 它，
既有调用方（`cli.py` / `story` / 测试）无需改动。
"""
from __future__ import annotations

from pathlib import Path

#: 这些目录名出现在路径里就跳过：翻译产物、引擎自带目录、缓存。
SKIP_DIR_PARTS = frozenset({
    'tl', 'common', 'renpy', 'cache', 'saves', '__pycache__',
})

#: 本工具自己生成的产物后缀：不能被当成"项目原文"扫进来（否则自我循环）。
GENERATED_SUFFIX = '.generated.rpy'


def discover_scripts(project_root, extra_dirs=None) -> list:
    """
    找出需要构建的章节脚本（返回 Path 列表，已排序、稳定）。

    扫描范围：有 `game/` 就只扫它，否则扫项目根；extra_dirs 追加。
    排除：`tl/`（翻译）、`common/` `renpy/` `cache/` `saves/` `__pycache__/`
    （引擎自带或缓存）、以及 `*.generated.rpy`（本工具的产物）。
    """
    project_root = Path(project_root)
    game = project_root / 'game'
    roots = [game] if game.exists() else [project_root]
    for d in (extra_dirs or []):
        roots.append(Path(d))

    found = []
    for root in roots:
        if not root.exists():
            continue
        for fp in sorted(root.rglob('*.rpy')):
            rel_parts = set(fp.relative_to(root).parts)
            if rel_parts & SKIP_DIR_PARTS:
                continue
            if fp.name.endswith(GENERATED_SUFFIX):
                continue
            found.append(fp)
    return found


__all__ = ['discover_scripts', 'SKIP_DIR_PARTS', 'GENERATED_SUFFIX']
