"""
storygraph.ui_qt.stage_thumb
============================
**这一拍的舞台图**（2026-10-27；用户："每一拍的舞台信息那里……可以除文字
描述之外，给一张这一拍的图片吗？就放在文字描述之前的上面（与舞台预览一致
的图片，但只有一拍，每当这一排有修改时，重新刷新这一拍的图）"）。

## 它在卡片上的位置（同一天第二版，改过一次）

第一版把它做成"按卡片宽度的整宽图"（480~1280px 宽、高 270~420），挂在
台词框下面 —— 用户看到的第一反应是：

> 不对！！！你这个不能放卡片界面啊！！！应该缩小放舞台界面！当前舞台下面
> 那一栏……缩小！再缩小，直到能看到全貌！不然你现在显示都显示不全何来
> 调整啊？？？

所以现在是 **`compact=True` 的固定小图（288×162，就是 16:9）**，塞在
卡片「当前舞台（这一拍生效）」标题下面，**与右边的文字摘要并排**：摘要本来
就要占那几行字，图因此几乎不额外吃高度，而整帧画面一眼看得全（不再需要
上下滚半个屏幕才拼得出一张图）。

`compact=False` 的整宽模式**保留**（`sizeHint` 480×270、高度跟宽度走），
别处要用"一张大图"时仍可拿它；两种模式共用同一条渲染链与缓存。

**像素与舞台预览完全一致**，因为它走的是同一条渲染链：

    Beat 序列 --build_frame_at--> StageFrame --app.stage_render.render_frame--> PIL

与「舞台快照」表、生成器、预览窗口同源（`app.stage` / `app.stage_render`）。
本文件只做三件事：什么时候渲染、渲染成多大、画在控件上。

**唯一的例外：compact 小图会重画名字/台词**（2026-10-29，用户报「渲染有问题」）。
整帧 1920×1080 的 26~30px 台词缩到 288 宽只剩 3~4px，糊成方块 —— 所以
compact 模式先在 PIL 侧缩到 target，再交给 `stage_render.draw_readable_say`
按缩略图自己的可读字号把名字/台词重画一遍（背景/立绘仍是引擎排版的缩放）。
整宽模式与舞台预览窗口**不受影响**：那里的字本来就看得清。

## 为什么不在 GUI 线程算

真机上合成一帧 100ms~1.3s（首次碰到大盘立绘要解码缩图，见
`docs/开发日志.md` §34 的实测）。卡片翻一拍就卡一秒是不能接受的，
所以：**帧在小控件里算（`build_frame_at` 2ms），渲染进后台线程**，
算完用 Qt 信号回到 GUI 线程变成 `QPixmap`。

## 三个"不骗人"的约定

  · 换拍时**立刻清掉上一张**（不拿上一拍的图顶着）：卡片上写着第 N 拍，
    却画着第 N-1 拍的舞台，比空着更糟 —— 预览窗口那边可以"顶着上一张 +
    角标"，因为它是连续播放；这里是一次性快照，只能要么是这一拍、要么明说
    "正在渲染"。
  · 内容没变就**不重画**：缓存键是帧的**内容指纹**（`frame_signature`），
    所以"同一拍连点几下保存"不会反复走一遍 1 秒的渲染；而"舞台真的变了"
    （换背景 / 加立绘 / 改台词）指纹就会变，自动重画。
  · 渲染不出来（没装 Pillow / 渲染器抛异常 / 缺素材）**要说出来**：
    控件上写原因，并把缺的素材名放进 tooltip。

## 无头 / 测试

正式路径是"防抖 + 后台线程 + 信号"，要等事件循环转起来才看得到图。
判据请用 `render_now()`：它在当前线程同步渲染完，当场能 `pixmap()` / `grab()`。
见 `tests/test_stage_thumb.py`。
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from storygraph.app import stage_render as R
from storygraph.app.stage import build_frame_at
from storygraph.core.safeguard import swallow
from storygraph.ui_qt import theme
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt.stage_preview import image_to_qimage, result_to_qimage

#: 翻拍时的防抖：连按方向键翻过去的那几拍不必每张都渲染一遍
#: （一张 100ms~1.3s，而人眼在翻页途中根本看不清中间那几张）。
DEBOUNCE_MS = 120

#: 渲染线程数。**两个**：新的一拍能在另一个线程立刻开跑，不必等上一张
#: （已经作废的）算完 —— 翻页时"等一张没人看的图"是最难受的延迟。
THREADS = 2

#: 缓存多少拍。一张按卡片宽 480 → 480×270 ≈ 0.5MB，最宽 1280 → ≈ 2.8MB；
#: 12 张 ≈ 6~20MB —— 够"来回翻、改完再看一眼"，又吃不掉大项目的内存。
CACHE_SIZE = 12

#: 渲染的目标宽度：控件宽 × 设备像素比，夹在 [480, 1280]。
#: 下限 480 是"卡片再窄也别糊"，上限 1280 是"再宽也不必渲 4K 再缩"。
MIN_TARGET_W = 480
MAX_TARGET_W = 1280

#: 控件高度的上下限（16:9 由宽度算；太矮/太高都不好看）。
MIN_H = 132
MAX_H = 420

#: 「当前舞台」那一栏里那张图的**固定尺寸**（16:9，2026-10-27 第二版）。
#:
#: 288×162 是"整帧都看得见、又不至于把卡片顶下去"的折中：它跟右边的文字
#: 摘要**并排**，而摘要本来就要占那几行，所以图几乎没有额外的高度成本。
#: 为什么不更大：用户要的是"能看到全貌、好判断该调什么"，不是"当壁纸看"。
COMPACT_W = 288
COMPACT_H = 162


# ----------------------------------------------------------------------
#  帧指纹
# ----------------------------------------------------------------------

def frame_signature(frame) -> tuple:
    """
    一帧的**内容指纹**（缓存键 / "要不要重画"的判据）。

    为什么不用 `id(frame)`（预览窗口的做法）：那边一帧对象在窗口生命期内
    不变；这里每刷一次都是新算出来的帧对象，`id` 每次都不同 ——
    拿它当键就等于"永远重画"。

    为什么不用 `hash(repr(frame))`：`repr` 里带 `0x7f…` 这类地址（`SpriteLayout`
    默认 repr），同样不稳定。所以**逐字段列出会影响像素的东西**：
    背景 / 立绘（图像名、位置、谁在说话）/ 图层 / 台词 / 说话人 / 转场。
    音乐与音效不影响画面，但一起放进来也无害（同拍换歌本来就是另一拍）。

    2026-10-30（S3 交接）：`at` 的真实几何落到 `StageFrame.scene_at`、
    逐层 `layers_at` 和 `Placement` 的新字段（`fit` / 尺寸族 / 采样 / alpha…
    上）—— 这些**漏一个就等于"改了没反应"**，所以：
      · 背景 `scene_at` 进签名；
      · 图层不只取 `layers` 字符串，还逐层带上 `layers_at` 的 at（见
        `_layers_key`：同一个 CG 换 at 时字符串一个字符都不变）；
      · 立绘的 `Placement` 走 `_place_key`，把 `fit` 等像素相关的字段全收。
    """
    sprites = tuple(
        (str(getattr(s, 'image', '') or ''), str(getattr(s, 'at', '') or ''),
         round(float(getattr(s, 'x', 0.0) or 0.0), 4),
         bool(getattr(s, 'speaking', False)),
         _place_key(getattr(s, 'place', None)))
        for s in (getattr(frame, 'sprites', None) or ()))
    return (int(getattr(frame, 'index', -1) or 0),
            str(getattr(frame, 'line_id', '') or ''),
            str(getattr(frame, 'label', '') or ''),
            str(getattr(frame, 'speaker', '') or ''),
            str(getattr(frame, 'speaker_name', '') or ''),
            str(getattr(frame, 'speaker_color', '') or ''),
            str(getattr(frame, 'what_color', '') or ''),
            str(getattr(frame, 'text', '') or ''),
            str(getattr(frame, 'scene', '') or ''),
            str(getattr(frame, 'scene_at', '') or ''),
            sprites,
            _layers_key(frame),
            str(getattr(frame, 'music', '') or ''),
            str(getattr(frame, 'sound', '') or ''),
            str(getattr(frame, 'voice', '') or ''),
            str(getattr(frame, 'with_', '') or ''),
            round(float(getattr(frame, 'pause', 0.0) or 0.0), 4))


def _layers_key(frame) -> tuple:
    """
    非角色图层（CG / UI）的 `(spec, at)` 列表 —— **逐层**，保留叠放顺序。

    2026-10-30（⑦ P0，S3 新加的 `StageFrame.layers_at`）：同一张 CG 从
    `cg01 at cg38402160` 改成 `cg01 at cgfit` 时，`layers` 字符串一个字符
    不变 —— 签名里只放字符串就会**沿用旧缓存**，卡片上的图永远不重画。

    解析规则与 `stage_render._draw_layers` 一字不差（先按 tag 找、再按整条
    spec 找），所以"签名认为变了"和"画面真的会变"是同一件事。
    老帧没有 `layers_at`（或者不是 dict）按空表兜底，不炸。
    """
    raw = str(getattr(frame, 'layers', '') or '')
    if not raw:
        return ()
    ats = getattr(frame, 'layers_at', None)
    if not isinstance(ats, dict):
        ats = {}
    out = []
    for spec in [part.strip() for part in raw.split(';') if part.strip()]:
        tag = spec.split()[0]
        out.append((spec, str(ats.get(tag) or ats.get(spec) or '')))
    return tuple(out)


def _place_key(place):
    """
    `Placement`（transform 算出来的几何）里**决定像素**的那些数。

    为什么要这么全（而不是只取 `x`）：项目里改了 `transform i11:` 之后，
    同一个 `at i11` 的 `x_px` / `zoom` / `anchor` 都会变 —— 只比 `x`
    （归一化兜底值，常常恒等于 0.5）会让卡片上的图**停在旧位置**上。

    2026-10-30（⑦ P0，S3）：`fit` 与尺寸族（`xsize` / `ysize`）、采样
    （`nearest`）、`alpha` / `rotate` 同样会改像素 —— 少一个就是"用户改了
    `fit` 但卡片缩略图不重画"。这些字段老版本 `Placement` 没有，
    `getattr` 兜底成 None，所以老帧不炸。
    """
    if place is None:
        return None
    out = []
    for name in ('x', 'x_px', 'x_kind', 'xanchor', 'xoffset', 'zoom',
                 'xzoom', 'yzoom', 'y_px', 'y_kind', 'yanchor', 'yoffset',
                 # —— 2026-10-30（⑦ P0）：尺寸 / 采样 / 透明度也算像素 ——
                 'fit', 'xsize', 'ysize', 'xsize_frac', 'ysize_frac',
                 'nearest', 'alpha', 'rotate',
                 # 'xpos 0.5' 与 'xpos 0.5px' 的 x_px 都在，但**语义**靠这几个
                 # frac 标记区分；改 transform 时常只翻标记不翻数字。
                 'xpos_frac', 'ypos_frac', 'xanchor_frac', 'yanchor_frac'):
        value = getattr(place, name, None)
        if isinstance(value, bool) or value is None:
            out.append(value)
        elif isinstance(value, (int, float)):
            out.append(round(float(value), 4))
        else:
            out.append(str(value))
    return tuple(out)


# ----------------------------------------------------------------------
#  后台渲染
# ----------------------------------------------------------------------

def _picture(got, frame, profile, target, compact: bool):
    """
    `RenderResult` → `QImage`（工作线程里调，别在 GUI 线程做）。

    **compact 小图**多一步：先缩到 target，再调
    `stage_render.draw_readable_say` 按缩略图自己的字号把名字/台词重画一遍
    （2026-10-29 用户报「渲染有问题」：整帧 26~30px 的台词缩到 288 宽只剩
    3~4px，糊成方块）。整宽图 / 舞台预览窗口不走这一步 —— 那里的字本来
    就是引擎排版、看得清，行为保持原样。
    """
    if not compact:
        return result_to_qimage(got, target)
    img = R.scale_result_image(got, target)
    if img is None:
        return result_to_qimage(got, target)
    try:
        img, _info = R.draw_readable_say(img, got, frame, profile)
    except Exception as e:                    # noqa: BLE001 - 重画失败别丢图
        # 可读重画只是"锦上添花"：它挂了也必须退回纯缩放的整帧图，
        # 否则后台线程直接抛异常 → 卡片永远停在"正在渲染"。
        swallow('stage_thumb.readable_say', e)
    return image_to_qimage(img)


class _Signals(QObject):
    """工作线程 → GUI 线程的唯一通道。"""

    #: `指纹, 代号, QImage|None, warnings, missing`
    done = Signal(object, int, object, object, object)


class _Gate:
    """
    "这一张还算不算数"的门闩（GUI 线程写、工作线程读）。

    与 `stage_preview._Gate` 同一个理由：从非 GUI 线程碰 QWidget 是未定义
    行为，这里只共享一个纯 Python 对象；排到队尾的任务发现自己过期就直接
    返回，不再白算一张 1080p。
    """

    __slots__ = ('key',)

    def __init__(self):
        self.key = None


class _Job(QRunnable):
    """在后台线程里合成一帧（并在那边缩到控件大小、小图再重画可读文字）。"""

    def __init__(self, signals, gate, gen, key, frame, project_root, registry,
                 profile, transform_index, screen, target, compact=False):
        super().__init__()
        self.signals = signals
        self.gate = gate
        self.gen = int(gen)
        self.key = key
        self.frame = frame
        self.project_root = project_root
        self.registry = registry
        self.profile = profile
        self.transform_index = transform_index
        self.screen = screen
        self.target = target
        #: 小图（卡片上 288×162 那张）才重画可读文字；整宽图保持引擎排版。
        self.compact = bool(compact)
        self.setAutoDelete(True)

    def run(self):
        if self.gate.key != self.key:
            return                            # 过期：一个字都不算
        got = None
        try:
            got = R.render_frame(
                self.frame, project_root=self.project_root,
                registry=self.registry, profile=self.profile,
                screen=self.screen, transform_index=self.transform_index)
        except Exception as e:                # noqa: BLE001 - 后台绝不炸
            swallow('stage_thumb.render_job', e)
            got = None
        # 缩放 + 小图可读文字**也在工作线程里做**（PIL 缩到目标尺寸 ≈ 20ms，
        # 比把 1920×1080 搬过线程边界再让 Qt 缩便宜得多）。
        qimg = _picture(got, self.frame, self.profile, self.target,
                        self.compact)
        warnings = list(getattr(got, 'warnings', []) or ())
        missing = list(getattr(got, 'missing', []) or ())
        try:
            self.signals.done.emit(self.key, self.gen, qimg, warnings, missing)
        except RuntimeError as e:
            # 窗口已经关了：控件随窗口销毁，emit 会 RuntimeError。
            swallow('stage_thumb.render_job.emit', e)


# ----------------------------------------------------------------------
#  控件
# ----------------------------------------------------------------------

class BeatStageThumb(QWidget):
    """
    卡片上的"这一拍的舞台图"。

    两种尺寸模式（同一条渲染链、同一个缓存）：

      · `compact=True`（卡片「当前舞台」那一栏，默认）：固定 288×162；
      · `compact=False`：16:9、按控件宽度自适应（整宽图）。

    公共接口（窗口只用这些）：
        set_session / show_beat / clear / note / pixmap / is_pending
        render_now（测试与无头）/ shutdown
    """

    #: 画面有效（True）或没有画面（False）。卡片/测试可听。
    rendered = Signal(bool)

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        #: 小图（固定尺寸）还是整宽图 —— 决定下面几处尺寸策略。
        self._compact = bool(compact)
        self.setObjectName('stageThumb')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if self._compact:
            # 固定尺寸：塞在标题下面、与摘要并排，**不**跟着列宽长胖。
            self.setSizePolicy(QSizePolicy.Policy.Fixed,
                               QSizePolicy.Policy.Fixed)
            self.setFixedSize(COMPACT_W, COMPACT_H)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Fixed)
            self.setMinimumHeight(MIN_H)
            self.setMaximumHeight(MAX_H)
        self._session = None
        self._project_root = ''
        self._profile = None
        #: 当前"想要"的那一拍的帧对象与指纹（`_key is None` = 没有画面）
        self._frame = None
        self._key = None
        self._idx = -1
        self._pix = None
        self._note = '还没有载入章节'
        self._pending = False
        self._missing: list = []
        self._warnings: list = []
        self._cache: OrderedDict = OrderedDict()
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._gate = _Gate()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(THREADS)
        #: 代号：每次请求 +1（迟到的结果对不上就只进缓存，不动画面）
        self._gen = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._enqueue)
        #: 诊断用：真的开跑了几次渲染（测试拿它判"有没有白算"）
        self.render_count = 0
        #: 画出来的那张图是按多宽的控件渲的（宽度大改要重画一张更清楚的）
        self._rendered_w = 0
        self._apply_aspect()
        self._sync_tooltip()

    # ------------------------------------------------------------------

    def set_session(self, session):
        """
        绑定会话（换项目 / 换章节都走这里）。

        `project_root` 变了就**清缓存**：同一个 `id(frame)` / 指纹在别的项目里
        可能一模一样，但素材完全不同 —— 拿旧图顶着就是"预览在骗人"。
        """
        root = str(getattr(session, 'project_root', '') or '')
        if session is not self._session or root != self._project_root:
            self._cache.clear()
            self._profile = None
        self._session = session
        self._project_root = root

    def show_beat(self, idx: int, session=None, *, force: bool = False):
        """
        把控件刷成**第 `idx` 拍**的画面（`idx < 0` / 越界 → 显示"没有可画的"）。

        `force=True`：内容指纹没变也重画（调试/换项目后用）。
        """
        if session is not None:
            self.set_session(session)
        s = self._session
        if s is None:
            self.clear('还没有载入章节')
            return
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = -1
        self._idx = idx
        try:
            frame = build_frame_at(
                list(getattr(s, 'beats', None) or []), idx,
                registry=s.registry(),
                transform_index=self._transform_index(),
                screen=self._screen())
        except Exception as e:                    # noqa: BLE001 - 画不出也别炸卡片
            swallow('stage_thumb.show_beat.frame', e)
            frame = None
        if frame is None:
            beats = list(getattr(s, 'beats', None) or [])
            if not beats:
                self.clear('还没有载入章节')
            elif idx < 0:
                self.clear('还没有选中任何一拍')
            else:
                self.clear(f'这一拍不存在（第 {idx + 1} 拍超出这一章的 '
                           f'{len(beats)} 拍）')
            return
        key = frame_signature(frame)
        if not force and key == self._key and self._pix is not None:
            self._timer.stop()                    # 别让上一次排的防抖任务白跑
            return                                # 同一拍、同一内容：不重画
        self._frame = frame
        self._key = key
        hit = self._cache.get(key)
        if hit is not None:
            self._timer.stop()                    # 命中缓存：排队中的渲染不用跑了
            self._cache.move_to_end(key)
            self._pix, self._warnings, self._missing = hit
            self._note = ''
            self._pending = False
            self._sync_tooltip()
            self.update()
            self.rendered.emit(True)
            return
        # 没缓存：**立刻清掉上一张**（画面上写"正在渲染"），再排后台任务。
        self._pix = None
        self._missing = []
        self._warnings = []
        self._note = self._pending_note()
        self._pending = True
        self._sync_tooltip()
        self._gen += 1
        self.update()
        self._schedule()

    def clear(self, note: str = ''):
        """
        没有画面（空会话 / 越界 / 主动清掉）。

        说明文字**永远不空**：一块黑底什么都不写，用户只会以为"工具坏了"。
        """
        self._frame = None
        self._key = None
        self._idx = -1
        self._pix = None
        self._pending = False
        self._missing = []
        self._warnings = []
        self._note = str(note or '') or '（这一拍没有舞台画面）'
        self._gen += 1
        self._gate.key = None
        self._timer.stop()
        self._sync_tooltip()
        self.update()
        self.rendered.emit(False)

    def note(self) -> str:
        """画面上那句说明（测试用："为什么这里没有图"）。"""
        return str(self._note or '')

    def pixmap(self):
        """当前这一拍的图（没画出来就是 None）。"""
        return self._pix

    def is_pending(self) -> bool:
        """这一拍还在后台渲染。"""
        return bool(self._pending)

    def beat_index(self) -> int:
        return int(self._idx)

    def rendered_missing(self) -> list:
        """这一帧**脚本里写了、磁盘上找不到**的素材名。"""
        return list(self._missing or ())

    def render_now(self):
        """
        同步渲染（测试 / 无头用；正式路径走防抖 + 后台线程）。

        返回 `QPixmap | None`（= 现在的画面）。
        """
        self._timer.stop()
        if self._frame is None or self._key is None:
            return None
        size = self._target_size()
        if size is None:
            return None
        self._gen += 1
        got = None
        try:
            # 可读重画要用**同一份** gui 排版（颜色/字体/前后缀），
            # 所以先确保 profile 载入（正式路径在 `_enqueue` 里已经确保过）。
            self._ensure_profile()
            got = R.render_frame(
                self._frame, project_root=self._project_root,
                registry=self._registry(), profile=self._profile,
                screen=self._screen(),
                transform_index=self._transform_index())
        except Exception as e:                    # noqa: BLE001
            swallow('stage_thumb.render_now', e)
            got = None
        self.render_count += 1
        self._on_done(self._key, self._gen,
                      _picture(got, self._frame, self._profile, size,
                               self._compact),
                      list(getattr(got, 'warnings', []) or ()),
                      list(getattr(got, 'missing', []) or ()))
        return self._pix

    def shutdown(self):
        """窗口关闭：别再排新任务（正在跑的会跑完并把结果丢掉）。"""
        try:
            self._timer.stop()
            self._pool.clear()
        except Exception:                         # noqa: BLE001
            swallow('stage_thumb.shutdown')

    # ---------------- 渲染上下文 ----------------

    def _registry(self):
        try:
            return self._session.registry() if self._session else None
        except Exception as e:                    # noqa: BLE001
            swallow('stage_thumb.registry', e)
            return None

    def _transform_index(self) -> dict:
        try:
            return dict(self._session.transform_index() or {}) \
                if self._session else {}
        except Exception as e:                    # noqa: BLE001
            swallow('stage_thumb.transform_index', e)
            return {}

    def _screen(self) -> tuple:
        try:
            size = self._session.screen_size() if self._session else None
            if size:
                return (int(size[0]), int(size[1]))
        except Exception as e:                    # noqa: BLE001
            swallow('stage_thumb.screen', e)
        return (1920, 1080)

    def _ensure_profile(self):
        if self._profile is not None or not self._project_root:
            return
        try:
            from storygraph.story.renpy_gui import load_gui_profile
            self._profile = load_gui_profile(self._project_root,
                                             registry=self._registry())
        except Exception as e:                    # noqa: BLE001
            swallow('stage_thumb.load_gui_profile', e)
            self._profile = None

    def _target_size(self):
        """
        渲染成多大：控件宽 × 设备像素比（夹在 [480, 1280]），高度按控件自己的
        高宽比算 —— 一次 1080p 渲染 100ms~1.3s，渲一张比控件大得多的图纯属浪费。
        """
        w = int(self.width() or 0)
        h = int(self.height() or 0)
        if w < 80 or h < 45:
            return None                           # 还没布局：等 resize 再排
        try:
            dpr = float(self.devicePixelRatioF() or 1.0)
        except Exception as e:                    # noqa: BLE001 - 拿不到就当 1.0
            swallow('stage_thumb.device_pixel_ratio', e)
            dpr = 1.0
        tw = int(round(w * dpr))
        tw = max(MIN_TARGET_W, min(MAX_TARGET_W, tw))
        th = max(1, int(round(tw * (float(h) / float(w)))))
        return (tw, th)

    # ---------------- 排队 ----------------

    def _schedule(self):
        """防抖后开跑；控件这会儿不可见（舞台块收着）就先攒着。"""
        if self._frame is None or self._key is None:
            return
        self._timer.stop()
        if not self.isVisible():
            return                                # showEvent / resize 时再排
        self._timer.start()

    def _enqueue(self):
        if self._frame is None or self._key is None:
            return
        size = self._target_size()
        if size is None:
            return                                # 等 resizeEvent
        self._ensure_profile()
        self._gate.key = self._key
        self.render_count += 1
        try:
            self._pool.start(_Job(
                self._signals, self._gate, self._gen, self._key, self._frame,
                self._project_root, self._registry(), self._profile,
                self._transform_index(), self._screen(), size,
                self._compact))
        except Exception as e:                    # noqa: BLE001
            swallow('stage_thumb.enqueue', e)

    def _on_done(self, key, gen, qimg, warnings, missing):
        """
        后台结果回来了：**先入缓存，再判"是不是当前这一拍"**。

        不是当前这一拍（用户已经翻走了）→ 只入缓存：翻回去就是 0 延迟。
        """
        pix = None
        if qimg is not None:
            try:
                pix = QPixmap.fromImage(qimg)
                if pix.isNull():
                    pix = None
            except Exception as e:                # noqa: BLE001
                swallow('stage_thumb.render_done', e)
                pix = None
        if pix is not None:
            self._cache[key] = (pix, list(warnings or ()), list(missing or ()))
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        if key != self._key:
            return
        self._pending = False
        self._pix = pix
        self._warnings = list(warnings or ())
        self._missing = list(missing or ())
        self._note = '' if pix is not None else self._failed_note()
        self._sync_tooltip()
        self.update()
        self.rendered.emit(pix is not None)

    def _pending_note(self) -> str:
        if not R.available():
            return '没装 Pillow，画不出真实舞台（装上 Pillow 就会显示）'
        return '正在按引擎排版渲染这一拍…'

    def _failed_note(self) -> str:
        if not R.available():
            return '没装 Pillow，画不出真实舞台（装上 Pillow 就会显示）'
        return '这一拍渲染不出来（见状态栏日志）'

    # ---------------- 画 ----------------

    def hasHeightForWidth(self) -> bool:          # noqa: N802 - Qt 命名
        return not self._compact

    def heightForWidth(self, w: int) -> int:      # noqa: N802 - Qt 命名
        if self._compact:
            return COMPACT_H
        return max(MIN_H, min(MAX_H, int(round(int(w) * 9.0 / 16.0))))

    def sizeHint(self) -> QSize:                  # noqa: N802 - Qt 命名
        if self._compact:
            return QSize(COMPACT_W, COMPACT_H)
        return QSize(480, 270)

    def _apply_aspect(self):
        """高度跟着宽度走（16:9）—— 卡片列宽变了，图不许变形也不许留大黑边。"""
        if self._compact:
            return                                # 小图是固定尺寸（COMPACT_W/H）
        try:
            w = int(self.width() or 0)
            if w <= 0:
                return
            want = max(MIN_H, min(MAX_H, int(round(w * 9.0 / 16.0))))
            if want != self.height():
                self.setFixedHeight(want)
        except Exception:                         # noqa: BLE001
            swallow('stage_thumb.apply_aspect')

    def resizeEvent(self, event):                 # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._apply_aspect()
        # 宽度大改（窗口拉伸）→ 旧图是缩出来的，重画一张更清楚的。
        # 阈值 12%：布局抖动几个像素不值得重渲染（一次 100ms~1.3s）。
        if self._pix is not None and self._key is not None:
            old = int(getattr(self, '_rendered_w', 0) or 0)
            if old and abs(int(self.width()) - old) > max(24, old // 8):
                self._pix = None
                self._note = self._pending_note()
                self._pending = True
                self._gen += 1
                self.update()
                self._schedule()
                return
        if self._pending:
            self._schedule()

    def showEvent(self, event):                   # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._pending:
            self._schedule()

    def hideEvent(self, event):                   # noqa: N802 - Qt 命名
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, event):                  # noqa: N802 - Qt 命名
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            w, h = self.width(), self.height()
            pix = self._pix
            if pix is None or pix.isNull():
                p.fillRect(0, 0, w, h, QColor(T.t('stage.letterbox')))
                p.setPen(QPen(QColor(T.t('stage.textDim'))))
                p.setFont(QFont(theme.font_family(), theme.font_pt('body')))
                text = self._note or '（这一拍没有舞台画面）'
                p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, text)
                return
            # 等比缩放 + 黑边（与预览窗口的全屏 letterbox 一致）
            scale = min(w / max(1, pix.width()), h / max(1, pix.height()))
            dw = max(1, int(pix.width() * scale))
            dh = max(1, int(pix.height() * scale))
            x = (w - dw) // 2
            y = (h - dh) // 2
            if dw < w or dh < h:
                p.fillRect(0, 0, w, h, QColor(T.t('stage.letterbox')))
            p.drawPixmap(x, y, dw, dh, pix)
            self._rendered_w = int(self.width())
            self._paint_badges(p, w, h)
        except Exception:                          # noqa: BLE001 - paint 里绝不外抛
            swallow('stage_thumb.paintEvent')
        finally:
            p.end()

    def _paint_badges(self, p, w: int, h: int):
        """
        右下角的小角标：**缺素材**（要人去修）—— 与预览窗口的横条同一件事。

        为什么**不**画"第 N 拍"：拍号紧跟在图下面的「当前舞台」第一行里
        （`stage_digest` 就是"第 N 拍 · label …"）。图上再写一遍是把
        游戏画面弄脏，而"这是哪一拍"一个字也不会因此更清楚。
        """
        if not self._missing:
            return
        try:
            font = QFont(theme.font_family(), theme.font_pt('caption'))
            p.setFont(font)
            fm = p.fontMetrics()
            text = f'⚠ 缺 {len(self._missing)} 个素材'
            pad = 6
            tw = fm.horizontalAdvance(text) + pad * 2
            th = fm.height() + pad
            x = int(w) - tw - 8
            y = int(h) - th - 8
            p.save()
            p.setOpacity(0.82)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.t('stage.speakerBg')))
            p.drawRoundedRect(x, y, tw, th, 4, 4)
            p.setPen(QPen(QColor(T.t('stage.warn'))))
            p.drawText(x + pad, y + pad + fm.ascent(), text)
            p.restore()
        except Exception:                          # noqa: BLE001
            swallow('stage_thumb.paint_badges')

    def _sync_tooltip(self):
        """
        鼠标停上去看什么：缺了哪些素材（名单）／这块图是什么。

        在**状态变化时**设（不是在 `paintEvent` 里设）：paint 只管画，
        它可能被重入、也可能在"还没画过"的时候被问 tooltip（测试就撞上了）。
        """
        try:
            if self._missing:
                self.setToolTip(
                    '这一拍缺这些素材（脚本里写了、磁盘上找不到）：\n'
                    + '\n'.join(str(m) for m in self._missing[:12])
                    + '\n\n（与「🎬 演出预览」同一套渲染；改完保存会自动重画）')
            else:
                self.setToolTip(
                    '这一拍的舞台画面（与「🎬 演出预览」同一套渲染，只画这一拍）\n'
                    '改动这一拍并保存后自动重画')
        except Exception:                          # noqa: BLE001
            swallow('stage_thumb.sync_tooltip')


__all__ = ['BeatStageThumb', 'frame_signature', 'DEBOUNCE_MS']
