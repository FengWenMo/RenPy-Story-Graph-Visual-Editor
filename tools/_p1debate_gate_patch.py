# -*- coding: utf-8 -*-
"""
P1 讨论探针（把关派）· ①②③ 补丁面
====================================
只读真机 + 合成样本副本，不动原文件。每条打印：动作 → 结果 → 产物 diff 摘要。

  ① 无标题 menu 加标题（从无到有）—— 合成 + 真机 script_pomo.rpy 两个 menu
  ② 移动拍 —— 真机上逐对移动的通过/拒绝统计 + 拒因归类
  ③ 改 if 条件 —— 单层 if / 链头 / elif / 嵌套 / 清空

跑法：/d/Python/python tools/_p1debate_gate_patch.py
"""
from __future__ import annotations

import difflib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.app.editor import EditorSession          # noqa: E402

MARKER = '# 由剧本表自动生成'
POMO = Path(r'D:\千连万理\RenPyTest\sr_general\game\pomodorro\script_pomo.rpy')


def _body(text: str) -> list:
    out, skip = [], False
    for ln in text.splitlines():
        if ln.startswith(MARKER):
            skip = True
            continue
        if skip and not ln.strip():
            skip = False
            continue
        out.append(ln)
    return out


def _read(path):
    return open(path, encoding='utf-8', newline='').read()


