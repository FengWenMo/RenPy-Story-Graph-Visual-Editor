# -*- coding: utf-8 -*-
"""
兼容别名：旧的"批量插入"入口 → 通用批处理 `app.batch_edit`。

2026-11-06 把插入扩成 insert / replace / delete 后，新代码请直接
`from storygraph.app.batch_edit import ...`；这个模块只为旧调用方保留，
不在这里重复实现任何逻辑。
"""
from __future__ import annotations

from storygraph.app.batch_edit import *  # noqa: F401,F403
from storygraph.app.batch_edit import __all__  # noqa: F401

