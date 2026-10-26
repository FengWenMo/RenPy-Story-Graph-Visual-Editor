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
    python tools/fasttest/run.py --check         # 只检查环境

退出码与 pytest 一致（0 通过 / 1 有失败 / 2 用法或收集中断 / 5 没收集到用例），
外加 3 = 已有另一轮在跑（--no-wait 或排队超时）。
本工具**不修改**任何测试、不 skip 任何用例、不改变判据；只改变"怎么调度"。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
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
                                   errors='replace')
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
        deadline = time.monotonic() + max(0.0, self.timeout)
        announced = False
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


def _print_environment(workers: int, serial: bool) -> None:
    print('[fasttest] 环境')
    print(f'  Python      : {sys.executable} ({sys.version.split()[0]})')
    print(f'  pytest      : {_module_version("pytest") or "?"}')
    ok, ver = _xdist_ok()
    print(f'  pytest-xdist: {ver}{"" if ok else "  ← 不可用"}')
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
    """删目录。Windows 上 `cmd rmdir /s /q` 比 shutil 快一个数量级。"""
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


def _prune_runs(keep: int = 3) -> None:
    """只保留最近几轮；**只动 .pip-tmp/fasttest/runs**，不碰别人的临时目录。

    ⚠ 上一轮的 `tmp/`（basetemp）**总是删掉**，只留 junit/log/summary：
    `test_disk_hygiene` 会递归扫 `.pip-tmp`，留着上一轮几千个临时目录会让
    它从秒级变成几十秒。需要看失败现场就在**下一次跑之前**看。
    """
    if not RUNS_DIR.is_dir():
        return
    try:
        dirs = [d for d in RUNS_DIR.iterdir() if d.is_dir()]
    except OSError:
        return
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for index, old in enumerate(dirs):
        if index < keep:
            _fast_rmtree(old / 'tmp')
        else:
            _fast_rmtree(old)


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
            if os.name == 'nt':
                subprocess.run(
                    ['cmd', '/c', 'rmdir', '/s', '/q', str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=600, check=False)
            else:
                shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                removed += 1
        except (OSError, subprocess.SubprocessError):
            pass
    if removed:
        print(f'[fasttest] pruned {removed} stale temp dir(s) '
              f'older than {age_hours:g}h under .pip-tmp', flush=True)
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


def parse_junit(path: Path) -> list[dict]:
    """返回每个用例的 {nodeid, file, status, time}；解析失败返回 []。"""
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
        })
    return cases


def _summarize(cases: list[dict], rc: int, elapsed: float,
               workers: int, serial: bool, run_dir: Path) -> dict:
    counts = Counter(case['status'] for case in cases)
    failed = [case['nodeid'] for case in cases
              if case['status'] in ('failed', 'error')]
    skipped = [case['nodeid'] for case in cases if case['status'] == 'skipped']
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
    print(f'  用时   {_fmt_seconds(summary["elapsed"])}'
          f'（{"串行" if summary["serial"] else str(summary["workers"]) + " 进程"}）')
    if reference_seconds and not summary['serial'] \
            and not summary.get('targeted') and not summary.get('extras') \
            and summary['elapsed'] > 0:
        print(f'  参考   历史串行约 {_fmt_seconds(reference_seconds)}'
              f'，本轮约 {reference_seconds / summary["elapsed"]:.1f}x')

    if previous and isinstance(previous, dict) \
            and not summary.get('targeted') and not summary.get('extras'):
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
        for node in summary['failed_ids'][:20]:
            print(f'    x {node}')
        if len(summary['failed_ids']) > 20:
            print(f'    … 还有 {len(summary["failed_ids"]) - 20} 条，见 JUnit')
        if not summary['serial']:
            print('  复核   怀疑是并行假失败：'
                  'python tools/fasttest/run.py --serial <nodeid>')
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
            '  python tools/fasttest/run.py --check\n'
            '\n'
            '不认识的参数会原样交给 pytest。注意：带文件/nodeid 的调用视为'
            '"定向运行"，不会更新耗时权重。\n'
        ))
    parser.add_argument('--workers', type=int, default=0,
                        help='xdist 进程数（默认 0=自动，最多 12）')
    parser.add_argument('--serial', action='store_true',
                        help='不用 xdist，单进程跑（对照/排查并行问题时用）')
    parser.add_argument('--no-order', action='store_true',
                        help='不按历史耗时重排文件（保持默认收集顺序）')
    parser.add_argument('--keep', action='store_true',
                        help='跑完保留本轮 basetemp（默认成功即删）')
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
    if not serial:
        ok, ver = _xdist_ok()
        if not ok:
            print(f'[fasttest] pytest-xdist 不可用（{ver}）。')
            print('           python -m pip install -r requirements-dev.txt')
            return 2

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with RunLock(wait=not args.no_wait, timeout=args.lock_timeout):
        if not args.no_prune_temp:
            _prune_stale_temp(24.0 if args.clean_temp else 24.0 * 7)
        _prune_runs(keep=3)
        run_dir = _make_run_dir()
        basetemp = run_dir / 'tmp'
        junit = run_dir / 'junit.xml'
        log_path = run_dir / 'output.log'

        # 只和"上一轮纯全量"比失败集合；拿定向/`-k` 的最后一轮比会吓人。
        previous = _read_json(LAST_FULL_PATH, None)
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
            cmd += ['-n', str(workers), '--dist', 'loadfile']
        cmd += [
            '--basetemp', str(basetemp),
            '--junitxml', str(junit),
            '-p', 'fasttest_order_plugin',
            '--durations', '30', '--durations-min', '1.0',
        ]
        if args.no_order:
            cmd.append('--fasttest-no-order')
        cmd += extras

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        env['FASTTEST_WEIGHTS'] = str(DURATIONS_PATH)
        old_pythonpath = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = str(TOOL_DIR) + (
            os.pathsep + old_pythonpath if old_pythonpath else '')

        _print_environment(workers, serial)
        print(f'[fasttest] 临时目录: {run_dir}')
        if targeted:
            print('[fasttest] 定向运行（不是全量）：本轮不更新耗时权重。')
        if extras:
            print(f'[fasttest] 透传参数: {" ".join(extras)}')
        print('[fasttest] 开始…\n', flush=True)

        started = time.monotonic()
        rc = 1
        try:
            with log_path.open('w', encoding='utf-8', errors='replace') as log:
                proc = subprocess.Popen(
                    cmd, cwd=str(ROOT), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', bufsize=1)
                try:
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        log.write(line)
                        log.flush()
                    rc = proc.wait()
                except KeyboardInterrupt:
                    print('\n[fasttest] 收到中断，正在结束 pytest…', flush=True)
                    _terminate_tree(proc)
                    rc = 130
        except OSError as exc:
            print(f'[fasttest] 启动 pytest 失败: {exc}')
            return 2

        elapsed = time.monotonic() - started
        cases = parse_junit(junit)
        summary = _summarize(cases, rc, elapsed, workers, serial, run_dir)
        summary['command'] = cmd
        summary['targeted'] = targeted
        summary['extras'] = list(extras)
        pure_full = not targeted and not extras

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
            shutil.rmtree(basetemp, ignore_errors=True)
        return int(rc)


if __name__ == '__main__':
    raise SystemExit(main())
