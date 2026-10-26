"""P1 讨论·实现派探针①e：extras 菜单零改动往返 + 改标题后 `set seen` 的命运。

验证两个问题（不改源码）：
  §1 零改动导出 extras 菜单 → 是否逐字节相同（含 `set seen` 不许变成 `$ set seen`）
  §2 有旧标题时改标题（真实代码，不打补丁）→ `set seen` 落成什么
  §3 单拍渲染 vs 整份渲染对 extras 的两套口径（铁律 6 病灶）

跑法： /d/Python/python tools/_p1debate_impl_extras_fate.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((bool(ok), name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


SRC_EX = ('label start:\n'
          '    menu:\n'
          '        set seen\n'
          '        "开始":\n'
          '            e "a"\n'
          '    return\n')
SRC_CAP_EX = ('label start:\n'
              '    menu:\n'
              '        "旧标题"\n'
              '        set seen\n'
              '        "开始":\n'
              '            e "a"\n'
              '    return\n')


def run(src, edit=None):
    from storygraph.app.editor import EditorSession
    tmp = Path(tempfile.mkdtemp(prefix='p1exf_'))
    try:
        game = tmp / 'game'
        game.mkdir()
        p = game / 'script.rpy'
        p.write_bytes(src.encode('utf-8'))
        s = EditorSession(project_root=tmp)
        s.load(p)
        if edit is not None:
            f = s.fields(0)
            f.update(edit)
            s.apply(0, f, cards=s.cards(0), advanced_open=True,
                    stage_loaded=f.get('Stage'))
        res = s.export(p)
        return bool(res.get('ok')), p.read_bytes().decode('utf-8'), res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print('=' * 68)
    print('§1 零改动导出（extras 菜单）→ 逐字节相同？')
    print('=' * 68)
    ok, after, res = run(SRC_EX)
    check('零改动 ok=True 且逐字节相同', ok and after == SRC_EX,
          f'ok={ok}\n期望={SRC_EX!r}\n实际={after!r}')
    check('产物里 set seen 没被补 $',
          ok and 'set seen' in after and '$ set seen' not in after,
          f'after={after!r}')

    print()
    print('=' * 68)
    print('§2 有旧标题 → 改标题（真实代码）')
    print('=' * 68)
    ok, after, res = run(SRC_CAP_EX, {'Text': '新标题'})
    exp = ('label start:\n    menu:\n        "新标题"\n        set seen\n'
           '        "开始":\n            e "a"\n    return\n')
    check('改标题落地且 set seen 原样', ok and after == exp,
          f'ok={ok} 拒因={res.get("blocking")}\n期望={exp!r}\n实际={after!r}')

    print()
    print('=' * 68)
    print('§3 两套口径：单拍渲染 vs 整份渲染（extras）')
    print('=' * 68)
    from storygraph.story.beat_model import Beat, KIND_MENU
    from storygraph.story.beat_render import render_beat_to_rpy
    b = Beat(seq=1, label='start', condition='', kind=KIND_MENU,
             text='新标题')
    b.raw = 'set seen'
    single = render_beat_to_rpy(b, audit=None)
    print(f'  单拍渲染：{single}')
    # 整份渲染：构造单拍 run 走 render_menu_block
    from storygraph.story.beat_render import render_menu_block
    whole = render_menu_block([b], indent='    ')
    print(f'  整份渲染：{whole}')
    single_txt = '\n'.join(single)
    whole_txt = '\n'.join(whole)
    check('两套渲染的 extras 写法一致（无 $ / 位置一致）',
          ('set seen' in single_txt and '$ set seen' not in single_txt
           and '$ set seen' not in whole_txt
           and single_txt.replace(' ', '')
           == whole_txt.replace(' ', '').replace('    ', '')),
          f'单拍={single}\n整份={whole}')
    check('单拍渲染没有给 set seen 补 $（铁律 6 / 不许把能跑的改坏）',
          not any(str(x).strip().startswith('$ set') for x in single),
          f'单拍={single}')

    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
