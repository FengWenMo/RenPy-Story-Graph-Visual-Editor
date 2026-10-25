# -*- coding: utf-8 -*-
"""第三轮审计探针 D：铁律 #6 ——「教它升级，不许改字节」。

教会自定义语法（`declare_statement` 落成 rules.json 声明）之后，
**首次导出必须与教会前逐字节相同**。学习只该让工具"多认得一行"，
不该让那一行被重新排版（参数顺序 / 引号 / 空格 / 块体归属）。

攻击形状（都是真机出现过的写法）：
  ① 位置参数 + 关键字：`mycue "闪电" intensity=3`
  ② 块型（冒号 + 块体）
  ③ 行尾注释
  ④ 异常空格：`mycue   "闪电"    intensity=3`
  ⑤ 单引号：`mycue '闪电' intensity=3`
  ⑥ 关键字顺序与声明顺序不同：`mycue intensity=3 layer=camera`
     （声明写 `layer str =camera intensity int =0` —— 顺序故意倒过来）

判据：同一章、同一拍内容，declare 前后各导一次，产物逐字节相同。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from storygraph.app.editor import EditorSession  # noqa: E402
from storygraph.story.patcher import patch_export  # noqa: E402
from storygraph.story.statement_registry import declare_statement  # noqa: E402

FAIL = []


def check(cond, msg):
    print(f'  [{"PASS" if cond else "FAIL"}] {msg}')
    if not cond:
        FAIL.append(msg)


def project(tmp, src):
    game = tmp / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    session = EditorSession(project_root=tmp)
    session.load(path)
    return session, path


def patched(session, path):
    res = session.compile()
    return patch_export(session.beats, path, res.rpy_text,
                        registry=session.registry(), symbols=session.symbols,
                        trust_ranges=True)


def case(tmp, name, stmt_line, decl):
    print(f'== {name} ==')
    src = (f'label start:\n'
           f'{stmt_line}\n'
           f'    e "台词"\n'
           f'    return\n')
    d = Path(tempfile.mkdtemp(dir=str(tmp)))
    try:
        # ① 教会前
        s1, p1 = project(d, src)
        before = patched(s1, p1)
        if not before.ok:
            check(False, f'{name}: 教会前补丁就失败了（{before.reasons[:1]}）')
            return
        b_text = before.text

        # ② 教会（落成 rules.json 声明）
        res = declare_statement(d, 'mycue', label='提示', **decl)
        print(f'  declare ok={res.get("ok")} action={res.get("action")}')
        if not res.get('ok'):
            check(False, f'{name}: declare_statement 失败 {res}')
            return

        # ③ 教会后（重新载入 → 声明生效）
        s2, p2 = project(d, src)
        after = patched(s2, p2)
        if not after.ok:
            check(False, f'{name}: 教会后补丁失败（{after.reasons[:1]}）')
            return
        a_text = after.text

        same = a_text == b_text
        print(f'  教会前产物行数={len(b_text.splitlines())} '
              f'教会后={len(a_text.splitlines())} 逐字节相同={same}')
        if not same:
            import difflib
            diff = difflib.unified_diff(b_text.splitlines(),
                                        a_text.splitlines(),
                                        '教会前', '教会后', lineterm='')
            for ln in list(diff)[:14]:
                print(f'    {ln}')
        check(same, f'{name}: 教会前后首次导出逐字节相同（铁律 6）')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sg_rule6_'))

    case(tmp, '① 位置参数+关键字', '    mycue "闪电" intensity=3',
         {'columns': [{'name': 'intensity', 'label': '强度',
                       'choices': ['1', '2', '3']}]})

    case(tmp, '② 块型（冒号+块体）',
         '    mycue "闪电" intensity=3:\n        target = ling\n'
         '        hold 0.5',
         {'block': True,
          'columns': [{'name': 'intensity', 'label': '强度',
                       'choices': ['1', '2', '3']}]})

    case(tmp, '③ 行尾注释', '    mycue "闪电" intensity=3   # 镜头提示',
         {'columns': [{'name': 'intensity', 'label': '强度',
                       'choices': ['1', '2', '3']}]})

    case(tmp, '④ 异常空格', '    mycue   "闪电"    intensity=3',
         {'columns': [{'name': 'intensity', 'label': '强度',
                       'choices': ['1', '2', '3']}]})

    case(tmp, '⑤ 单引号', "    mycue '闪电' intensity=3",
         {'columns': [{'name': 'intensity', 'label': '强度',
                       'choices': ['1', '2', '3']}]})

    # ⑥ 声明里关键字顺序与原文**故意不同**：渲染若按声明顺序重排就会现形
    case(tmp, '⑥ 关键字顺序与声明不同',
         '    mycue intensity=3 layer=camera',
         {'columns': [{'name': 'layer', 'label': '层'},
                      {'name': 'intensity', 'label': '强度'}]})

    print()
    if FAIL:
        print(f'命中 {len(FAIL)} 条：')
        for f in FAIL:
            print('  -', f)
        return 1
    print('全部形状通过：教会自定义语法不改一个字节。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
