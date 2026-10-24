# tools/make_placement_fixtures.py
"""
生成「文件落点」识别用的样例项目（G1 的测试样本）。

为什么用脚本生成而不是手写一堆文件
----------------------------------
样例里有中文目录/中文文件名/中文台词，手写容易被 shell 管道弄坏编码
（铁律 10：含中文/转义的复现一律写 .py）。脚本可重跑，改样例改这里就行。

维度（每个样例覆盖一组取值）
----------------------------
  D1 声明落点形态   专用集中 / 混在入口 / 散落各文件 / 数字前缀 / 多文件分拆 / 单文件
  D2 控制流落点     独立 controller / 内联 jump / call 子过程
  D3 目录结构       扁平 game/*.rpy / 嵌套 game/chapter/ / 中文目录
  D4 命名风格       snake_case / 官方模板名 / 中文名 / 数字前缀
  D5 生成物         已有 *_control.rpy / 已有 *.generated.rpy / 无
  D6 剧情时刻 $     有（分散在内容里）/ 无

七个样例对应七种真实形状（见 docs/分析_通用化与文件落点_2026-10-23.md §二）。
每个样例都带一个 `expected.json` —— G1 实现后，测试用它断言识别结果。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'tests' / 'fixtures' / 'placement'


def _write(path: Path, text: str) -> None:
    """落盘并**去掉公共缩进**——否则 Python 源码的缩进会混进 .rpy，直接语法错。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = textwrap.dedent(text).lstrip('\n')
    path.write_text(clean, encoding='utf-8')


def _expected(name: str, declarations, control_flow, content_globs, notes: str,
              declaration_style: str = 'dedicated') -> None:
    """
    写下期望的 placement 识别结果（G1 实现后的断言依据）。

    `declarations` / `control_flow` 取值约定：
      · 字符串 = 单个文件（或目录）落点
      · 列表   = 多个文件落点
      · ``     = 没有独立落点（散落内容文件里 / 内联）

    `declaration_style`：
      · ``dedicated``          专用声明文件（只有 declare/default，无台词）
      · ``mixed_with_content`` 与内容同文件（声明散落进章节）
      · ``single_file``        单文件原型（声明/控制流/内容同一文件）
    """
    data = {
        'sample': name,
        'placement': {
            'declarations': declarations,
            'control_flow': control_flow,
            'content_globs': content_globs,
            'declaration_style': declaration_style,
        },
        'notes': notes,
    }
    _write(ROOT / name / 'expected.json',
           json.dumps(data, ensure_ascii=False, indent=2) + '\n')


# ---------------------------------------------------------------------------
#  A. 三件套（sr_general 同形）—— 工具今天最熟的形状
# ---------------------------------------------------------------------------
def shape_a_triple() -> None:
    name = 'shape_a_triple'
    g = ROOT / name / 'game'
    _write(g / 'definitions.rpy', '''
        # 定义集中处（声明专用文件，无台词）
        define narrator = Character(ctc="ctc")
        define jc = DynamicCharacter('jc_name', image='jc')
        default jc_name = "「蒋忱」"
        default trust = 0
        default met_jc = False
    ''')
    _write(g / 'chapter' / 'chapter_controller.rpy', '''
        # 由剧情编辑器自动生成的控制流（请勿手动修改跳转部分）

        label chapter1_1_control:
            call chapter1_1 from _call_chapter1_1
            jump chapter1_2_control

        label chapter1_2_control:
            call chapter1_2 from _call_chapter1_2
            return
    ''')
    _write(g / 'chapter' / 'chapter1_1.rpy', '''
        label chapter1_1:
            scene bg classroom
            show jc happy
            jc "早上好，昨晚睡得好吗？"
            $ trust += 1
            $ met_jc = True
            jc "今天我们还有正事。"
            return
    ''')
    _write(g / 'chapter' / 'chapter1_2.rpy', '''
        label chapter1_2:
            scene bg lab
            jc "这里是青蓝实验室。"
            if met_jc:
                jc "我们之前见过面。"
            return
    ''')
    _expected(name,
              declarations='game/definitions.rpy',
              control_flow='game/chapter/chapter_controller.rpy',
              content_globs=['game/chapter/chapter*.rpy'],
              notes='声明专用文件 + 独立 controller + 嵌套目录 + snake_case + _control 生成物')


