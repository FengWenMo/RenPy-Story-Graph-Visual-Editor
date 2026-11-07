# -*- coding: utf-8 -*-
"""
`storygraph.rules.json` 的**三方合并**（2026-11-07 第七轮）

病与药方跟 `storygraph.ext.json` 是同一份（`core.threeway`）：

    甲窗：教一条 alpha → 存
    乙窗：教一条 beta  → 存        ← 乙的 base 里没有 alpha
    结果：alpha 没了，两次都 `ok: True`

外加一条"窗口删了别人的东西"：`save_calls` 的语义是"这一份就是全部"，
所以**不传 base** 时，别人在另一个窗口加的调用会被整条删掉。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import statement_registry as SR  # noqa: E402

CHILD = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from storygraph.story import statement_registry as SR
    root = Path(sys.argv[1]); name = sys.argv[2]; start = float(sys.argv[3])
    while time.time() < start:
        time.sleep(0.005)
    res = SR.declare_statement(root, name, label=name)
    print(res.get('ok'), res.get('action'), flush=True)
''')


def _project(tmp_path: Path) -> Path:
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    return root


def _rules(root: Path) -> dict:
    path = SR.manifest_path(root)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def _write_rules(root: Path, data: dict) -> Path:
    path = SR.manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    SR.clear_cache()
    return path


def _call(head, **kw) -> dict:
    body = {'head': head, 'columns': [{'name': '方向', 'position': 0}]}
    body.update(kw)
    return body


class TestConcurrentTeach:
    def test_six_processes_teaching_at_once_all_survive(self, tmp_path):
        """六个进程同时各教一条语句 → 六条都得在（少了就是"整份覆盖"）。"""
        root = _project(tmp_path)
        script = tmp_path / 'child.py'
        script.write_text(CHILD.replace('{root!r}', repr(str(ROOT))),
                          encoding='utf-8')
        env = dict(os.environ, PYTHONIOENCODING='utf-8',
                   PYTHONPATH=str(ROOT))
        names = [f'teach{i}' for i in range(6)]
        start = time.time() + 1.5
        procs = [subprocess.Popen([sys.executable, str(script), str(root), name,
                                   str(start)],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding='utf-8', env=env)
                 for name in names]
        outs = [p.communicate(timeout=60)[0] for p in procs]
        for out in outs:
            assert out.strip().startswith('True'), outs
        got = sorted(s['name'] for s in _rules(root).get('statements') or ())
        assert got == sorted(names), got


class TestSaveCallsMerge:
    def test_head_added_by_someone_else_after_i_opened_is_kept(self, tmp_path):
        """我打开窗口时只有 mine；别人加了 wipe；我存 → wipe 必须还在。"""
        root = _project(tmp_path)
        _write_rules(root, {'calls': [_call('mine')]})
        base = list(SR.load_calls(root)) if hasattr(SR, 'load_calls') else None
        # `base` = 我打开那一刻看到的那份
        if base is None:
            from storygraph.story.statement_registry import StatementRegistry
            base = list(StatementRegistry.for_project(root).calls)
        # 别人在这之后加了 wipe
        _write_rules(root, {'calls': [_call('mine'), _call('wipe')]})

        res = SR.save_calls(root, [_call('mine', label='我改的')], base=base)
        assert res['ok'], res
        heads = [c['head'] for c in _rules(root)['calls']]
        assert heads == ['mine', 'wipe'], heads
        assert 'wipe' not in res['deleted']

    def test_without_base_the_old_replace_semantics_still_hold(self, tmp_path):
        """不传 `base` = 老语义（"这一份就是全部"）—— 老调用方一个字不用改。"""
        root = _project(tmp_path)
        _write_rules(root, {'calls': [_call('mine'), _call('old')]})
        res = SR.save_calls(root, [_call('mine')])
        assert res['ok'], res
        heads = [c['head'] for c in _rules(root)['calls']]
        assert heads == ['mine'], heads
        assert res['deleted'] == ['old']

    def test_my_deletion_goes_through_when_nobody_touched_it(self, tmp_path):
        root = _project(tmp_path)
        _write_rules(root, {'calls': [_call('a'), _call('b')]})
        base = [{'head': 'a', 'columns': [{'name': '方向', 'position': 0}]},
                {'head': 'b', 'columns': [{'name': '方向', 'position': 0}]}]
        res = SR.save_calls(root, [_call('a')], base=base)
        assert res['ok'] and res['deleted'] == ['b'], res
        assert [c['head'] for c in _rules(root)['calls']] == ['a']

    def test_deleting_something_someone_else_changed_stops_and_writes_nothing(
            self, tmp_path):
        root = _project(tmp_path)
        _write_rules(root, {'calls': [_call('a'), _call('b', label='别人改的')]})
        base = [{'head': 'a', 'columns': [{'name': '方向', 'position': 0}]},
                {'head': 'b', 'columns': [{'name': '方向', 'position': 0}]}]
        res = SR.save_calls(root, [_call('a')], base=base)
        assert not res['ok'] and res['conflicts'] == ['b'], res
        assert res['written'] == []
        heads = [c['head'] for c in _rules(root)['calls']]
        assert heads == ['a', 'b'], heads

    def test_on_conflict_mine_overrides(self, tmp_path):
        root = _project(tmp_path)
        _write_rules(root, {'calls': [_call('a'), _call('b', label='别人改的')]})
        base = [{'head': 'a', 'columns': [{'name': '方向', 'position': 0}]},
                {'head': 'b', 'columns': [{'name': '方向', 'position': 0}]}]
        res = SR.save_calls(root, [_call('a')], base=base, on_conflict='mine')
        assert res['ok'], res
        assert [c['head'] for c in _rules(root)['calls']] == ['a']

    def test_origin_does_not_create_fake_conflicts(self, tmp_path):
        """带 `origin` 的调用表（界面读出来的）不许每条都被当成"被人改过"。"""
        root = _project(tmp_path)
        _write_rules(root, {'calls': [_call('a')]})
        with_origin = [dict(_call('a'), origin='storygraph.rules.json')]
        res = SR.save_calls(root, with_origin)
        assert res['ok'], res


class TestRefusals:
    def test_broken_json_is_refused_and_the_file_is_untouched(self, tmp_path):
        root = _project(tmp_path)
        path = SR.manifest_path(root)
        path.write_text('{oops', encoding='utf-8')
        res = SR.declare_statement(root, 'x')
        assert not res['ok'] and 'JSON' in res['reason']
        assert path.read_text(encoding='utf-8') == '{oops'

    def test_declare_statement_updates_in_place(self, tmp_path):
        root = _project(tmp_path)
        SR.declare_statement(root, 'alpha', label='甲')
        res = SR.declare_statement(root, 'alpha', label='甲2')
        assert res['action'] == 'updated'
        items = _rules(root)['statements']
        assert len(items) == 1 and items[0]['label'] == '甲2'
