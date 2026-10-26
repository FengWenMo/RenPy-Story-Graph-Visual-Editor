#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fasttest —— 本项目的**全量测试**专用加速入口。
================================================

为什么要单独做一个入口（而不是让大家记住一串 pytest 参数）：

  1. **全量串行约 20 分钟**，其中 4 组"真机金标"（scan / validate / patch /
     describe）各把 3.6 GB 的 sr_general 算三遍，占了大部分时间。
     `pytest-xdist` 并行能把它们摊到多个进程里。
  2. `pytest.ini` 里固定了 `--basetemp=.pip-tmp/pytest`；**两个 pytest 同时跑
     会互删同一个目录**，炸出成百上千个 ERROR（D6）。本工具每次给一个
     **全新的、带进程号的 basetemp**，并在入口加锁，避免"假回归"。
  3. xdist 默认按收集顺序发用例；金标文件很小、排在最后，于是 12 个进程
     先跑完、最后再一起等金标 —— 尾部很长。本工具会把**耗时长的文件排到
     队列最前面**（权重来自上一次运行，提交的 seed 只是冷启动），金标一开始
     就占住若干进程。
  4. 跑完只给一段能直接看懂的结论（通过/失败/跳过、耗时、失败节点、日志），
     而不是让 agent 在几千行输出里找。

用法
----

    python tools/fasttest/run.py                 # 全量（推荐）
    python tools/fasttest/run.py -x --tb=short   # 其余参数原样透传给 pytest
    python tools/fasttest/run.py --workers 8     # 机器忙时降并发
    python tools/fasttest/run.py --serial        # 对照：单进程跑
    python tools/fasttest/run.py --retry-failed  # 只复跑上一轮全量失败的
    python tools/fasttest/run.py --check         # 只检查环境

退出码与 pytest 一致（0 通过 / 1 有失败 / 2 用法或收集中断 / 5 没收集到用例），
外加 3 = 已有另一轮在跑（--no-wait 或排队超时）、124 = 卡死超时被本工具结束。
本工具**不修改**任何测试、不 skip 任何用例、不改变判据；只改变"怎么调度"。

卡死保护
--------

全量测试里只要有一个用例弹出**真实模态框**或在等一个永远不来的事件，pytest
就会停在 98% 一辈子 —— agent 那边看到的就是"读取任务输出"卡 20 分钟。
本工具因此做了三件事：

  · 每 15 秒打一行心跳（已跑多久、到百分之几、哪几条用例在跑），
    不至于让人以为进程死了；
  · 连续 300 秒**没有任何输出**就判定卡死，结束整棵进程树、保留现场，
    并在结论里点名正在跑的 nodeid，退出码 124；
  · 用一个小插件把"每个 worker 当前在跑哪条用例"写到文件里，卡死时可直接看。

这不是放宽判据：正常跑完仍然是 0/1；只有"永远跑不完"才会变成 124。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = Path(__file__).resolve().parent
STATE_DIR = ROOT / '.pip-tmp' / 'fasttest'
RUNS_DIR = STATE_DIR / 'runs'
LOCK_PATH = STATE_DIR / 'run.lock'
DURATIONS_PATH = STATE_DIR / 'durations.json'
LAST_PATH = STATE_DIR / 'last.json'
#: 只有"纯全量"（无 -k / 无文件参数）才更新它；用来算"新增失败/上轮已消失"。
LAST_FULL_PATH = STATE_DIR / 'last_full.json'
SEED_PATH = TOOL_DIR / 'seed.json'
#: 旧 basetemp 先原子改名到这里（同盘 rename 是瞬时的），再交给后台 cmd/rm
#: 慢慢删。**必须放在 `.pip-tmp` 外面**：`test_disk_hygiene` 会递归扫
#: `.pip-tmp`，把待删目录留在里面会让它从秒级变成几十秒。
TRASH_DIR = ROOT / '.fasttest-trash'

#: 心跳里只报最老的几条，避免 12 个 worker 刷屏。
_ACTIVE_SHOWN = 4

#: 默认并发。14 核机器上 12 个 worker + master 比较稳；再高会被 Qt / 磁盘
#: 抢成负收益。可用 FASTTEST_WORKERS 或 --workers 覆盖。
DEFAULT_MAX_WORKERS = 12
MIN_XDIST = (3, 6)


# ----------------------------------------------------------------------
#  小工具
# ----------------------------------------------------------------------

