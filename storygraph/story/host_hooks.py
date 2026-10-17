"""
**程序侧的钩子**：让程序用"一行/一个函数"把工具接上
=========================================================================

> 用户 2026-10-11 定的原则：
> "工具的价值不在于'不依赖程序'，而在于**'依赖程序时成本最低'**。
>  程序写一行 CDS 注册，工具自动派生声明；**程序写一个验证函数，工具自动调用**。
>  这才是团队协作该有的效率。"

`cds_registry` 已经做到"扫 CDS 代码 → 自动派生声明"。
这一份负责**第二条**：程序写一个**验证函数**，工具**自动找到并调用**它。

## 约定（程序侧要做的全部事情）

在项目根（或 `game/`）放一个 `storygraph.host.py`，里面**按需**定义两个函数：

```python
def declarations():
    '''返回额外的声明（形状同 storygraph.rules.json 的 statements 段）。'''
    return [{'name': 'weather', 'label': '天气', 'columns': [...]}]

def validate_statement(head, text):
    '''这个写法这一行长什么样合不合法？

    返回 (True, '')            → 我认它，没问题
    返回 (False, '为什么')      → 我认它，但这一行不对
    返回 None                  → 我不管这个（工具自己判断）
    '''
    if head == 'weather':
        return ('强度' in text), '天气必须写强度'
    return None
```

**工具侧**：`load_host_hooks()` 找文件 → **在子进程里**加载 → 暴露
`available` / `declarations()` / `validate(head, text)`。**程序不给，就什么都
不发生**（零成本）。

## 四条自我约束

  ① **程序不给就不发生**：没有 `storygraph.host.py` → `available=False`，
     工具行为与以前**一模一样**（不许多一分依赖）；
  ② **它写的代码不许弄坏工具**：项目代码在**独立子进程**里跑，崩了只死子进程；
     `declarations()`/`validate()` 抛异常只记不炸（"依赖程序"不能变成"被程序拖死"）；
  ③ **它说什么要能看见**：`notes` 记下"钩子说了什么/哪一步失败了"，
     报告里要如实转述（否则人不知道结论是谁给的）；
  ④ **它不能卡住工具**：加载 / `declarations()` / `validate()` 都有超时；
     超时就杀掉子进程，返回"没结论"（`None`），绝不假装通过。

## 信任边界（2026-10-17，调研 P1-4）

`exec` **不是沙箱**：项目里的 `storygraph.host.py` 可以 `import os`、读写文件、
发网络请求。只要存在"打开别人发来的项目""让 AI 分析一个下载的工程"，
这就是**任意代码执行**入口（`unknown_statements()` 扫描、MCP 的
`host_validate` 都会走到这里）。

所以默认**不执行**，必须显式授权。授权三选一（优先级从上到下）：

  1. 调用方传 `trust=True/False`（MCP 的 `host_validate(trust=...)` 等）；
  2. 环境变量 `STORYGRAPH_TRUST_HOST_HOOKS=1`（CI / 个人机器）；
  3. 项目根 `storygraph.rules.json` 里写：

         {"host_hooks": {"trust": true}}

     这是**项目级**的、可评审可 diff 的授权 —— 与声明同一种治理方式。

未授权时 `available=False`，`notes` 里写清"发现了钩子但没执行 + 怎么授权"；
调用方把它当"程序不参与"即可（三态里是 `null`，不是"没问题"）。

## 进程隔离（2026-10-18）

上一版只做了"默认拒绝"，一旦授权仍然是**主进程 `exec`**：项目代码可以
`sys.exit()`、死循环、`os._exit()`、打印几百 MB —— 任意一条都能把编辑器
带下去。现在固定走 `python -I storygraph/story/host_worker.py <hook>`：

  · **执行点只有一个**：`host_worker.py`；主进程只做 JSON-lines 请求/响应；
  · **超时**：加载 20s、`declarations()` 20s、`validate()` 10s（模块常量可调）；
    超时 → `terminate()` → 必要时 `kill()`，调用方拿到 `None` + `notes`；
  · **输出上限**：单行响应 8 MiB、stderr 只留最后 64 KiB；
  · **stdout 协议不被污染**：子进程把项目代码的 `print` 全部改道到 stderr；
  · **孤儿**：父进程关 stdin 子进程就退出；`HostHooks.close()` / 垃圾回收
    也会收尸。

⚠ **这不是安全沙箱**：子进程仍有当前用户的文件/网络权限。它解决的是
"稳定性 + 超时 + 洪水输出"，不是"恶意代码无害化"。真正的权限隔离
（无网络、只读挂载、独立用户）不在这一层做。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 程序侧钩子的文件名（项目根或 `game/` 下，二者取先找到的）
HOST_HOOK_NAMES = ('storygraph.host.py',)

#: 工具会调用的三个入口（其余不管）
HOST_API = ('declarations', 'validate_statement')

#: 环境变量授权（"1/true/yes/on" 为真）。
TRUST_ENV = 'STORYGRAPH_TRUST_HOST_HOOKS'

#: `storygraph.rules.json` 里的授权键（项目级、可评审）。
TRUST_RULES_KEY = 'host_hooks'

#: 子进程执行器（与这份文件同目录）
WORKER_PATH = Path(__file__).with_name('host_worker.py')

#: 各阶段超时（秒）。测试会临时调小。
STARTUP_TIMEOUT = 20.0
DECLARATIONS_TIMEOUT = 20.0
VALIDATE_TIMEOUT = 10.0

#: 单行响应上限 / stderr 保留上限（字节）。
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024


class HostError(RuntimeError):
    """子进程没给出可用回答（启动失败 / 超时 / 崩溃 / 协议损坏）。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


