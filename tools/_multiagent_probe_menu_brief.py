"""
tools/_multiagent_probe_menu_brief.py
=====================================
对抗探针：`storygraph.story.flow.menu_brief` / `menu_brief_text`
（2026-10-26 多智能体测试·审计重点：未提交新函数，还没接线、没判据）。

跑法（必须用这个解释器）：

    /d/Python/python tools/_multiagent_probe_menu_brief.py

结构：

  §0 约定自证 —— 拿**真实解析器**产的 beats 验证 `nest_path` DSL
     （`core/nest_path.py` 的 join_nest/option_key）在真机上成立。
     这一段是**探针自己的判据地基**：它红了 = 探针期望写错了，
     后面所有"期望"都不可信，必须先修探针。
  §1 menu_brief 行为判据 —— 真实 .rpy → 真实 beats → 与**构造的期望全文**比。
     这一段红 = 产品缺陷（每条都注明依据哪条契约）。
  §2 边界与行口径 —— mock rows（nest 用 §0 自证过的同一套约定构造）
     + 行边界（P0-A：splitlines 认 U+2028，Ren'Py 只认 \\n）。
  §3 menu_brief_text 输出全文。

判据纪律（第五/六轮教训）：
  · 期望一律**构造完整期望 dict/全文**，不做同索引 diff；
  · 期望依据写在每条 check 的 docstring 里（契约出处）；
  · 观察性用例（无法定对错的）只 dump 不 check，报告里如实写。

退出码：0 = 全绿；1 = 有红（§0 红 = 探针自身要修；§1/§2/§3 红 = 产品缺陷）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.core.nest_path import join_nest, option_key, split_nest  # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402
from storygraph.story.beat_build import beats_from_parse_result  # noqa: E402
from storygraph.story.flow import (  # noqa: E402
    menu_brief, menu_brief_text, parse_flow,
)

PASS, FAIL = '[PASS]', '[FAIL]'
results: list = []


def check(name, expected, actual, why=''):
    ok = expected == actual
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if why:
        print(f'       依据: {why}')
    if not ok:
        print(f'       期望: {expected!r}')
        print(f'       实际: {actual!r}')
    return ok


def note(name, detail):
    print(f'[观察] {name}')
    for line in str(detail).splitlines()[:14]:
        print(f'       {line}')


# ==================================================================
#  真实 .rpy → 真实 beats（绝不 mock 一套 nest）
# ==================================================================

def parse_beats(tmp: Path, name: str, script: str, newline='\n'):
    """写 .rpy（可指定换行）→ 解析 → 折拍。返回 beats 列表。"""
    fp = tmp / f'{name}.rpy'
    fp.write_bytes(script.replace('\n', newline).encode('utf-8'))
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    return beats_from_parse_result(pr)


def dump(beats):
    rows = []
    for b in beats:
        rows.append(
            f"seq={getattr(b, 'seq', '?')} kind={getattr(b, 'kind', '')!r} "
            f"label={getattr(b, 'label', '')!r} "
            f"nest={getattr(b, 'nest_path', None)!r} "
            f"menu_name={getattr(b, 'menu_name', '')!r} "
            f"text={getattr(b, 'text', '')!r} raw={getattr(b, 'raw', '')!r} "
            f"cond={getattr(b, 'condition', '')!r}")
    return '\n'.join(rows)


def opt_view(brief):
    """brief → 只留（text, type, target）三元组列表，方便构造期望。"""
    return [(o.get('text'), o.get('type'), o.get('target'))
            for o in (brief or {}).get('options') or ()]


# ==================================================================
#  §0 约定自证：真实解析器的 nest_path 与 core/nest_path.py 一致
# ==================================================================

S0 = '''label main:
    menu outer:
        "进内层":
            menu inner:
                "内选":
                    jump deep
            jump after_inner
        "空的":
        "有内容":
            if flag:
                jump yes
            else:
                jump no
    if other:
        menu in_if:
            "条件选":
                jump inif
'''

S0_MANY = 'label many:\n    menu many:\n'
for _i in range(1, 28):
    if _i == 1:
        S0_MANY += '        "空一":\n'
    elif _i == 27:
        S0_MANY += '        "二十七":\n            jump ch27\n'
    else:
        S0_MANY += f'        "选{_i}":\n            pass\n'


def section0(tmp: Path):
    print('\n===== §0 约定自证（真实解析器 vs core/nest_path.py DSL）=====')
    beats = parse_beats(tmp, 's0', S0)
    note('S0 拍清单', dump(beats))

    # 契约（core/nest_path.py 模块 docstring）：
    #   menu 头 nest = 父块路径（label 顶层 = ''）
    #   选项行     = join_nest(父, f'menu:{N}')   —— N 按 label 内文档序
    #   选项体直句 = join_nest(选项行路径, option_key(i))，i 为 1-based 选项位
    #   嵌套块继续 join（if/menu 各自计数、整个 label 共用 counters）
    menus = [b for b in beats if getattr(b, 'kind', '') == 'menu']
    opts = [b for b in beats if getattr(b, 'kind', '') == 'menu_option']

    check('§0.1 三个 menu 头（outer/inner/in_if）都落拍',
          ['outer', 'inner', 'in_if'],
          [str(getattr(b, 'menu_name', '') or '') for b in menus],
          'beat_build._emit_menu：MENU 语句一律产出')

    by_nest = {str(getattr(b, 'nest_path', '') or ''): b for b in beats}

    check('§0.2 顶层 menu 头 nest == ""',
          '', str(getattr(menus[0], 'nest_path', '') or ''),
          'menu_brief docstring：menu 头 nest=（空）——只对顶层 menu 成立')

    check('§0.3 嵌套 menu 头（内层）nest == "menu:1>A"（不是 ""！）',
          'menu:1>A', str(getattr(menus[1], 'nest_path', '') or ''),
          'add_stmt(path=None) 转发 box.nest_path；内层 menu 住在外层选项体里')

    check('§0.4 if 里的 menu 头 nest == "if:3"（不是 ""！）',
          'if:3', str(getattr(menus[2], 'nest_path', '') or ''),
          '同上 —— menu_brief 假设"menu 头 nest=只对顶层成立"')

    # 选项行 = join_nest(父, menu:N)：outer 的选项在 'menu:1'
    outer_opts = [b for b in opts
                  if str(getattr(b, 'nest_path', '') or '') == 'menu:1']
    check('§0.5 outer 三个选项行都在 "menu:1"',
          ['进内层', '空的', '有内容'],
          [str(getattr(b, 'text', '') or '') for b in outer_opts],
          '选项行路径到菜单为止（renpy_parser._w_menu）')

    # 选项体直句 = join_nest(block, option_key(i))：i=1 → A
    check('§0.6 选项体直句路径 = menu:1>option_key(i)',
          ['menu:1>A', 'menu:1>A', 'menu:1>B>if:1', 'menu:1>B>if:2'],
          [join_nest('menu:1', option_key(1))] * 2
          + [join_nest(join_nest('menu:1', option_key(2)), 'if:1'),
             join_nest(join_nest('menu:1', option_key(2)), 'if:2')],
          'join_nest + option_key 正向拼 —— 期望值独立于被测代码算出')

    deep = by_nest.get('menu:1>A>menu:2>A')
    check('§0.7 内层选项体（jump deep）路径 = menu:1>A>menu:2>A',
          'menu:1>A>menu:2>A',
          str(getattr(deep, 'nest_path', '') if deep else None),
          '内层 menu:N 计数在 label 内继续（counters 共用）')

    after = by_nest.get('menu:1>A')
    check('§0.8 外层选项体直句 jump after_inner 就在 "menu:1>A"（一层）',
          'jump after_inner', str(getattr(after, 'raw', '') if after else None),
          '直句 = 路径恰好等于选项体键；嵌套块更深')

    inif = [b for b in beats
            if str(getattr(b, 'raw', '') or '') in ('jump inif',)]
    check('§0.9 if 里 menu 的选项体 = "if:3>menu:3>A"',
          ['if:3>menu:3>A'],
          [str(getattr(b, 'nest_path', '') or '') for b in inif],
          'counters 按 label 连续：if:3 里的 menu 是本 label 第 3 个 menu')

    # 27 选项的位置键：option_key(27) == 'A27'（core 的明文约定）
    beats27 = parse_beats(tmp, 's0_many', S0_MANY)
    bodies27 = [str(getattr(b, 'nest_path', '') or '')
                for b in beats27
                if str(getattr(b, 'raw', '') or '') == 'jump ch27']
    check('§0.10 第 27 个选项体的键是 A27（不是 [ ）',
          ['menu:1>A27'], bodies27,
          'option_key：1..26 → A..Z，27 起 → A27（不许撞键）')
    return beats, beats27


# ==================================================================
#  §1 menu_brief 行为判据（真实 beats）
# ==================================================================

S_BASIC = '''label main:
    menu choice_a:
        "去河边":
            jump ch02
        "回家":
            return
        "站着":
            pass
    call screen phone_log
'''

S_SCREEN = '''label main:
    menu choice_b:
        "看日志":
            call screen phone_log
        "去地图":
            jump screen_map
        "剧本组":
            call screenplay
        "正经跳":
            jump ch02
'''

S_ARGS = '''label main:
    menu choice_c:
        "求助":
            call ch02(x, y) from _call_ch02_1
        "直跳":
            jump ch03
'''

S_EMPTY = '''label main:
    menu choice_d:
        "空的":
        "只算数":
            $ x = 1
        "补一句":
            extend "接上"
        "真跳":
            jump ch02
'''

S_NVL = '''label main:
    menu choice_e (nvl=True):
        "选一":
            jump ch02
'''

S_NESTED = '''label main:
    menu outer:
        "进内层":
            menu inner:
                "内选":
                    jump deep
        "走人":
            jump ch02
'''

S_NESTED_DIRECT = '''label main:
    menu outer2:
        "两层都要":
            menu inner2:
                "内选":
                    jump deep2
            jump after2
'''

S_IN_IF = '''label main:
    if flag:
        menu cond_menu:
            "条件选":
                jump ch02
    else:
        menu other_menu:
            "别的":
                jump ch03
'''

S_COND_OPT = '''label main:
    menu choice_f:
        "回家" if love < 2:
            jump home
        "去河边":
            jump river
'''

S_DOT = '''label main:
    menu main.route:
        "点号选":
            jump ch02
'''

S_JUMP_TO_MENU = '''label opener:
    e "开始"
    jump choice_a

label target:
    menu choice_a:
        "选一":
            jump ch02
'''

S_DUP = '''label a:
    menu twin:
        "甲一":
            jump a1

label b:
    menu twin:
        "乙一":
            jump b1
'''

S_BARE = '''label main:
    menu:
        "裸选":
            jump ch02
'''

S_IF_IN_OPT = '''label main:
    menu choice_h:
        "看情况":
            if flag:
                jump yes
            else:
                jump no
'''


def section1(tmp: Path):
    print('\n===== §1 menu_brief 行为判据（真实 .rpy → 真实 beats）=====')

    # ---- 基本形状（契约 docstring 的返回样例）----
    beats = parse_beats(tmp, 'basic', S_BASIC)
    check('§1.1 基本 menu：jump 认、return/pass 不算跳转',
          {'name': 'choice_a', 'count': 3,
           'options': [{'text': '去河边', 'type': 'jump', 'target': 'ch02'},
                       {'text': '回家', 'type': '', 'target': ''},
                       {'text': '站着', 'type': '', 'target': ''}]},
          menu_brief(beats, 'choice_a'),
          '契约：只认 jump/call；返回值全文构造比对')

    # ---- call screen 不算；screen 开头的 label 名不许误杀 ----
    beats = parse_beats(tmp, 'screen', S_SCREEN)
    check('§1.2 call screen 不算跳转 + jump screen_map / call screenplay 不许误杀',
          {'name': 'choice_b', 'count': 4,
           'options': [{'text': '看日志', 'type': '', 'target': ''},
                       {'text': '去地图', 'type': 'jump', 'target': 'screen_map'},
                       {'text': '剧本组', 'type': 'call', 'target': 'screenplay'},
                       {'text': '正经跳', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'choice_b'),
          '契约：call screen 不算（界面调用）；'
          '但 jump/call 到 screen 开头的真 label 是合法跳转，不许连坐')

    # ---- call 带参数/from：target 只该是 label（与 parse_flow_line 同口径）----
    beats = parse_beats(tmp, 'args', S_ARGS)
    check('§1.3 call 带参数与 from：target 只有 label 名',
          {'name': 'choice_c', 'count': 2,
           'options': [{'text': '求助', 'type': 'call', 'target': 'ch02'},
                       {'text': '直跳', 'type': 'jump', 'target': 'ch03'}]},
          menu_brief(beats, 'choice_c'),
          'parse_flow_line 同概念同口径：target/args/from 三段分开；'
          '把 (x, y) from _call_x 拼进 target = 两套出口两种待遇')

    # ---- 空选项体 / 只有 $ / extend ----
    beats = parse_beats(tmp, 'empty', S_EMPTY)
    check('§1.4 空选项体 / $ / extend 都是"不跳"，真跳才跳',
          {'name': 'choice_d', 'count': 4,
           'options': [{'text': '空的', 'type': '', 'target': ''},
                       {'text': '只算数', 'type': '', 'target': ''},
                       {'text': '补一句', 'type': '', 'target': ''},
                       {'text': '真跳', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'choice_d'),
          '契约：只认选项体里直接写的 jump/call')

    # ---- nvl ----
    beats = parse_beats(tmp, 'nvl', S_NVL)
    check('§1.5 menu (nvl=True) 带名照常找到',
          {'name': 'choice_e', 'count': 1,
           'options': [{'text': '选一', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'choice_e'),
          'menu [名] [(参数)]: 的名照常入 menu_name')

    # ---- 嵌套 menu：内层的跳转不许记到外层头上 ----
    beats = parse_beats(tmp, 'nested', S_NESTED)
    check('§1.6 嵌套 menu·外层：内层的 jump 不许算进外层选项',
          {'name': 'outer', 'count': 2,
           'options': [{'text': '进内层', 'type': '', 'target': ''},
                       {'text': '走人', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'outer'),
          '契约：只认**直接**写在选项体里的 jump/call，深一层不追；'
          '内层 menu 的 jump 属于内层选项，不属于外层')

    check('§1.7 嵌套 menu·内层：自己的选项要能查到',
          {'name': 'inner', 'count': 1,
           'options': [{'text': '内选', 'type': 'jump', 'target': 'deep'}]},
          menu_brief(beats, 'inner'),
          '按 nest_path 结构位：内层选项在 menu:1>A>menu:2')

    beats = parse_beats(tmp, 'nested2', S_NESTED_DIRECT)
    check('§1.8 嵌套 menu·外层有直句跳转：只认直句（after2），不认内层（deep2）',
          {'name': 'outer2', 'count': 1,
           'options': [{'text': '两层都要', 'type': 'jump', 'target': 'after2'}]},
          menu_brief(beats, 'outer2'),
          '直句在文档序更后也算直句；内层 jump 在前也不许抢先')

    # ---- menu 在 if 里 ----
    beats = parse_beats(tmp, 'inif', S_IN_IF)
    check('§1.9 if 里的 menu：选项要能查到（cond_menu）',
          {'name': 'cond_menu', 'count': 1,
           'options': [{'text': '条件选', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'cond_menu'),
          'nest_path 是拼接路径 if:1>menu:N —— 不能只认 startswith("menu:")')

    check('§1.10 else 分支里的 menu 也要能查到（other_menu）',
          {'name': 'other_menu', 'count': 1,
           'options': [{'text': '别的', 'type': 'jump', 'target': 'ch03'}]},
          menu_brief(beats, 'other_menu'),
          '同 §1.9')

    # ---- 条件选项 ----
    beats = parse_beats(tmp, 'condopt', S_COND_OPT)
    check('§1.11 条件选项：text 是裸文案（条件在 condition 字段，不混进 text）',
          {'name': 'choice_f', 'count': 2,
           'options': [{'text': '回家', 'type': 'jump', 'target': 'home'},
                       {'text': '去河边', 'type': 'jump', 'target': 'river'}]},
          menu_brief(beats, 'choice_f'),
          'beat 的 text/condition 分家（_emit_menu_option）')
    cond_of_first = next(
        str(getattr(b, 'condition', '') or '')
        for b in beats if getattr(b, 'kind', '') == 'menu_option')
    note('§1.11b 条件选项的 condition 值（brief 目前完全不带它 → UX 缺口）',
         f'condition={cond_of_first!r}')

    # ---- 点号 menu 名 ----
    beats = parse_beats(tmp, 'dot', S_DOT)
    check('§1.12 点号 menu 名（main.route）按字面能找到',
          {'name': 'main.route', 'count': 1,
           'options': [{'text': '点号选', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'main.route'),
          '字面匹配、不猜语义（铁律 4）')

    # ---- jump 目标是另一个 menu 名 ----
    beats = parse_beats(tmp, 'j2m', S_JUMP_TO_MENU)
    check('§1.13 jump 的目标是 menu 名：menu_brief 能按名查到这个 menu',
          {'name': 'choice_a', 'count': 1,
           'options': [{'text': '选一', 'type': 'jump', 'target': 'ch02'}]},
          menu_brief(beats, 'choice_a'),
          'menu 名是真 label（renpy_nodes）；按名查 = 控制流条的主场景')

    # ---- 两个同名 menu ----
    beats = parse_beats(tmp, 'dup', S_DUP)
    check('§1.14 两个同名 menu：取第一个、不崩（重名 label 本身是引擎错）',
          {'name': 'twin', 'count': 1,
           'options': [{'text': '甲一', 'type': 'jump', 'target': 'a1'}]},
          menu_brief(beats, 'twin'),
          '稳健性：不崩、不合并；重名交给 lint')

    # ---- 裸 menu（无名）----
    beats = parse_beats(tmp, 'bare', S_BARE)
    check('§1.15 裸 menu 查询空名：返回 {}（不崩）',
          {}, menu_brief(beats, ''),
          'docstring：不是 menu / 找不到 → {}；空名一律 {}')
    check('§1.15b 裸 menu 查询 None：返回 {}（不崩）',
          {}, menu_brief(beats, None),
          '同上')
    check('§1.15c kind=menu 但无 menu_name，拿任意名查：{}',
          {}, menu_brief(beats, 'nope'),
          '稳健性')

    # ---- if 里的选项体：深一层不追（契约明文）----
    beats = parse_beats(tmp, 'ifopt', S_IF_IN_OPT)
    check('§1.16 选项体里 if 再跳：深一层不追（就地继续）',
          {'name': 'choice_h', 'count': 1,
           'options': [{'text': '看情况', 'type': '', 'target': ''}]},
          menu_brief(beats, 'choice_h'),
          '契约 docstring：「深一层（if 里面再跳）不追：追了就得猜哪个分支才会跳」')

    # ---- 27 选项位置键 ----
    beats27 = parse_beats(tmp, 'many', S0_MANY)
    b27 = menu_brief(beats27, 'many')
    check('§1.17 27 选项：第 27 个的去向按 A27 键配对（jump ch27）',
          ('二十七', 'jump', 'ch27'),
          (b27.get('options') or [{}])[-1:] and
          ((b27['options'][-1].get('text'),
            b27['options'][-1].get('type'),
            b27['options'][-1].get('target')) if b27.get('options') else None),
          'option_key(27)=A27；menu_brief 用 chr(65+i) 在第 27 位变成 "["')
    check('§1.17b 27 选项：空的第 1 个选项不许偷走第 27 个的 jump',
          ('空一', '', ''),
          (b27.get('options') or [{}])[:1] and
          ((b27['options'][0].get('text'),
            b27['options'][0].get('type'),
            b27['options'][0].get('target')) if b27.get('options') else None),
          '前缀 A 会撞 A27（startswith 误配）——按位置键精确配对')


# ==================================================================
#  §2 边界与行口径（mock rows 用 §0 自证过的 nest 约定；行边界走真实解析）
# ==================================================================

class B:
    """最小拍替身（只带 menu_brief 读的字段；nest 用真约定的形状）。"""

    def __init__(self, kind='', nest='', menu_name='', text='', raw='',
                 label='main', condition=''):
        self.kind = kind
        self.nest_path = nest
        self.menu_name = menu_name
        self.text = text
        self.raw = raw
        self.label = label
        self.condition = condition


S_LINE = '''label main:
    menu choice_g:
        "软回车":
            jump ch02
        "正常":
            jump ch03
'''


def section2(tmp: Path):
    print('\n===== §2 边界与行口径 =====')

    # ---- head 在文件末尾 ----
    rows = [B(kind='menu', nest='', menu_name='tail_menu')]
    check('§2.1 head 在 rows 末尾：count 0、不崩',
          {'name': 'tail_menu', 'count': 0, 'options': []},
          menu_brief(rows, 'tail_menu'),
          '稳健性边界')

    # ---- head 之后紧跟另一个顶层拍，后面才是别的菜单的选项 ----
    rows = [
        B(kind='menu', nest='', menu_name='lonely'),
        B(kind='say', nest='', text='顶层台词'),
        B(kind='menu', nest='', menu_name='other'),
        B(kind='menu_option', nest='menu:2', text='别家的'),
        B(kind='flow', nest='menu:2>A', raw='jump steal'),
    ]
    check('§2.2 head 之后紧跟顶层拍：不该认到后面那个 menu 的选项',
          {'name': 'lonely', 'count': 0, 'options': []},
          menu_brief(rows, 'lonely'),
          '出了势力范围就该停：nest=="" 也是"出去了"')

    # ---- 孤儿选项（menu 头拍被删掉后紧跟另一个无选项 menu 头）----
    rows = [
        B(kind='menu', nest='', menu_name='victim'),
        B(kind='menu_option', nest='menu:2', text='孤儿选项'),
        B(kind='flow', nest='menu:2>A', raw='jump steal'),
    ]
    check('§2.3 无选项的 menu 头 + 紧跟孤儿选项（别家 nest）：不许认领',
          {'name': 'victim', 'count': 0, 'options': []},
          menu_brief(rows, 'victim'),
          '块号属于别的 menu（menu:2 ≠ 自己的势力范围）——'
          'S1 删拍/表编辑后孤儿行是真实存在')

    # ---- 跨 label 的 menu:1 撞（counters 按 label 重置）----
    rows = [
        B(kind='menu', nest='', menu_name='first', label='a'),
        B(kind='menu_option', nest='menu:1', text='甲选', label='a'),
        B(kind='flow', nest='menu:1>A', raw='jump a1', label='a'),
        B(kind='menu', nest='', menu_name='second', label='b'),
        B(kind='menu_option', nest='menu:1', text='乙选', label='b'),
        B(kind='flow', nest='menu:1>A', raw='jump b1', label='b'),
    ]
    check('§2.4 跨 label 两个 menu:1：互不串（各查各的）',
          [{'name': 'first', 'count': 1,
            'options': [{'text': '甲选', 'type': 'jump', 'target': 'a1'}]},
           {'name': 'second', 'count': 1,
            'options': [{'text': '乙选', 'type': 'jump', 'target': 'b1'}]}],
          [menu_brief(rows, 'first'), menu_brief(rows, 'second')],
          'counters 每 label 重置（_w_label 不传 counters）→ '
          'menu:1 可以在两个 label 各出现一次')

    # ---- 同 label 相邻 menu:1 / menu:10 前缀撞 ----
    rows = [
        B(kind='menu', nest='', menu_name='m1'),
        B(kind='menu_option', nest='menu:1', text='一'),
        B(kind='flow', nest='menu:1>A', raw='jump one'),
        B(kind='menu', nest='', menu_name='m10'),
        B(kind='menu_option', nest='menu:10', text='十'),
        B(kind='flow', nest='menu:10>A', raw='jump ten'),
    ]
    check('§2.5 menu:1 不许把 menu:10 的选项/跳转算进来',
          {'name': 'm1', 'count': 1,
           'options': [{'text': '一', 'type': 'jump', 'target': 'one'}]},
          menu_brief(rows, 'm1'),
          'startswith("menu:1") 会把 "menu:10…" 也罩住 —— 块边界要按 '
          '段（split_nest）比，不能按字符串前缀')

    # ---- kind=menu 但无 menu_name ----
    rows = [B(kind='menu', nest='', menu_name=''),
            B(kind='menu_option', nest='menu:1', text='选')]
    check('§2.6 无名 menu 拿空名查：{}（不把"查不到"伪装成"没有选项"）',
          {}, menu_brief(rows, ''),
          '稳健性')

    # ---- 行边界（P0-A）----
    # mock：raw 里嵌 U+2028（Word 软回车）。Ren'Py 只认 \n → 这是一整行。
    rows = [
        B(kind='menu', nest='', menu_name='ls'),
        B(kind='menu_option', nest='menu:1', text='软'),
        B(kind='flow', nest='menu:1>A', raw='jump\u2028ch02'),
    ]
    note('§2.7 U+2028 嵌在 jump 与目标之间（mock raw=jump\\u2028ch02）',
         f'brief={menu_brief(rows, "ls")!r}\n'
         f'parse_flow(该拍)={parse_flow(rows[2])!r}\n'
         f'Python 侧: "jump\\u2028ch02".split()='
         f'{chr(39)}jump\\u2028ch02{chr(39)}.split() 见下\n'
         f'split(None,1)={("jump\u2028ch02".split(None, 1))!r} '
         f'splitlines={("jump\u2028ch02".splitlines())!r}')

    # parse_flow 的行数判据用 str.splitlines()（flow.py:195）——
    # P0-A 口径：Ren'Py 只认 \n，core/textlines.py 明文禁止 splitlines。
    beat_weird = B(kind='flow', nest='', raw='jump\u2028ch02')
    check('§2.8 parse_flow：U+2028 不是换行（一行的 jump 该认出来）',
          {'type': 'jump', 'target': 'ch02', 'args': '', 'from_clause': '',
           'line': 'jump\u2028ch02'},
          parse_flow(beat_weird),
          'core/textlines.py：只认 \\n；flow.parse_flow 却用 str.splitlines()'
          ' 数行 → 软回车粘进来的 jump 静默退回"原样执行"')

    # CRLF / BOM / NEL 真实解析：
    beats = parse_beats(tmp, 'crlf', S_LINE, newline='\r\n')
    check('§2.9 CRLF 载体：menu_brief 照常',
          {'name': 'choice_g', 'count': 2,
           'options': [{'text': '软回车', 'type': 'jump', 'target': 'ch02'},
                       {'text': '正常', 'type': 'jump', 'target': 'ch03'}]},
          menu_brief(beats, 'choice_g'),
          '换行符归一不影响结构位')

    bom = '\ufeff' + S_LINE
    beats = parse_beats(tmp, 'bom', bom)
    check('§2.10 带 BOM 的文件：menu_brief 照常',
          {'name': 'choice_g', 'count': 2,
           'options': [{'text': '软回车', 'type': 'jump', 'target': 'ch02'},
                       {'text': '正常', 'type': 'jump', 'target': 'ch03'}]},
          menu_brief(beats, 'choice_g'),
          'BOM 不许污染 menu_name / raw')

    # NEL（\x85）真实解析：观察（不硬判 —— 解析器词法怎么认它先看事实）
    s_nel = 'label main:\n    menu choice_n:\n        "nel选":\n            jump ch02\x85tail\n'
    beats = parse_beats(tmp, 'nel', s_nel)
    note('§2.11 NEL(\\x85) 嵌在 jump 行尾（真实解析，观察项）',
         dump([b for b in beats
               if getattr(b, 'kind', '') in ('menu', 'menu_option', 'flow')
               or 'ch02' in str(getattr(b, 'raw', '') or '')]) +
         f"\nbrief={menu_brief(beats, 'choice_n')!r}")

    # U+2028 真实解析（jump 行中夹软回车）：观察
    s_ls = ('label main:\n    menu choice_l:\n        "ls选":\n'
            '            jump ch02\u2028tail\n        "正常":\n'
            '            jump ch03\n')
    beats = parse_beats(tmp, 'lsreal', s_ls)
    note('§2.12 U+2028 真实解析（jump ch02\\u2028tail）（观察项）',
         dump([b for b in beats
               if getattr(b, 'kind', '') in ('menu', 'menu_option', 'flow')]) +
         f"\nbrief={menu_brief(beats, 'choice_l')!r}")


# ==================================================================
#  §3 menu_brief_text 输出全文
# ==================================================================

def section3():
    print('\n===== §3 menu_brief_text 全文 =====')
    brief = {'name': 'choice_a', 'count': 2,
             'options': [{'text': '去河边', 'type': 'jump', 'target': 'ch02'},
                         {'text': '回家', 'type': '', 'target': ''}]}
    expected = ('⚑ 这是分支（menu choice_a），2 个选项：\n'
                '   A 「去河边」→ 跳转 ch02\n'
                '   B 「回家」→ 就地继续（不跳）')
    check('§3.1 两选项全文', expected, menu_brief_text(brief),
          '按实现逐行构造期望全文（含空格/箭头/书名号）')

    check('§3.2 空 brief → 空串', '', menu_brief_text({}))
    check('§3.3 count 0 → 空串', '', menu_brief_text(
        {'name': 'x', 'count': 0, 'options': []}))

    brief_call = {'name': 'c', 'count': 1,
                  'options': [{'text': '求助', 'type': 'call', 'target': 'ch02'}]}
    check('§3.4 call 显示"调用"',
          '⚑ 这是分支（menu c），1 个选项：\n   A 「求助」→ 调用 ch02',
          menu_brief_text(brief_call))


# ==================================================================

def main():
    tmp = Path(tempfile.mkdtemp(prefix='probe_menu_brief_'))
    try:
        section0(tmp)
        section1(tmp)
        section2(tmp)
        section3()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad0 = [n for ok, n in results if not ok and n.startswith('§0')]
    bad = [n for ok, n in results if not ok]
    print('\n===== 汇总 =====')
    print(f'共 {len(results)} 条：{sum(1 for ok, _ in results if ok)} 绿 / '
          f'{len(bad)} 红')
    if bad0:
        print(f'⚠ §0（探针自证）红了 —— 探针期望不可信，先修探针：{bad0}')
    if bad:
        print('红的条目：')
        for n in bad:
            print(f'  - {n}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
