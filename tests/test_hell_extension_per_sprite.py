# -*- coding: utf-8 -*-
"""
地狱测试：**一拍里并排好几条 `show`，"每一条"都要补**（2026-11-08 第十九轮）。

用户原话（真机 `chapter2_2.rpy`，截图里是一个拍里三条 `show` + 一行代码）：

> 不是……我的意思是……本来是两条分别的 show！你应该加两个那一句话……
> 但实际上呢……？三个 show 并列在一个卡片内……你只给了一个……

根因：插入位置只有 `after_last_show` / `after_last_change` 两种"**每拍补一次**"
（锚点取拍内最后一条 show）。配置里写着「每次立绘出现或改变」，落地却是
"每拍一行" —— 一拍里三条 `show` 只补在最后一条后面。

修法：`batch_edit` 多两个位置 `after_each_show` / `after_each_change`
（`_anchors` 返回**一组**锚点，逐条补、逐条幂等），配方层「每次…」按字面
落到 `after_each_*`，"只补最后一次"另存两个值。这一组钉住：

  ① 三条 show → 三条代码，**顺序**还在（第一条的补在第二条之前）；
  ② 已经补过其中一条，不许把其余几条也判成"已补"（这是"只给了一个"的另一面）；
  ③ 再跑一次逐字节不变；
  ④ 退场算不算"变化"由位置决定（`_each_change` 算、`_each_show` 不算）；
  ⑤ ATL 块（`show …:` + 块体）的锚点落在**块尾之后**，不许插进块里；
  ⑥ `python:` 块里长得像 `show` 的那行**永远不是锚点**（它没有语句行号）；
  ⑦ 界面上真能选到这两种位置、真能存进 `storygraph.ext.json`、真能导出落地。
"""
from __future__ import annotations

import json

from storygraph.app import batch_edit as BE
from storygraph.app.batch_recipes import build_rule, read_form

CUE = '$ renpy.transition(Dissolve(0.1), layer="master")'

#: 一个拍里三条 `show`（真机 2_2 第 37 拍就长这样）。
THREE_SHOWS = ('label c1:\n'
               '    # 转场到bg13.2\n'
               '    show lan base_hand_up sigh at i31\n'
               '    show xing aa worried at i32\n'
               '    show ye aa sideeye at i33\n'
               '    "有了第一次的经验，这次的闯入更加熟练了。"\n'
               '    return\n')


def _project(tmp_path, text=THREE_SHOWS, name='p'):
    root = tmp_path / name
    ch = root / 'game' / 'chapter' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(text, encoding='utf-8')
    return root, ch


def _rules(placement, text=CUE):
    return BE.parse_rules([{
        'id': 'cue',
        'when': {'change': 'sprite'},
        'insert': {'placement': placement, 'text': text},
    }])


def _plan(root, placement, text=CUE):
    return BE.plan_batch(root, _rules(placement, text),
                         chapters=['game/chapter/*.rpy'])


def _lines(ch):
    return ch.read_text(encoding='utf-8').splitlines()


# ---------------------------------------------------------------------------
#  一、逐条补：三条 show → 三条代码
# ---------------------------------------------------------------------------

def test_every_show_gets_its_own_line(tmp_path):
    root, ch = _project(tmp_path)
    plan = _plan(root, 'after_each_change')
    assert not plan.errors, plan.errors
    assert plan.pending == 3, plan.pending
    BE.apply_plan(plan)
    lines = _lines(ch)
    # 三条 show 后面各一行，而且**顺序不反**（第一条的补在第二条之前）
    assert lines[2].strip().startswith('show lan'), lines[2]
    assert lines[3].strip() == CUE, lines
    assert lines[4].strip().startswith('show xing'), lines[4]
    assert lines[5].strip() == CUE, lines
    assert lines[6].strip().startswith('show ye'), lines[6]
    assert lines[7].strip() == CUE, lines
    assert ch.read_text(encoding='utf-8').count(CUE) == 3


def test_the_old_one_line_per_beat_is_still_available(tmp_path):
    root, ch = _project(tmp_path)
    BE.apply_plan(_plan(root, 'after_last_change'))
    text = ch.read_text(encoding='utf-8')
    assert text.count(CUE) == 1, text
    assert text.count('show ') == 3          # 一条 show 都没动


