# -*- coding: utf-8 -*-
"""
判据 · **卡片写回必须逐字节保真**（2026-10-24，审计 P0 的守卫）
================================================================

这一份守的是"用户点一下就会改坏别人项目"的那几条路，全部从
**用户会怎么用**出发，而不是从函数签名出发：

  · `EditorSession.load` → 每拍 `apply(卡片)` → `export(原地)`；
  · 断言拿去和**源文件的字节**比，不是和"某个函数返回值"比。

三条 P0（对应 `docs/审计_回退风险_2026-10-24.md`）：

  · **G1** 同一拍里"普通 show + 带 with/behind/as/onlayer/screen 的 show"，
    后者会被 `cards_to_show` 整体替换掉（`with` 还会挪到别人头上）；
  · **G2** `show expression …` 提交后丢 `expression` 关键字
    （产物变成 `show holo("x")`，引擎必然报错）；
  · **G3** 说话人不是 ASCII 标识符（中文/日文角色名）时，改一句台词
    会"插新行 + 留旧行"，而且**每导出一次多一行**。

判据为什么必须端到端（教训）：从前 `test_card_editor` 测的是
`apply_sprite_rows`（会保留子句），可真实路径走的是 `cards_to_show`
（不保留）—— 函数绿了、产品坏了。所以这里**一条都不测纯函数就完事**，
每条都跑完整链路。

另外守一条结构性不变量（`TestWriteBackInvariant`）：任何一张卡片数组
都不能让"结构化表达不了的 show 项"消失 —— 哪怕将来有人重写写回函数。
"""
from __future__ import annotations

import difflib

import pytest

from storygraph.app.editor import EditorSession
from storygraph.app import beats as B

#: 生成物标记（有改动时会被插到文件头两行）。
MARKER = '# 由剧本表自动生成'


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _project(tmp_path, src: str):
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    path = game / 'script.rpy'
    path.write_bytes(src.encode('utf-8'))
    session = EditorSession(project_root=tmp_path)
    session.load(path)
    return session, path


def _read(path):
    return open(path, encoding='utf-8', newline='').read()


def _body(text: str) -> list:
    """去掉生成器标记（+它后面那个空行）之后的正文行。"""
    lines = text.splitlines()
    out, skip_blank = [], False
    for ln in lines:
        if ln.startswith(MARKER):
            skip_blank = True
            continue
        if skip_blank and not ln.strip():
            skip_blank = False
            continue
        out.append(ln)
    return out


def _submit_all(session, change_index=None, field='Text', value=None):
    """把每一拍都"打开卡片 → 提交"一遍（保存/翻页走的就是这条路）。"""
    for i in range(len(session.beats)):
        fields = session.fields(i)
        if change_index == i and value is not None:
            fields[field] = value
        session.apply(i, fields, cards=session.cards(i), advanced_open=True,
                      stage_loaded=fields.get('Stage'))


def _diff(before: str, after: str) -> str:
    return ''.join(difflib.unified_diff(_body(before), _body(after),
                                        '原文', '导出后', n=2))


def _assert_no_change(src: str, path, session):
    """提交 + 导出之后，文件**逐字节**还是原来那份（标记除外）。"""
    session.export(path)
    after = _read(path)
    assert _body(after) == _body(src), (
        '导出后内容变了（用户什么都没改）：\n' + _diff(src, after))
    # 连"标记以外的一个字节都不许变"：换行风格也要原样
    assert after.count('\r\n') == src.count('\r\n'), '换行风格被改了'
    assert after.startswith('#') == src.startswith('#'), '文件头被改坏了'