# ---------------------------------------------------------------------------
#  B. 官方模板形状 —— 声明混在 script.rpy，jump 内联
# ---------------------------------------------------------------------------
def shape_b_template() -> None:
    name = 'shape_b_template'
    g = ROOT / name / 'game'
    _write(g / 'script.rpy', '''
        # 官方模板：声明、剧情、跳转全在这个文件
        define e = Character("Eileen", color="#c8ffc8")
        define m = Character("Mary", color="#ffc8c8")
        default points = 0
        default seen_roof = False

        label start:
            scene bg room
            e "早上好。"
            $ points += 1
            menu:
                "去屋顶":
                    jump roof
                "留下":
                    jump stay

        label roof:
            scene bg roof
            e "风景真好。"
            $ seen_roof = True
            return

        label stay:
            e "那我们继续吧。"
            return
    ''')
    _write(g / 'options.rpy', '''
        define gui.show_name = True
        define config.has_sound = True
    ''')
    _write(g / 'gui.rpy', '''
        define gui.accent_color = '#99ccff'
    ''')
    _expected(name,
              declarations='game/script.rpy',
              control_flow='',   # 无独立控制流文件（内联 jump）
              content_globs=['game/script.rpy'],
              notes='声明混在 script.rpy + 内联 jump + 扁平目录 + 官方模板名 + 无生成物')


# ---------------------------------------------------------------------------
#  C. 分文件但混写 —— 每个 .rpy 自带 default，散落
# ---------------------------------------------------------------------------
def shape_c_scattered() -> None:
    name = 'shape_c_scattered'
    g = ROOT / name / 'game'
    _write(g / 'chapter1.rpy', '''
        default chapter1_done = False
        define c1 = Character("角色一")

        label ch1:
            c1 "第一章开始。"
            $ chapter1_done = True
            jump ch2
    ''')
    _write(g / 'chapter2.rpy', '''
        default chapter2_done = False
        default chapter2_score = 0
        define c2 = Character("角色二")

        label ch2:
            c2 "第二章开始。"
            $ chapter2_score += 1
            jump ch3
    ''')
    _write(g / 'chapter3.rpy', '''
        default chapter3_done = False

        label ch3:
            "第三章。"
            $ chapter3_done = True
            return
    ''')
    _expected(name,
              declarations=['game/chapter1.rpy',
                            'game/chapter2.rpy',
                            'game/chapter3.rpy'],   # 散落：声明与内容同文件
              control_flow='',
              content_globs=['game/chapter*.rpy'],
              notes='声明散落各文件（与内容同文件）+ 内联 jump + 扁平 + snake + 无生成物',
              declaration_style='mixed_with_content')


# ---------------------------------------------------------------------------
#  D. 数字前缀集中 —— 社区推荐写法（00 保证先加载）
# ---------------------------------------------------------------------------
def shape_d_prefixed() -> None:
    name = 'shape_d_prefixed'
    g = ROOT / name / 'game'
    _write(g / '00_variables.rpy', '''
        # 数字前缀保证先于其他文件加载
        define ernie = Character("Ernie")
        define bert = Character("Bert")
        default ernie_happiness = 0
        default bert_happiness = 0
    ''')
    _write(g / '01_story.rpy', '''
        label start:
            "故事开始。"
            call intro
            call middle
            return

        label intro:
            ernie "你好。"
            $ ernie_happiness += 1
            return

        label middle:
            bert "我们到了。"
            $ bert_happiness += 1
            return
    ''')
    _expected(name,
              declarations='game/00_variables.rpy',
              control_flow='',   # call 子过程，无独立 controller
              content_globs=['game/01_story.rpy'],
              notes='00 前缀集中声明 + call 子过程 + 扁平 + 数字前缀 + 无生成物')


