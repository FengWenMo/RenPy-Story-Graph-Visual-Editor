"""
tools/_multiagent_probe_menu_brief_evidence.py
==============================================
menu_brief 审计的**证据存档**：把失败用例的真实拍形态 dump 出来
（期望 vs 实际之外，还要能指着 raw/nest 说清"上游到底给了什么"）。

跑法：

    /d/Python/python tools/_multiagent_probe_menu_brief_evidence.py

看三件事：
  E1 点号 menu 名（`menu main.route:`）解析后到底有没有 menu 拍、
     选项去哪了（怀疑 `renpy_parser._RE_MENU_NAME` 只吃一段）。
  E2 `jump screen_map` / `call screenplay` 的拍 raw 长什么样
     （确认是 menu_brief 的 `startswith('screen')` 误杀，不是上游丢拍）。
  E3 `call ch02(x, y) from _call_ch02_1` 的 raw（确认 target 拼进参数/from）。
  E4 menu_brief_text 在 27 个选项时的行号键（chr(65+i) vs option_key）。
  E5 观察：选项体里 return / call screen 的显示归类。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.flow import menu_brief, menu_brief_text  # noqa: E402


def parse_beats(tmp: Path, name: str, script: str):
    fp = tmp / f'{name}.rpy'
    fp.write_text(script, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    return beats_from_parse_result(pr)


def dump(beats):
    rows = []
    for b in beats:
        rows.append(
            f"seq={getattr(b, 'seq', '?')} kind={getattr(b, 'kind', '')!r} "
            f"nest={getattr(b, 'nest_path', None)!r} "
            f"menu_name={getattr(b, 'menu_name', '')!r} "
            f"text={getattr(b, 'text', '')!r} raw={getattr(b, 'raw', '')!r} "
            f"opaque={getattr(b, 'opaque', '')!r}")
    return '\n'.join(rows) or '（没有拍）'


def main():
    tmp = Path(tempfile.mkdtemp(prefix='probe_mb_ev_'))
    try:
        print('===== E1 点号 menu 名 =====')
        s_dot = '''label main:
    menu main.route:
        "点号选":
            jump ch02
        "再选":
            jump ch03
    e "菜单之后"
'''
        beats = parse_beats(tmp, 'dot', s_dot)
        print(dump(beats))
        print('menu_brief(beats, "main.route") =',
              repr(menu_brief(beats, 'main.route')))
        print('menu_brief(beats, "main") =', repr(menu_brief(beats, 'main')))

        print('\n===== E2 screen 开头的 label 名 =====')
        s_screen = '''label main:
    menu choice_b:
        "看日志":
            call screen phone_log
        "去地图":
            jump screen_map
        "剧本组":
            call screenplay
'''
        beats = parse_beats(tmp, 'screen', s_screen)
        print(dump(beats))
        print('menu_brief(beats, "choice_b") =',
              repr(menu_brief(beats, 'choice_b')))

        print('\n===== E3 call 带参数与 from =====')
        s_args = '''label main:
    menu choice_c:
        "求助":
            call ch02(x, y) from _call_ch02_1
'''
        beats = parse_beats(tmp, 'args', s_args)
        print(dump(beats))
        print('menu_brief(beats, "choice_c") =',
              repr(menu_brief(beats, 'choice_c')))

        print('\n===== E4 menu_brief_text 27 选项的行号键 =====')
        s_many = 'label many:\n    menu many:\n'
        for i in range(1, 28):
            if i == 27:
                s_many += '        "二十七":\n            jump ch27\n'
            else:
                s_many += f'        "选{i}":\n            jump g{i}\n'
        beats = parse_beats(tmp, 'many', s_many)
        brief = menu_brief(beats, 'many')
        text = menu_brief_text(brief)
        print('count =', brief.get('count'))
        print('末 3 行：')
        for line in text.splitlines()[-3:]:
            print('   ', line)
        print('第 1 行选项的键（应为 A）：', text.splitlines()[1][:6])

        print('\n===== E5 观察：return / call screen 的显示归类 =====')
        s_ret = '''label main:
    menu choice_r:
        "回去":
            return
        "看屏":
            call screen phone
'''
        beats = parse_beats(tmp, 'ret', s_ret)
        brief = menu_brief(beats, 'choice_r')
        print('brief =', repr(brief))
        print(menu_brief_text(brief))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
