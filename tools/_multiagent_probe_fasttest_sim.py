"""
tools/_multiagent_probe_fasttest_sim.py
=======================================
fasttest（`tools/fasttest/run.py` + `fasttest_order_plugin.py`）设计审查的
**小型模拟验证** —— 不跑全量（全量会跟别的任务抢机器），只把静态审查的
几条结论钉成可执行证据：

  F1  RunLock：互斥 / 死 pid 清锁 / **pid 活着但不是 fasttest（PID 复用）**
      时锁卡死（`--no-wait` 立刻退 3、默认排队 30 分钟）。
  F2  basetemp 隔离：CLI `--basetemp` 必须真的盖掉 pytest.ini 的
      `addopts --basetemp=…`，且**不碰** ini 指的那个目录（D6 的根）。
  F3  失败归因门控：`--tb=short` 这类纯展示参数也会让"新增失败/已消失"
      对比静默消失（`pure_full = not targeted and not extras`）。
  F4  权重排序：重文件 / disk_hygiene 靠前、稳定排序、同权重保序。
  F5  `_pid_alive` 不许用 os.kill(pid,0)（Windows 会真杀进程）——
      只验证它对"确定不存在的 pid"返回 False，不复现杀进程。

跑法：

    /d/Python/python tools/_multiagent_probe_fasttest_sim.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
FASTTEST = ROOT / 'tools' / 'fasttest' / 'run.py'
PLUGIN = ROOT / 'tools' / 'fasttest' / 'fasttest_order_plugin.py'

PASS, FAIL = '[PASS]', '[FAIL]'
results: list = []


def check(name, ok, detail=''):
    results.append((ok, name))
    print(f'{PASS if ok else FAIL} {name}')
    if not ok and detail:
        for line in str(detail).splitlines()[:10]:
            print(f'       {line}')


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    tmp = Path(tempfile.mkdtemp(prefix='probe_fasttest_'))
    try:
        run = load_module(FASTTEST, 'probe_fasttest_run')
        plugin = load_module(PLUGIN, 'probe_fasttest_plugin')

        # ---------------- F1 RunLock ----------------
        lock_path = tmp / 'run.lock'

        # 1a 互斥：持有者活着（这里用本进程模拟）→ 第二个 --no-wait 立刻退 3
        held = run.RunLock(path=lock_path, wait=False)
        held.__enter__()
        try:
            try:
                run.RunLock(path=lock_path, wait=False).__enter__()
                check('F1a 两把锁互斥（第二个 --no-wait 立刻退 3）', False,
                      '第二把锁竟然获取成功')
            except SystemExit as e:
                check('F1a 两把锁互斥（第二个 --no-wait 立刻退 3）',
                      e.code == 3, f'退出码 {e.code!r}，期望 3')
        finally:
            held.__exit__(None, None, None)
        check('F1a2 持有者退出后锁文件已清', not lock_path.exists())

        # 1b 死 pid 的陈旧锁 → 被清掉、可获取
        lock_path.write_text(json.dumps({'pid': 999_999_999, 'token': 'x'}),
                             encoding='utf-8')
        got = run.RunLock(path=lock_path, wait=False).__enter__()
        check('F1b 陈旧锁（pid 已死）被清掉、可获取', got is not None)
        got.__exit__(None, None, None)

        # 1c PID 复用：锁文件里的 pid 活着、但**根本不是 fasttest**
        #    （Windows 会复用 pid）。目标行为：不该把无关进程当成持有者。
        #    现状：`_pid_alive` 只看 pid 死活 → 误判"另一轮正在跑"。
        lock_path.write_text(json.dumps(
            {'pid': os.getpid(), 'token': 'stale-but-alive',
             'started': '2026-10-26 00:00:00'}), encoding='utf-8')
        blocked = False
        code = None
        try:
            run.RunLock(path=lock_path, wait=False).__enter__().__exit__(
                None, None, None)
        except SystemExit as e:
            blocked = True
            code = e.code
        check('F1c 锁文件的 pid 属于无关进程时，不许当成"另一轮正在跑"',
              not blocked,
              f'锁文件 pid={os.getpid()}（本模拟进程，不是 fasttest），'
              f'RunLock 仍拒绝获取（SystemExit {code!r}）；'
              '默认 wait 模式会先空等 1800 秒才退 3。'
              '根因：_pid_alive 只判 pid 死活，Windows pid 复用即中招')
        lock_path.unlink(missing_ok=True)

        # 1d 超时后退出 3（wait 模式 + 0 秒超时）
        lock_path.write_text(json.dumps({'pid': os.getpid(), 'token': 't'}),
                             encoding='utf-8')
        try:
            run.RunLock(path=lock_path, wait=True, timeout=0.0).__enter__()
            check('F1d wait 模式排队超时也退 3', False, '竟然获取成功')
        except SystemExit as e:
            check('F1d wait 模式排队超时也退 3', e.code == 3)
        lock_path.unlink(missing_ok=True)

        # ---------------- F2 basetemp 覆盖（迷你 pytest） ----------------
        proj = tmp / 'miniproj'
        proj.mkdir()
        ini_basetemp = tmp / 'ini_basetemp'
        cli_basetemp = tmp / 'cli_basetemp'
        ini_basetemp.mkdir()
        (ini_basetemp / 'SENTINEL.txt').write_text('不许动', encoding='utf-8')
        (proj / 'pytest.ini').write_text(
            f'[pytest]\naddopts = --basetemp={ini_basetemp}\n',
            encoding='utf-8')
        out_file = tmp / 'tmp_path_used.txt'
        (proj / 'test_probe_basetemp.py').write_text(
            'def test_where_does_tmp_path_live(tmp_path):\n'
            f'    open(r"{out_file}", "w", encoding="utf-8").write(\n'
            '        str(tmp_path))\n',
            encoding='utf-8')
        proc = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', '--color=no',
             '--basetemp', str(cli_basetemp), '-p', 'no:cacheprovider'],
            cwd=str(proj), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=180)
        used = out_file.read_text(encoding='utf-8') if out_file.exists() else ''
        check('F2a CLI --basetemp 真的盖掉了 ini（tmp_path 落在 CLI 目录）',
              used.startswith(str(cli_basetemp)),
              f'tmp_path={used!r} 期望前缀 {str(cli_basetemp)!r}\n'
              f'pytest rc={proc.returncode}\n{proc.stdout[-400:]}')
        check('F2b ini 那个 basetemp 一根毫毛都没动（D6 互删根因）',
              (ini_basetemp / 'SENTINEL.txt').exists(),
              '快照被删 = 两个 pytest 仍会互删对方目录')

        # ---------------- F3 归因门控 ----------------
        parser = run._build_parser()

        def gating(argv):
            _args, extras = parser.parse_known_args(argv)
            targeted = any(run._looks_like_target(x) for x in extras)
            pure_full = (not targeted) and (not extras)
            return targeted, pure_full, extras

        check('F3a 无参数全量：归因+学习开门', gating([]) == (False, True, []))
        t, pure, extras = gating(['--tb=short'])
        check('F3b 带纯展示参数 --tb=short 的全量：失败归因/权重学习仍应开门',
              pure is True,
              f'targeted={t} pure_full={pure} extras={extras!r} —— '
              '它仍是全量，但 pure_full=False 让"新增失败/已消失"对比与权重'
              '学习被静默关掉（且没有 targeted 的那种提示）。'
              '目标行为：区分"筛选类参数"与"展示类参数"，展示类不关归因')
        t, pure, extras = gating(['tests/test_x.py'])
        check('F3c 定向文件：归因关、且有"定向运行"提示',
              (t is True) and (pure is False))

        # ---------------- F4 权重排序 ----------------
        # ⚠ 布局必须与真仓一致：PRIORITY_FILES 的键是**仓根相对路径**
        #   （'tests/test_disk_hygiene.py'），平铺在根下会匹配不上（第一版
        #   探针就这么错的，见报告「判据校准记录」）。
        tests_root = tmp / 'proj2'
        (tests_root / 'tests').mkdir(parents=True)
        for n in ('test_light.py', 'test_heavy.py', 'test_disk_hygiene.py'):
            (tests_root / 'tests' / n).write_text(
                'def test_x():\n    pass\n', encoding='utf-8')

        class _Cfg:
            rootpath = tests_root

            def getoption(self, name, default=None):
                return False

        def items(*names):
            return [SimpleNamespace(path=tests_root / 'tests' / n)
                    for n in names]

        got = items('test_light.py', 'test_heavy.py', 'test_disk_hygiene.py')
        os.environ['FASTTEST_WEIGHTS'] = str(tmp / 'w.json')
        (tmp / 'w.json').write_text(json.dumps(
            {'files': {'tests/test_light.py': 0.1,
                       'tests/test_heavy.py': 99.0}}),
            encoding='utf-8')
        plugin.SEED_PATH = tmp / 'no_seed.json'   # 隔离提交的 seed
        plugin.pytest_collection_modifyitems(None, _Cfg(), got)
        order = [p.path.name for p in got]
        check('F4a disk_hygiene 永远第一 + 重文件靠前',
              order == ['test_disk_hygiene.py', 'test_heavy.py',
                        'test_light.py'], f'实际顺序 {order}')

        # 同权重稳定排序（保持收集顺序）
        (tmp / 'w.json').write_text(json.dumps(
            {'files': {'tests/test_light.py': 1.0,
                       'tests/test_heavy.py': 1.0}}),
            encoding='utf-8')
        got = items('test_light.py', 'test_heavy.py')
        plugin.pytest_collection_modifyitems(None, _Cfg(), got)
        check('F4b 同权重稳定排序（不乱换序）',
              [p.path.name for p in got] == ['test_light.py', 'test_heavy.py'])

        # ---------------- F5 _pid_alive ----------------
        check('F5 _pid_alive(999999999) == False（不误报活）',
              run._pid_alive(999_999_999) is False)
        check('F5b _pid_alive(本进程) == True',
              run._pid_alive(os.getpid()) is True)
        del os.environ['FASTTEST_WEIGHTS']
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for ok, n in results if not ok]
    print(f'\n共 {len(results)} 条：{sum(1 for ok, _ in results if ok)} 绿 / '
          f'{len(bad)} 红')
    for n in bad:
        print('  -', n)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
