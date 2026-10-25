"""P0 审计·第六轮·压力面：一个"难缠但完全合法"的工程 × 全部写入路径。

为什么（2026-10-25 第六轮）：`sr_general` 太干净了 —— 没有菜单块、没有
`if/elif/else`、没有 `call screen`、没有自定义语句、没有重复台词。
这些恰恰是"改写点最容易搞错"的形状。所以这里造一个**合法但难缠**的工程：

  · 中文 label、带点号的 label（`main.route`）
  · `menu` 三个选项（含 `if` 条件选项）、`if/elif/else`
  · `call screen` + `$` python + `pause` + `play music` + `with` 转场
  · 两句一模一样的台词（对位不许张冠李戴）
  · 行尾注释、行尾空格、TAB 缩进块、超长台词
  · CRLF 载体 + 编排层 `_control` + 已有选择点文件

判据一律**字节**：没碰过的行必须逐字节不变；撤销必须逐字节回到原样。
**这不是"跑通就行"的测试**，而是"错一个字节就算红"。

跑法： python tools/_p0_audit_round6_stress.py
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
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines()[:12]:
            print(f'       {line}')


CRLF = '\r\n'

CH01 = '''# 第一章：难缠形状合集
define jc = Character("甲", color="#c8ffc8")
default love = 0

label main.route:
    scene bg room with dissolve
    play music "bgm/theme.ogg" fadein 1.0
    jc "第一句。"
    jc "第二句。"    # 行尾注释要保住
    if love > 3:
        jc "好感够了。"
    elif love > 1:
        jc "一般般。"
    else:
        jc "还早。"
    menu:
        "去河边":
            $ love += 1
            jc "河边的风。"
        "回家" if love < 2:
            jc "回家吧。"
        "什么都不做":
            pass
    call screen phone_dialogue_log
    pause 0.5
    jc "同一句"
    jc "同一句"
    extend "补一句。"
    return
'''


CH02 = '''label ch02:
    jc "第二章开头。"
    jump back_to_main
    return
'''


CONTROLLER = '''label main.route_control:
    call main.route
    return

label ch02_control:
    call ch02
    return
'''


def build(tmp: Path):
    game = tmp / 'game'
    game.mkdir(parents=True)
    ch1 = game / 'ch01.rpy'
    ch1.write_bytes(CH01.replace('\n', CRLF).encode('utf-8'))
    ch2 = game / 'ch02.rpy'
    ch2.write_bytes(CH02.replace('\n', CRLF).encode('utf-8'))
    ctl = game / 'chapter_controller.rpy'
    ctl.write_bytes(CONTROLLER.replace('\n', CRLF).encode('utf-8'))
    (game / 'screens.rpy').write_bytes(
        ('screen phone_dialogue_log():' + CRLF +
         '    frame:' + CRLF +
         '        text "log"' + CRLF).encode('utf-8'))
    return ch1, ch2, ctl


def lines(b: bytes):
    return b.split(b'\n')


MARKER = '由剧本表自动生成'.encode('utf-8')


def _strip_marker(b: bytes) -> bytes:
    """去掉工具在**首次真实导出**时插的生成标记（2 行：标记 + 空行）。

    这是设计内的（护栏靠首行标记认自家产物），而且只加一次 ——
    零改动往返不加（2026-10-19 修 P1-1）。判据里把它剔掉再比。
    """
    rows = b.split(b'\n')
    if rows and MARKER in rows[0]:
        cut = 1
        while cut < len(rows) and not rows[cut].strip():
            cut += 1
        return b'\n'.join(rows[cut:])
    return b


def only_one_line_changed(before: bytes, after: bytes):
    a, b = lines(_strip_marker(before)), lines(_strip_marker(after))
    if len(a) != len(b):
        return False, f'行数变了 {len(a)} → {len(b)}'
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 1, f'变了 {len(diff)} 行：{diff[:6]}'


def main():
    from storygraph.app.editor import EditorSession
    from storygraph.story.control_route import (apply_control_route,
                                                plan_control_route)

    tmp = Path(tempfile.mkdtemp(prefix='sg_r6s_'))
    try:
        ch1, ch2, ctl = build(tmp)
        orig = ch1.read_bytes()

        # ---------- ① 零改动往返：一个字节都不许动 ----------
        s = EditorSession(project_root=tmp)
        n = s.load(ch1)
        ok, why = only_one_line_changed(orig, orig)
        check(f'① 载入这一章：{n} 拍（菜单块 / if 链都认出来了）', n >= 8, f'{n} 拍')
        res = s.export(ch1)
        check('① 零改动导出成功', res.get('ok'), res.get('blocking') or res.get('note'))
        check('① 零改动导出：盘上逐字节相同', ch1.read_bytes() == orig,
              f'{len(ch1.read_bytes())} vs {len(orig)} 字节')

        # ---------- ② 改一句台词：只许动那一行 ----------
        s2 = EditorSession(project_root=tmp)
        s2.load(ch1)
        texts = [b.text for b in s2.beats]
        idx = next(i for i, t in enumerate(texts) if t.strip() == '第二句。')
        s2.apply(idx, {'Text': '第二句（改过）。'})
        res = s2.export(ch1)
        after = ch1.read_bytes()
        check('② 改一句：导出成功', res.get('ok'),
              res.get('blocking') or res.get('note'))
        ok, why = only_one_line_changed(orig, after)
        check('② 改一句：除那一行外逐字节不变（CRLF 也没动）', ok, why)
        check('② 那一行的行尾注释还在', '行尾注释要保住'.encode() in after)

        # ---------- ③ 重复台词：改第二句不许动第一句 ----------
        before3 = ch1.read_bytes()
        s3 = EditorSession(project_root=tmp)
        s3.load(ch1)
        dup = [i for i, b in enumerate(s3.beats) if b.text.strip() == '同一句']
        check('③ 两句一模一样的台词都认出来了', len(dup) == 2, f'{dup}')
        if len(dup) == 2:
            s3.apply(dup[1], {'Text': '同一句（后面那句）'})
            res = s3.export(ch1)
            after = ch1.read_bytes()
            ok, why = only_one_line_changed(before3, after)
            check('③ 改第二句只动了那一行', ok and res.get('ok'), why)

        # ---------- ④ 撤销：混合换行文件里必须逐字节回到原样 ----------
        # `graph/commands.py` 的 undo 就是这三步：读当前 → 比 new_text →
        # `style.apply(old_text, source=path)` 写回。这里跑同一组调用
        # （不搭 Qt 的 graph_view），而且故意用**混合换行**的控制流文件 ——
        # 从前 `apply(old_text)` 不传 source，会把整份折成一种换行。
        from storygraph.story.paths import write_text_atomic
        from storygraph.story.source_style import detect as _detect

        mixed = (b'label a_control://r//n    call a\r\n    return\r\n'
                 b'label b_control://n    call b\n    return\n')
        mixp = tmp / 'game' / 'mixed_ctl.rpy'
        mixp.write_bytes(mixed)
        style = _detect(mixp)
        touched = mixed.replace(b'    return\r\nlabel b', b'    x = 1\r\nlabel b')
        write_text_atomic(mixp, style.apply(touched.decode('utf-8'),
                                           source=mixp))
        wrote = mixp.read_bytes()
        # 撤销：把原文按**当前盘上那一份**逐行还原写回
        write_text_atomic(mixp, _detect(mixp).apply(mixed.decode('utf-8'),
                                                    source=mixp))
        back = mixp.read_bytes()
        check('④ 混合换行文件：改一行再撤销 → 逐字节回到原样', back == mixed,
              f'改后变了={wrote != mixed}；撤销后相同={back == mixed}\n'
              f'before={mixed!r}\nback  ={back!r}')

        # ---------- ⑤ 编排层接线：只多加一行 ----------
        ctl_orig = ctl.read_bytes()
        plan = plan_control_route(tmp, 'main.route', 'ch02', 'jump')
        check('⑤ 计划成功（点号 label 能认）', plan.ok,
              plan.blocking or plan.reason)
        if plan.ok:
            res = apply_control_route(plan)
            check('⑤ 写入成功', res.get('ok'), res.get('error'))
            after = ctl.read_bytes()
            lost = [l for l in ctl_orig.split(b'\r\n')
                    if l and l not in after.split(b'\r\n')]
            check('⑤ 原有行逐字节还在', not lost, f'丢={lost}')
            check('⑤ 行尾仍是 CRLF', after.count(b'\n') == after.count(b'\r\n'),
                  f"LF={after.count(b'\n')} CRLF={after.count(b'\r\n')}")

        # ---------- ⑥ 选择点：追加，不动已有行 ----------
        from storygraph.story.choice_point import (apply_choice_point,
                                                  plan_choice_point)
        cp = plan_choice_point(tmp, ch1, 'main.route', [
            {'text': '选项一', 'goto': 'ch02'},
            {'text': '选项二', 'goto': ''},
        ])
        if not cp.ok:
            check('⑥ 选择点计划', False, cp.blocking or cp.reason)
        else:
            target = Path(cp.path)
            before_ch = ch1.read_bytes()
            res = apply_choice_point(cp, project_root=tmp)
            check('⑥ 选择点写入成功', res.get('ok'), res.get('error'))
            check('⑥ 剧本文件一个字节都没动（选择点住自己的文件）',
                  ch1.read_bytes() == before_ch)
            if target.exists():
                txt = target.read_bytes()
                check('⑥ 选择点文件是 CRLF（跟随剧本）',
                      txt.count(b'\n') == txt.count(b'\r\n'),
                      f"LF={txt.count(b'\n')} CRLF={txt.count(b'\r\n')}")

        # ---------- ⑦ 连续两次原地导出：改 A → 导出 → 改 B → 导出 ----------
        # 这是**真实工作流**（改一句、看效果、再改一句），而且历史上断过
        # （第一次导出后内存里的行号过期 → 第二次对位率掉到 0）。
        # 判据：两次改动都在，且除这两行外其余逐字节不变（含 CRLF / 菜单 / if 链）。
        s7 = EditorSession(project_root=tmp)
        s7.load(ch1)
        before7 = ch1.read_bytes()
        i7 = next(i for i, b in enumerate(s7.beats) if '第一句' in b.text)
        s7.apply(i7, {'Text': '第一句（第一次改）'})
        r1 = s7.export(ch1)
        s7b = EditorSession(project_root=tmp)
        s7b.load(ch1)
        i7b = next(i for i, b in enumerate(s7b.beats) if '河边的风' in b.text)
        s7b.apply(i7b, {'Text': '河边的风（第二次改）'})
        r2 = s7b.export(ch1)
        after7 = ch1.read_bytes()
        a_rows, b_rows = lines(_strip_marker(before7)), lines(_strip_marker(after7))
        diff = [i for i in range(min(len(a_rows), len(b_rows)))
                if a_rows[i] != b_rows[i]]
        check('⑦ 连续两次原地导出：两次改动都还在', r1.get('ok') and r2.get('ok'),
              f'r1={r1.get("blocking") or r1.get("note")}；'
              f'r2={r2.get("blocking") or r2.get("note")}')
        check('⑦ 第二次导出只多动那一行（其余逐字节不变）',
              len(a_rows) == len(b_rows) and len(diff) == 2,
              f'行数 {len(a_rows)}→{len(b_rows)}；变了 {len(diff)} 行：{diff}')
        check('⑦ CRLF 仍是 CRLF',
              after7.count(b'\n') == after7.count(b'\r\n'))

        ok_n = sum(1 for ok, _ in results if ok)
        print(f'\n汇总：{ok_n}/{len(results)} 通过')
        return 0 if ok_n == len(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