def test_show_only_mode_ignores_hides(tmp_path):
    root, ch = _project(
        tmp_path,
        'label c1:\n'
        '    show lan happy\n'
        '    hide ye\n'
        '    "一"\n'
        '    return\n')
    BE.apply_plan(_plan(root, 'after_each_show'))
    assert ch.read_text(encoding='utf-8').count(CUE) == 1
    root2, ch2 = _project(tmp_path, name='q')
    ch2.write_text('label c1:\n'
                   '    show lan happy\n'
                   '    hide ye\n'
                   '    "一"\n'
                   '    return\n', encoding='utf-8')
    BE.apply_plan(_plan(root2, 'after_each_change'))
    assert ch2.read_text(encoding='utf-8').count(CUE) == 2


# ---------------------------------------------------------------------------
#  二、逐条幂等：补过的那一条不许连坐其余几条
# ---------------------------------------------------------------------------

def test_one_already_there_does_not_swallow_the_others(tmp_path):
    """只有**最后一条** show 后面已经有那行（真机 2_2 的原样：补过一次）。"""
    root, ch = _project(
        tmp_path,
        'label c1:\n'
        '    show lan base_hand_up sigh at i31\n'
        '    show xing aa worried at i32\n'
        '    show ye aa sideeye at i33\n'
        f'    {CUE}\n'
        '    "有了第一次的经验。"\n'
        '    return\n')
    plan = _plan(root, 'after_each_change')
    assert plan.pending == 2, plan.pending   # 前两条要补，最后一条已经在了
    BE.apply_plan(plan)
    lines = _lines(ch)
    assert [x.strip() for x in lines[1:7]] == [
        'show lan base_hand_up sigh at i31', CUE,
        'show xing aa worried at i32', CUE,
        'show ye aa sideeye at i33', CUE], lines
    assert lines[7].strip() == '"有了第一次的经验。"', lines


def test_a_second_run_changes_nothing(tmp_path):
    root, ch = _project(tmp_path)
    BE.apply_plan(_plan(root, 'after_each_change'))
    once = ch.read_text(encoding='utf-8')
    again = _plan(root, 'after_each_change')
    assert again.pending == 0
    BE.apply_plan(again)
    assert ch.read_text(encoding='utf-8') == once


# ---------------------------------------------------------------------------
#  三、结构：ATL 块 / python 块 / 注释
# ---------------------------------------------------------------------------

def test_an_atl_block_keeps_its_body_and_the_line_goes_after_it(tmp_path):
    root, ch = _project(
        tmp_path,
        'label c1:\n'
        '    show lan happy:\n'
        '        xpos 0.5\n'
        '        zoom 1.2\n'
        '    show xing sad\n'
        '    "一"\n'
        '    return\n')
    BE.apply_plan(_plan(root, 'after_each_change'))
    lines = [x.strip() for x in _lines(ch)]
    assert lines[1].startswith('show lan happy:'), lines
    assert lines[2] == 'xpos 0.5', lines
    assert lines[3] == 'zoom 1.2', lines
    assert lines[4] == CUE, lines             # 块**之后**，不是块里
    assert lines[5].startswith('show xing sad'), lines
    assert lines[6] == CUE, lines


def test_python_block_text_that_looks_like_show_is_never_an_anchor(tmp_path):
    root, ch = _project(
        tmp_path,
        'label c1:\n'
        '    python:\n'
        '        show = "lan happy"\n'
        '    show lan happy\n'
        '    "一"\n'
        '    return\n')
    BE.apply_plan(_plan(root, 'after_each_change'))
    lines = _lines(ch)
    assert lines[2].strip() == 'show = "lan happy"', lines   # 一个字节没动
    assert lines[3].strip().startswith('show lan'), lines
    assert lines[4].strip() == CUE, lines
    assert ch.read_text(encoding='utf-8').count(CUE) == 1


def test_a_commented_out_show_is_not_an_anchor(tmp_path):
    root, ch = _project(
        tmp_path,
        'label c1:\n'
        '    # show lan happy\n'
        '    show lan happy\n'
        '    "一"\n'
        '    return\n')
    BE.apply_plan(_plan(root, 'after_each_change'))
    assert ch.read_text(encoding='utf-8').count(CUE) == 1


