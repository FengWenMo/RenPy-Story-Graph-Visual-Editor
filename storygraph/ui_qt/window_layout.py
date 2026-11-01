"""
storygraph.ui_qt.window_layout
==============================
**窗口尺寸与卡片页宽度预算**：六个数字，一处定义。

为什么单独一个模块（2026-11-01 解导入环）：`main_window` 顶层导入
`window_state`（拿 `WindowStateMixin` / `fit_to_available`），而
`window_state` 又要用这里几个数 —— 从前它是"函数内 import main_window"，
于是 `main_window ↔ window_state` 成一个**模块级环**。数字没有依赖，
搬到叶子模块后两边都指向这里，环就断了。

`main_window` 仍然**re-export** 这些名字（`MW.DEFAULT_WINDOW_SIZE` 之类的
老写法、`tests/test_ui_width.py` 全都是从 `main_window` 拿的，不用动）。

# 卡片页宽度预算（2026-10-30，用户反馈第 5 条「UI 宽窄可变」）
#
# 用户原话：
#   > UI 能不能把这个部分改短点，目前看根本用不到滚动条才能滚出来的部分
#
# 探针、根因排序与分阶段方案见 `.pip-tmp/plan/05_ui_width.md`。落地成四个数：
#
#   · `DEFAULT_WINDOW_SIZE` —— 默认窗口（**没有记忆时**）1200×800，
#     不再 1600×950（用户 2026-10-30 拍板：默认为窄）。**2026-10-30 晚改成 1200**：
#     用户那块屏的可用区实测只有 1440×852（`System.Windows.Forms.Screen`），
#     1360 宽 = 屏宽的 94%，他看到的还是"铺满整个屏幕"（原话："还是那么宽看不全"）。
#     1200 ≈ 83%，右边留得下明显一截；记过位置的旧窗口由 `window_state.narrow_layout`
#     一次性压到这个上限（见那里）。
#   · `CARD_COLUMN_MIN_W`  —— 卡片页右栏（表单）的**硬下限**：分栏拖到这儿就停。
#     卡片地板实测已压到 ~296px（描边短文本 / 小图 240 / 24px 图标按钮 /
#     折行摘要标签），340 的兜底留了 40px 余量 = **结构性保证**"右栏永远不会
#     出现横向滚动条"，而不是"这次数值调小了但愿别超"；
#   · `CARD_SPLIT_DEFAULT` —— 默认分栏比例偏窄（1360 窗口下右栏实测 421）：
#     浏览背景时「选择…」按钮就在眼前，不用把横向滚动条拖到最右边；
#   · `CARD_STAGE_THUMB`   —— 卡片里「当前舞台」那张小图（16:9）。
#     `stage_thumb.COMPACT_W/H` 仍是组件自己的 sizeHint 真源（288×162），
#     但"288 + 摘要 45 + 间距 8 = 341"会把卡片地板顶在 365px（> 340 目标），
#     所以卡片里再收窄一档到 240×135（仍是 16:9，字仍可读）。
"""

from __future__ import annotations

DEFAULT_WINDOW_W = 1200
DEFAULT_WINDOW_H = 800
DEFAULT_WINDOW_SIZE = (DEFAULT_WINDOW_W, DEFAULT_WINDOW_H)

#: 右栏（`_split_card` 右半）的硬下限。见上：它等于"卡片放得下"的宽度。
CARD_COLUMN_MIN_W = 340
#: 卡片页分栏默认尺寸：左 = 拍列表，右 = 卡片表单（**默认窄**）。
#: 比例 640:420 ≈ 60:40 —— 1360 宽的窗口下右栏实测 421px
#: （视口 409、卡片最小宽 296）。
CARD_SPLIT_DEFAULT = (640, 420)
#: 一键「展开卡片」时右栏占的比例（双击分栏把手 / 右上角 ⤢）
CARD_SPLIT_EXPANDED_RATIO = 0.72
#: 卡片里「当前舞台」小图的落地尺寸（16:9）
CARD_STAGE_THUMB = (240, 135)

__all__ = ['CARD_COLUMN_MIN_W', 'CARD_SPLIT_DEFAULT',
           'CARD_SPLIT_EXPANDED_RATIO', 'CARD_STAGE_THUMB',
           'DEFAULT_WINDOW_H', 'DEFAULT_WINDOW_SIZE', 'DEFAULT_WINDOW_W']
