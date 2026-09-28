"""
tests/test_card_preview_fidelity.py
==================================
**卡片 → 舞台效果** 的一致性（用户 2026-09-28 要求：

    > 卡片后续能够被还原回舞台效果……

这条比"生成代码不报错"强得多：它要求
**"卡片上写的" == "生成的代码" == "舞台快照"** 三者说的是同一件事。

做法：把卡片/Beat 生成的代码里抽出的 show/hide/scene 语句，与
`stage_snapshot` 算出来的变化**逐条比对**。

为什么必须锁住：这三者一旦不一致，策划看到的就是"我改的和生成的不一样"
—— 那是信任问题，不是显示问题。
"""

import re

from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
from storygraph.story.script_table import (
    Beat, beats_from_parse_result, open_rpy_as_table, render_beats_to_rpy,
)
from storygraph.story.stage_snapshot import snapshot_rows

#: 从生成的代码里抽舞台语句：`show ling happy at left`
STAGE_LINE = re.compile(r'^\s*(show|hide|scene)\s+(.+?)\s*$')


def stage_ops(text):
    """生成代码 → [(verb, 图像名, at)]。"""
    out = []
    for line in text.splitlines():
        m = STAGE_LINE.match(line)
        if not m:
            continue
        verb, rest = m.group(1), m.group(2)
        # 去掉 with / behind 等后缀，只留图像名与 at
        rest = re.split(r'\s+(?:with|behind|onlayer|zorder|as)\s+', rest)[0]
        at = ''
        mm = re.search(r'\sat\s+(\S+)', rest)
        if mm:
            at = mm.group(1)
            rest = rest[:mm.start()].strip()
        out.append((verb, rest.strip(), at))
    return out


class TestCardPreviewFidelity:
    """
    同一拍：**代码里的舞台语句** 与 **快照里的变化** 必须对应。

    快照回答"这一拍舞台上变成什么样"，代码回答"实际会执行什么"——
    两者同源（都从 Beat 派生），但走的是两条不同的推导路径，
    所以必须用测试把它们钉在一起。
    """

    def test_show_in_code_and_snapshot_agree(self):
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 show='ling close_happy@left')
        code = render_beats_to_rpy([b])
        ops = stage_ops(code)
        assert ('show', 'ling_close_happy', 'left') in ops, ops

        snap = snapshot_rows([b])[0]
        assert 'ling close_happy@left' in snap['actors'], snap['actors']
        # 快照的变化清单也说"登场了"
        assert '+ ' in snap['display_changes']

    def test_scene_clears_in_both(self):
        """
        `scene` 清空全部立绘。

        注意顺序**由 `stage_order` 决定**，不能想当然：
          · `stage_order='show,scene'` → 先 show 后清场 → **台上没人**
          · `stage_order='scene,show'` → 先清场后 show → **台上有人**
        这和真实章节第 43 拍（`show,hide,scene` → 台上没人）是同一回事。
        """
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 scene='bg room', show='ling happy@left',
                 stage_order='show,scene')          # 先登场、后被清掉
        code = render_beats_to_rpy([b])
        assert 'scene' in [o[0] for o in stage_ops(code)]
        snap = snapshot_rows([b])[0]
        assert snap['actors'] == '', snap['actors']
        changes = snap['display_changes'] or ''
        assert '清空立绘' in changes, changes
        # 消歧说明也应出现（登场又被清掉）
        assert '注：' in changes, changes

    def test_scene_then_show_keeps_the_actor(self):
        """反向顺序：先清场再 show → 台上有人（第 67 拍那种）。"""
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 scene='bg room', show='ling happy@left',
                 stage_order='scene,show')
        snap = snapshot_rows([b])[0]
        assert 'ling' in snap['actors'], snap['actors']
        assert '注：' not in (snap['display_changes'] or '')

    def test_hide_in_code_and_snapshot_agree(self):
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 show='ling happy@left')
        b2 = Beat(seq=2, label='t', text='台词2', speaker='e',
                  hide='ling')
        code = render_beats_to_rpy([b, b2])
        assert ('hide', 'ling', '') in stage_ops(code)
        rows = snapshot_rows([b, b2])
        assert rows[0]['actors'] != ''
        assert rows[1]['actors'] == '', '快照没说退场'

    def test_multi_sprite_card_matches_snapshot(self):
        """多立绘：卡片上三张，代码三条 show，快照三个演员。"""
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 show='ling happy@left; mary smile@right; bob sad@center')
        code = render_beats_to_rpy([b])
        ops = [o for o in stage_ops(code) if o[0] == 'show']
        assert len(ops) == 3, ops
        snap = snapshot_rows([b])[0]
        for who in ('ling', 'mary', 'bob'):
            assert who in snap['actors'], (who, snap['actors'])

    def test_condition_wraps_in_code(self):
        b = Beat(seq=1, label='t', text='台词', speaker='e',
                 show='ling happy@left', condition='love >= 5')
        code = render_beats_to_rpy([b])
        assert 'if love >= 5:' in code
        # 条件不改变"这一拍舞台上是谁"
        snap = snapshot_rows([b])[0]
        assert 'ling' in snap['actors']