# ---------------------------------------------------------------------------
#  四、配方层：「每次」= 每一条（不是每拍一条）
# ---------------------------------------------------------------------------

def test_the_recipe_layer_means_literally_every_time():
    rule = build_rule({'mode': 'sprite_change'}, 'r1')
    assert rule['insert']['placement'] == 'after_each_change'
    form, why = read_form(rule)
    assert why == '' and form['mode'] == 'sprite_change'
    last = build_rule({'mode': 'sprite_change_last'}, 'r2')
    assert last['insert']['placement'] == 'after_last_change'
    form2, why2 = read_form(last)
    assert why2 == '' and form2['mode'] == 'sprite_change_last'


def test_the_two_grains_are_not_confused_when_read_back():
    each = build_rule({'mode': 'sprite_change'}, 'r1')
    last = build_rule({'mode': 'sprite_change_last'}, 'r1')
    assert read_form(each)[0]['mode'] != read_form(last)[0]['mode']


# ---------------------------------------------------------------------------
#  五、真控件：选得到、存得下、导得出
# ---------------------------------------------------------------------------

def _dialog(root):
    from storygraph.ui_qt.dialog_extensions import ExtensionDialog

    return ExtensionDialog(None, project_root=str(root))


def _declare(dlg, placement):
    dlg._new_point()
    dlg.ed_id.setText('cue.every')
    dlg.ed_label.setText('每次立绘变化都补')
    dlg.cb_kind.setCurrentIndex(dlg.cb_kind.findData('batch'))
    dlg.ck_batch.setChecked(True)
    dlg.cb_placement.setCurrentIndex(dlg.cb_placement.findData(placement))
    assert str(dlg.cb_placement.currentData()) == placement
    dlg.ed_text.setPlainText(CUE)
    assert dlg._save(), dlg.status.text()


def _export(root, ch):
    from storygraph.app.editor import EditorSession

    s = EditorSession(project_root=root)
    s.load(str(ch))
    return s.export(str(ch))


class TestTheDialogOffersBothGrains:
    def test_the_dropdown_lists_every_placement(self, qapp, tmp_path):
        root, _ch = _project(tmp_path)
        dlg = _dialog(root)
        try:
            got = [(dlg.cb_placement.itemData(i), dlg.cb_placement.itemText(i))
                   for i in range(dlg.cb_placement.count())]
        finally:
            dlg.close()
        keys = [k for k, _t in got]
        for want in BE.PLACEMENTS:
            assert want in keys, (want, keys)
        assert '每一条' in dict((k, t) for k, t in got)['after_each_change']

    def test_choosing_each_is_what_gets_written(self, qapp, tmp_path):
        root, _ch = _project(tmp_path)
        dlg = _dialog(root)
        try:
            _declare(dlg, 'after_each_change')
        finally:
            dlg.close()
        data = json.loads((root / 'storygraph.ext.json').read_text('utf-8'))
        insert = data['extensions'][0]['batch']['insert']
        assert insert['placement'] == 'after_each_change', insert
        assert insert['text'] == CUE

    def test_reopening_shows_the_same_grain(self, qapp, tmp_path):
        root, _ch = _project(tmp_path)
        dlg = _dialog(root)
        try:
            _declare(dlg, 'after_each_show')
        finally:
            dlg.close()
        dlg = _dialog(root)
        try:
            assert dlg.focus_on('cue.every')
            picked = dlg.cb_placement.currentData()
            assert picked == 'after_each_show', picked
        finally:
            dlg.close()

    def test_export_lands_one_line_per_show(self, qapp, tmp_path):
        root, ch = _project(tmp_path)
        dlg = _dialog(root)
        try:
            _declare(dlg, 'after_each_change')
        finally:
            dlg.close()
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert text.count(CUE) == 3, text
        assert '改了 3 行' in str(res.get('note') or ''), res

    def test_a_second_export_does_not_double_it(self, qapp, tmp_path):
        root, ch = _project(tmp_path)
        dlg = _dialog(root)
        try:
            _declare(dlg, 'after_each_change')
        finally:
            dlg.close()
        _export(root, ch)
        once = ch.read_text(encoding='utf-8')
        assert _export(root, ch)['ok']
        assert ch.read_text(encoding='utf-8') == once
