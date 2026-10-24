# -*- coding: utf-8 -*-
"""
G0 出口识别：判据测试。

这里钉的都是**容易写错**的判据，不是"能跑就行"：

  1. 末尾 `call` **不等于**隐式 return —— 它一样落空（我第一版就写错了）
  2. 落空要能说出"会撞进哪个 label"，而不是只说"有风险"
  3. `_control` / `start` 这类角色不该被当成"内容章"报警
  4. `$ renpy.full_restart()` 是终局，不是落空
  5. 降级清单要能生成人话（工具降级必须告诉用户）
"""
from __future__ import annotations

import textwrap

import pytest

from storygraph.story.exits import (
    EXIT_CALL_NO_RETURN, EXIT_DYNAMIC, EXIT_EMPTY, EXIT_FALLTHROUGH,
    EXIT_JUMP, EXIT_MENU, EXIT_RETURN, EXIT_TERMINATE,
    ROLE_CONTENT, ROLE_CONTROLLER,
    ROLE_ENTRY, label_exits_for_file, project_exits,
)


def write(tmp_path, name, body):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip('\n'), encoding='utf-8')
    return p


def one(tmp_path, body, label='ch01', name='a.rpy'):
    items = label_exits_for_file(write(tmp_path, name, body))
    got = [x for x in items if x.label == label]
    assert got, f'没解析出 label {label}；实际：{[x.label for x in items]}'
    return got[0]


# ---------------------------------------------------------------------------
#  四档基本出口
# ---------------------------------------------------------------------------

def test_return_is_the_only_wrappable_exit(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "一句台词"
            return
    ''')
    assert e.exit == EXIT_RETURN
    assert e.can_wrap is True
    assert e.is_risky is False


def test_jump_cannot_be_wrapped(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "一句台词"
            jump ch02
    ''')
    assert e.exit == EXIT_JUMP
    assert e.target == 'ch02'
    assert e.can_wrap is False
    # jump 是"这章自己写着去哪"，不是缺陷 —— 不报风险
    assert e.is_risky is False


def test_trailing_call_without_return_is_a_fallthrough_not_a_return(tmp_path):
    """⚠ 核心判据：Ren'Py 没有隐式 return。"""
    e = one(tmp_path, '''
        label ch01:
            "一句台词"
            call sub
    ''')
    assert e.exit == EXIT_CALL_NO_RETURN
    assert e.target == 'sub'
    assert e.can_wrap is False
    assert e.is_risky is True


def test_call_then_return_is_fine(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "一句台词"
            call sub
            return
    ''')
    assert e.exit == EXIT_RETURN
    assert e.can_wrap is True


def test_narration_ending_falls_into_next_label(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "一句台词"

        label ch02:
            "下一章"
            return
    ''')
    assert e.exit == EXIT_FALLTHROUGH
    assert e.falls_into == 'ch02'
    assert e.is_risky is True
    assert 'ch02' in e.reason


def test_last_label_in_file_has_no_falls_into(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "only one label here"
    ''')
    assert e.exit == EXIT_FALLTHROUGH
    assert e.falls_into == ''
    assert '文件末尾' in e.reason


def test_pass_ending_is_fallthrough(tmp_path):
    """`pass` 在 Ren'Py 里是合法语句，但它不是出口。"""
    e = one(tmp_path, '''
        label ch01:
            pass
    ''')
    assert e.exit == EXIT_FALLTHROUGH
    assert e.can_wrap is False


def test_truly_empty_label(tmp_path):
    """只有 `label x:` 一行，没有任何语句。"""
    e = one(tmp_path, '''
        label ch01:
        label ch02:
            return
    ''')
    assert e.exit == EXIT_EMPTY
    assert e.can_wrap is False
    assert e.is_risky is True


def test_full_restart_is_terminate_not_fallthrough(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "结尾"
            $ renpy.full_restart()
    ''')
    assert e.exit == EXIT_TERMINATE
    assert e.is_risky is False          # 终局不是缺陷
    assert e.can_wrap is False


# ---------------------------------------------------------------------------
#  角色：不该被误报的
# ---------------------------------------------------------------------------

