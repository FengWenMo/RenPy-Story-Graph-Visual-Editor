# -*- coding: utf-8 -*-
"""
`storygraph.rules.json` 的**读也在锁里**（2026-11-07 第八轮）
==========================================================

第七轮把"教语句 / 教调用 / 存调用"的读→改→写收进了 `_edit_manifest` 的
跨进程锁，但**删/清/重置**四条路还是"锁外读一次、备份时再读一次、写时用
第一次那份"。症状有两种，第二种最坏：

  ① 两个进程同时删**不同**的两条 → 后写的用的是自己读到的旧世界，
     先删的那条**复活**（两次都 `ok: True`）；
  ② 一个进程删掉"最后一条"（连文件一起删），另一个进程同刻在**加**一条
     → 删的那个没见过新加的那条，`unlink` 一落，新加的那条**静默消失**。

判据全是真子进程（与 `test_manifest_merge.py` 同一套夹具）：锁对不对，
只有真并发才算数。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.story import statement_registry as SR  # noqa: E402

CHILD = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from storygraph.story import statement_registry as SR
    root = Path(sys.argv[1]); action = sys.argv[2]; arg = sys.argv[3]
    start = float(sys.argv[4])
    while time.time() < start:
        time.sleep(0.005)
    if action == 'group':
        res = SR.declare_attr_groups(root, [{'name': arg, 'options': ['x']}])
    elif action == 'remove':
        res = SR.remove_statement(root, arg)
    else:
        res = SR.declare_statement(root, arg, label=arg)
    print(res.get('ok'), res.get('action'), res.get('removed'), flush=True)
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


def _run(tmp_path: Path, root: Path, jobs: list) -> list:
    """同时起 N 个子进程（同一个起跑线），返回每个进程那一行输出。"""
    script = tmp_path / 'child.py'
    script.write_text(CHILD.replace('{root!r}', repr(str(ROOT))),
                      encoding='utf-8')
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONPATH=str(ROOT))
    start = time.time() + 1.5
    procs = [subprocess.Popen([sys.executable, str(script), str(root),
                               action, arg, str(start)],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding='utf-8', env=env)
             for action, arg in jobs]
    return [p.communicate(timeout=90)[0].strip() for p in procs]


class TestConcurrentEditsKeepEverybodysWork:
    def test_six_processes_adding_six_groups_all_survive(self, tmp_path):
        """六条路同时加六组词组 → 六组都在（少一组就是"锁外读"）。"""
        root = _project(tmp_path)
        names = [f'g{i}' for i in range(6)]
        outs = _run(tmp_path, root, [('group', n) for n in names])
        assert all(o.startswith('True') for o in outs), outs
        got = sorted(g['name'] for g in _rules(root).get('attr_groups') or ())
        assert got == sorted(names), got

    def test_six_processes_removing_six_statements_all_of_them_go(self, tmp_path):
        """六条路同时各删一条 → 六条全没（不是"只剩最后删的那条"）。"""
        root = _project(tmp_path)
        names = [f's{i}' for i in range(6)]
        for n in names:
            SR.declare_statement(root, n)
        outs = _run(tmp_path, root, [('remove', n) for n in names])
        assert all(o.startswith('True') for o in outs), outs
        assert not (_rules(root).get('statements') or []), _rules(root)

    def test_delete_of_the_last_line_never_swallows_a_new_one(self, tmp_path):
        """
        最坏的那一种：甲删掉"最后一条"（顺带删文件），乙同刻在加一条。

        **不论谁先**，乙加的那条都必须在 —— 甲要是没看见它就把文件删了，
        那就是静默丢用户的声明。跑六次（时间窗被真文件 I/O 拉开）。
        """
        for round_ in range(6):
            root = tmp_path / f'proj{round_}'
            (root / 'game').mkdir(parents=True)
            SR.declare_statement(root, 'solo')
            outs = _run(tmp_path, root, [('remove', 'solo'), ('declare', 'added')])
            assert all(o.startswith('True') for o in outs), (round_, outs)
            names = [s['name'] for s in _rules(root).get('statements') or ()]
            assert 'added' in names, (round_, names)


class TestBackupAndDeleteBranches:
    def test_remove_keeps_the_file_when_other_config_remains(self, tmp_path):
        root = _project(tmp_path)
        main = SR.manifest_path(root)
        main.write_text(json.dumps({'statements': [{'name': 'pause'}],
                                    'host_hooks': {'trust': True}},
                                   ensure_ascii=False), encoding='utf-8')
        res = SR.remove_statement(root, 'pause')
        assert res['ok'] and res['removed'] and not res['deleted_file'], res
        data = json.loads(main.read_text(encoding='utf-8'))
        assert data['statements'] == [] and data['host_hooks'] == {'trust': True}
        assert res['backup'] and Path(res['backup']).is_file()

    def test_nothing_changed_means_no_backup_noise(self, tmp_path):
        """`changed=False` 不备份 —— 否则点一次"删"就多一份没人要的副本。"""
        root = _project(tmp_path)
        SR.declare_statement(root, 'solo')
        res = SR.remove_statement(root, 'not_there')
        assert res['ok'] and not res['removed'], res
        assert res['backup'] == ''
        assert not (root / 'storygraph.rules.backup').exists()

    def test_a_failed_backup_blocks_every_destructive_edit(self, tmp_path):
        """
        备份目录写不了 → 四条删/清/重置全部**拒绝**，清单一个字节不动。

        （锁把"备份 → 写"连成一段之后，这条边路只有一种结果：要么备份在、
        要么什么都没改 —— 不许出现"删了但没备份"。）
        """
        root = _project(tmp_path)
        (root / 'storygraph.rules.backup').write_text('我是文件不是目录',
                                                      encoding='utf-8')
        main = SR.manifest_path(root)
        main.write_text(json.dumps({
            'statements': [{'name': 'solo'}],
            'attr_groups': [{'name': 'outfit', 'options': ['泳装']}],
        }, ensure_ascii=False), encoding='utf-8')
        before = main.read_text(encoding='utf-8')
        for call in (lambda: SR.remove_statement(root, 'solo'),
                     lambda: SR.clear_attr_groups(root),
                     lambda: SR.reset_declarations(root),
                     lambda: SR.declare_attr_groups(
                         root, [{'name': 'outfit'}], replace=True)):
            res = call()
            assert res['ok'] is False, res
            assert '备份失败' in res['reason'], res
            assert main.read_text(encoding='utf-8') == before, '拒绝时改了文件'

    def test_clear_says_there_is_no_manifest(self, tmp_path):
        root = _project(tmp_path)
        res = SR.clear_attr_groups(root)
        assert res['ok'] is False and '没有' in res['reason'], res