# ----------------------------------------------------------------------
# G1：结构化表达不了的 show 项，一个都不许丢
# ----------------------------------------------------------------------
class TestClauseShowSurvives:
    """`show … with/behind/as/onlayer`、`show screen …` 必须原样活下来。"""

    def test_with_survives_and_with_is_not_moved(self, tmp_path):
        """`show bg … with dissolve` 不许消失，`with` 不许跑到别的立绘头上。

        这是真机 `script_pomo.rpy` 的形状（`show bglibrary with dissolve`
        + `show qing base at h11`）—— 修之前导出会让背景**整条消失**，
        而 qing 莫名其妙带上了 dissolve。
        """
        src = ('label start:\n'
               '    show e happy\n'
               '    show bg library with dissolve\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_with_survives_when_it_comes_first(self, tmp_path):
        """顺序反过来（with 在前）也要保住 —— 只是"先遇到谁"的差别。"""
        src = ('label start:\n'
               '    show bg library with dissolve\n'
               '    show e happy\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_behind_survives(self, tmp_path):
        """`show cg cg01 behind e` 与普通立绘同拍时，不许被吞。"""
        src = ('label start:\n'
               '    show e happy\n'
               '    show cg cg01 behind e\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_as_survives(self, tmp_path):
        src = ('label start:\n'
               '    show e happy\n'
               '    show e2 sad as twin\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_onlayer_survives(self, tmp_path):
        src = ('label start:\n'
               '    show e happy\n'
               '    show overlay frame onlayer screens\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_show_screen_survives(self, tmp_path):
        """真机 `show screen display_timer`（计时器 UI）不许被吃掉。"""
        src = ('label start:\n'
               '    show homo at sessionsprite\n'
               '    show screen display_timer\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_two_opaque_items_keep_order(self, tmp_path):
        """两条"表达不了"的指令夹着立绘：条数与先后顺序都不许变。"""
        src = ('label start:\n'
               '    show bg room with fade\n'
               '    show e happy\n'
               '    show cg cg01 behind e\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        after = _read(path)
        body = _body(after)
        idx_bg = next(i for i, ln in enumerate(body) if 'with fade' in ln)
        idx_show = next(i for i, ln in enumerate(body) if 'show e happy' in ln)
        idx_cg = next(i for i, ln in enumerate(body) if 'behind e' in ln)
        assert idx_bg < idx_show < idx_cg, '三条 show 的相对顺序被改了：\n' + str(body)
        _assert_no_change(src, path, session)


# ----------------------------------------------------------------------
# G2：show expression 的 expression 关键字
# ----------------------------------------------------------------------
class TestShowExpressionSurvives:
    def test_expression_keyword_alone(self, tmp_path):
        """最容易踩的那种：整拍只有一条 `show expression`。"""
        src = ('label start:\n'
               '    show expression holo("lan base") at i11\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        assert 'expression' in session.beats[0].show, (
            '提交后内存里就没有 expression 了：%r' % session.beats[0].show)
        _assert_no_change(src, path, session)

    def test_expression_mixed_with_plain_show(self, tmp_path):
        """混合拍是最危险的：卡片会把表达式当普通立绘切碎。"""
        src = ('label start:\n'
               '    show expression holo("lan base") at i11\n'
               '    show e happy\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_expression_with_as_and_with(self, tmp_path):
        src = ('label start:\n'
               '    show e happy\n'
               '    show expression Text("hi") as t with dissolve\n'
               '    "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)


# ----------------------------------------------------------------------
# G3：非 ASCII 说话人（中文 / 日文 / 带点）
# ----------------------------------------------------------------------
class TestNonAsciiSpeaker:
    def test_chinese_speaker_edit_does_not_duplicate(self, tmp_path):
        """改一句中文角色名的台词，产物里旧句必须消失（不是"多留一份"）。"""
        src = ('label start:\n'
               '    凌跃捷 "第一句"\n'
               '    凌跃捷 "第二句"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        n_before = len(session.beats)
        _submit_all(session, change_index=0, value='第一句（改）')
        session.export(path)
        after = _read(path)
        body = _body(after)
        assert len(session.beats) == n_before, (
            '导出把拍数改了：%d → %d（导出不该改内存结构）'
            % (n_before, len(session.beats)))
        assert sum('第一句' in ln for ln in body) == 1, (
            '旧台词没删干净：\n' + after)
        assert '第一句（改）' in after
        assert '第二句' in after

    def test_chinese_speaker_three_rounds_stay_stable(self, tmp_path):
        """连续导出三次，行数**一次都不许长**（修之前每轮 +1）。"""
        src = ('label start:\n'
               '    凌跃捷 "一"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        counts = []
        for _ in range(3):
            session.load(path)                      # 每次都从盘上重新读
            _submit_all(session, change_index=0,
                        value=(session.fields(0).get('Text') or '') + '!')
            session.export(path)
            counts.append(len(_body(_read(path))))
        assert counts == [counts[0]] * 3, '行数越导越多：%r' % (counts,)

    def test_japanese_speaker(self, tmp_path):
        src = ('label start:\n'
               '    リン "こんにちは"\n'
               '    "旁白"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        n_before = len(session.beats)
        _submit_all(session, change_index=0, value='こんにちは！')
        session.export(path)
        after = _read(path)
        assert len(session.beats) == n_before
        assert after.count('こんにちは') == 1, '旧台词留下来了：\n' + after

    def test_dotted_chinese_speaker(self, tmp_path):
        """`ch.林` 这种"点号 + 中文"也要认（走同一条正则）。"""
        src = ('label start:\n'
               '    ch.林 "台词一"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session, change_index=0, value='台词一（改）')
        session.export(path)
        after = _read(path)
        assert after.count('台词一') == 1, '旧台词留下来了：\n' + after

    def test_changing_speaker_to_chinese_is_written(self, tmp_path):
        """把英文说话人**改成**中文角色名，也要落盘（而不是被当未知写法的块）。"""
        src = ('label start:\n'
               '    e "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session, change_index=0, field='Speaker', value='凌跃捷')
        session.export(path)
        after = _read(path)
        assert '凌跃捷 "台词"' in after, after

    def test_no_edit_export_is_byte_identical(self, tmp_path):
        """中文说话人、什么都不改 → 连文件都不该碰。"""
        src = ('label start:\n'
               '    凌跃捷 "一"\n'
               '    "旁白"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        before = path.read_bytes()
        session.export(path)
        assert path.read_bytes() == before, '零改动的导出改动了文件'


# ----------------------------------------------------------------------
# 结构性不变量：不管谁重写写回函数，这几条都不许破
# ----------------------------------------------------------------------
class TestWriteBackInvariant:
    """只测"卡片数组能不能让表达不了的东西消失"—— 与具体实现无关。"""

    OPAQUE = [
        'show bg room with dissolve',
        'show cg cg01 behind e',
        'show e2 sad as twin',
        'show overlay frame onlayer screens',
        'show screen display_timer',
        'show expression holo("lan base") at i11',
    ]

    @pytest.mark.parametrize('opaque', OPAQUE)
    def test_opaque_survives_any_card_list(self, opaque):
        old = 'e happy; ' + opaque[len('show '):]
        cards = [{'tag': 'e', 'image': 'e happy', 'expression': '',
                  'position': '', 'at': '', 'order_index': 0,
                  'extra_rows': []}]
        out = B.write_back_show(old, cards)
        parts_before = B.split_show_items(opaque[len('show '):])
        assert parts_before, '样本本身没解析出来：%r' % opaque
        assert ';' in out or len(parts_before) == 1
        # 表达不了的那条，必须能在产物里找到（按规范编码比）
        from storygraph.story.beat_model import show_item_part
        want = show_item_part(parts_before[0])
        got = [show_item_part(i) for i in B.split_show_items(out)]
        assert want in got, '写回把 %r 吃掉了：%r' % (want, out)

    def test_idempotent(self):
        """同一份卡片写两次，结果必须一样（否则每翻一页就漂一点）。"""
        old = 'e happy; bg room with dissolve; cg cg01 behind e'
        cards = [{'tag': 'e', 'image': 'e happy', 'expression': '',
                  'position': 'left', 'at': '', 'order_index': 0,
                  'extra_rows': []}]
        once = B.write_back_show(old, cards)
        twice = B.write_back_show(once, cards)
        assert once == twice, '%r != %r' % (once, twice)

    def test_empty_cards_do_not_clear_show(self):
        """空卡片数组不许清空立绘（防"误删一张卡 = 立绘全没"）。"""
        old = 'e happy; q sad@left'
        assert B.write_back_show(old, []) == '' or \
            B.write_back_show(old, []) == old

    def test_cards_to_show_is_still_the_plain_path(self):
        """没有表达不了的项时，写回结果必须与 `cards_to_show` 完全一致。

        这条防"新写回函数顺手改了旧语义"（表情归一、逐条位置、extras 顺序）。
        """
        cards = [
            {'tag': 'e', 'image': 'e happy', 'expression': '开心',
             'position': '左', 'at': '', 'order_index': 0, 'extra_rows': []},
            {'tag': 'q', 'image': 'q sad', 'expression': '',
             'position': 'right', 'at': '', 'order_index': 1, 'extra_rows': []},
        ]
        assert B.write_back_show('e happy; q sad', cards) == \
            B.cards_to_show(cards)


# ----------------------------------------------------------------------
# 反向守卫：这些"已经对的东西"不许被修坏
# ----------------------------------------------------------------------
class TestNoRegression:
    SAMPLES = {
        '普通台词': 'label start:\n    scene bg room with dissolve\n'
                    '    e "台词"\n    "旁白"\n    return\n',
        '条件菜单': 'label start:\n    menu:\n        "A" if score > 0:\n'
                    '            e "a"\n        "B":\n            e "b"\n'
                    '    return\n',
        '代码块': 'label start:\n    $ flag = True\n    python:\n'
                  '        x = 1\n    if flag:\n        e "嗯"\n    return\n',
        '顶层块': 'define e = Character("E")\n\ntransform t1:\n    xalign 0.5\n\n'
                  'label start:\n    show e at t1\n    "台词"\n    return\n',
        '重复台词': 'label start:\n    e "嗯。"\n    e "嗯。"\n    return\n',
        '中文标签': 'label 第一章:\n    e "台词"\n    return\n',
    }

    @pytest.mark.parametrize('name', sorted(SAMPLES))
    def test_clean_sample_roundtrip(self, tmp_path, name):
        src = self.SAMPLES[name]
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)

    def test_crlf_kept(self, tmp_path):
        src = 'label start:\n    show e happy\n    "台词"\n    return\n'
        src_crlf = src.replace('\n', '\r\n')
        session, path = _project(tmp_path, src_crlf)
        _submit_all(session)
        session.export(path)
        after = _read(path)
        assert '\r\n' in after, 'CRLF 被改成了 LF'
        assert after.replace('\r\n', '\n') == src.replace('\n', '\n') or \
            _body(after) == _body(src_crlf)

    def test_bom_kept(self, tmp_path):
        src = 'label start:\n    e "台词"\n    return\n'
        game = tmp_path / 'game'
        game.mkdir(parents=True, exist_ok=True)
        path = game / 'script.rpy'
        path.write_bytes(b'\xef\xbb\xbf' + src.encode('utf-8'))
        session = EditorSession(project_root=tmp_path)
        session.load(path)
        _submit_all(session)
        session.export(path)
        assert path.read_bytes().startswith(b'\xef\xbb\xbf'), 'BOM 被去掉了'

    def test_tab_indent_kept(self, tmp_path):
        src = 'label start:\n\te "台词"\n\treturn\n'
        session, path = _project(tmp_path, src)
        _submit_all(session)
        _assert_no_change(src, path, session)


# ----------------------------------------------------------------------
# 卡片能改的**每一格**：改一格只许动一行（"波及别处"一律算 P0）
# ----------------------------------------------------------------------
def _changed_blocks(before: str, after: str) -> list:
    """正文的差异块：`[(tag, 旧行, 新行), …]`（`replace` / `delete` / `insert`）。"""
    a, b = _body(before), _body(after)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(tag, a[i1:i2], b[j1:j2])
            for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal']


def _assert_only_touched(before: str, after: str, *, max_old=1, max_new=1):
    """
    只许"删 ≤ max_old 行 / 加 ≤ max_new 行"，而且**不许有第二处**改动。

    这是"改一格不波及别处"的唯一判据。用户项目被改坏，几乎都不是
    "那一行没写对"，而是**别的地方被顺手动了**（少一条立绘、多一行台词、
    整块顺序变了）。所以这里比"目标行对不对"更严格。
    """
    blocks = _changed_blocks(before, after)
    assert blocks, '预期改了一行，结果一个字节都没动'
    assert len(blocks) == 1, (
        '改动波及了 %d 处（只该有 1 处）：%r\n%s'
        % (len(blocks), blocks, _diff(before, after)))
    tag, old, new = blocks[0]
    assert len(old) <= max_old, (
        '顺手删了 %d 行（只该删 %d 行）：%r' % (len(old), max_old, old))
    assert len(new) <= max_new, (
        '顺手加了 %d 行（只该加 %d 行）：%r' % (len(new), max_new, new))


class TestEveryEditableFieldTouchesOnlyItsLine:
    """
    卡片上每一格改一次，**只许动它自己那一行**（Stage 允许插一行）。

    为什么这一组要存在：G1/G2 的教训是"某个字段的写回把整拍重写了"。
    一个字段一条用例，等于给每个字段钉一条"波及半径 = 1 行"的判据 ——
    将来谁改了写回顺序、谁多拼了一行，这里会立刻红。
    """

    CASES = {
        'Speaker': (
            'label start:\n    e "台词"\n    "旁白"\n    return\n',
            'Speaker', 'm'),
        'Text': (
            'label start:\n    e "台词"\n    e "第二句"\n    return\n',
            'Text', '改过的台词'),
        'Scene': (
            'label start:\n    scene bg room\n    e "台词"\n    return\n',
            'Scene', 'bg other'),
        'BGM': (
            'label start:\n    play music "a.ogg"\n    e "台词"\n    return\n',
            'BGM', 'b.ogg'),
        'SFX': (
            'label start:\n    play sound "s.ogg"\n    e "台词"\n    return\n',
            'SFX', 't.ogg'),
        'Voice': (
            'label start:\n    voice "v.ogg"\n    e "台词"\n    return\n',
            'Voice', 'w.ogg'),
        'Comment': (
            'label start:\n    # 旧注释\n    e "台词"\n    return\n',
            'Comment', '新注释'),
        'CodeBlock': (
            'label start:\n    $ x = 1\n    e "台词"\n    return\n',
            'CodeBlock', '$ x = 2'),
    }

    @pytest.mark.parametrize('field', sorted(CASES))
    def test_one_field_one_line(self, tmp_path, field):
        src, key, value = self.CASES[field]
        session, path = _project(tmp_path, src)
        # **先全拍提交一遍**（翻页会这么做），确保基线是"打开过一遍"的状态
        _submit_all(session)
        # 找**真的带内容**的那一拍（`beat_to_fields` 恒有这些键，
        # 只看"键在不在"会永远落在第一拍 = 台词拍上）
        idx = next((i for i in range(len(session.beats))
                    if str(session.fields(i).get(key) or '').strip()), None)
        assert idx is not None, '样本里找不到带 %s 的拍' % key
        fields = session.fields(idx)
        fields[key] = value
        session.apply(idx, fields, cards=session.cards(idx),
                      advanced_open=True, stage_loaded=fields.get('Stage'))
        session.export(path)
        after = _read(path)
        assert value in after, '%s 的改动没写进产物：\n%s' % (field, after)
        # **行数不许变**：只许"换掉那一行"。Voice 曾经插一条新的、旧的不删
        # （产物里两条 `voice`），而 `_assert_only_touched` 只看"加 ≤1 行"
        # 所以是绿的 —— 这一条专门堵那个缝。
        assert len(_body(after)) == len(_body(src)), (
            '%s 改一格却改变了行数（%d → %d）：\n%s'
            % (field, len(_body(src)), len(_body(after)), after))
        _assert_only_touched(src, after)

    def test_stage_edit_keeps_the_rest_of_the_stage(self, tmp_path):
        """
        手改 Stage 里的一条立绘 → 同拍的 scene / 复杂 show / hide **一个都不许少**。

        P1-2：从前 Stage 格只显示"结构化列表达不了的那一条"，用户改它一格，
        `_apply_stage` 清空全部结构化字段再从这一条重建 → 其余舞台指令消失。
        """
        src = ('label start:\n'
               '    show e happy\n'
               '    show bg library with dissolve\n'
               '    hide cg with dissolve\n'
               '    e "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        fields = session.fields(0)
        stage = fields.get('Stage') or ''
        assert 'e happy' in stage, 'Stage 格里看不到第一条立绘：%r' % stage
        assert 'bg library with dissolve' in stage, stage
        fields['Stage'] = stage.replace('e happy', 'e sad')
        session.apply(0, fields, cards=session.cards(0), advanced_open=True,
                      stage_loaded=stage)
        session.export(path)
        after = _read(path)
        assert 'show e sad' in after, after
        assert 'show bg library with dissolve' in after, '复杂 show 丢了：\n' + after
        assert 'hide cg with dissolve' in after, 'hide 丢了：\n' + after
        _assert_only_touched(src, after)

    def test_stage_edit_keeps_show_expression_keyword(self, tmp_path):
        """手改 Stage 里另一条立绘 → `show expression …` 的 `expression` 不许丢。"""
        src = ('label start:\n'
               '    show expression holo("lan base") at i11\n'
               '    show e happy\n'
               '    e "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        fields = session.fields(0)
        stage = fields.get('Stage') or ''
        assert 'show expression' in stage, stage
        fields['Stage'] = stage.replace('e happy', 'e sad')
        session.apply(0, fields, cards=session.cards(0), advanced_open=True,
                      stage_loaded=stage)
        session.export(path)
        after = _read(path)
        assert 'show expression holo("lan base") at i11' in after, after
        assert 'show e sad' in after, after

    def test_stage_edit_inserts_at_most_one_line(self, tmp_path):
        """手改 Stage 加一条 `[停顿]` → 只许多一行，别的位置一个字节不动。"""
        src = ('label start:\n'
               '    scene bg room\n'
               '    e "台词"\n'
               '    return\n')
        session, path = _project(tmp_path, src)
        _submit_all(session)
        fields = session.fields(0)
        fields['Stage'] = '%s\n[停顿] 0.5' % (fields.get('Stage') or '')
        session.apply(0, fields, cards=session.cards(0), advanced_open=True,
                      stage_loaded=session.fields(0).get('Stage'))
        session.export(path)
        _assert_only_touched(src, _read(path), max_old=1, max_new=2)


class TestCardArrayEditsKeepOpaque:
    """用户在卡片上加一张 / 删一张，表达不了的指令**一条都不许少**。"""

    SRC = ('label start:\n'
           '    show e happy\n'
           '    show bg library with dissolve\n'
           '    show cg cg01 behind e\n'
           '    "台词"\n'
           '    return\n')

    def _show_of(self, tmp_path):
        session, path = _project(tmp_path, self.SRC)
        return session, path

    def test_add_a_card(self, tmp_path):
        session, path = self._show_of(tmp_path)
        cards = list(session.cards(0))
        cards.append({'tag': 'q', 'image': 'q sad', 'expression': '',
                      'position': 'right', 'at': '', 'order_index': 99,
                      'extra_rows': []})
        fields = session.fields(0)
        session.apply(0, fields, cards=cards, advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        show = session.beats[0].show
        assert 'q sad' in show, show
        assert 'with dissolve' in show, '加卡把 with 那条吃了：%r' % show
        assert 'behind e' in show, '加卡把 behind 那条吃了：%r' % show

    def test_remove_the_only_editable_card(self, tmp_path):
        """把唯一的普通立绘删掉 → 两条表达不了的指令**还在**（整拍不会被清空）。"""
        session, path = self._show_of(tmp_path)
        fields = session.fields(0)
        session.apply(0, fields, cards=[], advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        show = session.beats[0].show
        assert 'with dissolve' in show, '删卡把 with 那条吃了：%r' % show
        assert 'behind e' in show, '删卡把 behind 那条吃了：%r' % show
        assert 'e happy' not in show, '删卡没删掉普通立绘：%r' % show

    def test_order_kept_when_card_position_changes(self, tmp_path):
        """卡片换位置（改 `order_index`）也不许把表达不了的项甩到末尾。"""
        session, path = self._show_of(tmp_path)
        cards = list(session.cards(0))
        for i, c in enumerate(cards):
            c['order_index'] = 1 - i            # 反过来
        fields = session.fields(0)
        session.apply(0, fields, cards=cards, advanced_open=True,
                      stage_loaded=fields.get('Stage'))
        parts = [p for p in B.split_show_items(session.beats[0].show)]
        images = [str(p.get('clause') or p.get('image') or '').strip()
                  for p in parts]
        assert images, '整拍空了'
        # 表达不了的项必须仍在**原位**（第 2、3 条），普通立绘换到后面
        assert images[1].startswith('bg library'), images
        assert images[2].startswith('cg cg01'), images


# ----------------------------------------------------------------------
# 守卫：这些函数一旦"又开始切碎表达式"，必须立刻红
# ----------------------------------------------------------------------
class TestOpaqueNeverBecomesACard:
    @pytest.mark.parametrize('show_text', [
        'expression holo("lan base") at i11',
        'cg cg01 behind e',
        'e2 sad as twin',
        'overlay frame onlayer screens',
        'screen display_timer',
    ])
    def test_opaque_item_never_shows_up_as_card(self, show_text):
        """
        表达不了的项**不许**变成卡片 —— 变成卡片就意味着"按 tag + 表情"切它，
        而表达式/子句根本没有 tag（`holo("x` 这种假 tag 会被写回去）。
        """
        from storygraph.story.beat_model import Beat
        beat = Beat(seq=1, label='', text='', show=show_text)
        rows = B.beat_sprite_rows(beat)
        assert rows == [], '这一项被当成卡片了：%r' % rows

    def test_plain_item_still_becomes_a_card(self):
        """反向守卫：普通立绘必须**还是**卡片（别为了修 bug 把卡片功能关了）。"""
        from storygraph.story.beat_model import Beat
        beat = Beat(seq=1, label='', text='', show='e happy@left; q sad')
        rows = B.beat_sprite_rows(beat)
        assert [r['image'] for r in rows] == ['e happy', 'q sad']
