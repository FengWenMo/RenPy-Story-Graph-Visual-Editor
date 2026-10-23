"""
`storygraph.core.collab_status` —— 协作状态的**唯一真源**（值 + 中文名）。
================================================================================

这三个状态（草稿 / 待审 / 定稿）同时被三层用到：

  · `collab.collab_store`：规则、权限、审计、表结构默认值；
  · `app.session_collab`：给界面的薄壳；
  · `ui_qt.dialog_locks`：按钮上的中文（"标为草稿 / 送审 / 定稿"）。

放在 `core` 而不是 `collab`：依赖方向是 **ui_qt → app → core**，而 `collab`
是**旁支**（`tests/test_package_layering.py` 明写"主链不该依赖旁支"）。
第一版把常量留在 `collab_store`、界面直接 import 它 —— 那条守卫当场变红
（`dialog_locks.py:58 ui_qt -> collab`）。中文本该只有一份，所以它下沉到 core。

⚠ 改这里的字符串 = 改协作库里的 `status` 列取值。老库里存的是
`draft` / `in_review` / `approved`，改名等于让老数据变成"认不出的状态"
（`status_summary` 会把它算进"草稿"之外的地方）。**只许加，不许改。**
"""
from __future__ import annotations

STATUS_DRAFT = 'draft'
STATUS_REVIEW = 'in_review'
STATUS_APPROVED = 'approved'

#: 全部合法状态（顺序 = 工作流顺序：草稿 → 待审 → 定稿）。
STATUSES = (STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVED)

#: 给人看的状态名（界面、报告、CLI 都用这一份；别各写一套中文）。
STATUS_LABELS = {
    STATUS_DRAFT: '草稿',
    STATUS_REVIEW: '待审',
    STATUS_APPROVED: '定稿',
}

__all__ = ['STATUS_DRAFT', 'STATUS_REVIEW', 'STATUS_APPROVED',
           'STATUSES', 'STATUS_LABELS']
