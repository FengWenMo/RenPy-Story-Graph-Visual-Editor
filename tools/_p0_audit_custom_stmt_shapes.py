# -*- coding: utf-8 -*-
"""第二轮审计探针：自定义语句拍的**其他形状**（回归测试只覆盖了
`mycue intensity=3 layer=camera` + 台词 这一种）。

攻击面（都是真机上出现过的写法）：
  ① 位置参数 + 关键字参数混排：`mycue "闪电" intensity=3`
  ② 块型自定义语句（带冒号和块体）与台词同拍
  ③ 未声明（rules 里没有）+ 位置参数
  ④ CDS 声明（renpy.register_statement）而非 rules.json
  ⑤ 台词前有**两条**自定义语句
  ⑥ 自定义语句在台词**之后**
  ⑦ 自定义语句带行尾注释
  ⑧ 同时改说话人 + 台词
  ⑨ 自定义语句参数改动本身（intensity=3 → intensity=9）

判据（与 test_p0_round4 同一套）：
  · patch 产物 ok=True；
  · 台词行换成新值、自定义语句行**原样保留**；
  · 没有别的行被动（逐行比）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.story.patcher import patch_export  # noqa: E402

FAIL = []


def check(cond, msg):
    print(f'  [{"PASS" if cond else "FAIL"}] {msg}')
    if not cond:
        FAIL.append(msg)


def project(tmp_path, src, rules=None, cds=False):
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    if rules is not None:
        (tmp_path / 'storygraph.rules.json').write_text(
            json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    if cds:
        (game / 'cds.rpy').write_text(
            'init python:\n'
            '    renpy.register_statement("mycue", parse=lambda l: l.eol())\n',
            encoding='utf-8')
    session = EditorSession(project_root=tmp_path)
    session.load(path)
    return session, path


def patched(session, path):
    res = session.compile()
    return patch_export(session.beats, path, res.rpy_text,
                        registry=session.registry(), symbols=session.symbols,
                        trust_ranges=True)


def submit(session, index, **changes):
    fields = session.fields(index)
    fields.update(changes)
    # apply 返回 Optional[Beat]（None = 越界），不是 dict
    return session.apply(index, fields, cards=session.cards(index),
                         advanced_open=True,
                         stage_loaded=fields.get('Stage'))


def body(text):
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith('# 由剧本表自动生成'):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


def case(tmp, name, src, rules, edit_idx_text, expect_rows, speaker=None,
         cds=False, beat_index=0):
    print(f'== {name} ==')
    d = Path(tempfile.mkdtemp(dir=str(tmp)))
    try:
        session, path = project(d, src, rules=rules, cds=cds)
        beats = len(session.beats)
        print(f'  拍数={beats}')
        kw = {'Text': edit_idx_text}
        if speaker:
            kw['Speaker'] = speaker
        r = submit(session, beat_index, **kw)
        print(f'  apply 返回={"None(越界)" if r is None else "那一拍"}')
        p = patched(session, path)
        print(f'  patch ok={p.ok} changed={p.changed_lines} reasons={p.reasons[:1]}')
        if not p.ok:
            check(False, f'{name}: 补丁被拒（{p.reasons[:2]}）')
            return
        got = body(p.text)
        if got != expect_rows:
            print('  期望:', expect_rows)
            print('  实际:', got)
        check(got == expect_rows, f'{name}: 台词落地且只动该动行')
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sg_custom_'))
    RULES = {'statements': {'mycue': {
        'line': 'mycue intensity int =0 layer str =camera'}}}

    case(tmp, '① 位置参数+关键字参数（已声明）',
         'label start:\n    mycue "闪电" intensity=3 layer=camera\n'
         '    e "台词"\n    return\n',
         RULES, '改过的',
         ['label start:', '    mycue "闪电" intensity=3 layer=camera',
          '    e "改过的"', '    return'])

    case(tmp, '② 块型自定义语句（冒号+块体）与台词同拍',
         'label start:\n    mycue "闪电" intensity=3:\n'
         '        target = ling\n        hold 0.5\n'
         '    e "台词"\n    return\n',
         {'statements': {'mycue': {'line': 'mycue intensity int =0',
                                   'block': True}}},
         '改过的',
         ['label start:', '    mycue "闪电" intensity=3:',
          '        target = ling', '        hold 0.5',
          '    e "改过的"', '    return'])

    case(tmp, '③ 未声明 + 位置参数',
         'label start:\n    mycue "闪电" 3\n    e "台词"\n    return\n',
         None, '改过的',
         ['label start:', '    mycue "闪电" 3',
          '    e "改过的"', '    return'])

    case(tmp, '④ CDS 声明（register_statement）：改**台词拍**',
         'label start:\n    mycue "闪电"\n    e "台词"\n    return\n',
         None, '改过的',
         ['label start:', '    mycue "闪电"',
          '    e "改过的"', '    return'],
         cds=True, beat_index=1)

    # ④b 同一形状改**自定义语句拍**的 Text：只许动 mycue 的位置参数，
    #     台词行一个字节不许动（这是"参数格"语义，不是改坏）。
    print('== ④b CDS 声明：改自定义语句拍的 Text（参数格语义） ==')
    d = Path(tempfile.mkdtemp(dir=str(tmp)))
    try:
        session, path = project(
            d, 'label start:\n    mycue "闪电"\n    e "台词"\n    return\n',
            cds=True)
        print(f'  拍数={len(session.beats)}'
              f'（CDS 下 mycue 与台词**不合并**，各自成拍）')
        submit(session, 0, Text='打雷')
        p = patched(session, path)
        got = body(p.text) if p.ok else []
        print(f'  patch ok={p.ok} 实际={got}')
        check(p.ok and got == ['label start:', '    mycue "打雷"',
                               '    e "台词"', '    return'],
              '④b: 只改 mycue 位置参数，台词行原样')
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    case(tmp, '⑤ 两条自定义语句在台词前',
         'label start:\n    mycue "闪电" intensity=3\n'
         '    othercue layer=camera\n    e "台词"\n    return\n',
         RULES, '改过的',
         ['label start:', '    mycue "闪电" intensity=3',
          '    othercue layer=camera', '    e "改过的"', '    return'])

    case(tmp, '⑥ 自定义语句在台词之后',
         'label start:\n    e "台词"\n    mycue intensity=3 layer=camera\n'
         '    return\n',
         RULES, '改过的',
         ['label start:', '    e "改过的"',
          '    mycue intensity=3 layer=camera', '    return'])

    case(tmp, '⑦ 自定义语句带行尾注释',
         'label start:\n    mycue intensity=3 layer=camera   # 镜头\n'
         '    e "台词"\n    return\n',
         RULES, '改过的',
         ['label start:', '    mycue intensity=3 layer=camera   # 镜头',
          '    e "改过的"', '    return'])

    case(tmp, '⑧ 同时改说话人+台词',
         'label start:\n    mycue intensity=3 layer=camera\n'
         '    e "台词"\n    return\n',
         RULES, '改过的',
         ['label start:', '    mycue intensity=3 layer=camera',
          '    m "改过的"', '    return'],
         speaker='m')

    print()
    if FAIL:
        print(f'命中 {len(FAIL)} 条：')
        for f in FAIL:
            print('  -', f)
        return 1
    print('全部形状通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