class TestRealChapterCardMatchesGenerated:
    """
    真实章节上做一次全量核对：**代码里的舞台语句能被快照解释**。

    这里必须说清一件容易搞错的事：
    **快照的「本拍变化」是 delta，不是指令流水账。**
    重复 show 同一个立绘（幂等写法）在代码里是一条 show，
    但状态没变，所以快照**故意不记**。真实章节 `chapter1_3` 实测：
    代码 60 条 show，快照只有 48 条 `+` 变化 —— 差的 12 条就是幂等重复。

    所以核对分两部分：
      · **条数**：代码 show 条数 ≥ 快照"登场/换装"条数 + "退场"条数
        （幂等的那部分不产生变化）；
      · **语义**：快照里出现的每个立绘，都能在代码里找到对应 show。
    """

    # 真实项目路径统一从 `_source_paths` 取（可用环境变量
    # STORYGRAPH_REAL_PROJECT 覆盖，换机器不必改代码）
    from _source_paths import REAL_CHAPTER

    REAL = str(REAL_CHAPTER)

    def _load(self):
        from pathlib import Path
        p = Path(self.REAL)
        if not p.exists():
            import pytest
            pytest.skip('本机没有 chapter1_3.rpy')
        beats = beats_from_parse_result(
            ParseResultBuilder().build(RenPyParser().parse(p)))
        return beats

    @staticmethod
    def _delta_counts(rows):
        adds = sum(len([c for c in (r['display_changes'] or '').splitlines()
                        if c.startswith('+')]) for r in rows)
        dels = sum(len([c for c in (r['display_changes'] or '').splitlines()
                        if c.startswith('−')]) for r in rows)
        return adds, dels

    def test_snapshot_never_invents_sprites(self):
        """
        **关键方向**：快照里出现的立绘，代码里必须真的 show 过。
        快照凭空造出一个演员 = 骗人。
        """
        beats = self._load()
        code = render_beats_to_rpy(beats)
        shown = {o[1].replace('_', ' ').split()[0]
                 for o in stage_ops(code) if o[0] == 'show'}
        rows = snapshot_rows(beats)

        invented = set()
        for r in rows:
            for who in (r['actors'] or '').split(';'):
                who = who.strip()
                if not who:
                    continue
                tag = who.split()[0]
                if tag and tag not in shown:
                    invented.add(tag)
        assert not invented, f'快照里有代码从没 show 过的立绘：{invented}'

    def test_counts_are_consistent_with_idempotent_shows(self):
        """
        代码 show 条数 ≥ 快照变化条数（幂等重复不产生变化）。
        差得太多就说明两边不同源了。
        """
        beats = self._load()
        code = render_beats_to_rpy(beats)
        ops = stage_ops(code)
        code_shows = len([o for o in ops if o[0] == 'show'])
        code_hides = len([o for o in ops if o[0] == 'hide'])
        adds, dels = self._delta_counts(snapshot_rows(beats))

        assert code_shows >= adds, f'show {code_shows} < 变化 {adds}'
        assert code_hides >= dels, f'hide {code_hides} < 退场 {dels}'
        # 差的应该只是幂等重复，不该是"快照漏掉一半"
        assert code_shows - adds <= code_shows * 0.4, (
            f'差得太多：代码 {code_shows} 条 show，快照只记了 {adds} 条变化 '
            f'——两边可能不同源')

