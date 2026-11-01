"""一次性手术刀：把「🗂 资源映射」那一块从 main_window.py 切到 mapping_status.py。

只做搬运，不改一行函数体。切完由本脚本自己回读校验（两文件的 AST 顶层名字
集合 + 切走的行数），失败就抛，不留半截状态。
"""
from __future__ import annotations

import ast
import pathlib

MW = pathlib.Path('storygraph/ui_qt/main_window.py')
OUT = pathlib.Path('storygraph/ui_qt/mapping_status.py')

BANNER = (
    '# ==================================================================\n'
    '#  「🗂 资源映射」入口 + 状态栏那一行（④⑥ 第二阶段，2026-10-30）\n'
    '# ==================================================================\n'
    '#\n'
    '# **为什么全是模块级函数、而不是主类方法**：`test_main_window_mixins.py`\n'
    '# 用 `ast.walk` 数 `MainWindowQt` 的方法（预算 ≤35，嵌套 def 也算）——\n'
    '# 本轮实测 34/35，一个都不能再往主类里塞。这些函数只操作窗口状态，\n'
    '# 不需要成为主类 API（与 `toggle_card_column` / `CardActionRow` 同一条规矩）。\n'
)

HEADER = '''"""
storygraph.ui_qt.mapping_status
===============================
「🗂 资源映射」菜单 + 状态栏那一格：**装一次、刷很多次**。

2026-11-01 从 `ui_qt.main_window` 搬出来（解导入环）。为什么非搬不可：

`BeatCardEditor` 只依赖 main_window 的**一个**模块级名字 —— 就是这里的
`refresh_mapping_status`（立绘换成真图之后要立刻把"未识别张数"重算一遍）。
卡片编辑器随后也搬出 main_window 成了 `ui_qt.card_editor`，名字留在原处就是

    main_window → card_editor → main_window

那个模块级环。这一块本身只认「窗口对象上有哪些属性」，零反向依赖，
所以它是那个环上最便宜的断点。

**为什么全是模块级函数、而不是主类方法**：`test_main_window_mixins.py`
用 `ast.walk` 数 `MainWindowQt` 的方法（预算 ≤35，嵌套 def 也算）。
这些函数只操作窗口状态，不需要成为主类 API（与 `toggle_card_column`
同一条规矩）。main_window 里 re-export 老名字，`MW.refresh_mapping_status`
之类的老调用点（含测试）不变。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLabel

from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme


'''


def top_names(src: str) -> set[str]:
    out = set()
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def main() -> None:
    lines = MW.read_text(encoding='utf-8').splitlines(keepends=True)

    # 定位：从「🗂 资源映射」那块注释开始，到 `_install_mapping_ui` 结束
    start = next(i for i, s in enumerate(lines)
                 if s.strip().startswith('#  「🗂 资源映射」入口'))
    start = max(0, start - 1)                     # 连同上面的 `# ====` 横幅
    # `refresh_mapping_status(win, force=True)` 在块内出现两次（`mapping_reload`
    # 里也有），要的是 `_install_mapping_ui` 里那一次 —— 先定位函数头再找。
    install = next(i for i, s in enumerate(lines)
                   if s.startswith('def _install_mapping_ui') and i > start)
    end = next(i for i, s in enumerate(lines)
               if s.strip() == 'refresh_mapping_status(win, force=True)'
               and i > install)
    # `_install_mapping_ui` 的调用行是块内最后一条语句；再往前的两行空行留给主文件
    body = ''.join(lines[start:end + 1])
    assert 'def _install_mapping_ui' in body, body[:400]
    assert 'MAPPING_MENU_TEXTS' in body

    moved_names = top_names(body)
    print('搬走：', sorted(moved_names))
    assert moved_names == {
        'MAPPING_MENU_TEXTS', 'MAPPING_CACHE_ATTR', '_exec_mapping_dialog',
        '_exec_asset_picker', '_choose_mapping_file',
        '_choose_mapping_export_path', '_ignore_checked',
        'open_mapping_dialog', 'mapping_load_file', 'mapping_reload',
        'mapping_export_draft', 'mapping_save_to_project',
        'mapping_show_table', 'refresh_mapping_status', '_install_mapping_ui',
    }, sorted(moved_names)

    OUT.write_text(HEADER + body, encoding='utf-8')

    # 主文件里换成 re-export；说明写在上面（为什么这一块不住这儿了）
    note = (
        '# 「🗂 资源映射」菜单 + 状态栏那一行搬到 `ui_qt.mapping_status`\n'
        '# （2026-11-01 解导入环）：`BeatCardEditor` 要调 `refresh_mapping_status`，\n'
        '# 而它自己也搬去了 `ui_qt.card_editor` —— 名字留在本模块就是\n'
        '# `main_window → card_editor → main_window` 的模块级环。\n'
        '# 这里 re-export，`MW.refresh_mapping_status` 之类的老写法（含测试）不变。\n'
        'from storygraph.ui_qt.mapping_status import (  # noqa: F401 - 老调用点 re-export\n'
        '    MAPPING_CACHE_ATTR, MAPPING_MENU_TEXTS, _choose_mapping_export_path,\n'
        '    _choose_mapping_file, _exec_asset_picker, _exec_mapping_dialog,\n'
        '    _ignore_checked, _install_mapping_ui, mapping_export_draft,\n'
        '    mapping_load_file, mapping_reload, mapping_save_to_project,\n'
        '    mapping_show_table, open_mapping_dialog, refresh_mapping_status)\n'
    )
    new = ''.join(lines[:start]) + note + ''.join(lines[end + 1:])
    MW.write_text(new, encoding='utf-8')

    # 回读校验：搬走的名字在新家都在、老家里也都还在（靠 re-export）
    new_top = top_names(OUT.read_text(encoding='utf-8'))
    assert new_top == moved_names, sorted(new_top ^ moved_names)
    print(f'main_window.py: {len(lines)} -> {len(new.splitlines())} 行')
    print(f'mapping_status.py: {len(OUT.read_text(encoding="utf-8").splitlines())} 行')


if __name__ == '__main__':
    main()
