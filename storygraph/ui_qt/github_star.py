"""
storygraph.ui_qt.github_star
============================
卡片编辑页右上角的**一个 GitHub Star 小徽标**（2026-10-27）。

用户原话：

> 再在（卡片编辑器）的稍稍右上方空白处去做一个 github 的快速 star 呢？
> （点击这个如果能连得上网的话最好就能给我个标个星，如果不能联网也没关系）
> 注意：我希望它是一个独立于整体界面的代码块，到时候方便取下来，
> 不要耽误别人到时候对这个界面的改造

所以这个文件是**一个可整块删掉的独立件**：

  · 只依赖 Qt + 标准库（外加一个颜色常量模块 `tokens`，纯数据）；
  · **不 import** 任何窗口 / 会话 / 卡片 / 主界面代码（也没人反向依赖它）；
  · 主窗口那边只有 `attach_star_badge(self, rv)` **一行**调用（带 try/except）；
    删掉那一行 + 删掉本文件 = 界面完全恢复原样。

## 点一下会发生什么

1. **有 token**（环境变量 `STORYGRAPH_GITHUB_TOKEN` / `GITHUB_TOKEN`，或
   `%LOCALAPPDATA%/StoryGraph/github.json` 里的 `{"token": "..."}`）：
   直接 `PUT https://api.github.com/user/starred/<owner>/<repo>` ——
   一次点击就把星标上（已经标过也返回 204，幂等，不会重复报错）。
2. **没有 token**：先探一下 github.com 通不通，通就打开仓库网页
   （star 按钮就在那儿，人自己点）。
3. **连不上**（超时 / DNS 被挡 / 代理没开）：不弹框、不崩、不在浏览器里
   开一个死页面，按钮上写一句"连不上 GitHub（不影响使用）"，几秒后恢复。

网络请求**全部在后台线程**（`QThreadPool`，3 秒超时），GUI 线程不碰 socket；
**启动时不会自动联网**（不探测、不取 star 数）—— 只有人点它才发请求。

## 为什么要先探再开浏览器

没有 token 时"打开网页"是本机动作，网不通的话用户只会看到浏览器自己的
错误页，还以为是工具坏了。花 2~3 秒探一次，换来一句诚实的"连不上"。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QPushButton

from storygraph.core.safeguard import swallow

try:                                      # 颜色常量（纯数据，不是界面代码）
    from storygraph.ui_qt import tokens as _T
except Exception as e:                    # noqa: BLE001 - 拿不到就用内置兜底色
    swallow('github_star.tokens', e)
    _T = None

#: 这个项目自己的仓库（改这里就够，别处没有第二份）
DEFAULT_REPO = 'FengWenMo/RenPy-Story-Graph-Visual-Editor'

#: 网络超时（秒）。点一下最多等这么久，且**在后台线程**里等。
TIMEOUT = 3.0
#: 探活可以比 star 短一点（它只是决定"要不要开浏览器"）
PROBE_TIMEOUT = 2.5

#: 结果那句话显示多久（毫秒）后恢复成常态文案
FLASH_MS = 6000

_API = 'https://api.github.com'


# ----------------------------------------------------------------------
#  token / 网络（纯函数，测试直接打桩这几个）
# ----------------------------------------------------------------------

def read_token() -> str:
    """
    从环境变量或配置文件取 GitHub token（没有就空串 = 只开网页）。

    为什么还要看文件：普通用户不会设环境变量，但把一行 token 放进
    `%LOCALAPPDATA%/StoryGraph/github.json` 是能做到的（与 AI key 同一个
    存放约定，不进项目、不进 git）。
    """
    for name in ('STORYGRAPH_GITHUB_TOKEN', 'GITHUB_TOKEN', 'GH_TOKEN'):
        value = str(os.environ.get(name) or '').strip()
        if value:
            return value
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or ''
    if not base:
        return ''
    path = Path(base) / 'StoryGraph' / 'github.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    if isinstance(data, dict):
        return str(data.get('token') or '').strip()
    return ''

def star_repo(repo: str, token: str, timeout: float = TIMEOUT):
    """
    给仓库标星 → `(ok, message)`。

    `PUT /user/starred/{owner}/{repo}` 是**幂等**的：已经标过也回 204，
    所以"我早就 star 过了"不会被当成失败。
    """
    if not token:
        return (False, '没有 token')
    url = f'{_API}/user/starred/{repo}'
    req = urllib.request.Request(url, method='PUT')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'StoryGraph')
    req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, 'status', 0) or 0)
            resp.read()
    except urllib.error.HTTPError as e:
        # 401 = token 无效 / 过期；403 = 被限流或权限不足；404 = 仓库名不对
        if e.code == 401:
            return (False, 'token 无效或已过期')
        if e.code == 403:
            return (False, '被 GitHub 限流了')
        if e.code == 404:
            return (False, '仓库名对不上')
        return (False, f'GitHub 返回 {e.code}')
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return (False, f'连不上（{_short_error(e)}）')
    return (True, f'已 star（{code}）')


def probe_repo(repo: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """github.com 上这个仓库页打得开吗（只决定"要不要开浏览器"）。"""
    url = f'https://github.com/{repo}'
    req = urllib.request.Request(url, method='GET')
    req.add_header('User-Agent', 'StoryGraph')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1)                 # 只读一个字节：够证明连上了
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def open_repo_page(repo: str) -> bool:
    """在系统浏览器里打开仓库页（本机动作，不联网）。"""
    try:
        return bool(QDesktopServices.openUrl(QUrl(f'https://github.com/{repo}')))
    except Exception as e:                # noqa: BLE001 - 打不开也不许崩
        swallow('github_star.open_page', e)
        return False


def _short_error(exc) -> str:
    """异常 → 一句人话（别把 traceback 塞进按钮）。"""
    reason = getattr(exc, 'reason', None) or exc
    text = str(reason) or exc.__class__.__name__
    return text[:40]


# ----------------------------------------------------------------------
#  后台任务
# ----------------------------------------------------------------------

class _Signals(QObject):
    #: `(ok, message)`
    done = Signal(bool, str)


class _Job(QRunnable):
    """在后台线程里做**一件**网络事（结果用信号回 GUI 线程）。"""

    def __init__(self, signals, fn):
        super().__init__()
        self.signals = signals
        self.fn = fn
        self.setAutoDelete(True)

    def run(self):
        try:
            ok, message = self.fn()
        except Exception as e:            # noqa: BLE001 - 后台绝不炸
            ok, message = False, f'出错了（{e.__class__.__name__}）'
        try:
            self.signals.done.emit(bool(ok), str(message))
        except RuntimeError as e:
            swallow('github_star.job.emit', e)   # 控件已经销毁：正常收尾


# ----------------------------------------------------------------------
#  徽标
# ----------------------------------------------------------------------

_STYLE = """
QPushButton#githubStar {
    color: %(fg)s;
    background: transparent;
    border: 1px solid %(border)s;
    border-radius: 11px;
    padding: 3px 10px;
}
QPushButton#githubStar:hover {
    color: %(hover)s;
    border-color: %(hover)s;
}
QPushButton#githubStar:pressed {
    background: %(pressed)s;
}
QPushButton#githubStar:disabled {
    color: %(border)s;
}
"""

_IDLE_TEXT = '★ Star on GitHub'

_IDLE_TIP = (
    '给这个项目标个星（Star）\n'
    '· 配了 token：点一下直接 star（环境变量 GITHUB_TOKEN，或\n'
    '  %LOCALAPPDATA%\\StoryGraph\\github.json 里写 {"token": "..."}）\n'
    '· 没配 token：打开仓库页，点一下 Star 按钮\n'
    '· 没网也不影响任何编辑功能')


def _color(name: str, fallback: str) -> str:
    """从 `tokens` 取色；取不到就用兜底色（这个组件要能脱离主题单独站住）。"""
    try:
        return str(_T.t(name) if _T is not None else fallback)
    except Exception as e:                # noqa: BLE001
        swallow('github_star.color', e)
        return fallback


class GitHubStarBadge(QPushButton):
    """
    一个能自己站住的小按钮：点一下 → 标星（有 token）或打开仓库页。

    它**不认识**主窗口，也不改别人的布局；把自己塞进哪个布局由调用方决定。
    """

    def __init__(self, parent=None, repo: str = DEFAULT_REPO,
                 token: str = None, timeout: float = TIMEOUT):
        super().__init__(_IDLE_TEXT, parent)
        self.setObjectName('githubStar')
        self.repo = str(repo or DEFAULT_REPO)
        self._token = token                 # None = 自己去读（测试可注入）
        self._timeout = float(timeout)
        self.setToolTip(_IDLE_TIP)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.apply_style()
        self._busy = False
        #: 'star' = 有 token，走 API；'page' = 没 token，探活后开网页
        self._mode = 'page'
        self.repo_url = f'https://github.com/{self.repo}'
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(FLASH_MS)
        self._reset_timer.timeout.connect(self.reset_text)
        self.clicked.connect(self.start_star)
        # 退出时把自己摘干净（**不麻烦主窗口**：那边一行都不用为此改）
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.shutdown)
        except Exception as e:            # noqa: BLE001
            swallow('github_star.about_to_quit', e)

    # ------------------------------------------------------------------

    def token(self) -> str:
        """这次点击用哪个 token（注入优先，其次环境变量 / 配置文件）。"""
        return str(self._token if self._token is not None else read_token())

    def apply_style(self):
        """
        按当前主题重刷配色。

        颜色是**一次性写进 stylesheet** 的（不是每次重绘都读 token），所以将来
        真接上运行时换主题时，调一下这个方法就够（`tokens.set_theme()` 目前
        还没有调用点 —— 这行注释是给接它的人看的）。
        """
        self.setStyleSheet(_STYLE % {
            'fg': _color('text.secondary', '#5F6368'),
            'border': _color('border.default', '#DADCE0'),
            'hover': _color('brand.primary', '#1A73E8'),
            'pressed': _color('bg.cardHover', '#F1F3F4'),
        })

    def start_star(self):
        """
        点一下（也供测试直接调用）：**立刻返回**，网络在后台跑。"""
        if self._busy:
            return
        self._busy = True
        self._reset_timer.stop()
        token = self.token()
        self.setText('正在连接…' if token else '正在检查网络…')
        self.setEnabled(False)
        self.repo_url = f'https://github.com/{self.repo}'
        if token:
            fn = lambda: star_repo(self.repo, token, self._timeout)   # noqa: E731
            self._mode = 'star'
        else:
            fn = lambda: (probe_repo(self.repo, min(self._timeout,
                                                    PROBE_TIMEOUT)), '')
            self._mode = 'page'
        try:
            self._pool.start(_Job(self._signals, fn))
        except Exception as e:            # noqa: BLE001 - 线程都给不了也别炸界面
            swallow('github_star.start', e)
            self._busy = False
            self.setEnabled(True)
            self.setText('★ 打开仓库页')
            open_repo_page(self.repo)

    def _on_done(self, ok: bool, message: str):
        """后台回来了：决定"说哪句话 + 要不要开浏览器"。**在 GUI 线程**。"""
        self._busy = False
        self.setEnabled(True)
        try:
            if self._mode == 'star':
                if ok:
                    self._flash('⭐ 已 star，谢谢！')
                    return
                # 自动 star 失败（token 过期 / 被限流 / 没网）→ 退回开网页
                self._open_page(f'没能自动 star（{message}），已打开仓库页')
            else:
                if ok:
                    self._open_page('已打开仓库页 —— 点一下 Star 就行')
                else:
                    self._flash('连不上 GitHub（不影响使用）')
        except Exception as e:            # noqa: BLE001 - 收尾里绝不外抛
            swallow('github_star.done', e)
            self.reset_text()

    def _open_page(self, message: str):
        opened = open_repo_page(self.repo)
        self._flash(message if opened else '打不开浏览器 —— 地址是 ' + self.repo_url)

    def _flash(self, text: str):
        """把结果说一句，几秒后自己回到常态。"""
        self.setText(str(text))
        self.setToolTip(f'{text}\n（{self.repo_url}）')
        self._reset_timer.start()

    def reset_text(self):
        """恢复成常态文案（"几秒后自己恢复"与测试都用它）。"""
        self.setText(_IDLE_TEXT)
        self.setToolTip(_IDLE_TIP)

    def shutdown(self):
        """窗口关闭：别再有后台任务拖着进程。"""
        try:
            self._reset_timer.stop()
            self._pool.clear()
        except Exception as e:            # noqa: BLE001
            swallow('github_star.shutdown', e)


def attach_star_badge(window, layout, repo: str = DEFAULT_REPO):
    """
    把徽标挂上去（**主窗口唯一需要调用的一行**）。

    `window`：主窗口（挂在它身上，窗口关闭时能统一收掉）；
    `layout`：塞进哪个布局（卡片编辑页右栏的最上面，右对齐）。
    返回徽标本身（测试用）。

    这个函数只做三件事：建控件、加进布局、记在窗口上 —— 不碰任何别的东西。
    """
    badge = GitHubStarBadge(parent=window, repo=repo)
    layout.insertWidget(0, badge, 0, Qt.AlignmentFlag.AlignRight)
    if window is not None:
        try:
            window._github_star = badge
        except Exception as e:            # noqa: BLE001 - 挂不上也只是没有引用
            swallow('github_star.attach', e)
    return badge


__all__ = ['DEFAULT_REPO', 'GitHubStarBadge', 'attach_star_badge',
           'open_repo_page', 'probe_repo', 'read_token', 'star_repo']
