"""
导出产物的**文件名**（一个真源，零项目内依赖）
====================================================================

为什么单独一个模块（2026-11-01，技术债第十轮）：

`NEUTRAL_IR_NAME` / `UNITY_DATA_NAME` 原先定义在 `engine_exporters.py`。
`unity_package.package_files()` 要按同样的名字往包里装文件，于是它在
函数体里反着 `from storygraph.pipeline.engine_exporters import …` ——
`engine_exporters` 又要在顶层 import `unity_package`（导出时调
`package_files` / `write_package`），一条 2 环就这么立住了：

    engine_exporters → unity_package → engine_exporters

文件名是**导出契约**（消费者按名字找文件），既不归"适配器实现"也不归
"打包器"，放叶子模块后两边都向下拿，环消失。

⚠ 只搬常量、不改值：`engine_exporters` 仍然 re-export 这两个名字
（`tests/test_reexport_same_dir.py`、`tests/test_unity_package.py`、
`app/session_export.py` 都从那里取），对外 API 不变。
"""

from __future__ import annotations

#: 引擎中立的 IR（每次导出都写，也是同目录重复导出的合并对象）。
NEUTRAL_IR_NAME = 'story_ir.json'

#: Unity 适配产物（扁平结构，给 `JsonUtility.FromJson<T>` 直接吃）。
UNITY_DATA_NAME = 'UnityDialogueData.json'


__all__ = ['NEUTRAL_IR_NAME', 'UNITY_DATA_NAME']
