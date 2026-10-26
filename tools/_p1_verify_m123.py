#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P1 实测探针（2026-10-26）—— M1 / M2 / M3 三条 P1 的**真实测量**。

    /d/Python/python tools/_p1_verify_m123.py

判据不写死在探针里、而是现场量出来打印，每条给「期望 / 实得」对照。
跑完退出码 0 = 全部达标；1 = 有不达标（并打印是哪条）。

  · M1 点号菜单名 `menu main.route:` —— 认得出、不折进后面台词拍、
    字节往返、删台词拍不带走菜单、menu_brief 查得到；
  · M2 `jump screen_map` / `call screenplay` 不许被 `call screen` 连坐；
  · M3 `call ch02(x, y) from _f` 的 target 只有 `ch02`。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.beat_model import KIND_MENU, KIND_MENU_OPTION
from storygraph.story.flow import menu_brief

FAILS: list = []


def check(tag: str, ok: bool, expect, got) -> None:
    mark = 'PASS' if ok else 'FAIL'
    print(f'  [{mark}] {tag}')
    if not ok:
        print(f'         期望: {expect!r}')
        print(f'         实得: {got!r}')
        FAILS.append(tag)


def _beats(tmp: Path, src: str, stem: str = 'p1'):
    f = tmp / f'{stem}.rpy'
    f.write_bytes(src.encode('utf-8'))
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[f], graph=None)
    return beats_from_parse_result(pr)