def test_control_label_is_controller_and_never_risky(tmp_path):
    e = one(tmp_path, '''
        label ch01_control:
            call ch01 from _call_ch01_1
    ''', label='ch01_control')
    assert e.role == ROLE_CONTROLLER
    assert e.is_risky is False
    assert e.can_wrap is False          # 已经是包装层，不再包


def test_entry_label_is_entry(tmp_path):
    e = one(tmp_path, '''
        label start:
            "开场"
    ''', label='start')
    assert e.role == ROLE_ENTRY
    assert e.is_risky is False


def test_plain_chapter_is_content(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            return
    ''')
    assert e.role == ROLE_CONTENT


# ---------------------------------------------------------------------------
#  if / menu 收尾：结论要打折，但事实要报
# ---------------------------------------------------------------------------

def test_condition_at_the_end_marks_low_confidence(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "前面"
            if flag:
                "分支里最后一句"
        label ch02:
            return
    ''')
    assert e.exit == EXIT_FALLTHROUGH
    assert e.falls_into == 'ch02'
    assert e.last_condition, '最后一句带 if 条件时必须记下来'
    assert e.confidence == 'low'


def test_menu_ending_reports_every_option_not_just_the_last(tmp_path):
    """
    折平之后最后一条是最后一个选项里的 `jump`。
    如果按 `EXIT_JUMP` 报，就只报出一个目标的去向 —— 漏掉另一个。
    """
    e = one(tmp_path, '''
        label ch01:
            menu:
                "去屋顶":
                    jump ch_roof
                "留下":
                    jump ch_stay
    ''')
    assert e.exit == EXIT_MENU
    assert e.target == '', 'menu 收尾不能用一个 target 代表全体'
    assert [o['target'] for o in e.menu_options] == ['ch_roof', 'ch_stay']
    assert [o['text'] for o in e.menu_options] == ['去屋顶', '留下']
    assert e.can_wrap is False
    # 两个选项都 jump 走了，没有选项落空 → 不算缺陷
    assert e.is_risky is False
    assert e.confidence == 'low'


def test_menu_with_a_falling_option_is_risky(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            menu:
                "走了":
                    jump ch_roof
                "留下":
                    "留在原地说话"
    ''')
    assert e.exit == EXIT_MENU
    assert e.is_risky is True, '有一个选项落空，就必须报'


# ---------------------------------------------------------------------------
#  第三层：静态判不出来的出口 —— 承认不知道，不塞进"落空"
# ---------------------------------------------------------------------------

def test_renpy_jump_variable_is_dynamic_not_fallthrough(tmp_path):
    """
    这是用户点出来的边界：`$ renpy.jump(where)` 静态**判不出去哪**。
    判不出来就说判不出来 —— 报成"落空"等于把正常写法当缺陷。
    """
    e = one(tmp_path, '''
        label ch01:
            "台词"
            $ renpy.jump(where)
    ''')
    assert e.exit == EXIT_DYNAMIC
    assert e.is_risky is False, '判不出来 ≠ 用户写错了'
    assert e.can_wrap is False, '不知道去哪，当然不能包一层'
    assert '判不出来' in e.reason


def test_renpy_call_variable_is_dynamic(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "台词"
            $ renpy.call(target)
    ''')
    assert e.exit == EXIT_DYNAMIC
    assert e.is_risky is False


def test_jump_expression_is_dynamic(tmp_path):
    e = one(tmp_path, '''
        label ch01:
            "台词"
            jump expression target
    ''')
    assert e.exit == EXIT_DYNAMIC


def test_dynamic_jump_with_literal_is_still_dynamic(tmp_path):
    """
    `$ renpy.jump("b")` 的目标是字面量，但那是 **Python 表达式** ——
    工具不解析它。解析一半比不解析更危险（会说"目标是 b"，实际可能不是）。
    """
    e = one(tmp_path, '''
        label ch01:
            "台词"
            $ renpy.jump("b")
    ''')
    assert e.exit == EXIT_DYNAMIC


