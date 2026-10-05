"""
定位 E11：**哪种形状会让 `MENU_NO_CAPTION`（`\\x00menu`）漏进"生成文本"**，
害得增量补丁对不上、退回全量？

背景（`tools/hell_edit_rounds.py::B` 每轮都在报）：

    增量未采用：1 句台词在补丁结果里找不到（例：'\\x00menu'）

而真实项目里 **7 处裸 `menu:`**（`credits_screen` / `mini_game_puzzle` /
`pomodorro` / `script_pomo` / `script`）**都不再退化了** —— 说明触发它的
不是"裸菜单"本身，而是某个**更窄的形状**。这个探针把候选形状一个个试。

判据：导出后 `note` 里出现 `x00menu` → 这一形状**中招**。

用法：D:/Python/python.exe tools/probe_placeholder_leak.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession

SHAPES = {
    'bare_menu_at_start': '''label t:
    menu:
        "一":
            e "选一"
        "二":
            e "选二"
''',
    'bare_menu_after_say': '''label t:
    e "先一句话"
    menu:
        "一":
            e "选一"
''',
    'bare_menu_with_note_on_head': '''label t:
    e "先一句话"
    menu:  # 备注：菜单
        "一":  # 备注：选项
            e "选一"  # 备注：台词
''',
    'captioned_menu_with_note': '''label t:
    e "先一句话"
    menu "标题":  # 备注：菜单
        "一":
            e "选一"
''',
    'bare_menu_inside_if': '''label t:
    e "先一句话"
    if flag:
        menu:
            "一":
                e "选一"
''',
    'bare_menu_with_show_in_body': '''label t:
    e "先一句话"
    menu:
        "一":
            show ling happy at left
            e "选一"
''',
}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_ph_'))
    hits = []
    for name, src in SHAPES.items():
        d = tmp / name
        d.mkdir(parents=True, exist_ok=True)
        f = d / 'ch.rpy'
        f.write_text(src, encoding='utf-8')

        s = EditorSession(project_root=d)
        s.load(f)
        t = next((b for b in s.beats if (b.text or '').strip()
                  and str(b.kind) in ('say', 'narration')), None)
        if t is None:
            print(f'  [--] {name}: 没有可改的台词拍')
            continue
        t.text = '改过的一句话'
        # 先把**生成文本**摊开看一眼：那一行到底是什么形态
        from storygraph.story.beat_render import render_beats_to_rpy
        gen = render_beats_to_rpy(s.beats)
        gen_menu = [ln for ln in gen.splitlines()
                    if 'menu' in ln or chr(0) in ln]
        res = s.export(f, force=True)
        note = str(res.get('note') or '')
        hit = 'x00menu' in note
        if hit:
            hits.append(name)
        print(f'  [{"中" if hit else "过"}] {name}')
        print(f'        note = {note[:90]!r}')
        print(f'        生成文本里的菜单行 = {gen_menu!r}')
        print(f'        产物里有占位符吗 = '
              f'{"有" if chr(0) in f.read_text(encoding="utf-8") else "没有"}')
    print(f'\n中招形状：{hits if hits else "（一个都没有）"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
