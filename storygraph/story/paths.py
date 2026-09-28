"""
storygraph.story.paths
======================
**项目路径判定**：产物会不会落进 Ren'Py 的扫描范围里。

`inside_game_dir` 同时服务两条规则，所以必须是**一处实现**：
  · 生成门禁的 `LABEL_COLLISION`（block）—— 会被扫描才怕抢 label；
  · `OUTSIDE_BUILD_DIR`（warn）—— 落在 game/ 里就提醒挪到 build/。
"""

from __future__ import annotations
from pathlib import Path



def inside_game_dir(out_path, project_root=None) -> bool:
    """
    产物是否会落进 `<project_root>/game/`（= 会被 Ren'Py 扫描）。

    为什么这是**一个**判定：它同时决定两件事，不该各写一份——
      · `LABEL_COLLISION`（block）：只有会被扫描的产物才怕"和原文抢 label"；
        写到 `build/renpy/` 或临时目录的产物根本不被 Ren'Py 读，与原文
        同名无害。判它 = 把正常构建拦下来（真实发生的误报）。
      · `OUTSIDE_BUILD_DIR`（warn）：落在 game/ 里就提醒挪到 build/。

    Ren'Py 把 game/ 下所有 .rpy 当**同一个脚本**（官方 Quickstart 原话），
    这就是"在不在 game/ 里"成为分水岭的原因。
    """
    if not project_root or out_path is None:
        return False
    try:
        game_dir = (Path(project_root) / 'game').resolve()
        resolved = Path(out_path).resolve()
    except (OSError, TypeError):
        return False
    if not game_dir.is_dir():
        return False
    return resolved == game_dir or game_dir in resolved.parents



def _guess_project_root(out_path=None):
    """
    从一个**产物路径**反推项目根（含 `game/` 的那一级）。

    两处用途都要求它真的推得出来：
      · 缩略图定位（`export_beats_to_excel` 里给资源图库用）；
      · **生成门禁的 label 重名检查**（`write_beats_to_rpy` → `occupied_labels`）。

    2026-09-28 修：这个函数以前**恒返回 None**，于是 `occupied_labels(...)`
    永远拿到空集 —— `LABEL_COLLISION` 这条 block 级门禁**形同虚设**。
    它要拦的是"生成的 label 与项目其他 .rpy 重名"：Ren'Py 把 game/ 下
    所有 .rpy 当同一个脚本，重名 label 会让游戏直接起不来（真实事故形态）。

    推不出来（临时目录、产物不在任何含 game/ 的项目里）就返回 None：
    缩略图退回"多基准尝试"，门禁退回"不检查"——两者都安全，宁可少报。
    """
    if not out_path:
        return None
    try:
        path = Path(out_path).resolve()
    except (OSError, TypeError):
        return None
    for parent in path.parents:
        try:
            if (parent / 'game').is_dir():
                return parent
        except OSError:
            continue
    return None
