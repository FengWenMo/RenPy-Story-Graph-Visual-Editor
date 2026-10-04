"""
变异验证（**留痕**那一层，不是清洗那一层）
==========================================

`tools/mut_cell_sanitizer.py` 证明的是"**关掉清洗**会红"。
但这一轮修的是两件事，得各验一次：

    A. 清洗（值被压平）      → mut_cell_sanitizer.py        （已有）
    B. 留痕（`sanitized_cells` → W114 / summary / UI note）  ← 本文件

只验 A 就够吗？不够。B 是可以**单独退化**的：清洗照样生效、产物照样干净，
只是"用户不再被告知"。那时 `test_cell_control_chars.py` 里
`TestSingleLineCellsAreFlattened` 全绿 —— 绿得毫无意义，
而"静默改数据"这个缺陷已经回来了。**留痕必须有自己的变异入口。**

用法（从仓库根）：

    PYTHONPATH=.;tools python -m pytest tests/test_cell_control_chars.py \
        -q -p mut_cell_sanitize_trace

期望：`TestCleaningIsReported` 全红（validate W114 / compile summary /
UI 导出 note / 逐列留痕），而 `TestSingleLineCellsAreFlattened` **仍绿**
—— 证明这两层是分开的，也正是"只验 A 不够"的证据。
"""
from storygraph.story.beat_model import Beat

_orig = Beat.from_row.__func__


def _no_trace(cls, row):
    """旧行为：清洗照做，但**不告诉任何人**（静默改数据）。"""
    beat = _orig(cls, row)
    beat.sanitized_cells = []
    return beat


Beat.from_row = classmethod(_no_trace)


def pytest_configure(config):
    print('\n[mutation] 清洗留痕已关闭（值照压，但不说）\n')
