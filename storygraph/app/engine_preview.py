"""
storygraph.app.engine_preview
=============================
**用真正的 Ren'Py 引擎跑一遍这一章** —— 「🎮 引擎试玩」的后端。

为什么不能只靠 Pillow 预览（2026-10-29，用户拍板）
---------------------------------------------------
`ui_qt.stage_preview` 是把卡片**重新画**一遍：背景、立绘、对话框、BGM 都按
项目 `gui.rpy` 还原，改台词时看效果足够快。但它不是引擎：

  · `with dissolve` / `Dissolve(0.1)` 这类转场、ATL 动画、screen 特效；
  · 项目里 `init python` / 自定义 UI 框架 / 小游戏 / 菜单交互；
  · 引擎对坏写法的真实反应（落空、跳不存在的 label、变量没定义）

——这些**只有引擎自己跑才准**。文案和策划手上没有 VSCode、没有 Ren'Py、
没有 Python，所以把**引擎随工具一起发**（`renpy_sdk/`，见
`packaging/make_stage_release.py`），由这个模块负责"把它点起来"。

为什么是镜像，而不是直接跑用户的项目
------------------------------------
Ren'Py 跑一个项目会**往项目里写东西**：`game/*.rpyc`、`game/cache/`、
`game/saves/`、根目录的 `log.txt` / `errors.txt` / `traceback.txt`，
开发者模式下还会把脚本备份到 `%APPDATA%\\RenPy\\backups\\…`。
用户的项目是**唯一真源**（十条铁律第 1 条），预览绝不能碰：

  1. 在缓存目录里搭一个**镜像**：`.rpy` / `.rpyc` 逐字节复制（保留 mtime，
     引擎不会重复编译），图片 / 音乐 / 字体这类**只读大目录用 junction**
     （Windows 目录联接，不占空间、不复制）；
  2. 引擎的 `--savedir` 指到镜像的 `userdata/saves/`，`APPDATA` / `HOME`
     也重定向进镜像 —— 存档、persistent、脚本备份全部落在镜像里；
  3. 镜像里多注入一个 `zzz_storygraph_engine_preview.rpy`（见 `_write_entry`），
     用 `config.label_overrides["start"]` 把开场重定向到目标章；
  4. 原项目**一个字节都不写**（判据：`tests/test_engine_preview.py` 里比对
     文件 mtime/内容指纹）。

为什么"跳过主菜单"是环境变量，不是改脚本
------------------------------------------
`RENPY_SKIP_SPLASHSCREEN=1` / `RENPY_SKIP_MAIN_MENU=1` 是 Ren'Py 自己在
`renpy/common/00start.rpy` 里读的（`renpy.os.environ.get(...)`）。用它就
不用去改 `screens.rpy` 里的闪屏 / 主菜单，也就不会和项目自己的魔改打架。

三种入口
--------
  · `mode='chapter'`：跳到 `<label>_control`（有的话）或 `<label>` 本身 ——
    这是"看这一章从开始到结尾"最像真实游玩的方式；
  · `mode='beat'`：`--warp game/<file>.rpy:<line>` 直接落到**当前这一拍**
    （近似：前面几章的 `$` 赋值没有执行，变量可能缺，界面会提示）；
  · `mode='start'`：什么都不改，从游戏自己的 `label start` 开始（100% 原样）。

⚠ 只有 `mode='beat'` 依赖开发者模式（Ren'Py 的 `--warp` 在
`config.developer` 为假时直接抛异常）；SDK 未打包运行项目时开发者模式默认开。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.user_path import app_data_dir

#: 打包版里引擎目录的名字（`StoryGraph.exe` 旁边）。打包脚本与这里**必须同名**：
#: 改名字要同时改 `packaging/make_stage_release.py::RUNTIME_DIRNAME`。
RUNTIME_DIRNAME = 'renpy_sdk'

#: 演出专用版标记文件（`StoryGraph.exe` 旁边）。存在 = 这是演出专用版。
#: 标准版没有它 —— 所以"自动套预设 / 自动打开项目"只在演出专用版里发生。
EDITION_MARKER = '演出专用版.txt'

#: 在镜像里注入的入口文件名（放 `game/` 下，`zzz` 前缀保证最后加载）。
INJECT_NAME = 'zzz_storygraph_engine_preview.rpy'

#: 镜像缓存目录（`%LOCALAPPDATA%\\StoryGraph\\engine_preview\\`）里最多留几份。
#: 一份镜像通常 1~10 MB（脚本 + rpyc），但 junction 不占空间，几份都无所谓；
#: 留 8 份是给"同时在 3 个项目之间来回切"的人。
KEEP_MIRRORS = 8

#: 引擎窗口的类名（Ren'Py 用 SDL2/3）。只用来判断"窗口真的出来了没有"，
#: 判断不出来（非 Windows / 改过窗口类）时退化成"进程活着就算启动成功"。
_WINDOW_CLASSES = ('SDL_app', 'RenPy')

#: 镜像里不复制 / 不进 junction 的目录名（引擎自己的运行产物、VCS、缓存）。
_SKIP_DIRS = {'saves', 'cache', '__pycache__', '.git', 'tmp'}

#: 不复制进去的文件（引擎运行产物；复制过去反而会让上一次的报错被当成这一次的）。
_SKIP_FILES = {'log.txt', 'errors.txt', 'traceback.txt', 'traceback.rpy'}

#: 不复制进去的后缀（Ren'Py 的编译中间产物；`.rpyc` 要复制，见 docstring）。
_SKIP_SUFFIXES = {'.rpyb', '.bak', '.tmp'}

#: 项目根里值得复制进镜像的小文件（引擎偶尔读 `project.json` / 图标）。
_ROOT_FILES = ('project.json', 'icon.ico')

#: 从项目根猜引擎开发者模式的开关不用管 —— 见 `launch()` 的 env。


class EnginePreviewError(RuntimeError):
    """引擎预览失败，**带一句人话**（UI 直接显示 `str(e)`，细节放 `hint`）。"""

    def __init__(self, message: str, hint: str = ''):
        super().__init__(str(message))
        self.hint = str(hint or '')

    def describe(self) -> str:
        return f'{self}{("\\n" + self.hint) if self.hint else ""}'


@dataclass(frozen=True)
class EngineInfo:
    """找到的一份 Ren'Py 运行时（完整 SDK 根目录，里面有 `renpy.exe`）。"""

    home: Path
    source: str = ''

    @property
    def exe(self) -> Path:
        return Path(self.home) / 'renpy.exe'

    def version(self) -> str:
        """读 `renpy/vc_version.py`（不启动引擎，毫秒级）。读不出来就空串。"""
        try:
            text = (Path(self.home) / 'renpy' / 'vc_version.py').read_text(
                encoding='utf-8', errors='replace')
        except OSError:
            return ''
        match = re.search(r"""^\s*version\s*=\s*['"]([^'"]+)['"]""",
                          text, re.M)
        return match.group(1) if match else ''

    def describe(self) -> str:
        ver = self.version() or '版本未知'
        return f'Ren\'Py {ver}（{self.home}）'


