# -*- coding: utf-8 -*-
"""
导出时把**扩展点的「整章应用」**补进产物（2026-11-08）。

用户原话：

> 你写的这个扩展模块一点作用都没有发挥出来！我明明已经说了它是整章应用，
> 明明配置是对的！但是导出时完全不正确……（不信你去看 2-1-3 的导出，
> 完全没效果啊！）

根因：`batchable` 那个开关从前只喂「批处理窗口 / CLI batch」，导出这条路
根本不看它 —— 配得再对，导出也不会有那行代码。这一组判据钉住：

  · 导出之后真的补进去（锚点由 `app.batch_edit` 那一份算，不另写一套）；
  · 再导一次**不翻倍**（`skip_if_present`）；
  · 规则不合法（真机第一条就是 `insert.text` 空着）→ 警告里点名，一个字不写；
  · 没有声明 → 一个字节都不动。
"""
from __future__ import annotations

import json

import pytest

_SRC = ('label c:\n'
        '    show lan happy\n'
        '    "一"\n'
        '    show lan sad\n'
        '    "二"\n'
        '    return\n')

_CUE = '$ renpy.transition(Dissolve(0.1), layer="master")'


def _project(tmp_path, *, text=_CUE, ext=True):
    root = tmp_path / 'p'
    ch = root / 'game' / 'chapter' / 'c1.rpy'
    ch.parent.mkdir(parents=True)
    ch.write_text(_SRC, encoding='utf-8')
    if ext:
        (root / 'storygraph.ext.json').write_text(json.dumps({
            'schema': 'storygraph.ext/1',
            'extensions': [{
                'id': 'renpy.transition', 'kind': 'call', 'enabled': True,
                'override': True, 'batchable': True,
                'batch': {'when': {}, 'insert': {
                    'text': text,
                    'placement': 'after_last_show',
                    'skip_if_present': True}},
            }]}, ensure_ascii=False, indent=2), encoding='utf-8')
    return root, ch


def _export(root, ch):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=root)
    s.load(str(ch))
    return s.export(str(ch))


class TestTheExportAppliesTheDeclaredBatch:
    def test_the_line_lands_after_every_sprite_change(self, tmp_path):
        root, ch = _project(tmp_path)
        res = _export(root, ch)
        assert res['ok'], res
        text = ch.read_text(encoding='utf-8')
        assert text.count(_CUE) == 2, text
        assert '扩展批处理补了 2 行' in str(res.get('note') or ''), res

    def test_a_second_export_does_not_double_it(self, tmp_path):
        root, ch = _project(tmp_path)
        _export(root, ch)
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8').count(_CUE) == 2

    def test_the_second_export_says_why_nothing_changed(self, tmp_path):
        root, ch = _project(tmp_path)
        _export(root, ch)
        res = _export(root, ch)
        # 不是"没跑"，是"每一处都已有同一行" —— 要能分清
        assert not any('不合法' in w for w in (res.get('warnings') or [])), res

    def test_an_empty_rule_is_reported_not_silent(self, tmp_path):
        """真机那条：界面上打了字、存的时候没带上 → 导出必须点名说不合法。"""
        root, ch = _project(tmp_path, text='')
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8') == _SRC
        assert any('不能为空' in w for w in (res.get('warnings') or [])), res

    def test_without_any_declaration_nothing_happens(self, tmp_path):
        root, ch = _project(tmp_path, ext=False)
        res = _export(root, ch)
        assert res['ok'], res
        assert ch.read_text(encoding='utf-8') == _SRC

    def test_it_survives_a_third_round(self, tmp_path):
        root, ch = _project(tmp_path)
        for _ in range(3):
            assert _export(root, ch)['ok']
        assert ch.read_text(encoding='utf-8').count(_CUE) == 2


@pytest.mark.parametrize('placement,count', [
    ('after_last_show', 2), ('after_last_change', 2),
])
class TestPlacementsStillWork:
    def test_placement_comes_from_the_declaration(self, tmp_path, placement,
                                                  count):
        root, ch = _project(tmp_path)
        data = json.loads((root / 'storygraph.ext.json').read_text('utf-8'))
        data['extensions'][0]['batch']['insert']['placement'] = placement
        (root / 'storygraph.ext.json').write_text(
            json.dumps(data, ensure_ascii=False), encoding='utf-8')
        assert _export(root, ch)['ok']
        assert ch.read_text(encoding='utf-8').count(_CUE) == count
