# -*- coding: utf-8 -*-
"""
改完卡片之后，"当前舞台"说台上没人（2026-11-14 用户截图）
=========================================================

用户："好神奇……？为什么选中了立绘，却无法展示出来……？这个是bug吗？"

现场（`sr_general/chapter2_3_1` 第 5 拍，源码顺序是 scene 在前、show 在后）：

    scene black
    show lan clothes2 uh at f11
    $ renpy.transition(...)
    l "真的要去教室吗？……"

卡片上**立绘卡在**（`lan clothes2 uh`，位置 f11），可「当前舞台」写
`立绘：（台上没人）`、小图一片黑，还附一句"上面的「+ …（登场）」在本拍内
又被「scene」清掉了（源码顺序如此）"——那句话在说谎：源码里 scene **在前**。

## 根因：`sync_stage_order` 拿 verb 名去比带下标的 token

`FieldWriter.write` 每次提交都调 `Beat.sync_stage_order()`，想把
"卡片上新加的立绘"补进 `StageOrder` 列。它写的是：

    if verb not in merged:            # merged = ['scene#0', 'show#0']
        merged.append(verb)           # 'show' not in [...#0] → 永远为真

于是**每次**改卡片/翻页顺带提交，`scene`、`show` 都被追加一遍，
`stage_order` 变成 `'scene#0,show#0,scene,show'`。`op_tokens_full()` 多出
一条 `('scene', None)`，舞台快照照它**又清一次台** → 刚站上去的人被抹掉。

只在这一种拍上看得出来：**同一拍里既有 `scene` 又有 `show`**（其余拍
`beat.scene` 是空的，重复的 scene token 什么也不做）。

本文件钉两件事：
  · 归一化/回写不再重复（verb 名比较，`#idx` 剥掉）；
  · 就算拿到历史坏数据（内存里已经是 `…,scene,show`），快照也只认一个 scene。
"""
from __future__ import annotations

import pytest

CH = ('label ch01:\n'
      '    scene black\n'
      '    show lan clothes2 uh at f11\n'
      '    l "真的要去教室吗？"\n'
      '    return\n')


def _beat(**kw):
    from storygraph.story.beat_model import Beat

    base = dict(seq=1, label='ch01', kind='say', text='真的要去教室吗？')
    base.update(kw)
    return Beat(**base)


# ---------------------------------------------------------------------------
#  ① 归一化 / 回写：不再追加重复 token
# ---------------------------------------------------------------------------

class TestStageOrderIsNotDuplicated:

    def test_逐条下标的形式不会再被追加一遍(self):
        """`scene#0,show#0` 里已经有 scene / show 了 —— 一个都不许再加。"""
        b = _beat(scene='black', show='lan clothes2 uh@f11',
                  stage_order='scene#0,show#0')
        assert b.merged_stage_order() == 'scene#0,show#0'
        b.sync_stage_order()
        assert b.stage_order == 'scene#0,show#0', b.stage_order
        # 真源那一层也别多出第二条 scene
        scenes = [v for v, _i in b.op_tokens_full() if v == 'scene']
        assert len(scenes) == 1, b.op_tokens_full()

    def test_老格式里缺_verb_时照旧补上(self):
        """只写 `scene`（老表）而卡片上加了立绘 → 仍然要补一个 `show`。"""
        b = _beat(scene='black', show='lan uh@f11', stage_order='scene')
        b.sync_stage_order()
        assert b.stage_order == 'scene,show', b.stage_order

    def test_污染过的值读回来能自愈(self):
        """完全相同的 token 去重；重复的 `scene`（单值）一律丢掉。"""
        b = _beat(show='lan uh@f11', stage_order='show#0,show#0')
        assert b.merged_stage_order() == 'show#0'

        b2 = _beat(scene='black', show='lan uh@f11',
                   stage_order='scene#0,show#0,scene,show')
        merged = b2.merged_stage_order()
        assert merged == 'scene#0,show#0,show', merged
        scenes = [t for t in merged.split(',') if t.split('#')[0] == 'scene']
        assert scenes == ['scene#0'], scenes

    def test_合并只是算_不写回(self):
        b = _beat(scene='black', show='lan uh@f11', stage_order='scene')
        assert b.merged_stage_order() == 'scene,show'
        assert b.stage_order == 'scene', 'merged_stage_order 不许改数据'

    def test_手改_Stage_之后_hydrate_也不会再追加一遍(self):
        """
        同一个坑的第二处：`beat_build._hydrate_stage` 也拿 verb 名比原始 token。

        用户手改「舞台（高级）」那一格之后 `apply` 会跑 `hydrate_from_dsl`，
        它把 DSL 里出现的 verb 补进顺序列 —— 补的是**已经在了的**那两条。
        """
        from storygraph.story.beat_build import hydrate_from_dsl
        from storygraph.story.beat_render import make_symbols

        b = _beat(scene='black', show='lan clothes2 uh@f11',
                  stage_order='scene#0,show#0',
                  stage='show lan clothes2 uh at f11')
        hydrate_from_dsl([b], make_symbols(None))
        assert b.stage_order == 'scene#0,show#0', b.stage_order


# ---------------------------------------------------------------------------
#  ② 快照：一拍只有一个 scene，第二条 token 不许再清一次台
# ---------------------------------------------------------------------------

class TestSnapshotIgnoresTheSecondScene:

    def test_历史坏数据也不会把立绘清掉(self):
        from storygraph.story.stage_snapshot import stage_snapshots

        b = _beat(scene='black', show='lan clothes2 uh@f11',
                  stage_order='scene#0,show#0,scene,show')
        row = stage_snapshots([b])[-1]
        assert row['actors'], '有人被 scene 又清了一遍'
        assert 'lan' in str(row['actors'])
        # 也不许留那句"本拍内又被 scene 清掉了"
        assert not any('清掉了' in str(c.get('text', ''))
                       for c in row['changes']), row['changes']


# ---------------------------------------------------------------------------
#  ③ 端到端：用户那条路（载入 → 改/提交 → 当前舞台）
# ---------------------------------------------------------------------------

class TestCommitDoesNotChangeTheStage:

    @pytest.fixture
    def session(self, tmp_path):
        from storygraph.app.editor import EditorSession

        root = tmp_path / 'proj'
        (root / 'game').mkdir(parents=True, exist_ok=True)
        path = root / 'game' / 'ch01.rpy'
        path.write_text(CH, encoding='utf-8')
        s = EditorSession(project_root=root)
        s.load(path)
        return s

    def _say_index(self, s):
        return next(i for i, b in enumerate(s.beats)
                    if '真的要去教室吗' in str(b.text or ''))

    def test_原样提交一次_台上还是那个人(self, session):
        idx = self._say_index(session)
        before = session.stage_state(idx)
        session.apply(idx, dict(session.fields(idx)),
                      cards=[dict(c) for c in session.cards(idx)])
        after = session.stage_state(idx)
        assert [a.get('spec') for a in before.get('actors') or ()] == \
            [a.get('spec') for a in after.get('actors') or ()]
        assert after.get('actors'), '提交一次之后台上没人了'

    def test_改了表情之后_当前舞台写的是新表情(self, session):
        idx = self._say_index(session)
        cards = [dict(c) for c in session.cards(idx)]
        cards[0]['expression'] = 'stare'
        cards[0].pop('inherited', None)
        session.apply(idx, dict(session.fields(idx)), cards=cards)
        digest = session.stage_digest_text(idx)
        assert 'stare' in digest, digest
        assert '台上没人' not in digest, digest
