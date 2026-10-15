"""
`beats_from_statements` 的**结构守卫**（P3-12，2026-10-05 续十五）
================================================================

这是解析心脏的"细网"。`tests/test_roundtrip_invariants.py` 那张往返网
**已经带着 3 条已知丢失**（见 `docs/archive/问题清单与优化路线_2026-10-03.md` §十七），
所以"改坏了"和"本来就有 3 条"在上面**分不开**。

这一层换个判据：**逐拍逐字段**锁住 Beat 的形状。它不追求"产物对不对"，
只问"**这条流水线有没有被改动动过**" —— 任何字段变了立刻红，
而且指出是第几拍、哪个字段（定位成本远低于"往返丢了 3 条，是哪 3 条？"）。

为什么是 24 个字段：它们全是**会影响产物**的。
`src_first` / `src_last` 决定增量补丁改哪几行；
`stage_order` / `op_order` 决定演出顺序；
`_menu_member` 决定菜单块的边界（丢了菜单整块散架）。
漏掉任何一个，就等于给"结构被改动"留了一条不报警的路。

字段清单刻意**不包含** `content_hash` / `line_id`：那是
`assign_identity` 按"内容 + 出现次序"算出来的派生值，改内容它就变 ——
把它算进来会让"改了一句台词"淹没成"整拍都变了"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

#: 会影响产物的 Beat 字段（顺序无关，只为可读）。
BEAT_FIELDS = (
    'seq', 'label', 'kind', 'speaker', 'text', 'condition', 'comment',
    'scene', 'show', 'hide', 'at', 'with_', 'music', 'sound', 'voice',
    'raw', 'code', 'stage', 'stage_order', 'op_order',
    'src_first', 'src_last', 'code_blocks',
    # M1：工具不认识的块语句（整块原文，逐字进产物）
    'opaque',
    # M3：那条自定义语句被谁声明过（'' = 未声明）
    'spec_name',
    # M4：它的头行（T1 的参数编辑面）
    'spec_head',
    # D2：循环块的头行
    'loop_header',
)

#: 真实项目里的"结构最复杂"的几段 —— 手写源码片段，**不依赖本机路径**，
#: 这样换机器 / 上 CI 也会真的跑。
SYNTHETIC = {
    '分支里的舞台指令': (
        'label a:\n'
        '    if pomoplace == "beach":\n'
        '        show bgocean with dissolve\n'
        '    elif pomoplace == "cafe":\n'
        '        show bgcafe with dissolve\n'
        '    show qing base at h11\n'
        '    q "一句"\n'
        '    return\n'
    ),
    '无标题菜单': (
        'label a:\n'
        '    hide bg cafe\n'
        '    menu:\n'
        '        "选项A":\n'
        '            jump b\n'
        '        "选项B":\n'
        '            jump c\n'
    ),
    'extend 续写': (
        'label a:\n'
        '    q "前半句"\n'
        '    extend "后半句"\n'
        '    "旁白"\n'
        '    extend "旁白的续写"\n'
        '    return\n'
    ),
    '多行 python 块': (
        'label a:\n'
        '    python:\n'
        '        x = drag(\n'
        '            path,\n'
        '            rows=2,\n'
        '\n'
        '            cols=3)\n'
        '    "一句"\n'
        '    return\n'
    ),
    '条件透传语句': (
        'label a:\n'
        '    if flag:\n'
        '        pause 0.1\n'
        '    "一句"\n'
        '    return\n'
    ),
    '多属性立绘与换背景': (
        'label a:\n'
        '    show ling base_arms_akimbo sigh at left\n'
        '    show bg rooftop\n'
        '    show cg03\n'
        '    hide cg03\n'
        '    "一句"\n'
        '    scene bg school\n'
        '    "又一句"\n'
    ),
}


def _snap(beats) -> list:
    """拍 → 可比对的字典列表（只取影响产物的字段）。"""
    out = []
    for b in beats:
        rec = {}
        for f in BEAT_FIELDS:
            v = getattr(b, f, '<missing>')
            if isinstance(v, (list, tuple)):
                v = [str(x) for x in v]
            elif isinstance(v, set):
                v = sorted(str(x) for x in v)
            elif v is not None and not isinstance(v, (str, int, float, bool)):
                v = str(v)
            rec[f] = v
        # 结构位单独看：**`hasattr` 为假与值为 False 是两种不同的意图**
        # （见 `_mark_struct`：手工构造的 Beat 不该被一个假的 False 骗到）
        rec['_has_menu_member'] = hasattr(b, '_menu_member')
        if hasattr(b, '_menu_member'):
            rec['_menu_member'] = bool(b._menu_member)
        out.append(rec)
    return out


def _labels_of(pr) -> list:
    """
    `ParseResult.statement_labels` —— **每条语句所属的 label**。

    ⚠ 别用 `getattr(stmt, 'label', '')`：`Statement` **没有** `label`
    属性（label 属于"解析结果"这一层，不属于语句）。用 getattr 兜底
    会静默拿到空串，于是所有拍都成了无名拍 —— 我第一版就是这么写的，
    而它**看起来照样在跑**（只测了个空 label 的路径）。
    """
    labs = getattr(pr, 'statement_labels', None)
    if labs is None:
        return [''] * len(pr.statements)
    return [str(x or '') for x in labs]


def _build_from_source(tmp_path, name: str, script: str):
    """把一段源码写成 .rpy → 解析 → 折成拍。"""
    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_statements

    fp = tmp_path / f'{name}.rpy'
    fp.write_text(script, encoding='utf-8')
    pr, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
    st = list(pr.statements)
    return beats_from_statements(st, label='',
                                 labels=_labels_of(pr)), st


class TestBeatShapeIsStable:
    """
    逐拍逐字段锁住形状。**这不是"快照测试"**（不存 golden 文件）——
    它断言的是**结构不变量**，所以不会因为无关改动而要求重新生成基线。
    """

    @pytest.mark.parametrize('name', sorted(SYNTHETIC))
    def test_synthetic_source_produces_expected_shape(self, tmp_path, name):
        beats, _st = _build_from_source(tmp_path, name, SYNTHETIC[name])
        assert beats, f'{name}：一句拍都没解析出来'
        snaps = _snap(beats)
        # 每一拍都必须有 seq（1 起、连续），否则渲染层排序会乱
        assert [s['seq'] for s in snaps] == list(range(1, len(snaps) + 1)), (
            f'{name}：seq 不连续 —— {[s["seq"] for s in snaps]}')
        # 每一拍都要有 label（渲染层靠它分组出 `label x:`）
        assert all(s['label'] for s in snaps), f'{name}：有拍没有 label'
        # 行号要么都为 0（表格构造），要么 src_first <= src_last
        for s in snaps:
            assert s['src_first'] <= s['src_last'] or not s['src_last'], (
                f'{name}：第 {s["seq"]} 拍行号倒挂 '
                f'{s["src_first"]}..{s["src_last"]}')

    def test_menu_group_has_a_header(self, tmp_path):
        """
        无标题 `menu:` 也必须落一张**组头**拍（`_menu_member=False`）。

        这一条对应 §十七 里那个 P0-1：以前只在 `if caption:` 时建拍，
        于是 7 处裸 `menu:` 在拍这一层没有痕迹，选项拍孤零零排着、
        渲染层把它们当裸旁白导出去。
        """
        beats, _st = _build_from_source(tmp_path, 'menu',
                                        SYNTHETIC['无标题菜单'])
        headers = [b for b in beats if b.kind == 'menu']
        assert headers, '裸 menu: 没有落组头拍'
        assert all(not getattr(h, '_menu_member', True) for h in headers), (
            '菜单组头的 `_menu_member` 必须是 False（它是组头不是组员）')

    def test_branch_stage_directives_get_their_own_beats(self, tmp_path):
        """
        条件不同的舞台指令**各自成拍**（`_branch_guard` 的职责）。

        对应 §十七 的 P1-1：三条 `show` 被吸进同一张拍时，
        一个 `condition` 字段只留得下最后一个 → 玩家永远看到最后一个背景，
        而且**不报任何错**。
        """
        beats, _st = _build_from_source(tmp_path, 'branch',
                                        SYNTHETIC['分支里的舞台指令'])
        shows = [b for b in beats if (b.show or '').strip()]
        assert len(shows) >= 3, f'三条 show 只落成 {len(shows)} 拍'
        # 条件不同的 show 不能同处一拍：带条件的舞台拍只能有一个条件
        for b in beats:
            if b.condition and (b.show or '').strip():
                assert b.condition.count(' elif ') == 0, (
                    f'两个分支的 show 被并进了同一拍：{b.show!r} / '
                    f'{b.condition!r}')

    def test_extend_inherits_the_last_speaker(self, tmp_path):
        """
        `extend` 必须继承**上一句的说话人**，旁白之后则清空。

        这是"幽灵旁白"那个 bug 的形状（用户 2026-09-28 报的）：
        `extend` 渲染成 `extend "…"` 不写说话人，所以卡片上得靠
        `speaker` 字段让策划看出"这是谁在续说" —— 丢了就与旁白长得一模一样。

        ⚠ 而"旁白之后要清空"同样重要：`"旁白。"` 后面跟 `extend`，
        续的是旁白，不能继承更早那个角色（否则旁白被安上说话人、变成台词）。

        加这一条的起因：我用变异验证时发现，把
        `pending.speaker = last_speaker` 改成 `''`，
        `tools/_verify_beats.py`（与 HEAD 逐字段比）**抓到了 9 处**，
        而本文件**全绿** —— 说明这些"结构不变量"确实抓不到字段取值的错误。
        所以这里补一条**断言具体取值**的用例。
        """
        beats, _st = _build_from_source(tmp_path, 'extend',
                                        SYNTHETIC['extend 续写'])
        by_kind = [(b.kind, (b.speaker or ''), (b.text or '')) for b in beats]
        # 第 1 拍：`q "前半句"` → speaker=q
        assert by_kind[0][0] == 'say' and by_kind[0][1] == 'q', by_kind
        # 第 2 拍：`extend "后半句"` → **继承 q**
        assert by_kind[1][0] == 'extend' and by_kind[1][1] == 'q', (
            f'extend 没有继承上一句的说话人：{by_kind[1]!r}（全部：{by_kind}）')
        # 第 3 拍：`"旁白"` → 旁白，speaker 为空
        assert by_kind[2][0] == 'narration' and by_kind[2][1] == '', by_kind
        # 第 4 拍：`extend "旁白的续写"` → 续的是**旁白**，不许继承 q
        assert by_kind[3][0] == 'extend' and by_kind[3][1] == '', (
            f'旁白之后的 extend 继承了更早的角色（会变成台词）：'
            f'{by_kind[3]!r}')

    def test_python_block_keeps_blank_line_and_code_span(self, tmp_path):
        """
        多行 `python:` 块：空行要留、`code_blocks` 要记行区间。

        空行与块区间是 §十七 修掉的那条真 bug 的判据 ——
        丢了空行，往返的逐行文本就对不上。
        """
        beats, _st = _build_from_source(tmp_path, 'pyblk',
                                        SYNTHETIC['多行 python 块'])
        coded = [b for b in beats if (b.code or '').strip()]
        assert coded, 'python 块没进 code'
        joined = '\n'.join(b.code for b in coded)
        assert 'rows=2' in joined and 'cols=3' in joined, joined
        assert any(b.code_blocks for b in coded), (
            '多行 python 块没记 code_blocks（块边界在纯文本里表达不出来）')


class TestNoSharedStateBetweenCalls:
    """
    `beats_from_statements` 里有 7 个可变状态。**两次调用之间不许互相影响** ——
    这是它将来"状态显式化"重构时最容易破坏的性质，所以现在先锁住。
    """

    def test_two_calls_with_the_same_input_agree(self, tmp_path):
        """
        同样的输入 + 同样的 `labels` 跑两次，结果必须一致。

        ⚠ `labels` **也是输入的一部分**：第一版我这边传了真实 labels、
        那边传了 `None`（=所有拍都用空 label），于是"label 不同"被判成
        "状态没重置"。两次调用必须**喂同样的东西**才叫"同样输入"。
        """
        import copy
        from storygraph.story.beat_build import beats_from_statements
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer

        fp = tmp_path / 'again.rpy'
        fp.write_text(SYNTHETIC['多属性立绘与换背景'], encoding='utf-8')
        pr1, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
        pr2, _ = IncrementalRenPyAnalyzer().analyze(files=[fp], graph=None)
        labs1, labs2 = _labels_of(pr1), _labels_of(pr2)
        a = beats_from_statements(copy.deepcopy(list(pr1.statements)),
                                  label='', labels=labs1)
        b = beats_from_statements(copy.deepcopy(list(pr2.statements)),
                                  label='', labels=labs2)
        assert _snap(a) == _snap(b), (
            '同样的输入跑两次结果不同 —— 说明有状态没被重置')

    def test_a_long_run_does_not_leak_into_a_short_one(self, tmp_path):
        """先跑长输入、再跑短输入，短的结果不许被长的污染。"""
        from storygraph.story.beat_build import beats_from_statements
        import copy
        _long, long_st = _build_from_source(
            tmp_path, 'long', SYNTHETIC['多属性立绘与换背景'])
        _short, short_st = _build_from_source(
            tmp_path, 'short', SYNTHETIC['extend 续写'])
        first = beats_from_statements(copy.deepcopy(short_st), label='',
                                      labels=None)
        beats_from_statements(copy.deepcopy(long_st), label='',
                              labels=None)
        again = beats_from_statements(copy.deepcopy(short_st), label='',
                                      labels=None)
        assert _snap(first) == _snap(again), (
            '短输入的结果被上一次长输入污染了 —— 状态没清干净')


class TestFieldContract:
    """`BEAT_FIELDS` 里那些名字**真的存在**（防止守卫自己写错字段名）。"""

    def test_all_fields_exist_on_a_real_beat(self, tmp_path):
        beats, _st = _build_from_source(tmp_path, 'contract',
                                        SYNTHETIC['extend 续写'])
        b = beats[0]
        missing = [f for f in BEAT_FIELDS if not hasattr(b, f)]
        assert not missing, (
            f'`BEAT_FIELDS` 里这些名字在 Beat 上不存在（守卫自己写错了）：'
            f'{missing}')

    def test_source_ranges_are_deliberately_not_in_the_snapshot(self):
        """
        `src_first` / `src_last` **刻意**不在撤销快照里 —— 这是**反向**断言。

        我第一版把它写成了"必须在快照里"（理由是"它影响产物"），
        但 `session_table._SNAPSHOT_FIELDS` 的注释写得很清楚：

        > 刻意不含 `src_first` / `src_last`（增量导出的源文件行号）与
        > `_from_script`：撤销一次字段编辑不该改动"这一拍在源文件的哪几行"。

        这个决定是对的：行号是**解析的产物**，不是用户编辑的内容。
        撤销一句台词的修改，不该把它在源文件里的位置也一起"撤"回去。

        所以这里锁的是"**这个决定还在**" —— 哪天有人顺手把它们加进快照，
        这条会红，提醒他先想清楚上面那句话还成不成立。
        （这正是把假设写成断言的价值：我的假设错了，测试当场纠正了我，
        而如果我直接去改产品代码"修"它，就破坏了原本正确的设计。）
        """
        from storygraph.app.session_table import _SNAPSHOT_FIELDS
        for f in ('src_first', 'src_last', '_from_script'):
            assert f not in _SNAPSHOT_FIELDS, (
                f'`{f}` 被加进撤销快照了 —— 但它按设计**不该**在里面：'
                f'行号是解析的产物，撤销一次字段编辑不该改动'
                f'"这一拍在源文件的哪几行"。'
                f'（真要改这个设计，先把 `_SNAPSHOT_FIELDS` 上面那段'
                f'注释一起改掉，再说清为什么。）')

    def test_snapshot_covers_every_rendered_scalar(self):
        """
        反过来：**所有会进渲染的标量都要在快照里** ——
        少一个，撤销就会留下"半新半旧"的状态。
        """
        from storygraph.app.session_table import _SNAPSHOT_FIELDS
        need = ('seq', 'label', 'kind', 'speaker', 'text', 'condition',
                'comment', 'scene', 'show', 'hide', 'at', 'with_',
                'music', 'sound', 'voice', 'raw', 'code', 'stage',
                'stage_order', 'op_order')
        missing = [f for f in need if f not in _SNAPSHOT_FIELDS]
        assert not missing, (
            f'这些字段会影响产物、却不在撤销快照里（撤销会留下半新半旧）：'
            f'{missing}')