def _project(src: str, rules=None):
    tmp = Path(tempfile.mkdtemp(prefix='p1gate_'))
    game = tmp / 'game'
    game.mkdir()
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    if rules is not None:
        import json
        (tmp / 'storygraph.rules.json').write_text(
            json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    return tmp, path


def _submit(session, index, **changes):
    fields = session.fields(index)
    fields.update(changes)
    return session.apply(index, fields, cards=session.cards(index),
                         advanced_open=True, stage_loaded=fields.get('Stage'))


def _patched(session, path):
    from storygraph.story.patcher import patch_export
    res = session.compile()
    return patch_export(session.beats, path, res.rpy_text,
                        registry=session.registry(), symbols=session.symbols,
                        trust_ranges=True)


def _diff(before: list, after: list) -> str:
    return ''.join(difflib.unified_diff(before, after, 'before', 'after', n=1))


def _idx_of(session, text):
    return next(i for i, b in enumerate(session.beats)
                if (b.text or '').strip() == text)


# ======================================================================
# ① 无标题 menu 加标题（从无到有）
# ======================================================================
def probe_caption():
    print('=' * 70)
    print('① 无标题 menu 加标题（从无到有）')
    print('=' * 70)

    # —— A 合成：纯无标题 ——
    src = ('label start:\n'
           '    menu:\n'
           '        "开始":\n'
           '            e "a"\n'
           '        "结束":\n'
           '            e "b"\n'
           '    return\n')
    tmp, path = _project(src)
    s = EditorSession(project_root=tmp)
    s.load(path)
    _submit(s, 0, Text='新标题')
    p = _patched(s, path)
    ok = p.ok and '新标题' in p.text
    print(f'[{"PASS" if ok else "FAIL"}] A 合成无标题→加标题: ok={p.ok} '
          f'changed={p.changed_lines} reasons={p.reasons}')
    if p.ok:
        rows = _body(p.text)
        print(f'       产物前 4 行: {rows[:4]}')

    # —— B 合成：无标题 + extras（set seen）——
    src2 = ('label start:\n'
            '    menu:\n'
            '        set seen\n'
            '        "开始":\n'
            '            e "a"\n'
            '    return\n')
    tmp2, path2 = _project(src2)
    s2 = EditorSession(project_root=tmp2)
    s2.load(path2)
    _submit(s2, 0, Text='带extras')
    p2 = _patched(s2, path2)
    ok2 = p2.ok and '带extras' in p2.text
    print(f'[{"PASS" if ok2 else "FAIL"}] B 无标题+extras(set seen): ok={p2.ok} '
          f'changed={p2.changed_lines} reasons={p2.reasons}')
    if p2.ok:
        print(f'       产物: {_body(p2.text)}')

    # —— C 合成：无标题 + 条件选项 ——
    src3 = ('label start:\n'
            '    if flag:\n'
            '        menu:\n'
            '            "有条件" if flag:\n'
            '                e "a"\n'
            '            "无条件":\n'
            '                e "b"\n'
            '    return\n')
    tmp3, path3 = _project(src3)
    s3 = EditorSession(project_root=tmp3)
    s3.load(path3)
    idx = next(i for i, b in enumerate(s3.beats)
               if (getattr(b, 'kind', '') or '') == 'menu')
    _submit(s3, idx, Text='嵌套菜单标题')
    p3 = _patched(s3, path3)
    ok3 = p3.ok and '嵌套菜单标题' in p3.text
    print(f'[{"PASS" if ok3 else "FAIL"}] C if 内无标题菜单加标题: ok={p3.ok} '
          f'changed={p3.changed_lines} reasons={p3.reasons}')

    # —— D 真机 script_pomo.rpy 两个 menu ——
    if POMO.exists():
        tmp4 = Path(tempfile.mkdtemp(prefix='p1gate_real_'))
        game = tmp4 / 'game'
        game.mkdir()
        dst = game / POMO.name
        shutil.copyfile(POMO, dst)
        before = _read(dst)
        s4 = EditorSession(project_root=tmp4)
        s4.load(dst)
        menu_ids = [i for i, b in enumerate(s4.beats)
                    if (getattr(b, 'kind', '') or '') == 'menu']
        print(f'       真机 menu 拍下标: {menu_ids}')
        for mi in menu_ids:
            b = s4.beats[mi]
            old = (b.text or '')[:20]
            _submit(s4, mi, Text=f'标题X{mi}')
            p4 = _patched(s4, dst)
            landed = p4.ok and f'标题X{mi}' in p4.text
            others = all(ln.strip() == new.strip()
                         for ln, new in zip(_body(before), _body(p4.text))
                         if '标题' not in new)
            print(f'[{"PASS" if landed else "FAIL"}] D 真机 menu#{mi} '
                  f'(原标题={old!r}): ok={p4.ok} changed={p4.changed_lines} '
                  f'reasons={p4.reasons}')
            if p4.ok:
                d = _diff(_body(before), _body(p4.text))
                print('       diff:')
                for ln in d.splitlines()[:12]:
                    print('        ', ln)
            # 还原会话再测下一个
            s4 = EditorSession(project_root=tmp4)
            s4.load(dst)
        # —— E 真机：那个 " " 空白标题的菜单（205 行）改成真标题 ——
        s5 = EditorSession(project_root=tmp4)
        s5.load(dst)
        for mi in [i for i, b in enumerate(s5.beats)
                   if (getattr(b, 'kind', '') or '') == 'menu']:
            b = s5.beats[mi]
            if (b.text or '').strip() in ('', ' '):
                _submit(s5, mi, Text='真标题')
                p5 = _patched(s5, dst)
                landed = p5.ok and '真标题' in p5.text
                print(f'[{"PASS" if landed else "FAIL"}] E 真机空白标题menu#{mi}'
                      f'→真标题: ok={p5.ok} changed={p5.changed_lines} '
                      f'reasons={p5.reasons}')
                if p5.ok:
                    for ln in _diff(_body(before), _body(p5.text)).splitlines()[:12]:
                        print('        ', ln)
                break
    else:
        print('[SKIP] 真机项目不在')


# ======================================================================
# ② 移动拍 —— 真机统计
# ======================================================================
def probe_move():
    print('=' * 70)
    print('② 移动拍（真机复杂文件）')
    print('=' * 70)
    if not POMO.exists():
        print('[SKIP] 真机项目不在')
        return
    tmp = Path(tempfile.mkdtemp(prefix='p1gate_move_'))
    game = tmp / 'game'
    game.mkdir()
    dst = game / POMO.name
    shutil.copyfile(POMO, dst)

    accepted, refused = [], {}
    n = 0
    s = EditorSession(project_root=tmp)
    s.load(dst)
    total = len(s.beats)
    print(f'       真机拍数: {total}')
    # 采样移动：每个拍试一次移到下一个相邻位、以及跳 3 位
    for src in range(total - 1):
        for delta in (1, 3):
            dst_i = src + delta
            if dst_i >= total:
                continue
            s = EditorSession(project_root=tmp)
            s.load(dst)
            mv = s.move_beat(src, dst_i)
            n += 1
            if not mv.get('ok'):
                key = str(mv.get('error'))[:60]
                refused.setdefault(f'移动被拦(结构): {key}', []).append(
                    (src, dst_i))
                continue
            p = _patched(s, dst)
            if p.ok:
                accepted.append((src, dst_i, p.changed_lines))
            else:
                rs = ' | '.join(str(r)[:70] for r in p.reasons)
                refused.setdefault(rs, []).append((src, dst_i))
    print(f'       试了 {n} 种移动：补丁成功 {len(accepted)}，'
          f'拒绝 {n - len(accepted)}')
    for reason, cases in sorted(refused.items(), key=lambda kv: -len(kv[1])):
        print(f'       [{len(cases):3d} 次] {reason}')
        print(f'              例: {cases[:4]}')
    if accepted:
        print(f'       成功例: {accepted[:8]}')

    # —— 审计点名的那一刀：把 elif 支的台词挪出分支 ——
    s = EditorSession(project_root=tmp)
    s.load(dst)
    for i, b in enumerate(s.beats):
        if (b.text or '').strip().startswith('25分钟定时结束'):
            for j in range(i + 1, min(i + 4, total)):
                s2 = EditorSession(project_root=tmp)
                s2.load(dst)
                mv = s2.move_beat(i, j)
                if not mv.get('ok'):
                    print(f'       elif 支台词 {i}->{j}: 移动被拦 '
                          f'{str(mv.get("error"))[:70]}')
                    continue
                p = _patched(s2, dst)
                told = ' | '.join(str(r)[:80] for r in p.reasons)
                print(f'       elif 支台词 {i}->{j}: ok={p.ok} '
                      f'理由={told or "(无)"}')
            break


# ======================================================================
# ③ 改 if 条件
# ======================================================================
def probe_condition():
    print('=' * 70)
    print('③ 改 if 条件')
    print('=' * 70)
    cases = [
        ('单层 if 条件',
         'label start:\n    $ flag = True\n    if flag:\n'
         '        e "在里面"\n    return\n',
         lambda s: _submit(s, _idx_of(s, '在里面'), condition='not flag')),
        ('链头（elif 链）条件',
         'label start:\n    if a == 1:\n        e "甲"\n'
         '    elif a == 2:\n        e "乙"\n    else:\n        e "丙"\n'
         '    return\n',
         lambda s: s.apply(_idx_of(s, '甲'), {'condition': 'a == 9'})),
        ('elif 支条件',
         'label start:\n    if a == 1:\n        e "甲"\n'
         '    elif a == 2:\n        e "乙"\n    else:\n        e "丙"\n'
         '    return\n',
         lambda s: s.apply(_idx_of(s, '乙'), {'condition': 'a == 9'})),
        ('嵌套 if 条件',
         'label start:\n    if a:\n        if b:\n            e "里面"\n'
         '    return\n',
         lambda s: s.apply(_idx_of(s, '里面'), {'condition': 'c'})),
        ('清空条件',
         'label start:\n    $ flag = True\n    if flag:\n'
         '        e "在里面"\n    return\n',
         lambda s: s.apply(_idx_of(s, '在里面'), {'condition': ''})),
    ]
    for name, src, edit in cases:
        tmp, path = _project(src)
        s = EditorSession(project_root=tmp)
        s.load(path)
        edit(s)
        before = _read(path)
        res = s.export(path)
        after = _read(path)
        moved = before != after
        if res.get('ok'):
            d = _diff(_body(before), _body(after))
            print(f'[PASS] {name}: 导出成功，改动 {"；".join(d.splitlines()[:6])}')
        else:
            told = ' | '.join(str(x)[:90] for x in (res.get('blocking') or []))
            same = '(文件未动)' if not moved else '(⚠ 文件被改了!)'
            print(f'[REJ]  {name}: 被拒 {same}')
            print(f'       理由: {told}')

    # —— 真机：breakline 条件那一拍 ——
    if POMO.exists():
        tmp = Path(tempfile.mkdtemp(prefix='p1gate_cond_'))
        game = tmp / 'game'
        game.mkdir()
        dst = game / POMO.name
        shutil.copyfile(POMO, dst)
        before = _read(dst)
        s = EditorSession(project_root=tmp)
        s.load(dst)
        for i, b in enumerate(s.beats):
            if (b.text or '').strip().startswith('时间到了'):
                s.apply(i, {'condition': 'breakline == 9'})
                res = s.export(dst)
                after = _read(dst)
                told = ' | '.join(str(x)[:90] for x in (res.get('blocking') or []))
                state = '成功' if res.get('ok') else '被拒'
                print(f'[{state}] 真机 elif 支台词改条件 (拍{i}): '
                      f'文件{"变了" if before != after else "未动"}')
                if not res.get('ok'):
                    print(f'       理由: {told}')
                else:
                    for ln in _diff(_body(before), _body(after)).splitlines()[:10]:
                        print('        ', ln)
                break


if __name__ == '__main__':
    probe_caption()
    print()
    probe_move()
    print()
    probe_condition()
    print()
    print('探针跑完（临时目录未清理：用完自行删除 %TEMP%\\p1gate_*）')