_EOF = object()


class _TooLong:
    """读到超长行（由读线程放进队列）。"""


class _HostProcess:
    """一个常驻的子进程；JSON-lines 请求/响应，带超时与输出上限。"""

    def __init__(self, hook_path: Path, cwd: Path):
        self.hook_path = Path(hook_path)
        self.notes: list = []
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self._dead = False
        self._dead_reason = ''
        self._stderr_chunks: list = []
        self._stderr_size = 0
        self.proc = None
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        # 不把 PYTHONPATH / PYTHONHOME 交给项目代码；`-I` 本身也会忽略它们。
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
        self.proc = subprocess.Popen(
            [sys.executable, '-I', str(WORKER_PATH), str(self.hook_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(cwd), env=env, **kwargs)
        self._reader = threading.Thread(target=self._read_stdout, daemon=True,
                                        name='host-hook-stdout')
        self._reader.start()
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True,
                                            name='host-hook-stderr')
        self._err_reader.start()

    # ------------------------------------------------------------------
    #  读线程
    # ------------------------------------------------------------------
    def _read_stdout(self):
        try:
            while True:
                raw = self.proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    self._q.put(_EOF)
                    return
                if len(raw) > MAX_LINE_BYTES or not raw.endswith(b'\n'):
                    self._q.put(_TooLong())
                    self._fail_later('响应超过 8 MiB 或不是一行 JSON')
                    return
                self._q.put(raw)
        except (OSError, ValueError):
            self._q.put(_EOF)

    def _read_stderr(self):
        try:
            while True:
                chunk = self.proc.stderr.read(4096)
                if not chunk:
                    return
                if self._stderr_size < MAX_STDERR_BYTES:
                    room = MAX_STDERR_BYTES - self._stderr_size
                    self._stderr_chunks.append(chunk[:room])
                    self._stderr_size += min(len(chunk), room)
        except (OSError, ValueError):
            return

    def _stderr_tail(self, limit: int = 800) -> str:
        raw = b''.join(self._stderr_chunks).decode('utf-8', 'replace').strip()
        return raw[-limit:]

    # ------------------------------------------------------------------
    #  失败处理
    # ------------------------------------------------------------------
    def _fail_later(self, reason: str):
        """在锁外标记死亡（读线程不能等锁，否则会和请求线程互锁）。"""
        if not self._dead:
            self._dead = True
            self._dead_reason = reason

    def _kill(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError as e:
            swallow('story.host_hooks.kill', e)

    def _fail(self, kind: str, reason: str):
        self._fail_later(reason)
        self._kill()
        self._close_pipes()
        tail = self._stderr_tail()
        if tail:
            self.notes.append(f'子进程 stderr 末尾：{tail}')
        raise HostError(kind, reason)

    def _close_pipes(self):
        for name in ('stdin', 'stdout', 'stderr'):
            pipe = getattr(self.proc, name, None)
            try:
                if pipe is not None:
                    pipe.close()
            except (OSError, ValueError) as e:
                swallow('story.host_hooks.close_pipe', e)

    # ------------------------------------------------------------------
    #  协议
    # ------------------------------------------------------------------
    def _read_message(self, timeout: float):
        try:
            item = self._q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            self._fail('timeout', f'子进程 {timeout:.1f}s 内没有响应')
        if item is _EOF:
            code = self.proc.poll()
            reason = f'子进程提前退出（exit={code}）'
            self._fail_later(reason)
            self._close_pipes()
            tail = self._stderr_tail()
            if tail:
                self.notes.append(f'子进程 stderr 末尾：{tail}')
            raise HostError('eof', reason)
        if isinstance(item, _TooLong):
            self._fail('oversize', '响应超过 8 MiB 或协议损坏（不是一行 JSON）')
        try:
            msg = json.loads(item.decode('utf-8', 'replace'))
        except Exception as exc:                        # noqa: BLE001
            self._fail('protocol', f'子进程回了不认识的 JSON：{exc}')
        if not isinstance(msg, dict):
            self._fail('protocol', '子进程回的不是 JSON 对象')
        return msg

    def start(self):
        msg = self._read_message(STARTUP_TIMEOUT)
        if msg.get('op') != 'ready':
            self._fail('protocol', f'子进程第一行不是 ready：{str(msg)[:200]}')
        if not msg.get('ok'):
            reason = msg.get('error') or '项目钩子加载失败'
            self._kill()
            self._close_pipes()
            raise HostError('startup', reason)
        return self

    def request(self, op: str, timeout: float, **fields) -> dict:
        with self._lock:
            if self._dead:
                raise HostError('dead', self._dead_reason or '子进程已退出')
            self._next_id += 1
            rid = self._next_id
            payload = {'id': rid, 'op': op}
            payload.update(fields)
            try:
                self.proc.stdin.write(
                    (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._fail('write', f'写不进子进程：{exc}')
            deadline = time.monotonic() + timeout
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    self._fail('timeout', f'{op} 超过 {timeout:.1f}s 没有返回')
                msg = self._read_message(remain)
                if msg.get('id') == rid:
                    return msg
                # 不是本次请求的响应：项目代码可能直接写了 stdout；忽略并记一笔。
                self.notes.append('子进程里有一条不属于任何请求的输出（已忽略）')

    def close(self):
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                self._close_pipes()
                return
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except (OSError, ValueError) as e:
                swallow('story.host_hooks.close_stdin', e)
            try:
                self.proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._kill()
            self._close_pipes()


def _safe_close(proc: _HostProcess):
    try:
        proc.close()
    except Exception as exc:                            # noqa: BLE001 - 收尸不许炸
        swallow('story.host_hooks.close', exc)


class HostHooks:
    """程序提供的钩子（没有就是 `available=False` 的空壳）。"""

    def __init__(self, available=False, path='', notes=None, decls=None,
                 validate=None, denied=False, worker=None):
        self.available = available
        self.path = path
        self.notes = list(notes or [])
        self._decls = decls
        self._validate = validate
        #: 发现了钩子但**没授权执行**（与"根本没有钩子"分开，报告要能说清）
        self.denied = bool(denied)
        #: 子进程（没授权 / 没钩子 / 起不来 → None）
        self._worker = worker
        self._finalizer = (weakref.finalize(self, _safe_close, worker)
                           if worker is not None else None)

    @property
    def worker_pid(self):
        """执行项目代码的子进程 pid（没起子进程 → None；测试用）。"""
        return getattr(self._worker, 'proc', None) and self._worker.proc.pid

    # ---- 工具侧的调用口 ----
    def declarations(self) -> list:
        """程序额外声明的语句（读不出来 → 空表 + 记一笔）。"""
        if self._worker is None:
            if self._decls is None:
                return []
            try:
                got = self._decls()
            except Exception as e:                      # noqa: BLE001 - 程序代码不许炸工具
                swallow('story.host_hooks.declarations', e)
                self.notes.append(f'程序的 declarations() 抛异常：'
                                  f'{type(e).__name__}: {e}')
                return []
        else:
            try:
                msg = self._worker.request('declarations', DECLARATIONS_TIMEOUT)
            except HostError as e:
                self.notes.append(f'程序的 declarations() 没拿到结果：{e}')
                return []
            self.notes.extend(self._worker.notes)
            self._worker.notes.clear()
            if not msg.get('ok'):
                self.notes.append(f'程序的 declarations() 抛异常：'
                                  f'{msg.get("error") or "未知错误"}')
                return []
            got = msg.get('value')
            if got is None:
                self.notes.append('程序的钩子没有定义 declarations()（已跳过）')
                return []
        if not isinstance(got, (list, tuple)):
            self.notes.append('程序的 declarations() 没返回列表 —— 已忽略')
            return []
        rows = [r for r in got if isinstance(r, dict) and r.get('name')]
        if len(rows) != len(got):
            self.notes.append(f'程序的 declarations() 里有 {len(got) - len(rows)} '
                              f'条格式不对，已跳过')
        return rows

    def validate(self, head: str, text: str):
        """
        调程序的验证函数。返回 `(True|False|None, 原话)`。

        `None` = **程序不管这个**（工具自己判断）—— 与"程序说没问题"是两件事，
        所以必须区分（否则会假装所有写法都被程序验过了）。
        """
        if self._worker is None:
            if self._validate is None:
                return None, ''
            try:
                got = self._validate(str(head or ''), str(text or ''))
            except Exception as e:                      # noqa: BLE001
                swallow('story.host_hooks.validate', e)
                self.notes.append(f'程序的 validate_statement("{head}") 抛异常：'
                                  f'{type(e).__name__}: {e}')
                return None, f'（程序的验证函数抛异常：{type(e).__name__}）'
        else:
            try:
                msg = self._worker.request('validate', VALIDATE_TIMEOUT,
                                           head=str(head or ''),
                                           text=str(text or ''))
            except HostError as e:
                self.notes.append(f'程序的 validate_statement("{head}") 没拿到结果：{e}')
                return None, f'（程序的验证函数没有给出结论：{e}）'
            self.notes.extend(self._worker.notes)
            self._worker.notes.clear()
            if not msg.get('ok'):
                self.notes.append(f'程序的 validate_statement("{head}") 抛异常：'
                                  f'{msg.get("error") or "未知错误"}')
                return None, f'（程序的验证函数抛异常：{msg.get("error") or "未知错误"}）'
            got = msg.get('value')
        if got is None:
            return None, ''
        if isinstance(got, (tuple, list)) and len(got) >= 1:
            ok = got[0]
            why = str(got[1]) if len(got) > 1 else ''
            return (None if ok is None else bool(ok)), why
        return bool(got), ''

    def close(self):
        if self._finalizer is not None:
            self._finalizer()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def find_host_hook(project_root) -> Path:
    """项目根或 `game/` 下的 `storygraph.host.py`（没有 → `None`）。"""
    root = Path(project_root) if project_root else None
    if root is None:
        return None
    for base in (root, root / 'game'):
        for name in HOST_HOOK_NAMES:
            fp = base / name
            try:
                if fp.is_file():
                    return fp
            except OSError as e:                     # noqa: BLE001
                swallow('story.host_hooks.find', e)
    return None


def _truthy(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'y')


def trust_decision(project_root, trust=None) -> tuple:
    """
    是否允许执行项目钩子 → `(allowed, 人话理由)`。

    优先级：显式参数 > 环境变量 > `storygraph.rules.json` 的
    `host_hooks.trust` > **默认拒绝**。
    """
    if trust is not None:
        return bool(trust), '调用方显式指定 trust=' + str(bool(trust))
    env = os.environ.get(TRUST_ENV)
    if env is not None and str(env).strip() != '':
        return _truthy(env), f'环境变量 {TRUST_ENV}={env}'
    try:
        from storygraph.story.project_conventions import load_manifest
        data, path, _warnings = load_manifest(project_root)
    except Exception as e:                      # noqa: BLE001 - 读不到就当没授权
        swallow('story.host_hooks.trust_manifest', e)
        data, path = {}, ''
    if isinstance(data, dict):
        block = data.get(TRUST_RULES_KEY)
        if isinstance(block, dict) and 'trust' in block:
            return (bool(block.get('trust')),
                    f'{Path(path).name} 的 {TRUST_RULES_KEY}.trust')
        if 'trust_host_hooks' in data:
            return (bool(data.get('trust_host_hooks')),
                    f'{Path(path).name} 的 trust_host_hooks')
    return False, (f'默认不执行项目代码（可执行入口）。要授权：在项目根的 '
                   f'storygraph.rules.json 写 '
                   f'{{"{TRUST_RULES_KEY}": {{"trust": true}}}}，'
                   f'或设环境变量 {TRUST_ENV}=1')


def load_host_hooks(project_root, trust=None) -> HostHooks:
    """
    读程序的钩子（**没有就返回空壳**；没授权就不执行；程序代码报错也只记不炸）。

    ⚠ 这是本工具**唯一**会执行"项目里的代码"的地方 —— 所以：
      · 只认**固定文件名** `storygraph.host.py`（不是"扫到 .py 就跑"）；
      · **默认不执行**，必须显式授权（见模块 docstring 的"信任边界"）；
      · 已授权时也**只在子进程里跑**（`host_worker.py`），带超时与输出上限；
      · 任何异常都进 `notes`，报告里看得见。
    """
    fp = find_host_hook(project_root)
    if fp is None:
        return HostHooks()
    allowed, why = trust_decision(project_root, trust=trust)
    if not allowed:
        return HostHooks(
            available=False, path=str(fp), denied=True,
            notes=[f'发现程序钩子 {fp.name}，但**没有执行**：{why}'])
    try:
        fp.read_text(encoding='utf-8', errors='replace')[:1]
    except OSError as e:                            # noqa: BLE001
        swallow('story.host_hooks.read', e)
        return HostHooks(notes=[f'读不动程序的钩子 {fp.name}：{e}'])

    root = Path(project_root) if project_root else fp.parent
    try:
        worker = _HostProcess(fp, root).start()
    except HostError as e:
        note = f'程序的钩子 {fp.name} 没跑起来：{e}'
        if e.kind == 'startup':
            note = f'程序的钩子 {fp.name} 加载失败：{e}'
        return HostHooks(path=str(fp), notes=[note])
    except OSError as e:                            # noqa: BLE001 - 连 python 都起不来
        swallow('story.host_hooks.spawn', e)
        return HostHooks(path=str(fp),
                         notes=[f'起不了执行 {fp.name} 的子进程：{e}'])

    notes = []
    # 子进程已加载模块；入口是否存在由第一次调用时说清楚（避免额外往返）。
    return HostHooks(available=True, path=str(fp), notes=notes, worker=worker)


__all__ = ['DECLARATIONS_TIMEOUT', 'HOST_API', 'HOST_HOOK_NAMES', 'HostError',
           'HostHooks', 'MAX_LINE_BYTES', 'MAX_STDERR_BYTES', 'STARTUP_TIMEOUT',
           'TRUST_ENV', 'TRUST_RULES_KEY', 'VALIDATE_TIMEOUT', 'WORKER_PATH',
           'find_host_hook', 'load_host_hooks', 'trust_decision']
