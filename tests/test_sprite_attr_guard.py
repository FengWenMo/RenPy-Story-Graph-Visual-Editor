# -*- coding: utf-8 -*-
"""
立绘属性门禁（导出前只读扫描）的判据 —— 2026-11-05
==================================================

用户定的两条：

> 只报，不乱改……导出时也要再警告一次，并且说明其可能导致无法运行。
> 若是没有声明却依旧能跑……会有这种情况吗？

**有**：`aa` 不在 `layeredimage ye` 里，但在项目 `defined_words` 里，
剧本里用了 41 处，引擎忽略它正好落到默认层。所以判据不能是"只许声明过的
属性"——那会把 `aa` 这类哨兵、`_hd_cut` 这类资源表内部件、`config.
adjust_attributes` 映射出来的词全部误杀。

这里钉的是**窄规则**：

  · 四类证据（声明 / 资源表 / defined_words / 同名图片）都不沾的词才报；
  · 拼法变体（`base_hand_down` ↔ `base_handdown`）给 `fix`，但**不自动改**；
  · `aa` / 自定义词 / 未知 tag 一律不报；
  · 扫描只读，不改 `Beat.show`。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storygraph.app import sprite_attr_guard as AG                 # noqa: E402
from storygraph.story.beat_model import Beat                      # noqa: E402

YE_DECLARED = ['ahah', 'gratified', 'happy', 'hi', 'base_handdown',
               'base_swimsuit']


class _Sym:
    """只回答属性门禁要的四个问题（真 `Symbols` 的替身）。"""

    def __init__(self, declared=None, attrs=None, words=None, images=None):
        self._declared = declared if declared is not None else {'ye': YE_DECLARED}
        self._attrs = attrs if attrs is not None else {
            'ye': YE_DECLARED + ['_hd_cut', 'base_handdown_mask']}
        self._words = set(words or {'aa'})
        self._images = dict(images or {})

    def declared_attrs_of(self, tag):
        return list(self._declared.get(tag, ()))

    def attrs_of_tag(self, tag):
        return list(self._attrs.get(tag, ()))

    def defined_words(self):
        return sorted(self._words)

    def token_image_path(self, tag, token):
        return self._images.get((tag, token), '')


_UNSET = object()


def _hits(spec, sym=_UNSET):
    return AG.unknown_attrs_of_spec(spec, _Sym() if sym is _UNSET else sym)


class TestUnknownAttrsOfSpec:
    def test_spelling_variant_is_reported_with_the_fix(self):
        hits = _hits('ye base_hand_down gratified')
        assert len(hits) == 1, hits
        hit = hits[0]
        assert hit['attr'] == 'base_hand_down'
        assert hit['fix'] == 'base_handdown'
        assert 'base_handdown' in hit['reason']
        assert '忽略' in hit['consequence']

    def test_defined_word_is_not_reported(self):
        """`aa` 是项目定义的服装代号：不在 declared 里，但**不许报**。"""
        assert _hits('ye aa happy') == []

    def test_declared_word_is_not_reported(self):
        assert _hits('ye base_handdown happy') == []

    def test_resource_table_internal_part_is_not_reported(self):
        assert _hits('ye _hd_cut happy') == []

    def test_image_file_on_disk_counts_as_known(self):
        sym = _Sym(images={('ye', 'lose'): 'game/images/ye/lose.png'})
        assert _hits('ye lose happy', sym) == []

    def test_unknown_without_near_miss_has_no_fix(self):
        hits = _hits('ye custom_thing happy')
        assert len(hits) == 1, hits
        assert hits[0]['fix'] == ''
        assert '没找到拼法相近' in hits[0]['reason']

    def test_unknown_tag_is_left_to_the_identity_guard(self):
        """`nobody` 没声明过 → 不是这一层的事（identity_guard 报）。"""
        assert _hits('nobody happy') == []

    def test_at_clause_is_not_an_attribute(self):
        hits = _hits('ye base_hand_down happy at i11')
        assert [h['attr'] for h in hits] == ['base_hand_down'], hits

    def test_no_symbols_means_no_judgement(self):
        assert _hits('ye base_hand_down happy', sym=None) == []


class TestScanSpriteAttrs:
    def _beats(self):
        return [
            Beat(seq=1, label='ch', text='一', show='ye base_hand_down hi'),
            Beat(seq=2, label='ch', text='二', show='ye aa happy'),
            Beat(seq=3, label='ch', text='三',
                 show='lan aa shy; ye base_hand_down hi'),
        ]

    def test_finds_only_the_typo_and_keeps_the_original(self):
        beats = self._beats()
        before = [(b.show, b.seq) for b in beats]
        res = AG.scan_sprite_attrs(beats, _Sym())
        assert [s.seq for s in res.suspects] == [1, 3], res.suspects
        assert res.checked == 4, res.checked
        assert [(b.show, b.seq) for b in beats] == before, '扫描必须只读'
        assert 'base_handdown' in res.suspects[0].reason

    def test_clean_beats_are_silent(self):
        beats = [Beat(seq=1, label='x', text='t', show='ye aa happy')]
        res = AG.scan_sprite_attrs(beats, _Sym())
        assert not res
        assert res.report() == ''

    def test_no_symbols_is_degraded_not_clean(self):
        res = AG.scan_sprite_attrs(self._beats(), None)
        assert res.degraded is True
        assert not res

    def test_no_declared_tags_is_degraded(self):
        """有 show、但项目一条声明都没有 → 判不了，不许假装干净。"""
        res = AG.scan_sprite_attrs(self._beats(), _Sym(declared={}, attrs={}))
        assert res.degraded is True
        assert res.judged == 0

    def test_report_says_it_can_run_but_looks_wrong(self):
        res = AG.scan_sprite_attrs(self._beats(), _Sym())
        text = res.report()
        assert '第 1 拍' in text and 'base_handdown' in text
        assert '最坏会让游戏起不来' in text, '要提到可能跑不起来'
        assert '能跑' in text and '画面不是你要的' in text, \
            '也要说清最常见只是画面错'
