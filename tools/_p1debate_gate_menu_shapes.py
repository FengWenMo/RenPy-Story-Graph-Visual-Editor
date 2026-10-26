# -*- coding: utf-8 -*-
"""
P1 讨论探针（把关派）· ① 补充：菜单形状矩阵
==============================================
⚠ 本文件用 Write 工具落盘（铁律 #10：含转义的复现不得走 shell heredoc ——
  本轮 /tmp 探针第一版把 `\\n` 写成 `/n`，造出过"菜单解析不出"的假象）。

形状：无标题 / 无标题+extras / 带菜单名 / 带参数 / 带名+参数 /
      CRLF / 2 空格缩进 / 行尾注释 / 有标题改标题 / 有标题+extras 改标题。
每条：加（或改）标题 → 补丁结果 + 产物形状。

跑法：/d/Python/python tools/_p1debate_gate_menu_shapes.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.editor import EditorSession          # noqa: E402
from storygraph.story.patcher import patch_export        # noqa: E402


def _probe(name: str, src: str, new_title: str = '新标题',
           pick='menu'):
    tmp = Path(tempfile.mkdtemp(prefix='p1gate_ms_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    s = EditorSession(project_root=tmp)
    s.load(path)
    kinds = [getattr(b, 'kind', '') for b in s.beats]
    idx = next((i for i, b in enumerate(s.beats)
                if getattr(b, 'kind', '') == pick), None)
    if idx is None:
        print(f'[SKIP] {name}: 没解析出 menu 拍（拍={kinds}）')
        return
    fields = s.fields(idx)
    old = fields.get('Text')
    fields['Text'] = new_title
    s.apply(idx, fields, cards=s.cards(idx), advanced_open=True,
            stage_loaded=fields.get('Stage'))
    res = s.compile()
    p = patch_export(s.beats, path, res.rpy_text,
                     registry=s.registry(), symbols=s.symbols,
                     trust_ranges=True)
    landed = p.ok and new_title in p.text
    print(f'[{"PASS" if landed else "FAIL"}] {name}: ok={p.ok} '
          f'changed={p.changed_lines} 旧标题={old!r} '
          f'reasons={p.reasons}')
    if p.ok:
        rows = [r for r in p.text.splitlines() if r.strip()][:6]
        print('        产物:', rows)


def main():
    _probe('A 无标题（2 选项）',
           'label start:\n    menu:\n        "开始":\n            e "a"\n'
           '        "结束":\n            e "b"\n    return\n')
    _probe('B 无标题 + extras set',
           'label start:\n    menu:\n        set seen\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('B2 无标题 + extras $ 语句',
           'label start:\n    menu:\n        $ seen_x = 1\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('C 带菜单名（无标题）',
           'label start:\n    menu mymenu:\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('D 带参数（无标题）',
           'label start:\n    menu (nvl=True):\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('E 带名+参数（无标题）',
           'label start:\n    menu mymenu (nvl=True):\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('F CRLF 无标题',
           'label start:\r\n    menu:\r\n        "开始":\r\n'
           '            e "a"\r\n    return\r\n')
    _probe('G 2 空格缩进无标题',
           'label start:\n  menu:\n    "开始":\n      e "a"\n  return\n')
    _probe('H 行尾注释 menu:  # 备注',
           'label start:\n    menu:  # 备注\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('I 有标题→改标题',
           'label start:\n    menu:\n        "旧标题"\n        "开始":\n'
           '            e "a"\n    return\n')
    _probe('J 有标题 + extras 改标题',
           'label start:\n    menu:\n        "旧标题"\n        set seen\n'
           '        "开始":\n            e "a"\n    return\n')
    _probe('K 有标题 + extras 在标题前改标题',
           'label start:\n    menu:\n        set seen\n        "旧标题"\n'
           '        "开始":\n            e "a"\n    return\n')
    _probe('L 无标题单选项',
           'label start:\n    menu:\n        "开始":\n            e "a"\n'
           '    return\n')


if __name__ == '__main__':
    main()
    print()
    print('探针跑完（临时目录未清理：用完自行删除 %TEMP%\\p1gate_ms_*）')
