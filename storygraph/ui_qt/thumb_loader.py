"""
storygraph.ui_qt.thumb_loader
=============================
Qt 版**异步缩略图加载器**（纯 Qt 实现，内核约束在 `app.thumbs`）：

  · 立绘常见 1500×2200，**绝不许在 UI 线程同步解码** —— 走 `QThreadPool`；
  · 同一张卡片反复点（chip 换表情/位置）→ 同一路径 + 尺寸命中**内存缓存**，
    第二次 0 延迟；
  · 先给**骨架/占位**（界面不空等），到了**只替换那一块**；
  · API：`request(slot, path, apply, max_size=None)`。

线程安全（比"主线程同步解码"省事的地方）：
  · 解码在**工作线程**完成，产物是 `QImage`（**不是 GUI 对象**，跨线程安全）；
  · 转 `QPixmap`（GUI 对象）**只在主线程**做（通过信号把 QImage 送回来再转）。
  · `QPixmap` 缓存**只在主线程**读写 —— 信号把结果排队回主线程，天然不用锁。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from storygraph.app.thumbs import cache_key, decode_sync, resolve_thumb_source

CACHE_LIMIT = 64

#: 同时在解的张数上限。**为什么要自己限流**：`QThreadPool` 是先进先出的 ——
#: 一屏一屏往下滚，几十上百个"用户早就滚过去、现在根本看不见"的任务排在
#: 前面，可见的那几张得等它们全解完才轮到（用户 2026-10-05：
#: "这个选择器内图片的加载还是不够迅速，优先加载目前在窗口内的图片！"）。
#: 自己限流之后，池子里**始终只有我们要的那几张**，剩下的按优先级排队。
def default_max_in_flight() -> int:
    try:
        from PySide6.QtCore import QThread
        n = int(QThread.idealThreadCount() or 4)
    except Exception as e:                         # noqa: BLE001
        swallow('ui_qt.thumb_loader.default_max_in_flight', e)
        n = 4
    return max(2, min(16, n))

#: 优先级：**窗口内可见的** > 普通请求（卡片上的立绘） > 预取 / 已经滚过去的
PRIORITY_FOCUS = 100
PRIORITY_NORMAL = 10
PRIORITY_BACKGROUND = 0


def _pil_to_qimage(im) -> QImage:
    """PIL 图 → QImage（RGBA8888）。必须在工作线程里调用。"""
    if im.mode != 'RGBA':
        im = im.convert('RGBA')
    data = im.tobytes('raw', 'RGBA')
    # `.copy()` 很关键：QImage 若直接引用 Python 的 bytes 缓冲，
    # 缓冲一被回收就是野指针（这是 Qt+Python 的经典坑）。
    return QImage(data, im.width, im.height,
                  QImage.Format.Format_RGBA8888).copy()


def _qt_decode(path, max_size, project_root) -> QImage:
    """
    退路：没装 Pillow 时用 Qt 自己解码。

    `setScaledSize` 让**解码器直接按目标尺寸出图**（JPEG 是解码级缩放，
    比"整图读进来再缩"省一个数量级的时间）；PNG 至少也省掉一次大图缩放。
    """
    real = resolve_thumb_source(path, project_root)
    if not real:
        return QImage()
    reader = QImageReader(str(real))
    reader.setAutoTransform(True)
    w, h = max_size
    src = reader.size()
    if w and h and src.isValid():
        target = src.scaled(int(w), int(h), Qt.KeepAspectRatio)
        if target.width() < src.width() or target.height() < src.height():
            reader.setScaledSize(target)
    return reader.read()


def _decode(path, max_size, project_root) -> QImage:
    """
    一张图的解码（**工作线程里跑**）。

    先走两个前端共用的内核 `app.thumbs.decode_sync`：
      · PIL `draft()` → **解码器级降采样**（冷缓存下是数量级差别）；
      · 结果写**磁盘缓存**（同一张图第二次打开直接读小图）；
      · 立绘 1500×2200、CG 更大，主线程同步解码必然卡界面。
    没装 Pillow 时才退回 `QImageReader`（所以这里不是"两套解码逻辑"，
    而是一条主路 + 一条明确的降级路）。
    """
    pil = decode_sync(path, max_size, project_root)
    if pil is not None:
        return _pil_to_qimage(pil)
    return _qt_decode(path, max_size, project_root)


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
            img = _decode(self.path, self.max_size, self.loader.project_root)
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
    （控件销毁 → 结果自然丢弃）。

    调度模型（2026-10-05 改成**优先级队列**）
    -----------------------------------------
    以前是"每个请求直接丢进 `QThreadPool`" + 一个 per-slot 防抖计时器。
    问题是 `QThreadPool` 先进先出：滚动时排进去的几十上百个任务会挡在
    "现在看得见的那几张"前面。现在改成：

      · `_want`：**想要什么**（slot → 路径/尺寸/回调/优先级），最新一次为准；
      · `_running`：正在解的那几张（**同时最多 `max_in_flight` 张**）；
      · `_pump()`：谁空出来就按 (优先级, 先来后到) 补一张进去。

    于是池子里永远只有我们当下最想要的那几张 —— 可见项先出图，滚过去的
    那些排在后面（也终究会解完，不会"缺口大"）。防抖计时器因此删掉了：
    同一个 slot 连着改（chip 连点）时旧任务还在跑，新的只更新 `_want`，
    跑完自然接着解最新的那一张。
    """

    #: (slot, key, QImage, path) —— 在主线程（loader 所属线程）触发
    image_ready = Signal(object, object, object, object)

    def __init__(self, widget, max_size=(160, 220), project_root=None,
                 max_in_flight=None):
        super().__init__(widget)
        self._widget = widget
        self.project_root = project_root
        self._default_size = tuple(max_size)
        self._max_in_flight = (int(max_in_flight) if max_in_flight
                               else default_max_in_flight())
        self._cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._want: dict = {}                # slot -> 想要什么（见类 docstring）
        self._running: dict = {}             # slot -> 正在解的那个 key
        self._seq = 0                        # 同优先级内"先来后到"
        self._shutting_down = False          # 关窗后工作线程要能看到（见 _Job.run）
        self.image_ready.connect(self._on_ready)

    # ------------------------------------------------------------------

    def request(self, slot, path, apply, max_size=None, placeholder=None,
                priority=None):
        """
        请求一张缩略图（可反复调用，**最新一次的 `apply` 为准**）。

        slot:   调用方给的稳定标识 —— 用来取代同一条的旧请求
        path:   图片路径或图像名（解析交给 `resolve_thumb_source`）
        apply:  回调，`apply(pixmap_or_None, path)`，**在主线程**执行；
                给 `None` 表示"只要缓存，不要回调"（见 `prefetch`）
        max_size: 本张图的目标尺寸（不给就用实例默认值）
        placeholder: 可选"先画个骨架"的回调（无参）。给了它就**不再**用
                `apply(None)` 占位 —— 否则调用方分不清"还在解"和"解不出来"
                （立绘卡以前会把"这张图读不出来"直接写上去，图还没到就说坏了）。
        priority: 不给 = `PRIORITY_NORMAL`。选择器会把**窗口内可见的**那几张
                用 `focus()` 提到 `PRIORITY_FOCUS`。
        """
        path = str(path or '').strip()
        if self._shutting_down:              # 已关窗：不再排队，直接放弃
            return
        size = tuple(max_size) if max_size else self._default_size
        if not path:
            self._safe_apply(apply, None, path)
            return
        # 内存缓存的键用**解析后的真实文件**：`path` 可能是 `cg cg03` 这种
        # image spec，对它 `stat()` 会失败 → 键里没有 mtime → 换了素材还命中旧图。
        real = resolve_thumb_source(path, self.project_root) or path
        key = cache_key(real, size)
        photo = self._cache.get(key)
        if photo is not None:                 # 内存命中：0 延迟
            try:
                self._cache.moveToEnd(key)
            except Exception:                # noqa: BLE001
                swallow('thumb_loader.request')
            self._want.pop(slot, None)
            self._safe_apply(apply, photo, path)
            return

        old = self._want.get(slot)
        self._seq += 1
        if priority is None:
            # 已经在排队的同一条：**保留它原来的优先级** —— 后一次的普通请求
            # 不该把"窗口内可见"降级（升降级只由 `focus()` 决定）。
            priority = old['priority'] if old is not None else PRIORITY_NORMAL
        self._want[slot] = {'path': path, 'key': key, 'size': size,
                            'apply': apply, 'placeholder': placeholder,
                            'priority': int(priority), 'seq': self._seq}
        self._pump()

    def prefetch(self, path, max_size=None):
        """
        后台**只解进缓存**、不回调（翻到下一拍时图已经在内存里了）。

        优先级 `PRIORITY_BACKGROUND`：**预取永远不许挡住别人** ——
        卡片上真正要显示的那张先解，空下来的线程才轮到预取。
        """
        path = str(path or '').strip()
        if not path or self._shutting_down:
            return
        size = tuple(max_size) if max_size else self._default_size
        real = resolve_thumb_source(path, self.project_root) or path
        key = cache_key(real, size)
        if key in self._cache:
            return
        if any(w['key'] == key for w in self._want.values()):
            return
        self.request(('prefetch', key), path, None, max_size=size,
                     priority=PRIORITY_BACKGROUND)

    def focus(self, slots):
        """
        把 `slots`（**当前窗口内可见**的那些）提到最前，其余降回普通。

        用户 2026-10-05：

        > 这个选择器内图片的加载还是不够迅速，优先加载目前在窗口内的图片！

        选择器每次算出可见范围就调它一次。**降级同样重要**：滚过去的那些
        （仍然是"想要"，迟早会解完）不该再挡着新滚出来的。
        """
        keep = set(slots or ())
        for slot, want in self._want.items():
            if slot in keep:
                want['priority'] = max(want['priority'], PRIORITY_FOCUS)
            elif want['priority'] == PRIORITY_FOCUS:
                want['priority'] = PRIORITY_NORMAL
        self._pump()

    def clear_pending(self, keep=None):
        """
        丢掉"还在排队、但还没开始"的想头（候选列表整个换掉时调）。

        `keep(slot) -> bool`：给判据时**只丢判据说是旧的那些**。演出预览要的
        就是这个 —— 换一帧时把别的帧的想头丢掉，但**预取**（下一拍那几张）
        得留着，否则每翻一页都把刚预热的丢掉，预取等于没做。
        """
        for slot in [s for s in self._want
                     if s not in self._running
                     and (keep is None or not keep(s))]:
            self._want.pop(slot, None)

    def queued(self) -> list:
        """排队中的 slot，**按实际会执行的顺序**（给测试与自检看顺序用）。"""
        items = [(w['priority'], w['seq'], s)
                 for s, w in self._want.items() if s not in self._running]
        # 优先级高的先做；同一优先级里**先要的先做**（正常排队语义）
        items.sort(key=lambda t: (-t[0], t[1]))
        return [s for _p, _q, s in items]

    def status(self, slot) -> str:
        """这条现在什么状态：`cached` / `running` / `queued` / `unknown`（自检用）。"""
        want = self._want.get(slot)
        if want is not None and want['key'] in self._cache:
            return 'cached'
        if slot in self._running:
            return 'running'
        if want is not None:
            return 'queued'
        return 'unknown'

    # ------------------------------------------------------------------

    def _pump(self):
        """
        把"最想要的"补进线程池，直到在解的张数到上限。

        池子里**只有这里发出去的任务** —— 所以优先级是真的生效的：
        可见项永远排在"滚过去的那些"前面。
        """
        if self._shutting_down:
            return
        pool = QThreadPool.globalInstance()
        while len(self._running) < self._max_in_flight:
            picked = self._next_wanted()
            if picked is None:
                return
            slot, want = picked
            # 排队期间它可能已经被**别的 slot 解好**了（同一张图在两个位置上，
            # 或者预取先跑完）—— 那就直接回填，别再解一遍。
            # 不查这一步的话，预取完的那张会在真请求进来时**重解一次**
            # （预览逐帧播放时最明显：每张图都解两遍，白等一个解码时间）。
            photo = self._cache.get(want['key'])
            if photo is not None:
                self._want.pop(slot, None)
                self._safe_apply(want['apply'], photo, want['path'])
                continue
            # 先骨架/占位，界面不空等。调用方给了 `placeholder` 就用它 ——
            # `apply(None)` 对"立绘卡"那种调用方含义是"图读不出来"，
            # 拿它占位就是在图还没到的时候谎报损坏。
            if want['placeholder'] is not None:
                self._safe_call(want['placeholder'])
            elif want['apply'] is not None:
                self._safe_apply(want['apply'], None, want['path'])
            self._running[slot] = want['key']
            pool.start(_Job(self, slot, want['key'], want['path'],
                            want['size']))

    def _next_wanted(self):
        """排队里最该解的那一条：先比优先级，同一优先级里**先要的先做**。"""
        best = None
        for slot, want in self._want.items():
            if slot in self._running:
                continue
            rank = (want['priority'], -want['seq'])
            if best is None or rank > best[0]:
                best = (rank, slot, want)
        if best is None:
            return None
        return best[1], best[2]

    def _on_ready(self, slot, key, img, path):
        """
        解码结果回到主线程。

        **必须核对 key**：`slot` 只标识"哪张卡"，同一张卡换表情/位置时会连续
        发多个请求。旧实现不看 key：旧请求的结果回来时，`_pending.pop(slot)`
        取到的是**新请求的回调** → 把旧图交给了新回调（用户看到旧图，甚至
        什么也看不到）；还把旧图缓存到**新键**上 → 之后对新图的所有请求都
        命中这张旧图。实际表现就是"CG / 换表情后的图怎么也出不来"。

        判据是"**结果 == 此刻想要的那张**"（`want['key'] == key`），不是
        "`_running[slot]` 对不对" —— 后者在"正在解的过程中用户又改了"时
        仍然是旧 key，会把旧图回填给新回调（这个坑 2026-10-05 实测踩到）。
        结果本身按**它自己的 key** 入缓存（那是它真实的身份），只是不回填。
        """
        self._running.pop(slot, None)
        want = self._want.get(slot)

        pixmap = None
        try:
            if isinstance(img, QImage) and not img.isNull():
                pixmap = QPixmap.fromImage(img)
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.thumb_loader._on_ready', e)
            pixmap = None
        if pixmap is not None:
            self._cache[key] = pixmap
            try:
                self._cache.moveToEnd(key)
            except Exception:                # noqa: BLE001
                swallow('thumb_loader._on_ready')
            while len(self._cache) > CACHE_LIMIT:
                self._cache.popitem(last=False)

        if want is None or want['key'] != key:
            # 过期结果（同一 slot 已经有更新的想头）：只入缓存，不回填
            self._pump()
            return
        self._want.pop(slot, None)
        self._safe_apply(want['apply'], pixmap, path)
        self._pump()

    def _safe_apply(self, apply, pixmap, path):
        """回调 `apply(pixmap_or_None, path)`；`apply is None` = 只要缓存。"""
        if apply is None:
            return
        try:
            apply(pixmap, path)
        except Exception:                    # noqa: BLE001 - 控件已销毁
            swallow('thumb_loader._safe_apply')

    def _safe_call(self, fn):
        try:
            fn()
        except Exception:                    # noqa: BLE001 - 控件已销毁
            swallow('thumb_loader._safe_call')

    def shutdown(self):
        """
        停止接收任何结果。**必须幂等**（close 事件可能来两次）。

        注意：这里**不阻塞等线程池**——解码中的图会自然跑完，只是结果
        被丢掉（`_shutting_down` + `_Job.run` 里的 try/except 双重兜底）。
        阻塞等待会让"关窗"卡住，体验比丢几张缩略图差得多。
        """
        self._shutting_down = True
        self._want.clear()
        self._running.clear()