@dataclass(frozen=True)
class PreviewTarget:
    """一次引擎预览要跑到哪里。由 `make_target()` 生成，别自己拼。"""

    project_root: Path
    #: 真正 `jump` 的 label（可能是 `<label>_control`）
    label: str = ''
    #: 人看的章节 label（原 label，不带 `_control`）
    chapter_label: str = ''
    #: >0 时用 `--warp` 落到这一行（1-based，源文件里的行号）
    line: int = 0
    #: `--warp` 用的相对项目根的路径（`game/...`，正斜杠）
    file: str = ''
    #: 'chapter' / 'beat' / 'start'
    mode: str = 'chapter'

    @property
    def warp_spec(self) -> str:
        """`game/chapter/ch2.rpy:123`（Ren'Py `--warp` 的格式）。"""
        rel = str(self.file or '').replace('\\', '/').lstrip('/')
        if rel and not rel.startswith('game/'):
            rel = 'game/' + rel
        return f'{rel}:{int(self.line)}'

    def describe(self) -> str:
        if self.mode == 'start':
            return '从游戏开头完整试玩'
        if self.mode == 'beat':
            return f'从「{self.chapter_label or self.label}」第 {self.line} 行开始（近似）'
        return f'「{self.chapter_label or self.label}」从章首开始'