def _configure_stdout() -> None:
    """让输出在"被 agent/管道捕获"时用 UTF-8。

    Windows 上 Python 往管道写时默认用 GBK（中文项目里 pytest 的失败信息
    会被解码成乱码）。终端里保留系统编码，避免真人看到花屏；重定向/管道
    时统一 UTF-8，日志文件本身也始终是 UTF-8。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors='replace')  # type: ignore[attr-defined]
            else:
                stream.reconfigure(encoding='utf-8',  # type: ignore[attr-defined]
                                   errors='replace', line_buffering=True)
        except (AttributeError, ValueError):
            pass


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f'{seconds:.1f}s'
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f'{minutes}m{sec:02d}s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h{minutes:02d}m'


_PROGRESS_RE = re.compile(r'\[\s*(\d+)%\]')
_EOF = object()

#: 每 15 秒打一行心跳；这是给 agent / 人看的"还活着"，不改变判据。
DEFAULT_HEARTBEAT = 15.0
#: 连续这么久**没有任何输出**就认为卡死。5 分钟足够覆盖最慢的金标单项
#: （历史最长文件约 170 秒、观测到的最长静默约 200 秒），又不会让一次
#: 模态框卡住 20 分钟。真的还有更长静默的合法用例时用 --idle-timeout 调大。
DEFAULT_IDLE_TIMEOUT = 300.0


def _active_tests(watch_dir: Path, limit: int = _ACTIVE_SHOWN
                  ) -> list[tuple[float, str]]:
    """读 fasttest_watch_plugin 写的"当前在跑"文件，按已跑时长倒序。"""
    if not watch_dir.is_dir():
        return []
    now = time.time()
    items: list[tuple[float, str]] = []
    try:
        paths = list(watch_dir.glob('*.json'))
    except OSError:
        return []
    for path in paths:
        data = _read_json(path, {})
        if not isinstance(data, dict):
            continue
        nodeid = str(data.get('nodeid') or '')
        if not nodeid:
            continue
        # worker 崩溃时 logfinish 没机会清文件；心跳别一直报"僵尸用例"。
        try:
            pid = int(data.get('pid') or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and not _pid_alive(pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        try:
            started = float(data.get('started') or now)
        except (TypeError, ValueError):
            started = now
        items.append((max(0.0, now - started), nodeid))
    items.sort(reverse=True)
    return items[:limit]


def _active_text(active: list[tuple[float, str]]) -> str:
    return '; '.join(f'{nodeid} ({age:.0f}s)' for age, nodeid in active)


class _OutputPump:
    """后台读 pytest stdout，主线程才能做心跳/超时判断。

    不能直接 `for line in proc.stdout`：一行都不来的时候主线程会永远阻塞，
    正是"卡 20 分钟却看不出在等什么"的现场。读线程只负责排队，主线程
    带 0.5 秒超时取，于是心跳和看门狗都能准时跑。
    """

    def __init__(self, stream):
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._read, args=(stream,), daemon=True,
            name='fasttest-output')
        self._thread.start()

    def _read(self, stream) -> None:
        try:
            for line in stream:
                self._queue.put(line)
        except (OSError, ValueError, UnicodeError):
            pass
        finally:
            self._queue.put(_EOF)

    def get(self, timeout: float):
        return self._queue.get(timeout=timeout)


def _read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.tmp{os.getpid()}')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    os.replace(tmp, path)


def _cpu_count() -> int:
    return os.cpu_count() or 4


def _default_workers() -> int:
    env = os.environ.get('FASTTEST_WORKERS', '').strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min(DEFAULT_MAX_WORKERS, _cpu_count() - 1))


def _pid_alive(pid: int) -> bool:
    """跨平台判断 pid 是否还活着。**不能**在 Windows 上用 os.kill(pid, 0)：
    那会调用 TerminateProcess，直接把别的进程杀掉。"""
    if not pid or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(
            ['taskkill', '/T', '/F', '/PID', str(proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ----------------------------------------------------------------------
#  并发锁：同一时刻只允许一轮 fasttest（裸 pytest 不受保护，见 README）
# ----------------------------------------------------------------------

class RunLock:
    def __init__(self, path: Path = LOCK_PATH, wait: bool = True,
                 timeout: float = 1800.0):
        self.path = Path(path)
        self.wait = wait
        self.timeout = timeout
        self.token = uuid.uuid4().hex

    def __enter__(self):
        payload = {
            'pid': os.getpid(),
            'token': self.token,
            'started': _now_str(),
            'cwd': str(ROOT),
        }
        start_wait = time.monotonic()
        deadline = start_wait + max(0.0, self.timeout)
        announced = False
        next_notice = start_wait + 60.0
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(self.path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_json(self.path, {}) or {}
                pid = holder.get('pid') if isinstance(holder, dict) else None
                if pid and _pid_alive(int(pid)):
                    if not self.wait:
                        print(
                            f'[fasttest] 另一轮全量测试正在跑（pid={pid}，'
                            f'开始于 {holder.get("started", "?")}）。\n'
                            f'          等它跑完再试；加 --no-wait 只是不排队，'
                            f'不代表并发安全。', file=sys.stderr, flush=True)
                        raise SystemExit(3)
                    if time.monotonic() >= deadline:
                        print(
                            f'[fasttest] 等待另一轮测试超时（pid={pid}）。'
                            f'若确认它已卡死，删除 {self.path} 后重试。',
                            file=sys.stderr, flush=True)
                        raise SystemExit(3)
                    if not announced:
                        print(f'[fasttest] 另一轮全量测试正在跑'
                              f'（pid={pid}，开始于 '
                              f'{holder.get("started", "?")}），排队等待…',
                              flush=True)
                        announced = True
                    elif time.monotonic() >= next_notice:
                        # 排队也可能等很久；定期吭一声，别让 agent 以为卡死。
                        print(f'[fasttest] 仍在等待 pid={pid} 的运行结束'
                              f'（已等待 {_fmt_seconds(time.monotonic() - start_wait)}）…',
                              flush=True)
                        next_notice = time.monotonic() + 60.0
                    time.sleep(2.0)
                    continue
                # 进程已死 / 文件损坏 → 清掉陈旧锁
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    time.sleep(0.5)
                continue
            except OSError as exc:
                print(f'[fasttest] 无法创建运行锁 {self.path}: {exc}',
                      file=sys.stderr, flush=True)
                raise SystemExit(2) from exc
            else:
                try:
                    os.write(fd, json.dumps(payload, ensure_ascii=False)
                             .encode('utf-8'))
                except OSError:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    raise
                finally:
                    os.close(fd)
                return self

    def __exit__(self, exc_type, exc, tb):
        holder = _read_json(self.path, {}) or {}
        if isinstance(holder, dict) and holder.get('token') == self.token:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


# ----------------------------------------------------------------------
#  环境检查
# ----------------------------------------------------------------------

def _module_version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:  # noqa: BLE001 - 报告环境，不值得挑异常类型
        return None


def _xdist_ok() -> tuple[bool, str]:
    try:
        import xdist  # noqa: F401
    except ImportError:
        return False, '未安装'
    ver = _module_version('pytest-xdist') or '?'
    parts = []
    for piece in ver.split('.')[:2]:
        digits = ''.join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 2:
        parts.append(0)
    if tuple(parts) < MIN_XDIST:
        return False, f'{ver}（需要 >= {MIN_XDIST[0]}.{MIN_XDIST[1]}）'
    return True, ver


def _xdist_supports_no_reorder() -> bool:
    """老版本 xdist 没有 --no-loadscope-reorder，不能盲传。

    requirements-dev 只要求 `pytest-xdist>=3.6`，而选项名随版本可能变化；
    直接读插件源码判断（比启动一次 `pytest --help` 便宜），查不到时按
    "支持"处理——真不支持时 pytest 会立刻以 4 退出，不会被误判成红线。
    """
    try:
        import inspect

        import xdist.plugin as xdist_plugin
        return '--no-loadscope-reorder' in inspect.getsource(xdist_plugin)
    except Exception:  # noqa: BLE001 - 只是兼容性探测
        return True


def _print_environment(workers: int, serial: bool) -> None:
    print('[fasttest] 环境')
    print(f'  Python      : {sys.executable} ({sys.version.split()[0]})')
    print(f'  pytest      : {_module_version("pytest") or "?"}')
    ok, ver = _xdist_ok()
    print(f'  pytest-xdist: {ver}{"" if ok else "  ← 不可用"}')
    if not serial and ok:
        print(f'  xdist 重排  : '
              f'{"关闭（重文件优先）" if _xdist_supports_no_reorder() else "无法关闭（版本过旧）"}')
    print(f'  openpyxl    : {_module_version("openpyxl") or "未安装"}')
    print(f'  PySide6     : {_module_version("PySide6") or "未安装（UI 测试会被跳过）"}')
    real = os.environ.get('STORYGRAPH_REAL_PROJECT') or r'D:\千连万理\RenpyTest\sr_general'
    print(f'  真机项目    : {real}'
          f'{"  [存在]" if Path(real).is_dir() else "  [缺失：金标会 skip]"}')
    print(f'  并发        : {"1（--serial）" if serial else workers} / {_cpu_count()} 核')


def _check_environment(workers: int, serial: bool) -> int:
    _print_environment(workers, serial)
    problems = []
    if _module_version('pytest') is None:
        problems.append('pytest 未安装：python -m pip install -r requirements-dev.txt')
    if importlib.util.find_spec('openpyxl') is None:
        problems.append(
            'openpyxl 未安装：python -m pip install -r requirements-dev.txt')
    for plugin in ('fasttest_order_plugin', 'fasttest_watch_plugin'):
        if not (TOOL_DIR / f'{plugin}.py').is_file():
            problems.append(f'缺少插件文件 tools/fasttest/{plugin}.py')
    if not serial:
        ok, _ver = _xdist_ok()
        if not ok:
            problems.append(
                'pytest-xdist 不可用：python -m pip install -r requirements-dev.txt')
    if problems:
        print('\n[fasttest] 不能开始：')
        for problem in problems:
            print(f'  - {problem}')
        return 2
    print('\n[fasttest] 环境可用。')
    return 0


# ----------------------------------------------------------------------
#  运行目录与历史清理
# ----------------------------------------------------------------------

def _make_run_dir() -> Path:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir = RUNS_DIR / f'{stamp}-{os.getpid()}'
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _fast_rmtree(path: Path) -> None:
    """同步删目录。只作为后台清理启动失败时的兜底；正常路径不用它。"""
    path = Path(path)
    if not path.exists():
        return
    try:
        if os.name == 'nt':
            subprocess.run(
                ['cmd', '/c', 'rmdir', '/s', '/q', str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=600, check=False)
        else:
            shutil.rmtree(path, ignore_errors=True)
    except (OSError, subprocess.SubprocessError):
        pass


def _spawn_rmtree(path: Path) -> None:
    """**不等待**地删目录。

    删 1.5 万个临时文件实测要 40 秒，删更多要几分钟；在 pytest 启动前同步
    做这件事，agent 看到的就是"读取任务输出"卡五分钟。这里把删除丢给一个
    脱离的 `cmd /c rmdir`，父进程立刻继续。
    """
    path = Path(path)
    if not path.exists():
        return
    try:
        if os.name == 'nt':
            creationflags = (getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
                             | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP',
                                       0x00000200))
            subprocess.Popen(
                ['cmd', '/c', 'rmdir', '/s', '/q', str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags, close_fds=True)
        else:
            subprocess.Popen(
                ['rm', '-rf', '--', str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
                close_fds=True)
    except OSError:
        # 极少数环境不允许脱离进程；退回同步删，至少不会丢目录。
        _fast_rmtree(path)


def _trash_path(path: Path) -> bool:
    """把待删目录**原子改名**到 `.fasttest-trash/`，再后台删。

    同盘 rename 不碰文件内容，1.5 万个文件也是毫秒级；这样 pytest 启动前
    不会再去遍历/删除旧 basetemp。返回 True 表示已经交出去（或本来就不存在）。
    """
    path = Path(path)
    if not path.exists():
        return True
    target = path
    try:
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        target = TRASH_DIR / (f'{path.name}-{int(time.time() * 1000)}'
                              f'-{os.getpid()}')
        os.replace(path, target)
    except OSError:
        target = path  # 改名失败（被占用等）就原地后台删
    _spawn_rmtree(target)
    return True


def _clean_trash() -> None:
    """把上一轮没删完的 trash 再交给后台；不阻塞启动。"""
    if not TRASH_DIR.is_dir():
        return
    try:
        entries = list(TRASH_DIR.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_dir():
                _spawn_rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            pass


def _prune_runs(keep: int = 3) -> None:
    """只保留最近几轮；**只动 .pip-tmp/fasttest/runs**，不碰别人的临时目录。

    ⚠ 上一轮的 `tmp/`（basetemp）不再同步删：先 rename 到 `.fasttest-trash/`
    再后台删，否则 1.5 万个文件要 40 秒、积压多时能卡 5 分钟。junit/log/
    summary 留在原处；需要看失败现场就在**下一次跑之前**看。
    """
    if not RUNS_DIR.is_dir():
        return
    try:
        dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir()]
    except OSError:
        return

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    dirs.sort(key=_mtime, reverse=True)
    for index, old in enumerate(dirs):
        if index < keep:
            _trash_path(old / 'tmp')
        else:
            _trash_path(old)


#: 不能自动删的 `.pip-tmp` 子目录：
#:   · fasttest —— 本工具自己的运行记录，由 `_prune_runs` 管；
#:   · pytest   —— pytest.ini 配的默认 basetemp，可能正被裸 pytest 使用；
#:   · __pycache__。
_STALE_KEEP = {'fasttest', 'pytest', '__pycache__'}


def _prune_stale_temp(age_hours: float) -> int:
    """删掉 `.pip-tmp` 下**长期没人动**的临时目录。

    为什么值得做：`test_disk_hygiene.py` 会递归遍历整个 `.pip-tmp` 数文件；
    这个仓的历史临时目录能堆到 20 万个文件，实测量出 **391 秒**，直接成为
    全量测试的最长单项。本工具自己的 run 目录只留 3 轮，但裸 pytest / 探针
    留下的目录仍会累积，所以顺手清掉"超过 N 小时没动"的（默认 7 天）。
    只动 `.pip-tmp`，不碰源码、测试、用户数据；删不掉就跳过。
    """
    base = ROOT / '.pip-tmp'
    if not base.is_dir():
        return 0
    cutoff = time.time() - max(0.0, float(age_hours)) * 3600.0
    targets: list[Path] = []
    try:
        for entry in os.scandir(base):
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name in _STALE_KEEP:
                    continue
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            targets.append(Path(entry.path))
    except OSError:
        return 0

    removed = 0
    for path in targets:
        try:
            if _trash_path(path):
                removed += 1
        except OSError:
            pass
    if removed:
        print(f'[fasttest] 已把 {removed} 个超过 {age_hours:g}h 的旧临时目录'
              f'改名到 {TRASH_DIR.name}/，后台删除（启动不等）', flush=True)
    return removed


# ----------------------------------------------------------------------
#  JUnit 结果
# ----------------------------------------------------------------------

def _case_location(case: ET.Element) -> tuple[str, list[str]]:
    """从 JUnit testcase 还原 (相对文件路径, 类名链)。

    xdist 聚合出来的 JUnit **没有 `file` 属性**（只有 classname），所以主路径
    是 `tests.test_foo.TestBar` → `tests/test_foo.py` + `['TestBar']`。
    有 `file` 时也用它校正（普通 pytest 的 JUnit 会带）。
    """
    classname = case.get('classname') or ''
    raw = (case.get('file') or '').replace('\\', '/')
    classes: list[str] = []

    if raw:
        try:
            raw = Path(raw).resolve().relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            raw = raw.lstrip('./')
        module = raw[:-3].replace('/', '.') if raw.endswith('.py') else raw
        if classname.startswith(module + '.'):
            classes = classname[len(module) + 1:].split('.')
        return raw, classes

    parts = classname.split('.')
    if len(parts) >= 2 and parts[0] == 'tests':
        # 测试文件都平铺在 tests/ 下；parts[1] 就是 test_xxx 模块名
        return f'tests/{parts[1]}.py', parts[2:]
    if classname:
        return classname.replace('.', '/') + '.py', []
    return '', []


def _nodeid_from_case(case: ET.Element) -> str:
    rel, classes = _case_location(case)
    name = case.get('name') or '?'
    if not rel:
        return name
    pieces = [rel, *classes, name]
    return '::'.join(pieces)


def _failure_brief(case: ET.Element) -> str:
    """取失败/错误的一句话摘要，给"一眼看懂"用；详细信息仍在 output.log。"""
    node = case.find('failure')
    if node is None:
        node = case.find('error')
    if node is None:
        return ''
    raw = node.get('message') or node.text or ''
    for line in raw.splitlines():
        text = ' '.join(line.split())
        if text:
            return text[:200]
    return ''


def parse_junit(path: Path) -> list[dict]:
    """返回每个用例的 {nodeid, file, status, time, detail}；解析失败返回 []。"""
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError):
        return []
    cases = []
    for case in root.iter('testcase'):
        if case.find('failure') is not None:
            status = 'failed'
        elif case.find('error') is not None:
            status = 'error'
        elif case.find('skipped') is not None:
            skipped = case.find('skipped')
            kind = (skipped.get('type') or '') if skipped is not None else ''
            status = 'xfailed' if kind == 'pytest.xfail' else 'skipped'
        else:
            status = 'passed'
        try:
            seconds = float(case.get('time') or 0.0)
        except ValueError:
            seconds = 0.0
        rel, _classes = _case_location(case)
        cases.append({
            'nodeid': _nodeid_from_case(case),
            'file': rel,
            'status': status,
            'time': seconds,
            'detail': (_failure_brief(case)
                       if status in ('failed', 'error') else ''),
        })
    return cases


def _summarize(cases: list[dict], rc: int, elapsed: float,
               workers: int, serial: bool, run_dir: Path) -> dict:
    counts = Counter(case['status'] for case in cases)
    failed_cases = [case for case in cases
                    if case['status'] in ('failed', 'error')]
    failed = [case['nodeid'] for case in failed_cases]
    skipped = [case['nodeid'] for case in cases if case['status'] == 'skipped']
    by_file = Counter(case['file'] or '?' for case in failed_cases)
    return {
        'ts': _now_str(),
        'python': sys.executable,
        'workers': workers,
        'serial': serial,
        'elapsed': round(elapsed, 3),
        'exit_code': rc,
        'total': len(cases),
        'passed': counts.get('passed', 0),
        'failed': counts.get('failed', 0),
        'error': counts.get('error', 0),
        'skipped': len(skipped),
        'xfailed': counts.get('xfailed', 0),
        'failed_ids': failed,
        'failed_details': [
            {'nodeid': case['nodeid'], 'detail': case.get('detail') or ''}
            for case in failed_cases
        ],
        'failed_by_file': dict(sorted(by_file.items(),
                                      key=lambda item: -item[1])),
        'skipped_ids': skipped,
        'run_dir': str(run_dir),
    }


def _update_durations(cases: list[dict]) -> None:
    """把本轮每文件耗时并进 .pip-tmp/fasttest/durations.json（EWMA）。"""
    old = _read_json(DURATIONS_PATH, {}) or {}
    old_files = old.get('files', {}) if isinstance(old, dict) else {}
    fresh: dict[str, float] = {}
    for case in cases:
        if case['status'] == 'skipped' and case['time'] <= 0:
            continue
        rel = case['file']
        if not rel:
            continue
        fresh[rel] = fresh.get(rel, 0.0) + case['time']
    if not fresh:
        return
    merged = {}
    for rel, seconds in {**old_files, **fresh}.items():
        prev = old_files.get(rel)
        cur = fresh.get(rel)
        if prev is None:
            merged[rel] = round(float(cur), 3)
        elif cur is None:
            merged[rel] = round(float(prev), 3)
        else:
            merged[rel] = round(0.55 * float(prev) + 0.45 * float(cur), 3)
    data = {
        'version': 1,
        'updated': _now_str(),
        'note': '由 tools/fasttest/run.py 自动学习；只用于排序，不影响判据',
        'files': dict(sorted(merged.items())),
    }
    _write_json(DURATIONS_PATH, data)


def _print_summary(summary: dict, previous: dict | None,
                   reference_seconds: float | None) -> None:
    total = summary['total']
    fail = summary['failed'] + summary['error']
    if summary.get('timed_out'):
        verdict = 'TIMEOUT'
    else:
        verdict = 'PASS' if summary['exit_code'] == 0 and fail == 0 else 'FAIL'
    print()
    print('=' * 66)
    print(f'[fasttest] RESULT: {verdict}')
    if total:
        counts = (f'  用例   {total}   通过 {summary["passed"]}   '
                  f'失败 {summary["failed"]}   错误 {summary["error"]}   '
                  f'跳过 {summary["skipped"]}')
        if summary.get('xfailed'):
            counts += f'   预期失败 {summary["xfailed"]}'
        print(counts)
    else:
        print('  用例   未生成 JUnit 结果（收集阶段就中断/崩溃了？）')
    if summary.get('retry_failed'):
        print(f'  范围   只复跑上一轮失败的 {summary["retry_failed"]} 条'
              f'（非全量）')
    print(f'  用时   {_fmt_seconds(summary["elapsed"])}'
          f'（{"串行" if summary["serial"] else str(summary["workers"]) + " 进程"}）')
    if summary.get('timed_out'):
        print(f'  卡死   {summary["timed_out"]}；整棵 pytest 树已结束'
              f'（退出码 124）')
        for node in (summary.get('active_tests') or [])[:12]:
            print(f'    ? {node}')
        print('  复核   先单跑上面某条：'
              'python tools/fasttest/run.py --serial <nodeid>')
    if reference_seconds and not summary['serial'] \
            and not summary.get('targeted') and not summary.get('extras') \
            and not summary.get('timed_out') and summary['elapsed'] > 0:
        print(f'  参考   历史串行约 {_fmt_seconds(reference_seconds)}'
              f'，本轮约 {reference_seconds / summary["elapsed"]:.1f}x')

    if previous and isinstance(previous, dict) \
            and not summary.get('targeted') and not summary.get('extras') \
            and not summary.get('timed_out'):
        before = set(previous.get('failed_ids') or [])
        after = set(summary['failed_ids'])
        new = sorted(after - before)
        fixed = sorted(before - after)
        if new:
            print(f'  新增失败 {len(new)}（与上一轮相比）：')
            for node in new[:10]:
                print(f'    ! {node}')
            if len(new) > 10:
                print(f'    … 还有 {len(new) - 10} 条，见 JUnit')
        if fixed:
            print(f'  上轮失败已消失 {len(fixed)} 条。')

    if summary['failed_ids']:
        print(f'  失败   {len(summary["failed_ids"])}')
        by_file = summary.get('failed_by_file') or {}
        if len(by_file) > 1:
            top = list(by_file.items())[:6]
            print('  分布   ' + ' · '.join(
                f'{Path(rel).name} {count}' for rel, count in top))
        details = {item.get('nodeid'): item.get('detail') or ''
                   for item in (summary.get('failed_details') or [])
                   if isinstance(item, dict)}
        for node in summary['failed_ids'][:20]:
            print(f'    x {node}')
            detail = details.get(node)
            if detail:
                print(f'      {detail}')
        if len(summary['failed_ids']) > 20:
            print(f'    … 还有 {len(summary["failed_ids"]) - 20} 条，见 JUnit')
        if not summary['serial']:
            print('  复核   怀疑是并行假失败：'
                  'python tools/fasttest/run.py --serial <nodeid>'
                  '（或 --retry-failed 只复跑上一轮失败）')
    rel_dir = Path(summary['run_dir'])
    try:
        shown = rel_dir.relative_to(ROOT).as_posix()
    except ValueError:
        shown = str(rel_dir)
    print(f'  日志   {shown}/output.log')
    print(f'  明细   {shown}/junit.xml')
    print('=' * 66)


# ----------------------------------------------------------------------
#  主流程
# ----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='tools/fasttest/run.py',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
        description='storygraph 全量测试加速器：并行 + 独立 basetemp + 重文件优先。',
        epilog=(
            '常用：\n'
            '  python tools/fasttest/run.py\n'
            '  python tools/fasttest/run.py -x --tb=short\n'
            '  python tools/fasttest/run.py --workers 8\n'
            '  python tools/fasttest/run.py --serial\n'
            '  python tools/fasttest/run.py --retry-failed\n'
            '  python tools/fasttest/run.py --check\n'
            '\n'
            '不认识的参数会原样交给 pytest。注意：带文件/nodeid 的调用视为'
            '"定向运行"，不会更新耗时权重。\n'
        ))
    parser.add_argument('--workers', type=int, default=0,
                        help='xdist 进程数（默认 0=自动，最多 12）')
    parser.add_argument('--serial', action='store_true',
                        help='不用 xdist，单进程跑（对照/排查并行问题时用）')
    parser.add_argument('--retry-failed', action='store_true',
                        help='只复跑上一轮纯全量失败的 nodeid（没有失败记录/'
                             '没有失败时直接退出，不会偷偷跑全量）')
    parser.add_argument('--no-order', action='store_true',
                        help='不按历史耗时重排文件（保持默认收集顺序）')
    parser.add_argument('--keep', action='store_true',
                        help='跑完保留本轮 basetemp（默认成功即删）')
    parser.add_argument('--heartbeat', type=float, default=DEFAULT_HEARTBEAT,
                        help=f'每 N 秒打印一次进度心跳（默认 '
                             f'{DEFAULT_HEARTBEAT:g}；0=关闭）')
    parser.add_argument('--idle-timeout', type=float,
                        default=DEFAULT_IDLE_TIMEOUT,
                        help=f'连续 N 秒没有输出就判定卡死并结束，退出码 124'
                             f'（默认 {DEFAULT_IDLE_TIMEOUT:g}；0=关闭）')
    parser.add_argument('--max-seconds', type=float, default=0.0,
                        help='整轮总时限秒数（默认 0=不设上限；串行全量可设 0）')
    parser.add_argument('--no-timeout', action='store_true',
                        help='等同于 --idle-timeout 0 --max-seconds 0'
                             '（只用于明确知道会长时间无输出的场景）')
    parser.add_argument('--no-wait', action='store_true',
                        help='已有另一轮在跑时立刻退出，不排队')
    parser.add_argument('--lock-timeout', type=float, default=1800.0,
                        help='排队等待上限秒数（默认 1800）')
    parser.add_argument('--clean-temp', action='store_true',
                        help='开跑前清理 .pip-tmp 下超过 24h 的临时目录'
                             '（默认只清 >7 天）')
    parser.add_argument('--no-prune-temp', action='store_true',
                        help='完全不动 .pip-tmp 下的旧临时目录')
    parser.add_argument('--check', action='store_true',
                        help='只检查环境（pytest/xdist/PySide6/真机项目）后退出')
    return parser


def _looks_like_target(extra: str) -> bool:
    if not extra or extra.startswith('-'):
        return False
    if '::' in extra:
        return True
    if extra.endswith('.py'):
        return True
    normalized = extra.replace('\\', '/')
    return normalized == 'tests' or normalized.startswith('tests/')


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args, extras = _build_parser().parse_known_args(argv)

    serial = bool(args.serial)
    targeted = any(_looks_like_target(extra) for extra in extras)
    workers = 1 if serial else (args.workers or _default_workers())
    if not serial:
        workers = max(1, workers)
        if targeted:
            workers = min(workers, 4)

    if args.check:
        return _check_environment(workers, serial)

    # xdist 是并行路径的硬依赖；缺了就别假装能跑
    no_reorder = True
    if not serial:
        ok, ver = _xdist_ok()
        if not ok:
            print(f'[fasttest] pytest-xdist 不可用（{ver}）。')
            print('           python -m pip install -r requirements-dev.txt')
            return 2
        no_reorder = _xdist_supports_no_reorder()
        if not no_reorder:
            print('[fasttest] 提示：当前 pytest-xdist 没有 '
                  '--no-loadscope-reorder，重文件优先可能被它重排覆盖。')

    if args.no_timeout:
        args.idle_timeout = 0.0
        args.max_seconds = 0.0
    if args.idle_timeout < 0:
        args.idle_timeout = 0.0
    if args.max_seconds < 0:
        args.max_seconds = 0.0

    retry_ids: list[str] = []
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with RunLock(wait=not args.no_wait, timeout=args.lock_timeout):
        # 只和"上一轮纯全量"比失败集合；拿定向/`-k` 的最后一轮比会吓人。
        previous = _read_json(LAST_FULL_PATH, None)
        if args.retry_failed:
            failed = (previous.get('failed_ids')
                      if isinstance(previous, dict) else None)
            if not isinstance(failed, list) or not failed:
                if previous is None:
                    print('[fasttest] 还没有"纯全量"运行记录，无法 '
                          '--retry-failed；请先跑一次 python tools/fasttest/run.py。',
                          file=sys.stderr, flush=True)
                    return 2
                print(f'[fasttest] 上一轮纯全量'
                      f'（{previous.get("ts", "?")}）没有失败，无需复跑。',
                      flush=True)
                return 0
            retry_ids = [str(node) for node in failed if node]
            if not retry_ids:
                print('[fasttest] 上一轮纯全量的失败记录里没有有效 nodeid，'
                      '请先跑一次全量。', file=sys.stderr, flush=True)
                return 2
            if len(retry_ids) > 200:
                print(f'[fasttest] 上一轮失败 {len(retry_ids)} 条，'
                      f'只复跑前 200 条；其余见 JUnit。', flush=True)
                retry_ids = retry_ids[:200]
            extras = retry_ids + extras
            targeted = True
            print(f'[fasttest] --retry-failed：复跑上一轮'
                  f'（{previous.get("ts", "?")}）失败的 {len(retry_ids)} 条',
                  flush=True)
        if targeted:
            workers = min(workers, 4)

        # 先把"人/agent 要看的启动信息"打出来：清理旧 basetemp 可能很慢，
        # 不能让它挡在第一条输出后面，否则 WorkBuddy 之类会显示"读取任务输出"。
        _print_environment(workers, serial)
        print('[fasttest] 准备临时目录（旧 basetemp 后台清理，不阻塞启动）…',
              flush=True)

        _clean_trash()
        if not args.no_prune_temp:
            _prune_stale_temp(24.0 if args.clean_temp else 24.0 * 7)
        _prune_runs(keep=3)
        run_dir = _make_run_dir()
        basetemp = run_dir / 'tmp'
        watch_dir = run_dir / 'watch'
        junit = run_dir / 'junit.xml'
        log_path = run_dir / 'output.log'
        watch_dir.mkdir(parents=True, exist_ok=True)

        # 只和"上一轮纯全量"比失败集合；previous 已在入口读好。
        seed = _read_json(SEED_PATH, {}) or {}
        try:
            reference = float(seed.get('reference_seconds') or 0) or None
        except (TypeError, ValueError):
            reference = None

        cmd = [sys.executable, '-m', 'pytest', '-q', '--color=no']
        if not serial:
            # loadfile：**整个测试文件只在一个 worker 上跑**。比 load 保守，
            # 保留文件内顺序（Qt/模块级状态/同一 tmp 目录的测试不会跨进程），
            # 代价只是单个重文件的测试要串行 —— 它们本来就只有 1~2 条。
            #
            # --no-loadscope-reorder 必须显式关：pytest-xdist 从某个版本起
            # 默认按"文件里的用例数"重排 work unit（loadscope/loadfile 都
            # 继承），会**完全覆盖**本工具的"重文件优先"——于是 3 条用例的
            # test_disk_hygiene、2 条的真机金标全被排到 99%，长尾重新出现。
            cmd += ['-n', str(workers), '--dist', 'loadfile']
            if no_reorder:
                cmd.append('--no-loadscope-reorder')
        cmd += [
            '--basetemp', str(basetemp),
            '--junitxml', str(junit),
            '-p', 'fasttest_order_plugin',
            '-p', 'fasttest_watch_plugin',
            '--durations', '30', '--durations-min', '1.0',
        ]
        if args.no_order:
            cmd.append('--fasttest-no-order')
        cmd += extras

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        env['FASTTEST_WEIGHTS'] = str(DURATIONS_PATH)
        env['FASTTEST_WATCH_DIR'] = str(watch_dir)
        old_pythonpath = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = str(TOOL_DIR) + (
            os.pathsep + old_pythonpath if old_pythonpath else '')

        print(f'[fasttest] 临时目录: {run_dir}')
        if targeted:
            print('[fasttest] 定向运行（不是全量）：本轮不更新耗时权重。')
        if extras:
            print(f'[fasttest] 透传参数: {" ".join(extras)}')
        print('[fasttest] 开始…\n', flush=True)

        started = time.monotonic()
        last_output = started
        last_percent = -1
        timed_out = ''
        timed_out_active: list[tuple[float, str]] = []
        rc = 1
        try:
            with log_path.open('w', encoding='utf-8', errors='replace') as log:
                proc = subprocess.Popen(
                    cmd, cwd=str(ROOT), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', bufsize=1)
                assert proc.stdout is not None
                pump = _OutputPump(proc.stdout)
                next_beat = (started + args.heartbeat
                             if args.heartbeat else 0.0)
                try:
                    while True:
                        try:
                            item = pump.get(timeout=0.5)
                        except queue.Empty:
                            item = None
                        if item is _EOF:
                            break
                        if item is not None:
                            line = str(item)
                            sys.stdout.write(line)
                            sys.stdout.flush()
                            log.write(line)
                            log.flush()
                            last_output = time.monotonic()
                            match = _PROGRESS_RE.search(line)
                            if match:
                                last_percent = int(match.group(1))

                        now = time.monotonic()
                        if args.heartbeat and now >= next_beat:
                            active = _active_tests(watch_dir)
                            pieces = [
                                f'[fasttest] 心跳 {_fmt_seconds(now - started)}',
                                (f'进度 {last_percent}%'
                                 if last_percent >= 0 else '进度 ?'),
                            ]
                            if active:
                                pieces.append('在跑 ' + _active_text(active))
                            pieces.append(
                                f'最近输出 {now - last_output:.0f}s 前')
                            line = ' · '.join(pieces)
                            print(line, flush=True)
                            log.write(line + '\n')
                            log.flush()
                            next_beat = now + args.heartbeat

                        if (args.idle_timeout
                                and now - last_output >= args.idle_timeout):
                            timed_out = (f'连续 {args.idle_timeout:g}s '
                                         f'没有任何输出')
                            break
                        if (args.max_seconds
                                and now - started >= args.max_seconds):
                            timed_out = f'超过总时限 {args.max_seconds:g}s'
                            break

                    if timed_out:
                        timed_out_active = _active_tests(watch_dir, limit=12)
                        msg = (f'\n[fasttest] 检测到卡死（{timed_out}），'
                               f'正在结束 pytest…')
                        print(msg, flush=True)
                        log.write(msg + '\n')
                        log.flush()
                        if timed_out_active:
                            detail = ('[fasttest] 卡住时正在跑：'
                                      + _active_text(timed_out_active))
                            print(detail, flush=True)
                            log.write(detail + '\n')
                            log.flush()
                        _terminate_tree(proc)
                        rc = 124
                    else:
                        rc = proc.wait()
                except KeyboardInterrupt:
                    print('\n[fasttest] 收到中断，正在结束 pytest…', flush=True)
                    _terminate_tree(proc)
                    rc = 130
                except Exception as exc:  # noqa: BLE001 - 绝不能让 pytest 变孤儿
                    print(f'\n[fasttest] 监控循环异常：{exc}，'
                          f'正在结束 pytest…', file=sys.stderr, flush=True)
                    traceback.print_exc()
                    _terminate_tree(proc)
                    rc = 2
        except OSError as exc:
            print(f'[fasttest] 启动 pytest 失败: {exc}')
            return 2

        elapsed = time.monotonic() - started
        cases = parse_junit(junit)
        summary = _summarize(cases, rc, elapsed, workers, serial, run_dir)
        summary['command'] = cmd
        summary['targeted'] = targeted
        summary['extras'] = list(extras)
        summary['timed_out'] = timed_out
        summary['active_tests'] = [node for _age, node in timed_out_active]
        summary['retry_failed'] = len(retry_ids) if args.retry_failed else 0
        pure_full = not targeted and not extras and not timed_out

        # 只从"无任何透传参数的纯全量"学习权重：`-k` / `--lf` / 单文件都会
        # 让某文件只跑一部分，学进去会把排序带偏。
        if cases and pure_full and not serial:
            try:
                _update_durations(cases)
            except OSError:
                pass  # 权重是优化，不是判据

        _print_summary(summary, previous, reference)
        _write_json(run_dir / 'summary.json', summary)
        _write_json(LAST_PATH, summary)
        if pure_full:
            _write_json(LAST_FULL_PATH, summary)

        if not args.keep and rc == 0:
            # 成功即把 basetemp 改名到 trash 后台删；不让 1.5 万个文件
            # 的删除拖住本次命令的退出（agent 会一直等 stdout EOF）。
            _trash_path(basetemp)
        return int(rc)


if __name__ == '__main__':
    raise SystemExit(main())
