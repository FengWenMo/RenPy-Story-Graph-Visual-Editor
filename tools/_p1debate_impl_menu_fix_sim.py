"""P1 讨论·实现派探针①c：P1① 修法**可行性证明**（monkeypatch，不动源码）。

根因（已钉死）：
  · 嵌在 if 里：单拍渲染 gen = ['if flag:', 'menu:', '"新标题"']，
    caption 通道拿 `gen_bodies[0]`（='if flag:'）去比 `menu:` → 通道不触发。
  · 带 extras：gen = ['menu:', '"新标题"', 'set seen']，extras 的行不在
    拍的行区间里（src 只覆盖 `menu:`），`all(_is_caption_line)` 被卡住。
    extras 若被通道写进区间 → 与区间外的原 extras 重复。

修法（模拟版）：
  1. 通道判据用 `_body_lines()` 剥开头结构行再比头（与 patcher:993 同源）；
  2. gen[1:] 里**非 caption 且文件里逐字已有**的行（extras）先摘掉
     ——它们在区间外原地不动，通道只写标题行；
  3. 改写后的 old/gen 交回原函数时传 `allow_edit=False`（一对一保序通道）
     ——防原通道二次触发把注入的标题剥掉（上一版模拟就是这么翻车的）。

跑法： /d/Python/python tools/_p1debate_impl_menu_fix_sim.py
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


def make(tmp: Path, src: str):
    game = tmp / 'game'
    game.mkdir(parents=True, exist_ok=True)
    p = game / 'script.rpy'
    p.write_bytes(src.encode('utf-8'))
    return p


def body(text: str) -> list:
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


def run(src: str, text_new: str):
    from storygraph.app.editor import EditorSession
    tmp = Path(tempfile.mkdtemp(prefix='p1fixsim_'))
    try:
        p = make(tmp, src)
        s = EditorSession(project_root=p.parents[1])
        s.load(p)
        f = s.fields(0)
        f.update({'Text': text_new})
        s.apply(0, f, cards=s.cards(0), advanced_open=True,
                stage_loaded=f.get('Stage'))
        res = s.export(p)
        return bool(res.get('ok')), body(
            p.read_bytes().decode('utf-8')), res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_fix():
    from storygraph.story import patcher as P

    orig = P._replace_matched

    def patched(old_lines, gen_bodies, allow_edit=True, file_squash=None,
                relax_speaker=False, removed_out=None, child_pad=''):
        if allow_edit and old_lines and gen_bodies:
            _mh = P._norm(old_lines[0])
            gh = P._body_lines(gen_bodies)              # 修法 1
            if (gh and P._MENU_HEAD_RE.match(_mh) and _mh.endswith(':')
                    and P._norm(gh[0]) == _mh):
                squash = set(file_squash or ())
                gen_caps = [x for x in gh[1:] if P._is_caption_line(x)]
                gen_oth = [x for x in gh[1:] if not P._is_caption_line(x)]
                old_caps = [x for x in old_lines[1:] if P._is_caption_line(x)]
                old_oth = [x for x in old_lines[1:]
                           if not P._is_caption_line(x)]
                # 修法 2：extras（非 caption 且文件里逐字已有）先摘掉
                keep_oth, drop_oth = [], []
                for x in gen_oth:
                    (drop_oth if P._squash(x) in squash or P._squash(x) in {
                        P._squash(o) for o in old_oth} else keep_oth).append(x)
                extras_ok = not keep_oth
                caps_changed = ([P._squash(x) for x in old_caps]
                                != [P._squash(x) for x in gen_caps])
                if extras_ok and caps_changed:
                    if len(old_lines) >= 2 and P._is_caption_line(old_lines[1]):
                        _pad = P.indent_prefix(old_lines[1])
                    else:
                        _pad = (child_pad or (
                            P.indent_prefix(old_lines[0])
                            + (P.indent_prefix(gh[1]) if len(gh) >= 2
                               else '    ')))
                    _new_caps = [_pad + x for x in gen_caps]
                    new_old = [old_lines[0]] + _new_caps + list(old_oth)
                    new_gen = [gh[0]] + list(keep_oth)
                    pre = max(len(old_caps), len(gen_caps), 1)
                    # 修法 3：allow_edit=False 走一对一保序，防二次触发
                    out, ch, unlanded, g, sk = orig(
                        new_old, new_gen, allow_edit=False,
                        file_squash=file_squash,
                        relax_speaker=relax_speaker,
                        removed_out=removed_out, child_pad=child_pad)
                    return out, ch + pre, unlanded, g, sk
        return orig(old_lines, gen_bodies, allow_edit=allow_edit,
                    file_squash=file_squash, relax_speaker=relax_speaker,
                    removed_out=removed_out, child_pad=child_pad)

    P._replace_matched = patched
    return orig


NEST = ('label start:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "开始":\n'
        '                e "a"\n'
        '    return\n')
NEST_EXP = ['label start:', '    if flag:', '        menu:',
            '            "新标题"', '            "开始":',
            '                e "a"', '    return']
EXTRAS = ('label start:\n'
          '    menu:\n'
          '        set seen\n'
          '        "开始":\n'
          '            e "a"\n'
          '    return\n')
EXTRAS_EXP = ['label start:', '    menu:', '        "新标题"',
              '        set seen', '        "开始":',
              '            e "a"', '    return']
BARE = ('label start:\n'
        '    menu:\n'
        '        "开始":\n'
        '            e "a"\n'
        '    return\n')
BARE_EXP = ['label start:', '    menu:', '        "新标题"',
            '        "开始":', '            e "a"', '    return']
NAMED = ('label start:\n'
         '    menu my_menu:\n'
         '        "开始":\n'
         '            e "a"\n'
         '    return\n')
NAMED_EXP = ['label start:', '    menu my_menu:', '        "新标题"',
             '        "开始":', '            e "a"', '    return']
CAP_THEN_EX = ('label start:\n'
               '    menu:\n'
               '        "旧标题"\n'
               '        set seen\n'
               '        "开始":\n'
               '            e "a"\n'
               '    return\n')
CAP_THEN_EX_EXP = ['label start:', '    menu:', '        "新标题"',
                   '        set seen', '        "开始":',
                   '            e "a"', '    return']


def main():
    orig = install_fix()
    try:
        print('=' * 68)
        print('§1 嵌套菜单加标题（修法1：_body_lines 剥 if 头）')
        print('=' * 68)
        ok, got, res = run(NEST, '新标题')
        check('嵌套形态落地且全文逐行正确', ok and got == NEST_EXP,
              f'ok={ok} 拒因={res.get("blocking")}\n期望={NEST_EXP}\n实际={got}')

        print()
        print('=' * 68)
        print('§2 extras 菜单加标题（修法2：extras 文件内逐字已有→原地不动）')
        print('=' * 68)
        ok, got, res = run(EXTRAS, '新标题')
        check('extras 形态落地且 extras 不动', ok and got == EXTRAS_EXP,
              f'ok={ok} 拒因={res.get("blocking")}\n期望={EXTRAS_EXP}\n实际={got}')
        ok, got, res = run(CAP_THEN_EX, '新标题')
        check('旧标题+extras 改标题（extras 不动）', ok and got == CAP_THEN_EX_EXP,
              f'ok={ok} 拒因={res.get("blocking")}\n期望={CAP_THEN_EX_EXP}\n实际={got}')

        print()
        print('=' * 68)
        print('§3 回归面：既有形态不许坏')
        print('=' * 68)
        ok, got, res = run(BARE, '新标题')
        check('裸 menu 加标题仍正确', ok and got == BARE_EXP,
              f'ok={ok} 拒因={res.get("blocking")}\n期望={BARE_EXP}\n实际={got}')
        ok, got, res = run(NAMED, '新标题')
        check('带名字 menu 加标题仍正确', ok and got == NAMED_EXP,
              f'ok={ok}\n期望={NAMED_EXP}\n实际={got}')
        ok, got, res = run(BARE, '')
        check('清空标题仍正确（标题消失）',
              ok and got == ['label start:', '    menu:', '        "开始":',
                             '            e "a"', '    return'],
              f'ok={ok} 实际={got}')
        ws = ('label start:\n    menu:\n        " "\n        "开始":\n'
              '            e "a"\n    return\n')
        ok, got, res = run(ws, ' ')
        check('空白标题不被误删',
              ok and got == body(ws),
              f'ok={ok} 期望={body(ws)} 实际={got}')
    finally:
        from storygraph.story import patcher as P
        P._replace_matched = orig

    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
