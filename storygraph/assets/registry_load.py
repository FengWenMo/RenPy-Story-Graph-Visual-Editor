"""
读资源表（`asset_tables/*.xlsx`）—— **只找表、只解析，不碰构建**。

为什么它不在 `pipeline/build.py`（2026-11-01 拆环）：这个函数的名字里就写着
**asset** registry，做的事全是"找目录 + 解析表"，跟"构建流水线"没有关系。
它住在 `pipeline` 里，于是 `story.statement_registry`（要知道项目有哪些角色）
只能往**上层** import —— 那是一条反向边，也是 `pipeline` ↔ `story` 那个大环
上的一条环比。搬到 `assets/`（下层的底层数据）之后，两边都往下看。

⚠ `pipeline.build` 继续 re-export：那是既有调用方（cli / main_qt / authoring /
`session_assets` / 二十多个测试）一直用的入口。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow


def load_asset_registry(project_root, asset_dir=None, walk=None):
    """读资源表（不存在就返回 None，校验会相应降级为 L1/L2 为主）。"""
    project_root = Path(project_root)
    candidates = []
    if asset_dir:
        candidates.append(Path(asset_dir))
    candidates += [project_root / 'asset_tables',
                   project_root / 'game' / 'asset_tables']
    # ⚠ 只认**真表**：`*.bak.xlsx`（我们自己的备份）、`~$*.xlsx`（Excel 锁文件）
    # 和 `*.broken-*.xlsx`（读不出来时挪到一边的坏文件）都不算"有表" ——
    # 否则一个只放着备份的目录会让这里返回一张**空表**，于是永远不去建表、
    # 打开项目看到 0 个资源（2026-10-05 实测）。
    try:
        from storygraph.assets.asset_registry import is_real_table_file
    except Exception as e:                      # noqa: BLE001 - 极端情况退回宽判
        swallow('assets.registry_load.load_asset_registry.import', e)

        def is_real_table_file(p):
            return str(p).lower().endswith('.xlsx')
    for c in candidates:
        try:
            if c.exists() and any(is_real_table_file(p)
                                  for p in c.glob('*.xlsx')):
                from storygraph.assets.asset_registry import (load_hooks,
                                                              parse_asset_tables)
                try:
                    return parse_asset_tables(
                        c, hooks=load_hooks(project_root=project_root),
                        project_root=project_root, walk=walk)
                except Exception as e:
                    swallow('assets.registry_load.load_asset_registry', e)
                    return None
        except OSError as e:                    # noqa: BLE001
            swallow('assets.registry_load.load_asset_registry', e)
            continue
    return None


__all__ = ['load_asset_registry']
