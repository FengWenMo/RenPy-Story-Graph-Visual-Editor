# -*- coding: utf-8 -*-
"""
storygraph.ui_qt.sprite_codes_view
==================================
美术需求表的写法（`A7`）在卡片上的投影 —— **纯函数，不碰 Qt、不读文件**。

用户 2026-11-02：

> 映射归映射……但是你看这个选项内！你也看不出来是 A3 呀！！！那我仍然不知道
> 这是 A3 表情……那这个映射注册出来的意义在哪儿呢？

病根：词汇表只把**单个词**翻成人话（`base_hand_up` → 「A 衣服」），而
"服装 A + 表情 3 = A3"是**两维合起来**才成立的事；卡片标题从头到尾只有引擎
写法 —— 映射填得再全，也只有「资源映射」那一页自己看得见。

这里只做三件事：认维度（`slot_kind`）、拼代号（`art_code`）、摆候选
（`slot_choices`）。Qt 那边只负责把结果塞进控件，判据可以不起界面直接测。
"""
from __future__ import annotations

__all__ = ['slot_kind', 'code_of', 'art_code', 'slot_choices']


def slot_kind(row) -> str:
    """引擎分组 → 词汇表的种类（`outfit` / `clothes` → 服装，其余 → 表情）。"""
    group = str((row or {}).get('group') or '').lower()
    return 'costume' if group.startswith(('outfit', 'clothes')) else 'expression'


def code_of(vocab, kind, word, tag) -> str:
    """一个词在映射里登记的代号（查不到 / 没有词汇表都是 `''`）。"""
    finder = getattr(vocab, 'find', None)
    if not callable(finder) or not str(word or '').strip():
        return ''
    return str(getattr(finder(kind, word, tag), 'code', '') or '').strip()


def art_code(rows, vocab, tag) -> tuple:
    """
    分组行 → (`A7`, 一句拆解)；**两半都查得到才拼**，否则 `('', '')`。

    只有 `A` 或只有 `7` 的时候，那串半截代号看起来就像"整张图整个就是它"，
    比不显示更容易被误读成另一张图。
    """
    def value_of(kind):
        return next((str(r.get('value') or '') for r in rows or ()
                     if slot_kind(r) == kind and r.get('value')), '')

    outfit, expr = value_of('costume'), value_of('expression')
    letter = code_of(vocab, 'costume', outfit, tag)
    number = code_of(vocab, 'expression', expr, tag)
    if not (letter and number):
        return '', ''
    return f'{letter}{number}', (f'美术需求表的写法：{letter}{number}\n'
                                 f'  服装 {letter} = {outfit}\n'
                                 f'  表情 {number} = {expr}')


def slot_choices(spec: str, tag: str, rows, vocab, apply) -> list:
    """
    多维图 → 右键候选的分栏，每栏顶上多一条 **「✕ 不写这一维」**。

    `apply(group, value) -> spec` 由调用方给（它才知道 symbols / facts /
    layer_model）；这里只管"拼出来之后该叫什么"。

    每条候选是 `(真值, 显示, 提示)` 三元组：显示里带**美术代号**
    （`A7｜base_hand_up angry`），**真值永远是引擎写法** —— 显示改了，
    写回一个字节都不许改（`labeled_choices` 那条总规矩）。
    """
    kind_of = {str(o): slot_kind(r) for r in rows
               for o in (r.get('options') or ())}
    out = []
    for row in rows:
        group = str(row.get('group') or '')
        label = str(row.get('label') or group)
        values = []
        cleared = apply(group, '')
        if cleared and cleared != spec:
            values.append((cleared, f'✕ 不写「{label}」（＝引擎默认）',
                           '这一维不写进 show，画面上按 layeredimage 的默认走。\n'
                           '⚠ 不写 ≠ 组里第一个词：写了就是把那一件钉死。'))
        for opt in (row.get('options') or ()):
            full = apply(group, str(opt))
            if not full or any(v[0] == full for v in values):
                continue
            words = full.split()[1:]
            letter = code_of(vocab, 'costume', next(
                (w for w in words if kind_of.get(w) == 'costume'), ''), tag)
            number = code_of(vocab, 'expression', next(
                (w for w in words if kind_of.get(w) == 'expression'), ''), tag)
            code = f'{letter}{number}' if (letter and number) else ''
            values.append((full, f'{code}｜{" ".join(words)}' if code
                           else ' '.join(words), full))
        if values:
            out.append({'group': group, 'label': label, 'values': values})
    return out

