"""
storygraph.ui_qt.thumb_loader
=============================
Qt 版**异步缩略图加载器**（与 tkinter 版同一个设计约束，只是换成 Qt）：

  · 立绘常见 1500×2200，**绝不许在 UI 线程同步解码** —— 走 `QThreadPool`；
  · 同一张卡片反复点（chip 换表情/位置）→ 同一路径 + 尺寸命中**内存缓存**，
    第二次 0 延迟；
  · 先给**骨架/占位**（界面不空等），到了**只替换那一块**；
  · API 与 tkinter 版对齐：`request(slot, path, apply, max_size=None)` ——
    换 Qt 时调用处一行不用改。

线程安全（比 tkinter 版省事的地方）：
  · 解码在**工作线程**完成，产物是 `QImage`（**不是 GUI 对象**，跨线程安全）；
  · 转 `QPixmap`（GUI 对象）**只在主线程**做（通过信号把 QImage 送回来再转）。
  · `QPixmap` 缓存**只在主线程**读写 —— 信号把结果排队回主线程，天然不用锁。
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from storygraph.ui.thumb_loader import cache_key, resolve_thumb_source

DEBOUNCE_MS = 120
CACHE_LIMIT = 64


class _Job(QRunnable):
    """一张图的解码任务（跑在工作线程里，产物是 QImage）。"""

    def __init__(self, loader, slot, key, path, max_size):
        super().__init__()
        self.loader = loader
        self.slot = slot
        self.key = key
        self.path = path
        self.max_size = max_size

    def run(self):
        img = QImage()
        try:
            real = resolve_thumb_source(self.path, self.loader.project_root)
            if real:
                reader = QImageReader(str(real))
                reader.setAutoTransform(True)
                img = reader.read()
                if not img.isNull():
                    w, h = self.max_size
                    if w and h:
                        img = img.scaled(w, h, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation)
        except Exception:                    # noqa: BLE001 - 缺图就是空 QImage
            img = QImage()
        # 交还主线程（loader 是 QObject，信号会排队到它所属线程）。
        #
        # **必须防"信号宿主已销毁"**：关窗/关对话框时，工作线程可能还在跑；
        # 此时 loader 的 C++ 对象已经没了，`emit` 会抛
        # `RuntimeError: Signal source has been deleted`（PySide6 会把它
        # 打到 stderr，刷屏且看起来像崩溃）。所以：
        #   1. 先看 loader 的 shutdown 标志（Python 侧，最快）；
        #   2. 再兜住 emit 本身的异常（C++ 对象被删的竞态）。
        if getattr(self.loader, '_shutting_down', True):
            return
        try:
            self.loader.image_ready.emit(self.slot, self.key, img, self.path)
        except RuntimeError:
            return                           # 宿主已销毁：静默丢弃结果


class ThumbLoader(QObject):
    """
    一个窗口/面板一个实例。**挂在哪个控件树上，生命周期就跟谁走**
    （控件销毁 → QTimer 不再触发，结果自然丢弃）。
    """

    #: (slot, key, QImage, path) —— 在主线程（loader 所属线程）触发
    image_ready = Signal(object, object, object, object)

    def __init__(self, widget, max_size=(160, 220), project_root=None,
                 debounce_ms: int = DEBOUNCE_MS):
        super().__init__(widget)
        self._widget = widget
        self.project_root = project_root
        self._default_size = tuple(max_size)
        self._debounce = max(0, int(debounce_ms))
        self._cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._pending: dict = {}             # slot -> (path, apply, size)
        self._jobs: dict = {}                # slot -> 已发出任务的 key
        self._debounce_timer: dict = {}      # slot -> QTimer
        self._shutting_down = False          # 关窗后工作线程要能看到（见 _Job.run）
        self.image_ready.connect(self._on_ready)

    # ------------------------------------------------------------------

    def request(self, slot, path, apply, max_size=None):
        """
        请求一张缩略图（可反复调用，旧的会被取消/取代）。

        slot:   调用方给的稳定标识 —— 用来取消过期任务
        path:   图片路径或图像名（解析交给 `resolve_thumb_source`）
        apply:  回调，`apply(pixmap_or_None, path)`，**在主线程**执行
        max_size: 本张图的目标尺寸（不给就用实例默认值）
        """
        path = str(path or '').strip()
        if self._shutting_down:              # 已关窗：不再排队，直接放弃
            return
        size = tuple(max_size) if max_size else self._default_size
        if not path:
            self._safe_apply(apply, None, path)
            return
        key = cache_key(path, size)
        photo = self._cache.get(key)
        if photo is not None:                 # 内存命中：0 延迟
            try:
                self._cache.moveToEnd(key)
            except Exception:                # noqa: BLE001
                pass
            self._safe_apply(apply, photo, path)
            return

        # 防抖：同一张卡频繁改（chip 连点）时，只在停手后真正开工。
        # `_pending` 里**只存最新一次**的回调 —— 结果以它为准。
        self._pending[slot] = (path, apply, size)
        timer = self._debounce_timer.get(slot)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=slot: self._start(s))
            self._debounce_timer[slot] = timer
        timer.start(self._debounce)

    # ------------------------------------------------------------------

    def _start(self, slot):
        got = self._pending.get(slot)
        if got is None:
            return
        path, apply, size = got
        key = cache_key(path, size)
        self._safe_apply(apply, None, path)  # 先骨架/占位，界面不空等
        self._jobs[slot] = key
        QThreadPool.globalInstance().start(_Job(self, slot, key, path, size))

    def _on_ready(self, slot, key, img, path):
        # 这是**最后一次请求**的回调（防抖期里 `_pending` 存的是最新一次）
        pending = self._pending.pop(slot, None)
        job_key = self._jobs.pop(slot, None)
        apply = pending[1] if pending is not None else None

        pixmap = None
        try:
            if isinstance(img, QImage) and not img.isNull():
                pixmap = QPixmap.fromImage(img)
        except Exception:                    # noqa: BLE001
            pixmap = None
        if pixmap is not None and job_key is not None:
            self._cache[job_key] = pixmap
            try:
                self._cache.moveToEnd(job_key)
            except Exception:                # noqa: BLE001
                pass
            while len(self._cache) > CACHE_LIMIT:
                self._cache.popitem(last=False)

        if apply is not None:
            self._safe_apply(apply, pixmap, path)

    def _safe_apply(self, apply, pixmap, path):
        try:
            apply(pixmap, path)
        except Exception:                    # noqa: BLE001 - 控件已销毁
            pass

    def shutdown(self):
        """
        停止接收任何结果。**必须幂等**（close 事件可能来两次）。

        注意：这里**不阻塞等线程池**——解码中的图会自然跑完，只是结果
        被丢掉（`_shutting_down` + `_Job.run` 里的 try/except 双重兜底）。
        阻塞等待会让"关窗"卡住，体验比丢几张缩略图差得多。
        """
        self._shutting_down = True
        for timer in list(self._debounce_timer.values()):
            try:
                timer.stop()
            except Exception:                # noqa: BLE001
                pass
        self._debounce_timer.clear()
        self._pending.clear()
        self._jobs.clear()
