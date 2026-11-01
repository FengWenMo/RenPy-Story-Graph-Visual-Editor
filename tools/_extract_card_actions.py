"""一次性手术刀：把卡片那十九个模块级回调从 main_window.py 切到 ui_qt/card_actions.py。

这十九个函数都是 `fn(win, req)` 形态 —— 它们不是主窗口的方法，是"工具栏/面板
按钮点下去要干的那件事"。搬走之后 `window_graph_page` 才可能 import 它们
（否则就是 `main_window ↔ window_graph_page` 的环）。
"""
from __future__ import annotations

import ast
import pathlib

MW = pathlib.Path('storygraph/ui_qt/main_window.py')
OUT = pathlib.Path('storygraph/ui_qt/card_actions.py')

HEADER = '''"""
storygraph.ui_qt.card_actions
=============================
卡片上那些**按钮点下去要干的事**：改尺寸 / 换景 / 写回 at / 并入同样的一批 /
立绘挑图 / 去资源映射里修。

全是 `fn(win, req)` 形态的模块级函数，不是主窗口的方法 —— 理由和
`mapping_status` 那边一样：`tests/test_main_window_mixins.py` 数主类里的
方法（预算 ≤35），而这些东西只需要"窗口对象上有哪些属性"，没有一个该成为
主类 API。

2026-11-01 从 `ui_qt.main_window` 搬出来，为的是**搬页面构建器**：
`_build_card_page` / `_build_toolbars` 要进 `GraphPageMixin`，而它们用到的
`apply_size_from_card` / `BeatCardEditor` 那时还住在 main_window ——
`window_graph_page → main_window` 就是反向边。这一块落到本模块后方向理顺：

    main_window → window_graph_page → {card_actions, card_editor, mapping_status}

⚠ 判据要打桩的对象变了：从前 `monkeypatch.setattr(MW, '_ask_scene_image', …)`
改的是 main_window 的模块级名字；这些函数现在读的是**本模块**的名字，
所以打桩要打在 `storygraph.ui_qt.card_actions` 上（`import … as CA`）。
main_window 里仍 re-export 这些函数，读得到、调得动。
"""

from __future__ import annotations

from functools import partial

from PySide6.QtWidgets import QDialog, QMessageBox

from storygraph.core.safeguard import swallow
from storygraph.ui_qt.mapping_status import (_exec_asset_picker,
                                             open_mapping_dialog)


'''


def top_names(src: str) -> set[str]:
    return {n.name for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


EXPECT = {
    'pick_sprite_result', 'open_mapping_from_card', 'notify_card_notice',
    '_confirm_at_plan', '_plan_preview_at', '_after_preview_beat_edit',
    'apply_at_from_preview', '_screen_pair', '_plan_size_from_card',
    '_refresh_card_after_size', '_plan_sg_from_card', 'apply_size_from_card',
    '_ask_scene_image', '_plan_scene_from_card', 'apply_scene_from_card',
    '_confirm_merge_plan', 'apply_merge_from_panel',
    '_refresh_card_after_merge', 'open_stage_preview_with_at',
}


def main() -> None:
    lines = MW.read_text(encoding='utf-8').splitlines(keepends=True)
    start = next(i for i, s in enumerate(lines)
                 if s.startswith('#  立绘网格')) - 1
    assert lines[start].startswith('# ==='), lines[start]
    cut = next(i for i, s in enumerate(lines)
               if s.startswith('# 卡片编辑器（一句话一张表）'))
    end = cut
    while not lines[end - 1].strip():             # 尾部空行留给主文件的排版
        end -= 1
    body = ''.join(lines[start:end]).rstrip('\n') + '\n'

    moved = top_names(body)
    print('搬走：', sorted(moved))
    assert moved == EXPECT, sorted(moved ^ EXPECT)

    OUT.write_text(HEADER + body, encoding='utf-8')

    rex = '\n'.join(f'    {n},' for n in sorted(EXPECT))
    note = (
        '# 卡片上的那些"按钮点下去干的事"搬到 `ui_qt.card_actions`（2026-11-01）：\n'
        '# 它们是 `fn(win, req)` 形态的模块级回调，不是主类的方法；搬走之后\n'
        '# `window_graph_page` 才 import 得到它们（否则就是反向边）。\n'
        '# 这里 re-export，`MW.apply_size_from_card` 之类的老写法（含测试）不变。\n'
        '# ⚠ 要打桩 `_ask_scene_image` / `_confirm_merge_plan` 的判据请打在\n'
        '# `storygraph.ui_qt.card_actions` 上 —— 函数读的是那里的名字。\n'
        'from storygraph.ui_qt.card_actions import (  # noqa: F401 - 老调用点 re-export\n'
        f'{rex})\n'
    )
    new = ''.join(lines[:start]) + note + ''.join(lines[cut:])
    MW.write_text(new, encoding='utf-8')
    print(f'card_actions.py: {len(OUT.read_text(encoding="utf-8").splitlines())} 行')
    print(f'main_window.py: {len(lines)} -> {len(new.splitlines())} 行')


if __name__ == '__main__':
    main()
