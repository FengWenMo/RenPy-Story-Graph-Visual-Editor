# -*- coding: utf-8 -*-
"""
补丁被拒时的"为什么"（2026-11-08 第十九轮）。

用户原话 + 截图：

> 工具报错说有19处不统一！不给我增量导出……查原因！

`19 句台词在补丁结果里找不到` 只说了**多少**，说不出**为什么**；而三种原因的
处理办法完全不同：

  · 那些台词/卡片是**新写的**（源文件里本来没有）→ 增量只能改已有的行；
  · 盘上那一份被换成了**另一版** → 先对齐版本；
  · 只是说话人/章节变了 → 重新载入再改。

这一组钉住 `session_export._patch_failed_facts`：它把"新加的卡"与"新写的
台词"数出来，拼在拒因**后面**（拒因本身一个字不动）。
"""
from __future__ import annotations

_SRC = ('label c1:\n'
        '    show lan happy\n'
        '    "原文一"\n'
        '    "原文二"\n'
        '    return\n')


def _session(tmp_path):
    from storygraph.app.editor import EditorSession

    ch = tmp_path / 'game' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(_SRC, encoding='utf-8')
    s = EditorSession(project_root=tmp_path)
    s.load(str(ch))
    return s, ch


class _FakePatch:
    ok = False
    text = ''
    coverage = 0.6
    matched_beats = 3
    total_beats = 4
    reasons = ['1 句台词在补丁结果里找不到（例：\'新写的\'）']
    empty_beats = 0


def _force_failure(monkeypatch):
    import storygraph.story.patcher as P

    monkeypatch.setattr(P, 'patch_export',
                        lambda *a, **k: _FakePatch())


def test_a_newly_written_line_is_named_as_new(tmp_path):
    s, _ch = _session(tmp_path)
    s.beats[1].text = '这是一句完全新写的台词'
    facts = s._patch_failed_facts(_FakePatch())
    joined = ' '.join(facts)
    assert '句台词在载入时那份文件里找不到' in joined, facts
    assert '完全新写' in joined, facts


def test_a_card_without_source_lines_is_counted(tmp_path):
    s, _ch = _session(tmp_path)
    s.beats[2].src_first = 0
    facts = s._patch_failed_facts(_FakePatch())
    assert any('没有对应行' in x for x in facts), facts


def test_the_facts_are_appended_but_the_reason_is_untouched(qapp,
                                                            tmp_path,
                                                            monkeypatch):
    """拒因第一句还是 patcher 给的原话；事实排在它后面。"""
    _force_failure(monkeypatch)
    s, ch = _session(tmp_path)
    s.beats[1].text = '新写的一句'
    res = s.export(str(ch))
    assert res['ok'] is False
    assert 'PATCH_FAILED' in (res.get('codes') or []), res
    blocking = res.get('blocking') or []
    assert blocking and '在补丁结果里找不到' in blocking[0], blocking
    assert any('在载入时那份文件里找不到' in x for x in blocking[1:]), blocking
    assert ch.read_text(encoding='utf-8') == _SRC, '拒绝时一个字节都不许动'


def test_no_fact_is_invented_when_nothing_is_new(tmp_path):
    s, _ch = _session(tmp_path)
    facts = s._patch_failed_facts(_FakePatch())
    joined = ' '.join(facts)
    assert '在载入时那份文件里找不到' not in joined, facts
    assert '没有对应行' not in joined, facts