def test_real_fallthrough_is_still_reported(tmp_path):
    """放水只针对"判不出来"，真落空必须继续报 —— 否则这一档就废了。"""
    e = one(tmp_path, '''
        label ch01:
            "台词"
        label ch02:
            return
    ''')
    assert e.exit == EXIT_FALLTHROUGH
    assert e.is_risky is True


def test_dynamic_exit_gets_its_own_degradation_not_the_risk_one(tmp_path):
    """能力边界要和"缺陷"分开报：混在一起，用户会对所有报警免疫。"""
    write(tmp_path, 'game/dyn.rpy', '''
        label ch_dyn:
            "台词"
            $ renpy.jump(where)
    ''')
    rep = project_exits(tmp_path)
    kinds = {d['kind']: d for d in rep.degradations()}

    assert 'dynamic_exit' in kinds
    assert kinds['dynamic_exit']['level'] == 'readonly'
    assert 'label_falls_through' not in kinds, '动态出口不能被算成落空'
    assert rep.by_label('ch_dyn').is_risky is False


# ---------------------------------------------------------------------------
#  整项目：降级清单（工具降级必须告诉用户）
# ---------------------------------------------------------------------------

def test_trailing_comment_does_not_count_as_an_exit(tmp_path):
    """
    真机形状（`fc_play`）：最后一句是代码，后面跟一段分节注释，
    再跟下一个 label 的声明。注释不改变执行流，不能当出口。
    """
    e = one(tmp_path, '''
        label ch01:
            "说句话"
            $ flag = True
            ## ---- 下一段 ----

        label ch02:
            return
    ''')
    assert e.exit == EXIT_FALLTHROUGH
    assert e.last_kind == 'python', f'应取到 python，实际 {e.last_kind}'
    assert e.falls_into == 'ch02'


def test_self_jump_is_a_loop_not_a_destination(tmp_path):
    """真机形状（`test_image_credits`）：章末跳回自己 = 原地循环。"""
    e = one(tmp_path, '''
        label ch01:
            menu:
                "返回":
                    return
            jump ch01
    ''')
    assert e.exit == EXIT_JUMP
    assert e.is_self_loop is True
    assert '原地循环' in e.reason
    assert e.can_wrap is False


def test_project_report_lists_degradations_in_plain_words(tmp_path):
    write(tmp_path, 'game/ch01.rpy', '''
        label ch01:
            "没写出口的章"
    ''')
    write(tmp_path, 'game/ch02.rpy', '''
        label ch02:
            call sub
    ''')
    write(tmp_path, 'game/controller.rpy', '''
        label ch02_control:
            call ch02 from _call_ch02_1
            jump ch01_control
    ''')

    rep = project_exits(tmp_path)
    kinds = {d['kind'] for d in rep.degradations()}
    assert 'label_falls_through' in kinds
    assert 'call_without_return' in kinds

    for d in rep.degradations():
        assert d['level'] in ('blocked', 'readonly')
        assert d['title'] and d['message'] and d['hint']
        assert d['labels']

    # 计数要和明细对得上
    assert rep.counts()[EXIT_FALLTHROUGH] >= 1
    assert rep.by_label('ch01').falls_into == ''


def test_one_broken_file_does_not_kill_the_scan(tmp_path):
    write(tmp_path, 'game/good.rpy', '''
        label good:
            return
    ''')
    (tmp_path / 'game' / 'bad.rpy').write_text(
        'label bad:\n    "引号没闭合\n', encoding='utf-8')

    rep = project_exits(tmp_path)
    assert rep.by_label('good') is not None, '坏文件不该让好文件也读不出来'


@pytest.mark.parametrize('body,expected', [
    ('label x:\n    return\n', EXIT_RETURN),
    ('label x:\n    jump y\n', EXIT_JUMP),
    ('label x:\n    call y\n', EXIT_CALL_NO_RETURN),
    ('label x:\n    "台词"\n', EXIT_FALLTHROUGH),
    ('label x:\n    $ renpy.quit()\n', EXIT_TERMINATE),
])
def test_classify_table(tmp_path, body, expected):
    e = one(tmp_path, body, label='x', name='t.rpy')
    assert e.exit == expected
