"""
storygraph.ui_qt.wrap_label
===========================
**最小宽只留 24px 的换行标签**（2026-10-30，用户第 5 条「右栏不许出横向滚动条」）。

为什么需要：`QLabel(wordWrap=True)` 的最小宽 = **最长的那个词**。卡片上
「当前舞台」摘要里 `base_arms_akimbo` / 长的 bgm id 这类 token 实测能把那一行
顶到 240+150+8 ≈ 398px，整张卡片地板随之 422px —— 右栏 340 放不下，横向滚动条
又出现（`tests/test_ui_width.py` 守着）。

这些文字本来就是"折行显示的多行说明"：压窄 = **行数变多**，一个字都不会少
（与"用 AlwaysOff 把滚动条关掉、内容被静默裁掉"是两回事）。

2026-10-31：从 `main_window.py` 搬出来成独立模块 —— 尺寸面板（`size_panel.py`）
也要用同一个类，而 `main_window` 反过来要 import 尺寸面板（**环**）。搬出来之后
两边都 import 这里，`main_window.NarrowWrapLabel` 这个名字也还在（历史引用不用改）。
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel


class NarrowWrapLabel(QLabel):
    """换行标签，**最小宽只留 24px**：长单词不许把整张卡片顶宽。"""

    NARROW_W = 24

    def minimumSizeHint(self) -> QSize:           # noqa: N802 - Qt 接口
        base = super().minimumSizeHint()
        return QSize(min(int(base.width()), self.NARROW_W), base.height())


__all__ = ['NarrowWrapLabel']