def _opts(brief) -> list:
    return [(o.get('text'), o.get('type'), o.get('target'))
            for o in (brief or {}).get('options') or ()]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='p1_verify_'))

    # ==============================================================
    print('M1 点号菜单名 `menu main.route:`')
    # ==============================================================
    src = (
        'label start:\n'
        '    menu main.route:\n'
        '        "选项 A":\n'
        '            jump done\n'
        '        "选项 B":\n'
        '            call helper\n'
        '    "菜单之后"\n'
        '    return\n'
        '\n'
        'label done:\n'
        '    return\n'
        '\n'
        'label helper:\n'
        '    return\n'
    )
    beats = _beats(tmp, src, 'm1')

    heads = [b for b in beats if b.kind == KIND_MENU]
    opts = [b for b in beats if b.kind == KIND_MENU_OPTION]
    check('菜单拍 1 张', len(heads) == 1, 1, len(heads))
    check("menu_name == 'main.route'",
          heads and heads[0].menu_name == 'main.route',
          'main.route', heads[0].menu_name if heads else None)
    check('选项拍 2 张（不再隐形）', len(opts) == 2, 2, len(opts))

    says = [b for b in beats if '菜单之后' in str(getattr(b, 'text', '') or '')]
    say = says[0] if says else None
    check('台词拍 1 张', len(says) == 1, 1, len(says))
    opaque = str(getattr(say, 'opaque', '') or '') if say else '(无拍)'
    check('台词拍不驮菜单（opaque 空）', not opaque.strip(), '',
          opaque[:60] if opaque else '')

    out = render_beats_to_rpy(beats)
    check('渲染含 `menu main.route:`', 'menu main.route:' in out,
          'menu main.route:', out)
    check('渲染含菜单之后台词', '菜单之后' in out, '菜单之后', out)

    # 字节往返：不改动 → 除**生成标记头**外逐字节相同
    # （`render_beats_to_rpy` 会盖 `# 由剧本表自动生成…` 那行 —— 那是
    #  GENERATOR_MARKER，不是内容差异；判据要扣掉它，否则永远红。）
    def _body(t: str) -> str:
        lines = t.replace('\r\n', '\n').split('\n')
        while lines and (not lines[0].strip()
                         or lines[0].lstrip().startswith('#')):
            lines.pop(0)
        return '\n'.join(lines).rstrip('\n')

    rt = render_beats_to_rpy(_beats(tmp, src, 'm1b'))
    check('不改内容时除生成标记外逐字节相同', _body(rt) == _body(src),
          _body(src)[:80], _body(rt)[:80])

    # 删台词拍不带走菜单：把那张台词拍从序列里拿掉再渲染
    kept = [b for b in beats
            if not ('菜单之后' in str(getattr(b, 'text', '') or ''))]
    after = render_beats_to_rpy(kept)
    check('删台词拍后菜单仍在', 'menu main.route:' in after
          and '选项 A' in after, 'menu + 选项还在', after)
    check('删台词拍后台词真没了', '菜单之后' not in after, '', after)

    brief = menu_brief(beats, 'main.route')
    check("menu_brief('main.route') 有名字",
          brief.get('name') == 'main.route', 'main.route', brief.get('name'))
    check("menu_brief 选项 2 条", brief.get('count') == 2, 2, brief.get('count'))
    check("menu_brief 去向正确",
          _opts(brief) == [('选项 A', 'jump', 'done'),
                           ('选项 B', 'call', 'helper')],
          [('选项 A', 'jump', 'done'), ('选项 B', 'call', 'helper')],
          _opts(brief))
    check("menu_brief('main') 查不到（不猜全名前缀）",
          menu_brief(beats, 'main') == {}, {}, menu_brief(beats, 'main'))

    # 回归：老形态一个不许坏
    print('M1 回归：既有菜单头形态')
    for head, name, args in [
        ('menu:', '', ''),
        ('menu (nvl=True):', '', '(nvl=True)'),
        ('menu choice_menu:', 'choice_menu', ''),
        ('menu named_menu (screen="choice"):',
         'named_menu', '(screen="choice")'),
        ('menu .local:', '.local', ''),
    ]:
        s = ('label start:\n    ' + head + '\n'
             '        "A":\n            jump done\n'
             '    return\n\nlabel done:\n    return\n')
        bs = _beats(tmp, s, 'reg')
        hs = [b for b in bs if b.kind == KIND_MENU]
        ok = (len(hs) == 1 and hs[0].menu_name == name
              and hs[0].menu_args == args)
        check(f'{head} 仍建模', ok, (name, args),
              (hs[0].menu_name, hs[0].menu_args) if hs else None)
        r = render_beats_to_rpy(bs)
        check(f'{head} 仍逐字节写回', head in r, head, r)

    # `menu hide:` 不查关键字表 —— 当名字原样写回（铁律 3/4）
    s = ('label start:\n    menu hide:\n'
         '        "A":\n            jump done\n'
         '    return\n\nlabel done:\n    return\n')
    bs = _beats(tmp, s, 'hide')
    hs = [b for b in bs if b.kind == KIND_MENU]
    r = render_beats_to_rpy(bs)
    check('menu hide: 当名字照原样写回',
          hs and hs[0].menu_name == 'hide' and 'menu hide:' in r,
          'menu hide:', r if not hs else (hs[0].menu_name, r))

    # ==============================================================
    print('M2 screen 开头的真 label 不许被 call screen 连坐')
    # ==============================================================
    s2 = (
        'label main:\n'
        '    menu m:\n'
        '        "开界面":\n'
        '            call screen confirm\n'
        '        "去地图":\n'
        '            jump screen_map\n'
        '        "剧本组":\n'
        '            call screenplay\n'
        '    jump done\n'
        '\n'
        'label done:\n'
        '    return\n'
    )
    b2 = _beats(tmp, s2, 'm2')
    got2 = _opts(menu_brief(b2, 'm'))
    check('M2 三条去向', got2 == [('开界面', '', ''),
                                 ('去地图', 'jump', 'screen_map'),
                                 ('剧本组', 'call', 'screenplay')],
          [('开界面', '', ''), ('去地图', 'jump', 'screen_map'),
           ('剧本组', 'call', 'screenplay')], got2)

    # ==============================================================
    print('M3 call 的 target 不带参数与 from')
    # ==============================================================
    s3 = (
        'label main:\n'
        '    menu m:\n'
        '        "求助":\n'
        '            call ch02(x, y) from _call_ch02_1\n'
        '        "直跳":\n'
        '            jump ch03\n'
        '    jump done\n'
        '\n'
        'label done:\n'
        '    return\n'
    )
    b3 = _beats(tmp, s3, 'm3')
    got3 = _opts(menu_brief(b3, 'm'))
    check('M3 target 只有 label',
          got3 == [('求助', 'call', 'ch02'), ('直跳', 'jump', 'ch03')],
          [('求助', 'call', 'ch02'), ('直跳', 'jump', 'ch03')], got3)

    print()
    if FAILS:
        print(f'== 不达标 {len(FAILS)} 条 ==')
        for t in FAILS:
            print(f'  - {t}')
        return 1
    print('== 全部达标 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
