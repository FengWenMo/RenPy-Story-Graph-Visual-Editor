# -*- coding: utf-8 -*-
"""
`core.filelock` 的判据：**跨进程**的"这份文件归我改"（2026-11-07 第七轮）

这里全部用**真子进程**。为什么不能用线程：`msvcrt.locking` / `flock` 是
**按进程**的（同一进程再锁同一段会失败，而不同进程才会真的排队）——
用线程测出来的"锁"是假的。
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

from storygraph.core.filelock import (  # noqa: E402
    ENV_LOCK_DIR, LockTimeout, file_lock, lock_dir, lock_path_for)

HOLD = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {root!r})
    from storygraph.core.filelock import file_lock
    target = sys.argv[1]
    hold = float(sys.argv[2])
    with file_lock(target, timeout=10):
        print('locked', flush=True)
        time.sleep(hold)
    print('released', flush=True)
''')


def _child(tmp_path: Path) -> Path:
    path = tmp_path / 'hold_lock.py'
    path.write_text(HOLD.replace('{root!r}', repr(str(ROOT))), encoding='utf-8')
    return path


def _spawn(script: Path, target, hold: float, env) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(script), str(target),
                             str(hold)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', env=env)


@pytest.fixture()
def isolated_locks(tmp_path, monkeypatch):
    """锁目录指到 tmp（**不许**往真实用户缓存里塞判据垃圾）。"""
    monkeypatch.setenv(ENV_LOCK_DIR, str(tmp_path / 'locks'))
    return tmp_path / 'locks'


def test_lock_file_lives_outside_the_project(isolated_locks, tmp_path):
    """锁文件在用户缓存里、按路径派生 —— 用户项目里不许出现 `.lock`。"""
    project = tmp_path / 'proj'
    project.mkdir()
    target = project / 'storygraph.ext.json'
    got = lock_path_for(target)
    assert got.parent == isolated_locks
    assert project not in got.parents
    assert got == lock_path_for(target)                  # 稳定映射
    # 同一份文件的不同写法 → 同一把锁（Windows 大小写/斜杠）
    assert got == lock_path_for(str(target).replace('/', '\\'))


def test_another_process_holds_it_so_we_wait_and_give_up(isolated_locks,
                                                         tmp_path):
    """子进程持锁 1.2s；父进程等 0.2s → LockTimeout（**不静默放行**）。"""
    target = tmp_path / 'a.json'
    env = dict(os.environ, PYTHONIOENCODING='utf-8',
               PYTHONPATH=str(ROOT), **{ENV_LOCK_DIR: str(isolated_locks)})
    proc = _spawn(_child(tmp_path), target, 1.2, env)
    try:
        first = proc.stdout.readline().strip()
        assert first == 'locked', first
        started = time.monotonic()
        with pytest.raises(LockTimeout) as exc:
            with file_lock(target, timeout=0.2):
                pass
        waited = time.monotonic() - started
        assert str(target) == exc.value.path
        assert 0.15 <= waited < 1.5, waited
    finally:
        proc.communicate(timeout=10)


def test_after_the_other_process_releases_we_get_it(isolated_locks, tmp_path):
    """上一条的正面：子进程结束后（锁由**系统**释放），父进程能立刻拿到。"""
    target = tmp_path / 'b.json'
    env = dict(os.environ, PYTHONIOENCODING='utf-8',
               PYTHONPATH=str(ROOT), **{ENV_LOCK_DIR: str(isolated_locks)})
    proc = _spawn(_child(tmp_path), target, 0.2, env)
    out, _ = proc.communicate(timeout=10)
    assert 'released' in out, out
    with file_lock(target, timeout=1.0):
        pass                                     # 拿得到，不抛


def test_lock_dir_follows_the_env_override(isolated_locks):
    assert lock_dir() == isolated_locks


# ======================================================================
#  真并发：六个进程同时存 ext.json，六个标签都得在
# ======================================================================

SAVE_CHILD = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from storygraph.ext.registry import ExtensionRegistry
    root = Path(sys.argv[1]); ident = sys.argv[2]; label = sys.argv[3]
    start = float(sys.argv[4])
    while time.time() < start:      # **对齐起跑线**：六个进程同时进入 save
        time.sleep(0.005)
    reg = ExtensionRegistry.for_project(root)
    reg.get(ident).label = label
    res = reg.save()
    print(res.get('ok'), res.get('count'), res.get('reason'), flush=True)
''')


def test_six_processes_saving_at_once_keep_every_change(tmp_path, isolated_locks):
    """
    六个进程各自改**自己那条**的标签、同时存 —— 六条改动一条都不许丢。

    没有跨进程锁时（第六轮的三方合并）：会出现"两边读到同一份 theirs、
    后写的把先写的抹掉"，而且每个进程都返回 `ok: True`。
    加上锁之后：排队 → 每次都能读到前一个的结果 → 六个标签全在。
    """
    root = tmp_path / 'proj'
    (root / 'game').mkdir(parents=True)
    names = [f'p{i}' for i in range(6)]
    (root / 'storygraph.ext.json').write_text(json.dumps({
        'schema': 'storygraph.ext/1',
        'extensions': [{'id': n, 'kind': 'statement', 'label': ''}
                       for n in names]}, ensure_ascii=False), encoding='utf-8')
    script = tmp_path / 'save_child.py'
    script.write_text(SAVE_CHILD.replace('{root!r}', repr(str(ROOT))),
                      encoding='utf-8')
    env = dict(os.environ, PYTHONIOENCODING='utf-8',
               PYTHONPATH=str(ROOT), **{ENV_LOCK_DIR: str(isolated_locks)})
    start = time.time() + 1.5
    procs = [subprocess.Popen([sys.executable, str(script), str(root), n,
                               f'标签-{n}', str(start)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', env=env)
             for n in names]
    outs = [p.communicate(timeout=60)[0] for p in procs]
    for name, out in zip(names, outs):
        assert out.strip().startswith('True'), (name, out)
    data = json.loads((root / 'storygraph.ext.json').read_text(encoding='utf-8'))
    got = {e['id']: e.get('label') for e in data['extensions']}
    assert got == {n: f'标签-{n}' for n in names}, got