# ---------------------------------------------------------------------------
#  E. 多文件分拆声明 —— names.rpy / chars.rpy / flags.rpy
# ---------------------------------------------------------------------------
def shape_e_multifile() -> None:
    name = 'shape_e_multifile'
    g = ROOT / name / 'game'
    _write(g / 'data' / 'names.rpy', '''
        default player_name = "旅人"
        default partner_name = "向导"
    ''')
    _write(g / 'data' / 'chars.rpy', '''
        define p = DynamicCharacter('player_name')
        define g = DynamicCharacter('partner_name')
    ''')
    _write(g / 'data' / 'flags.rpy', '''
        default met_partner = False
        default chose_left = False
    ''')
    _write(g / 'story' / 'flow.rpy', '''
        # 控制流文件，但不叫 _control、也不在 chapter/ 下
        label act1_flow:
            call act1_scene1
            jump act2_flow

        label act2_flow:
            call act2_scene1
            return
    ''')
    _write(g / 'story' / 'act1.rpy', '''
        label act1_scene1:
            scene bg path
            g "我们出发吧。"
            $ met_partner = True
            return
    ''')
    _write(g / 'story' / 'act2.rpy', '''
        label act2_scene1:
            scene bg crossroad
            menu:
                "向左":
                    $ chose_left = True
                    jump act2_left
                "向右":
                    jump act2_right

        label act2_left:
            "你选择了左边。"
            return

        label act2_right:
            "你选择了右边。"
            return
    ''')
    _expected(name,
              declarations=['game/data/names.rpy',
                            'game/data/chars.rpy',
                            'game/data/flags.rpy'],   # 多文件分拆——列表落点
              control_flow='game/story/flow.rpy',
              content_globs=['game/story/act*.rpy'],
              notes='声明多文件分拆 + 独立 flow.rpy（不叫 _control）+ 双层目录 + 无生成物')


# ---------------------------------------------------------------------------
#  F. 中文命名 —— 目录/文件/label 都可能是中文
# ---------------------------------------------------------------------------
def shape_f_chinese() -> None:
    name = 'shape_f_chinese'
    g = ROOT / name / 'game'
    _write(g / '变量定义.rpy', '''
        # 中文文件名的声明专用文件
        default 好感度 = 0
        default 见过面 = False
        define 小明 = Character("小明", color="#c8ffc8")
    ''')
    _write(g / '章节' / '剧情控制.rpy', '''
        # 中文名的控制流文件
        label 第一章_控制:
            call 第一章 from _call_第一章
            jump 第二章_控制

        label 第二章_控制:
            call 第二章 from _call_第二章
            return
    ''')
    _write(g / '章节' / '第一章.rpy', '''
        label 第一章:
            scene bg 教室
            show 小明 happy
            小明 "早上好。"
            $ 好感度 += 1
            $ 见过面 = True
            return
    ''')
    _write(g / '章节' / '第二章.rpy', '''
        label 第二章:
            scene bg 实验室
            if 见过面:
                小明 "我们又见面了。"
            return
    ''')
    _expected(name,
              declarations='game/变量定义.rpy',
              control_flow='game/章节/剧情控制.rpy',
              content_globs=['game/章节/第*.rpy'],
              notes='中文文件名 + 中文目录 + 中文 label + 中文控制流')


# ---------------------------------------------------------------------------
#  G. 单文件原型 —— 一切都在一个文件里（小项目/原型）
# ---------------------------------------------------------------------------
def shape_g_single() -> None:
    name = 'shape_g_single'
    g = ROOT / name / 'game'
    _write(g / 'all_in_one.rpy', '''
        # 原型阶段：声明、剧情、跳转全在一个文件
        define n = Character("旁白")
        default hp = 100
        default game_over = False

        label start:
            n "你醒来了。"
            $ hp -= 10
            jump explore

        label explore:
            n "前面有两条路。"
            menu:
                "进洞":
                    jump cave
                "回头":
                    jump back

        label cave:
            n "洞里很黑。"
            if hp <= 0:
                $ game_over = True
                jump bad_end
            jump start

        label back:
            n "你回到了原点。"
            return

        label bad_end:
            n "你倒下了。"
            return
    ''')
    _expected(name,
              declarations='game/all_in_one.rpy',
              control_flow='game/all_in_one.rpy',   # 同一文件——落点重合
              content_globs=['game/all_in_one.rpy'],
              notes='单文件原型：声明/控制流/内容同文件（落点重合的极端情况）',
              declaration_style='single_file')


def main() -> None:
    for fn in (shape_a_triple, shape_b_template, shape_c_scattered,
               shape_d_prefixed, shape_e_multifile, shape_f_chinese,
               shape_g_single):
        fn()
        print(f'[ok] {fn.__name__}')
    print(f'\n生成完毕：{ROOT}')


if __name__ == '__main__':
    main()