@dataclass
class EngineRun:
    """一次已经点起来的引擎（子进程 + 镜像目录）。"""

    process: subprocess.Popen
    target: PreviewTarget
    engine: EngineInfo
    project_dir: Path
    mirror_root: Path
    log_path: Path
    command: tuple = ()
    started_at: float = field(default_factory=time.time)
    _window_ready: bool = False
    _closed: bool = False
    _stdout_handle: object = None

    # ---- 状态 -----------------------------------------------------
    def poll(self):
        """子进程退出码；还活着返回 None。"""
        return self.process.poll()

    def running(self) -> bool:
        return self.process.poll() is None

    def ready(self) -> bool:
        """
        "引擎窗口出来了吗"。

        先看进程活着 + 找到属于它的 `SDL_app` 窗口；找不到窗口实现时退化
        成"进程活着超过 6 秒"。**失败不会抛异常**（预览已经起来了，判断
        窗口只是给状态栏用）。
        """
        if self._window_ready:
            return True
        if not self.running():
            return False
        try:
            if _pid_has_window(self.process.pid):
                self._window_ready = True
                return True
        except Exception as e:                      # noqa: BLE001 - 判不出来不致命
            swallow('app.engine_preview.ready', e)
            if time.time() - self.started_at >= 6.0:
                self._window_ready = True
        return self._window_ready

    def wait_ready(self, timeout: float = 25.0) -> str:
        """
        等窗口出来。返回 `'ready'` / `'exited'` / `'timeout'`。

        为什么不无限等：引擎崩了（脚本语法错）时进程会退出并留 `errors.txt`，
        界面要能马上说"没起来，原因是…"，而不是转圈等人来关。
        """
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            if self.poll() is not None:
                return 'exited'
            if self.ready():
                return 'ready'
            time.sleep(0.25)
        return 'timeout'

    # ---- 诊断 -----------------------------------------------------
    def log_tail(self, lines: int = 30) -> str:
        """引擎自己的 `log.txt` 末尾（没写出来就退回 stdout 捕获）。"""
        lines = max(1, int(lines))
        for path in (self.project_dir / 'log.txt', self.log_path):
            try:
                text = Path(path).read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
            if tail:
                return '\n'.join(tail)
        return ''

    def error_report(self) -> str:
        """
        失败原因：`errors.txt` / `traceback.txt` 优先，其次 log 末尾。

        这两种文件是 Ren'Py 报错的**权威落点**；只读 log 的末尾常常只有
        "Interface start" 这种正常行，让人误以为没问题。
        """
        for name in ('errors.txt', 'traceback.txt'):
            p = self.project_dir / name
            try:
                text = p.read_text(encoding='utf-8', errors='replace').strip()
            except OSError:
                continue
            if text:
                return text[-2000:]
        return self.log_tail(40)

    # ---- 停止 -----------------------------------------------------
    def stop(self, grace: float = 3.0) -> None:
        """结束引擎（先礼貌 `terminate`，超时再 `kill` 整棵进程树）。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=max(0.5, float(grace)))
                except subprocess.TimeoutExpired:
                    _taskkill(self.process.pid)
        except Exception as e:                      # noqa: BLE001 - 退出路径不抛
            swallow('app.engine_preview.stop', e)
        try:
            if self._stdout_handle is not None:
                self._stdout_handle.close()
        except Exception as e:                      # noqa: BLE001 - 关不掉也不该炸
            swallow('app.engine_preview.stop_stdout', e)

    def describe(self) -> str:
        state = '运行中' if self.running() else f'已退出（{self.poll()}）'
        return f'{self.target.describe()} · 引擎{state}'


# ---------------------------------------------------------------------------
# 引擎发现
# ---------------------------------------------------------------------------

#: 源码运行时仓库根 —— **模块级算一次**。
#: 刻意不在函数里读 `__file__`：`tests/test_no_undefined_names.py` 用 symtable
#: 扫每个函数"引用了但模块级看不到的全局名"，函数级的 `__file__` 会被它报成
#: NameError 风险（模块级则不算，因为模块作用域本身就有）。打包后不走这条。
try:
    _SOURCE_ROOT: Path | None = Path(__file__).resolve().parents[2]
except (IndexError, OSError):                     # pragma: no cover - 极端环境
    _SOURCE_ROOT = None


def _frozen() -> bool:
    import sys
    return bool(getattr(sys, 'frozen', False))


def app_dir() -> Path | None:
    """
    工具自己所在的目录。

      · 打包后：`StoryGraph.exe` 所在目录（引擎 / 预设 / 项目都按它找）；
      · 源码运行：仓库根（开发时引擎走 `STORYGRAPH_RENPY_HOME` 或开发机 SDK）。

    算不出来返回 `None`（调用方当"没有"处理，不抛）。
    """
    import sys
    if _frozen():
        try:
            return Path(os.path.abspath(sys.executable)).parent
        except (OSError, ValueError):
            return None
    return _SOURCE_ROOT


#: 兼容旧名字（本模块内部曾用 `_exe_dir`，mixin/测试还在按那个叫）
_exe_dir = app_dir


#: 源码运行时才尝试的开发机 SDK 位置（打包版永远只认 exe 旁边那份）。
#: 顺序即优先级；`STORYGRAPH_RENPY_HOME` 环境变量永远排第一（测试/换版本用）。
_DEV_SDK_CANDIDATES = (
    Path(r'D:\software\renpy-8.4.1-sdk'),
)


def find_engine(explicit=None) -> EngineInfo | None:
    """
    找一份可用的 Ren'Py 运行时（返回 `None` = 没找到，不是异常）。

    顺序（先到先得）：

      1. `explicit`（调用方指定）；
      2. 环境变量 `STORYGRAPH_RENPY_HOME` / `RENPY_SDK_HOME`
         —— 让测试和"换一份引擎试试"不用改代码；
      3. **exe 旁边**的 `renpy_sdk/`（演出专用版的正常路径）；
      4. 源码运行时：仓库里 / 开发机上那份 SDK（仅方便开发）。

    每一份都要求 `renpy.exe` **与** `renpy/` 目录同时在（缺一个的目录
    通常是拷贝到一半的坏包 —— 只报"找到了"会让后面启动时莫名其妙地失败）。
    """
    seen: list[Path] = []

    def _probe(home) -> EngineInfo | None:
        try:
            p = Path(home).expanduser()
        except (TypeError, ValueError, OSError):
            return None
        if not p or p in seen:
            return None
        seen.append(p)
        if (p / 'renpy.exe').is_file() and (p / 'renpy').is_dir():
            return EngineInfo(home=p.resolve(), source=str(p))
        return None

    if explicit is not None:
        hit = _probe(explicit)
        if hit is not None:
            return hit

    for env_name in ('STORYGRAPH_RENPY_HOME', 'RENPY_SDK_HOME'):
        raw = (os.environ.get(env_name) or '').strip()
        if raw:
            hit = _probe(raw)
            if hit is not None:
                return hit

    exe_dir = app_dir()
    if exe_dir is not None:
        hit = _probe(exe_dir / RUNTIME_DIRNAME)
        if hit is not None:
            return hit
        # 有人可能把 `renpy.exe` 直接摊在 exe 旁边（手工拼包），也认
        direct = _probe(exe_dir)
        if direct is not None:
            return direct

    if not _frozen():
        for cand in _DEV_SDK_CANDIDATES:
            hit = _probe(cand)
            if hit is not None:
                return hit
        root = app_dir()
        if root is not None:
            hit = _probe(root / RUNTIME_DIRNAME)
            if hit is not None:
                return hit
    return None


def engine_problem_hint() -> str:
    """找不到引擎时给用户看的那句"那你该怎么办"。"""
    if stage_edition():
        return ('这个包应该自带引擎（renpy_sdk\\），可能被杀毒软件隔离了，'
                '或解压不完整。重新解压一次；还不行把整包发给开发者。')
    return ('标准版不带引擎（要省 300MB+）。想要「🎮 引擎试玩」，请用'
            '演出专用版（StoryGraph-Stage），或者装一份 Ren\'Py SDK 后设置'
            '环境变量 STORYGRAPH_RENPY_HOME 指向它。')


def preview_supported() -> tuple:
    """`(能不能用, 给人看的说明)` —— UI 拿它决定按钮的提示语。"""
    engine = find_engine()
    if engine is None:
        return False, engine_problem_hint()
    return True, engine.describe()


# ---------------------------------------------------------------------------
# 演出专用版 / 镜像目录
# ---------------------------------------------------------------------------

def stage_edition() -> bool:
    """
    这是不是**演出专用版**（打包时塞了完整引擎的那一版）。

    判据两个，任一成立即可：
      · exe 旁边有 `演出专用版.txt`（打包脚本写）；
      · 环境变量 `STORYGRAPH_STAGE_EDITION=1`（源码调试/测试用）。

    **标准版不许出现任何"自动写项目"的行为**（自动套预设、自动打开项目），
    所以这些都要先问这一句 —— 用户拿标准版打开别人的项目时，工具的行为
    必须和以前一模一样。
    """
    if os.environ.get('STORYGRAPH_STAGE_EDITION') == '1':
        return True
    exe_dir = app_dir()
    if exe_dir is None:
        return False
    return (exe_dir / EDITION_MARKER).is_file()


def cache_root() -> Path:
    """
    引擎镜像的根目录。

    默认 `%LOCALAPPDATA%\\StoryGraph\\engine_preview\\`（跟着 `user_path`
    那套规则走，不另起炉灶）。测试可以用 `STORYGRAPH_ENGINE_CACHE` 指到
    `tmp_path`，避免在用户目录里留东西。
    """
    raw = (os.environ.get('STORYGRAPH_ENGINE_CACHE') or '').strip()
    if raw:
        return Path(raw)
    return app_data_dir('engine_preview')


def _mirror_root(project_root: Path) -> Path:
    key = hashlib.sha1(str(project_root).lower().encode('utf-8')).hexdigest()[:12]
    name = re.sub(r'[^\w\u4e00-\u9fff.-]+', '_', project_root.name)[:32] or 'project'
    return cache_root() / f'{name}-{key}'


def _prune_mirrors(root: Path) -> None:
    """只留最近 `KEEP_MIRRORS` 份镜像；正在被引擎用的那份不删（按 mtime 最新）。"""
    try:
        dirs = [p for p in Path(root).iterdir() if p.is_dir()]
    except OSError:
        return
    dirs.sort(key=lambda p: _safe_mtime(p), reverse=True)
    for old in dirs[KEEP_MIRRORS:]:
        # 镜像里可能有 junction：用安全删（绝不跟进 junction 删真文件）
        _rmtree_safe(old)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ---------------------------------------------------------------------------
# 镜像：脚本复制 + 资源目录 junction
# ---------------------------------------------------------------------------

def _append(**fields):
    """`safeguard` 的记账口（项目里到处用；预览失败也必须留痕）。"""
    swallow('app.engine_preview', Exception(str(fields)))


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, 'isjunction', None)
    if checker is not None:
        try:
            return bool(checker(path))
        except OSError:
            return False
    return path.is_symlink()


def _remove_path(path: Path) -> None:
    """删一个镜像里的东西：junction / 符号链接只摘链接，绝不跟进。"""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and _is_junction(path):
        os.rmdir(path)
        return
    if path.is_dir():
        _rmtree_safe(path)
        return
    try:
        path.unlink()
    except OSError as e:                        # noqa: BLE001 - 删不掉就留着
        swallow('app.engine_preview.remove_path', e)


def _rmtree_safe(path: Path) -> None:
    """删一棵**含 junction** 的树：junction 只摘链接，不跟进去删目标里的真文件。"""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and _is_junction(path):
        os.rmdir(path)
        return
    if path.is_file():
        path.unlink()
        return
    try:
        entries = list(os.scandir(path))
    except OSError:
        return
    for entry in entries:
        child = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
            if _is_junction(child) or entry.is_symlink():
                try:
                    os.rmdir(child)
                except OSError as e:            # noqa: BLE001 - 摘不掉就留着
                    swallow('app.engine_preview.rm_junction', e)
            else:
                _rmtree_safe(child)
        else:
            try:
                child.unlink()
            except OSError as e:                # noqa: BLE001 - 删不掉就留着
                swallow('app.engine_preview.rm_file', e)
    try:
        os.rmdir(path)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def _make_junction(link: Path, target: Path) -> bool:
    """
    给目录建 junction（**不复制**，Windows 目录联接不需要管理员权限）。

    失败返回 False，调用方退化成复制 —— "建不出 junction"不该让预览点不起来
    （跨盘/网络盘、被安全软件拦，都可能；复制慢但结果一样）。
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or link.is_symlink():
        _remove_path(link)
    try:
        cp = subprocess.run(
            ['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
            capture_output=True, text=True, encoding='mbcs', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return link.is_dir()
    except (OSError, ValueError) as e:
        _append(step='junction', error=e)
        return False


def _has_rpy(folder: Path) -> bool:
    """这个目录（含子目录）里有没有 `.rpy` —— 有就逐层复制，没有就整棵 junction。"""
    for _root, _dirs, files in os.walk(folder):
        if any(str(f).endswith('.rpy') for f in files):
            return True
    return False


def _game_dir(project_root: Path) -> Path | None:
    """
    项目里**名字精确等于 `game`** 的那个目录（没有返回 `None`）。

    为什么不写 `(root / 'game').is_dir()`：Windows 文件系统不区分大小写，
    项目旁边有个 `Game/` 目录时那一句也会返回 True —— 于是"选错一层目录"
    被当成合法项目，后面镜像里少了一堆脚本，报错却指向别处。名字必须精确。
    """
    try:
        for entry in os.scandir(project_root):
            if entry.name == 'game' and entry.is_dir():
                return Path(entry.path)
    except OSError:
        return None
    return None


def _scan_sources(project_root: Path) -> tuple:
    """
    走一遍项目，分成两张表：

      · `copies`：相对路径 → 源文件（要逐字节复制：`.rpy` / `.rpyc` /
        根目录小文件）；
      · `links`：相对路径 → 源目录（整棵 junction：图片 / 音乐 / 字体…）。

    目录按"里面有没有 `.rpy`"决定：有 → 逐层往下（脚本必须复制，否则
    引擎的 `.rpyc` 会写回项目）；没有 → 整棵软链（大资源零拷贝）。
    """
    copies: dict[str, Path] = {}
    links: dict[str, Path] = {}

    def walk(src: Path, rel_prefix: str) -> None:
        try:
            entries = sorted(os.scandir(src), key=lambda e: e.name.lower())
        except OSError as e:
            _append(step='scan', path=str(src), error=e)
            return
        for entry in entries:
            name = entry.name
            rel = f'{rel_prefix}/{name}' if rel_prefix else name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name in _SKIP_DIRS:
                    continue
                child = Path(entry.path)
                if _has_rpy(child):
                    walk(child, rel)
                else:
                    links[rel] = child
                continue
            if name in _SKIP_FILES:
                continue
            suffix = Path(name).suffix.lower()
            if suffix in _SKIP_SUFFIXES:
                continue
            copies[rel] = Path(entry.path)

    game = _game_dir(project_root)
    if game is not None:
        walk(game, 'game')
    for name in _ROOT_FILES:
        p = project_root / name
        if p.is_file():
            copies[name] = p
    return copies, links


def _load_manifest(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_manifest(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding='utf-8')
        os.replace(tmp, path)
    except OSError as e:
        _append(step='manifest', error=e)


def _copy_if_changed(src: Path, dst: Path) -> bool:
    """复制（保留 mtime —— 引擎比 mtime 决定要不要重编译；不变就不动）。"""
    try:
        st = src.stat()
    except OSError:
        return False
    try:
        if dst.is_file():
            dt = dst.stat()
            if dt.st_size == st.st_size and dt.st_mtime_ns == st.st_mtime_ns:
                return False
    except OSError as e:                        # noqa: BLE001 - 判不出来就重拷
        swallow('app.engine_preview.copy_stat', e)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.copy2(src, dst)
        return True
    except OSError as e:
        _append(step='copy', path=str(src), error=e)
        return False


def mirror_project(project_root, *, force: bool = False) -> Path:
    """
    把项目同步进镜像（**可以反复调**，第二次只动变过的文件）。

    返回镜像里的**项目根**（不是缓存根）。原项目只读。

    同步规则：
      · 新文件复制、变过的文件重写（mtime/size 相同就跳过）；
      · 源里删掉的 `.rpy` / `.rpyc` 从镜像删掉（否则引擎会把已删的章
        当成还在 —— `.rpyc` 没 `.rpy` 时 Ren'Py 会直接拿它跑）；
      · 目录在"有 `.rpy` → 逐层复制"和"没有 → junction"之间变化时，
        把镜像里那棵临时树换掉（两边都是镜像内的东西，安全）。
    """
    root = _check_project(project_root)
    mirror_root = _mirror_root(root)
    project_dir = mirror_root / 'project'
    manifest_path = mirror_root / 'mirror.json'
    manifest = _load_manifest(manifest_path)
    old_files = dict(manifest.get('files') or {})
    old_links = dict(manifest.get('links') or {})

    copies, links = _scan_sources(root)
    project_dir.mkdir(parents=True, exist_ok=True)

    # ⚠ 先摘掉"这次不再当链接"的旧 junction，再复制文件。
    #   反过来的顺序有一个**会写坏用户项目**的坑：某个目录上次没有 .rpy、
    #   整棵 junction 到项目里，这次加了 .rpy 就得逐层复制 —— 如果先复制，
    #   `dst/rel` 正好落在 junction 里，`shutil.copy2` 会写进**用户的真实目录**。
    for rel in set(old_links) - set(links):
        _remove_path(project_dir / rel)

    new_files: dict[str, list] = {}
    for rel, src in copies.items():
        dst = project_dir / rel
        if _copy_if_changed(src, dst) or dst.is_file():
            try:
                st = dst.stat()
                new_files[rel] = [int(st.st_mtime_ns), int(st.st_size)]
            except OSError as e:                # noqa: BLE001 - 记不上指纹下次再拷
                swallow('app.engine_preview.mirror_stat', e)

    # 源里删掉的文件：镜像里也删（连同旁边自动生成的 .rpyc）
    for rel in set(old_files) - set(new_files):
        _remove_path(project_dir / rel)
        if rel.endswith('.rpy'):
            _remove_path(project_dir / (rel + 'c'))

    # 目录：没 `.rpy` 的整棵 junction；结构变了就替换
    new_links: dict[str, str] = {}
    for rel, src in links.items():
        dst = project_dir / rel
        if dst.exists() and _is_junction(dst):
            new_links[rel] = str(src)
            continue
        if dst.exists():
            _remove_path(dst)               # 原先是"逐层复制"的临时树，换成链接
        if _make_junction(dst, src):
            new_links[rel] = str(src)
        else:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            new_links[rel] = f'copy:{src}'
    for rel in set(old_links) - set(new_links):
        # 只摘**还是链接**的那些路径：`game/data` 这种"上次是 junction、
        # 这次有 .rpy 变成逐层复制"的目录已经在上面换成了真目录 + 文件，
        # 这里再删一次就把刚复制好的镜像文件删了（实测：镜像里整个目录消失，
        # 引擎启动报找不到脚本）。
        target = project_dir / rel
        if _is_junction(target) or target.is_symlink():
            _remove_path(target)

    _save_manifest(manifest_path, {
        'project': str(root),
        'files': new_files,
        'links': new_links,
        'updated': time.time(),
    })
    return project_dir


def _check_project(project_root) -> Path:
    root = Path(str(project_root or '')).expanduser()
    if not root.is_dir():
        raise EnginePreviewError(
            f'项目目录不存在：{project_root}',
            hint='选项目根目录（里面有 game/ 文件夹的那个）。')
    if _game_dir(root) is None:
        raise EnginePreviewError(
            f'这里没有 game/ 文件夹：{root}',
            hint='Ren\'Py 项目的根目录里一定有 game/；你可能选到了更上一层或里面一层的目录。')
    return root.resolve()


# ---------------------------------------------------------------------------
# 注入入口
# ---------------------------------------------------------------------------

def _entry_label(project_root: Path) -> str:
    """
    注入 label 的名字。默认 `sg_engine_preview`；项目里撞名就加后缀。

    撞名不是理论问题：工具的预览入口是**写进用户项目镜像的**，如果哪天
    项目自己有个同名 label，`jump` 会跳到人家的 label 上 —— 看起来像
    "引擎预览跑错章"，其实一点都不难查，只是会把用户吓一跳。
    """
    from storygraph.app.chapter_index import build_index
    try:
        names = set()
        for fe in build_index(project_root).files:
            names.update(fe.label_names())
    except Exception as e:                          # noqa: BLE001 - 扫不出来就用默认名
        _append(step='entry_label', error=e)
        return 'sg_engine_preview'
    name = 'sg_engine_preview'
    n = 1
    while name in names:
        n += 1
        name = f'sg_engine_preview_{n}'
    return name


def _write_entry(project_dir: Path, target: PreviewTarget) -> Path:
    """
    在镜像里写预览入口（**只写镜像**）。

    `mode='start'` 什么都不用做（正常 start 就是它）；其余两种都把
    `config.label_overrides["start"]` 指到注入 label 上。为什么用
    `init 999`：`init` 按优先级从低到高跑，999 跑在项目自己的所有
    `init` 之后，所以就算项目自己也改了 `label_overrides`，我们也是最后一手。
    """
    path = project_dir / 'game' / INJECT_NAME
    if target.mode == 'start':
        try:
            if path.exists():
                path.unlink()
        except OSError as e:                    # noqa: BLE001 - 旧入口删不掉也照跑
            swallow('app.engine_preview.entry_cleanup', e)
        return path
    entry = _entry_label(target.project_root)
    label = target.label or 'start'
    body = (
        '# ── StoryGraph 引擎预览入口（自动生成，关掉引擎后可删）──────\n'
        '# 只存在于预览镜像里，**不会**写进你的项目。\n'
        '#\n'
        '# 原理：Ren\'Py 从 `label start` 开始，这里把 start 重定向到本文件；\n'
        '# 再配合启动时的 RENPY_SKIP_MAIN_MENU / RENPY_SKIP_SPLASHSCREEN，\n'
        '# 点一下就能直接看到目标章，而不是先看一遍 Logo 和主菜单。\n'
        'init 999 python:\n'
        '    config.label_overrides["start"] = "%s"\n'
        '\n'
        'label %s:\n'
        '    jump %s\n' % (entry, entry, label)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    return path


def _write_capture(project_dir: Path) -> None:
    """
    自动截图（只在测试/验收时用；正常预览不注入）。

    为什么用这个而不是"截 Windows 窗口"：引擎窗口可能被别的窗口挡着，
    `PIL.ImageGrab` 抓到的是挡在前面的那个窗口（2026-10-28 踩过：截图里
    是 DSH 的侧栏，被误判成"引擎黑屏卡住"）。`renpy.screenshot` 由引擎
    自己出图，**窗口被遮住也不影响**。
    """
    path = project_dir / 'game' / 'zzz_storygraph_engine_capture.rpy'
    body = (
        '# StoryGraph 验收用自动截图（自动生成）\n'
        'init 999 python:\n'
        '    import os as _sg_os, time as _sg_time\n'
        '    _sg_cap = {"t0": _sg_time.time(), "last": -99.0, "n": 0}\n'
        '\n'
        '    def _sg_autocap():\n'
        '        s = _sg_cap\n'
        '        if s["n"] >= 6:\n'
        '            return\n'
        '        now = _sg_time.time()\n'
        '        if now - s["t0"] < 5 or now - s["last"] < 2.5:\n'
        '            return\n'
        '        s["last"] = now\n'
        '        s["n"] += 1\n'
        '        try:\n'
        '            renpy.screenshot(_sg_os.path.join(\n'
        '                config.basedir, "sg_shot_%02d.png" % s["n"]))\n'
        '        except Exception:\n'
        '            pass\n'
        '\n'
        '    config.periodic_callbacks.append(_sg_autocap)\n'
    )
    path.write_text(body, encoding='utf-8')


def _clear_captures(project_dir: Path) -> None:
    for old in project_dir.glob('sg_shot_*.png'):
        try:
            old.unlink()
        except OSError as e:                    # noqa: BLE001 - 旧截图删不掉不致命
            swallow('app.engine_preview.clear_captures', e)


# ---------------------------------------------------------------------------
# 目标解析
# ---------------------------------------------------------------------------

def make_target(project_root, *, label: str = '', file_path=None,
                line: int = 0, mode: str = 'chapter') -> PreviewTarget:
    """
    把"用户点的东西"翻译成 `PreviewTarget`。

    · `label`：章节 label（卡片页当前 label / 结构图选中节点）；
    · `file_path`：章节文件（没给 label 时用它取文件里第一个 label）；
    · `line`：>0 → `mode='beat'`，从这一行 warp 进去（近似，见模块 docstring）；
    · `mode='start'`：从头完整试玩。

    找不到 label 时**明确报错**（"这一章还没导出 / label 名字变了"），
    绝不悄悄跳 start —— 那会让人以为"预览坏了"。
    """
    root = _check_project(project_root)
    from storygraph.app.chapter_index import build_index
    index = build_index(root)
    by_label: dict[str, str] = {}
    first_of_file: dict[str, str] = {}
    for fe in index.files:
        for le in fe.labels:
            by_label.setdefault(le.name, fe.rel)
        if fe.labels:
            first_of_file[fe.rel] = fe.labels[0].name

    label = str(label or '').strip()
    rel = ''
    if file_path:
        try:
            rel = Path(file_path).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            rel = Path(str(file_path)).as_posix()
    if not label and rel:
        label = first_of_file.get(rel, '')

    if mode == 'start':
        return PreviewTarget(project_root=root, label='start',
                             chapter_label='', mode='start')

    if not label:
        raise EnginePreviewError(
            '还不知道要试玩哪一章。',
            hint='在卡片页先载入一章（或在结构图里点一个模块），再点「🎮 引擎试玩」。')
    if label not in by_label:
        raise EnginePreviewError(
            f'项目里没有 label「{label}」—— 这一章可能还没导出成 .rpy。',
            hint='先在卡片页「⤓ 导出 .rpy」，再试玩。')

    if line and int(line) > 0:
        target_rel = rel or by_label.get(label, '')
        if not target_rel:
            raise EnginePreviewError(f'找不到「{label}」所在的 .rpy 文件。')
        return PreviewTarget(project_root=root, label=label,
                             chapter_label=label, line=int(line),
                             file=target_rel, mode='beat')

    # 章首：优先 `<label>_control`（工具的编排层会把"进章 + 后续菜单"串起来）
    control = f'{label}_control'
    jump_to = control if control in by_label else label
    return PreviewTarget(project_root=root, label=jump_to,
                         chapter_label=label, mode='chapter')


# ---------------------------------------------------------------------------
# 启动 / 收尾
# ---------------------------------------------------------------------------

_RUNS: list[EngineRun] = []


def live_runs() -> tuple:
    """当前还活着的引擎（UI 用它避免重复点、关窗时停干净）。"""
    return tuple(r for r in _RUNS if r.running())


def _child_env(mirror_root: Path) -> dict:
    """
    子进程环境：**把 Ren'Py 的用户级写入全部关进镜像**。

    · `RENPY_SKIP_*`：跳过闪屏 / 主菜单（见模块 docstring）；
    · `RENPY_DISABLE_BACKUPS`：关掉"备份脚本到 %APPDATA%\\RenPy\\backups"。
      不关的话每点一次试玩就在用户真实用户目录里多一份备份（10-27 之前
      `lint` 也这样，已经堆了几百个目录）；
    · `APPDATA` / `LOCALAPPDATA` / `HOME` / `USERPROFILE`：重定向，保证
      persistent / 随便什么用户级缓存都落在镜像里。
    """
    env = dict(os.environ)
    env['RENPY_SKIP_MAIN_MENU'] = '1'
    env['RENPY_SKIP_SPLASHSCREEN'] = '1'
    env['RENPY_DISABLE_BACKUPS'] = 'I take responsibility for this.'
    env.pop('RENPY_AUTO_LOAD', None)
    userdata = mirror_root / 'userdata'
    home = userdata / 'home'
    env['APPDATA'] = str(userdata / 'Roaming')
    env['LOCALAPPDATA'] = str(userdata / 'Local')
    env['HOME'] = str(home)
    env['USERPROFILE'] = str(home)
    for p in (env['APPDATA'], env['LOCALAPPDATA'], home):
        try:
            Path(p).mkdir(parents=True, exist_ok=True)
        except OSError as e:                    # noqa: BLE001 - 建不出来引擎自己会退
            swallow('app.engine_preview.child_userdir', e)
    return env


def _taskkill(pid: int) -> None:
    """整棵进程树（renpy.exe 会再起一个 python 子进程，只 kill 父的会留孤儿）。"""
    try:
        subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'],
                       capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, ValueError):
        try:
            os.kill(int(pid), 9)
        except OSError as e:                    # noqa: BLE001 - 进程可能已经没了
            swallow('app.engine_preview.taskkill', e)


def launch(target: PreviewTarget, *, engine=None, capture: bool = False) -> EngineRun:
    """
    点起引擎。返回 `EngineRun`（调用方要拿着它 poll / stop / 看错误）。

    `capture=True` 会注入自动截图（只给测试/验收用，正常预览不要开）。

    失败抛 `EnginePreviewError`（找不到引擎 / 项目不对 / 窗口没起来……），
    消息是给人看的；UI 直接弹 `describe()`。
    """
    if not isinstance(target, PreviewTarget):
        target = make_target(target)
    eng = engine if isinstance(engine, EngineInfo) else find_engine(engine)
    if eng is None:
        raise EnginePreviewError('没找到内置的 Ren\'Py 引擎。',
                                 hint=engine_problem_hint())
    if not eng.exe.is_file():
        raise EnginePreviewError(f'引擎不完整：{eng.exe} 不存在。',
                                 hint=engine_problem_hint())

    project_dir = mirror_project(target.project_root)
    mirror_root = project_dir.parent
    _prune_mirrors(cache_root())
    _write_entry(project_dir, target)
    if capture:
        _clear_captures(project_dir)
        _write_capture(project_dir)

    saves = mirror_root / 'userdata' / 'saves'
    saves.mkdir(parents=True, exist_ok=True)
    for stale in ('log.txt', 'errors.txt', 'traceback.txt'):
        try:
            (project_dir / stale).unlink()
        except OSError as e:                    # noqa: BLE001 - 上一次的报错删不掉也不致命
            swallow('app.engine_preview.clear_stale_logs', e)

    cmd = [str(eng.exe), str(project_dir), 'run',
           '--savedir', str(saves)]
    if target.mode == 'beat':
        cmd += ['--warp', target.warp_spec]
    try:
        log_handle = open(mirror_root / 'engine_stdout.log', 'wb')
    except OSError:
        log_handle = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(project_dir), env=_child_env(mirror_root),
            stdin=subprocess.DEVNULL, stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, ValueError) as e:
        try:
            if log_handle is not subprocess.DEVNULL:
                log_handle.close()
        except Exception as close_err:              # noqa: BLE001 - 收尾不打紧
            swallow('app.engine_preview.launch_close_log', close_err)
        raise EnginePreviewError(f'引擎起不来：{e}',
                                 hint=f'引擎：{eng.exe}') from e

    run = EngineRun(process=proc, target=target, engine=eng,
                    project_dir=project_dir, mirror_root=mirror_root,
                    log_path=mirror_root / 'engine_stdout.log',
                    command=tuple(cmd), _stdout_handle=log_handle)
    _RUNS[:] = [r for r in _RUNS if r.running()]
    _RUNS.append(run)
    return run


def stop_all(grace: float = 3.0) -> None:
    """退出前把所有还活着的引擎停掉（窗口关了还留个游戏进程最烦人）。"""
    for run in list(_RUNS):
        try:
            run.stop(grace=grace)
        except Exception as e:                  # noqa: BLE001 - 退出路径不抛
            swallow('app.engine_preview.stop_all', e)
    _RUNS.clear()


# ---------------------------------------------------------------------------
# Windows 窗口探测
# ---------------------------------------------------------------------------

def _pid_has_window(pid: int) -> bool:
    """
    这个进程有没有一个**可见的** Ren'Py 窗口。

    非 Windows 直接返回 False（调用方会退化成"进程活着算启动"）。
    """
    import sys
    if not sys.platform.startswith('win'):
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)

    def callback(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        if buf.value not in _WINDOW_CLASSES:
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) == int(pid):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return bool(found)


__all__ = [
    'RUNTIME_DIRNAME', 'EDITION_MARKER', 'INJECT_NAME', 'KEEP_MIRRORS',
    'EnginePreviewError', 'EngineInfo', 'PreviewTarget', 'EngineRun',
    'find_engine', 'engine_problem_hint', 'preview_supported',
    'stage_edition', 'cache_root', 'mirror_project',
    'make_target', 'launch', 'live_runs', 'stop_all',
]
