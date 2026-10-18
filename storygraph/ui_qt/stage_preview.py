"""
storygraph.ui_qt.stage_preview
==============================
Qt 版**舞台演出预览**：把这一章按卡片效果"播"出来。

对齐用户诉求（2026-09-28）：
> 我希望你能够写一个舞台演出核心，让这一章能够实时根据卡片效果跑起来

语义**一行都不重写** —— 帧与素材解析全来自 `storygraph.app.stage`
（与「舞台快照」表、`render_beat_to_rpy` 同源）。本文件只做 Qt 的画与播放：

  · `StageCanvas` — 画一帧：背景 → 立绘（真图，异步解码）→ 图层 → 名条/对话框；
      缺图自动降级成占位方块（**绝不留一片黑**，也绝不空等）；
  · `StagePreviewWindow` — 播放 / 暂停 / 上下一句 / 跳到某拍 /
      「只看有变化的拍」 + 键盘(←/→/空格/Home/End)。

为什么要防"上一帧的图贴到这一帧"：异步解码回来时帧可能已经翻了，
所以每个请求都带 **帧号+序号**，回来时对不上就丢弃 —— 否则"预览在骗人"，
比没有预览更糟。
"""

from __future__ import annotations

import time

from storygraph.core.safeguard import swallow
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from storygraph.app.stage import (BLOCK_H, BLOCK_W, SPRITE_H_RATIO, SPRITE_MAX,
                                  STAGE_H, STAGE_W, audio_spec_name,
                                  image_index, resolve_frame_assets,
                                  resolve_solid_color, sprite_box,
                                  stage_audio_cues)
from storygraph.app.thumbs import natural_size
from storygraph.ui_qt import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.thumb_loader import ThumbLoader

PLAY_MS = 900          # 自动播放间隔；节奏仅示意，不代表真实时长

#: 预取往后几拍（用户 2026-10-05："每次加载时……总有一个蓝色框当占位符
#: 一闪而过"）。预告下一两拍的图，翻到它时就是内存命中（0 延迟），
#: 加载中的占位框自然也就看不见了。
PREFETCH_AHEAD = 2
#: 预取队列的上限（跳来跳去时别把线程池塞满）
PREFETCH_QUEUE_LIMIT = 40


def _is_stale_frame_slot(slot, idx) -> bool:
    """这个 loader slot 是"**别的帧**的素材"吗（预取的 slot 不算）？"""
    return (isinstance(slot, tuple) and len(slot) >= 2
            and slot[0] in ('bg', 'sp', 'layer') and slot[1] != idx)


class StageAudio:
    """
    预览的**声音**：BGM 循环 + 音效 / 语音一次性（QtMultimedia）。

    用户 2026-10-05：

    > 演出的预览界面可能仍旧有点问题，第一个是 bgm 未被实际播放

    语义（照 Ren'Py）：
      · **BGM 是持续状态** —— 这一拍没换曲就继续放，绝不每拍重头开始；
      · **音效 / 语音是一次性的** —— 换到那一拍响一次。

    播放器**可注入**（`player_factory`）：没装 QtMultimedia、或者没有音频设备
    的环境里退化成"什么都不放"，并把原因写在 `note()` 里给界面显示 ——
    绝不因为放不出声就把预览搞崩。
    """

    #: BGM 音量（提示性的预览，别盖过用户别的音频）
    BGM_VOLUME = 0.55
    FX_VOLUME = 0.9

    def __init__(self, project_root=None, registry=None, enabled: bool = True,
                 player_factory=None):
        self.project_root = project_root
        self._registry = registry
        self._enabled = bool(enabled)
        self._error = ''
        self._notes: list = []
        self._music_path = None
        self._last_key = None
        self._bgm = None
        self._fx = None
        self._factory = player_factory
        self._make_players()

    # ------------------------------------------------------------------

    def _make_players(self):
        if self._factory is not None:
            # 注入的假播放器（测试用）：`() -> (bgm, bgm_out, fx, fx_out)`
            try:
                made = self._factory()
                self._bgm, self._bgm_out, self._fx, self._fx_out = made
            except Exception as e:                # noqa: BLE001
                self._error = f'音频初始化失败（{e.__class__.__name__}）'
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except Exception as e:                    # noqa: BLE001
            self._error = f'这个环境没有音频后端（{e.__class__.__name__}）'
            return
        try:
            self._bgm = QMediaPlayer()
            out = QAudioOutput()
            out.setVolume(self.BGM_VOLUME)
            self._bgm.setAudioOutput(out)
            self._bgm.setLoops(QMediaPlayer.Loops.Infinite)
            self._bgm_out = out                  # 保引用：被回收就没声了
            self._fx = QMediaPlayer()
            fx_out = QAudioOutput()
            fx_out.setVolume(self.FX_VOLUME)
            self._fx.setAudioOutput(fx_out)
            self._fx_out = fx_out
        except Exception as e:                    # noqa: BLE001
            self._error = f'音频初始化失败（{e.__class__.__name__}）'
            self._bgm = self._fx = None

    def note(self) -> str:
        """给界面显示的一句话（正常时为空）。"""
        return self._error

    def set_registry(self, registry):
        self._registry = registry

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        if not self._enabled:
            self.stop()

    def set_frame(self, frame, key=None):
        """
        切到某一帧：该换曲就换曲，该响一次就响一次。

        `key` 用来判"同一帧被重画"（例如切「真实贴图」开关）—— 同一帧不重放
        音效/语音，否则点一下开关就重复喊一句。

        返回 `stage_audio_cues` 的结果（含 `notes`：**找不到文件**、**近似匹配**
        这类必须让用户看见的话 —— 用户 2026-10-05 "我这里没有声音" 就是
        脚本引用的文件名在磁盘上不存在，以前预览一声不响、也不说为什么）。
        """
        cues = stage_audio_cues(frame, self._registry, self.project_root)
        self._notes = list(cues.get('notes') or ())
        if not self._enabled:
            return cues
        music = cues.get('music')
        if music != self._music_path:
            self._music_path = music
            if music:
                self._play(self._bgm, music, loop=True)
            else:
                self._stop(self._bgm)
        if key is not None and key == self._last_key:
            return cues                            # 同一帧重画：不重放
        self._last_key = key
        for _kind, path in cues.get('once') or ():
            self._play(self._fx, path, loop=False)
        return cues

    def notes(self) -> list:
        """这一拍"必须让用户知道"的声音提示（缺文件 / 近似匹配）。"""
        return list(self._notes)

    def _play(self, player, path, loop: bool = False):
        if player is None or not path:
            return
        try:
            player.stop()
            player.setSource(QUrl.fromLocalFile(str(path)))
            player.play()
        except Exception as e:                    # noqa: BLE001
            self._error = f'播放失败：{e.__class__.__name__}'

    @staticmethod
    def _stop(player):
        if player is None:
            return
        try:
            player.stop()
        except Exception:                         # noqa: BLE001
            swallow('stage_preview._stop')

    def stop(self):
        self._stop(self._bgm)
        self._stop(self._fx)
        self._music_path = None
        self._last_key = None


def layer_model_for(project_root):
    """
    取这个项目的合成立绘模型（`LayerModel`）；没有就返回 None。

    设计文档：`docs/archive/设计_合成立绘合成与姿势切换_2026-10-05.md`。

    ⚠ 缓存已**收敛到** `story.layered_model.load_layer_model_cached`（带**文件指纹**
    失效）：实测真机建一次 0.28 s，而卡片每刷新一拍都要问一次 ——
    所以缓存必须能失效（从前这里那份"一次会话只读一次盘"的缓存做不到）。
    这里保留函数名只为不改调用点；**新的调用点请直接用那个函数**。
    """
    root = str(project_root or '')
    if not root:
        return None
    try:
        from storygraph.story.layered_model import load_layer_model_cached
        return load_layer_model_cached(root)
    except Exception as e:                        # noqa: BLE001 - 认不出走老路
        swallow('ui_qt.stage_preview.layer_model_for', e)
        return None


class StageCanvas(QWidget):
    """
    舞台画布：一帧的画面。

    绘制顺序 = 真实游戏的层级：背景 → 立绘 → 非角色图层 → 说话人角标。

    立绘的位置/大小：**有项目 transform 就按真实几何画**（原图尺寸 × zoom +
    xcenter/xoffset/yanchor/ypos — 见 `app.stage.sprite_box`）；没算出来才退回
    "按屏幕百分比"的示意图（老行为，保持兼容）。
    """

    def __init__(self, parent=None, project_root=None, real_images=True,
                 screen=None):
        super().__init__(parent)
        self.setMinimumSize(STAGE_W // 2, STAGE_H // 2)
        self.setAutoFillBackground(False)
        self.project_root = project_root
        self.real_images = real_images
        self.screen = tuple(screen) if screen else (1920, 1080)
        self._images: dict = {}
        self._loader = ThumbLoader(self, max_size=SPRITE_MAX,
                                   project_root=project_root)
        self._frame = None
        self._frame_idx = -1
        self._bg_pix = None
        #: 当前帧的立绘贴图：{序号: QPixmap}
        self._sprites: dict = {}
        #: 当前帧立绘的**真实几何**（虚拟屏幕像素）：{序号: (left, top, w, h)}
        self._boxes: dict = {}
        #: **正在解码**的立绘序号（用来画"低调的加载中"，而不是亮蓝占位块）
        self._loading: set = set()
        #: 这一帧**脚本里写了、磁盘上找不到**的素材名（缺图要显眼地说出来）
        self._missing: list = []
        self._layer_pix = None
        #: **转场淡入**（2026-10-18，审计 P1-8"无转场"）：1.0 = 不透明。
        #: `with dissolve/fade` 的那一拍从 0.15 淡到 1（350ms），硬切直接 1。
        self._fade_alpha = 1.0
        self._fade_started = 0.0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._tick_fade)

    # ------------------------------------------------------------------

    def begin_transition(self, fade: bool) -> None:
        """
        这一拍切过去时要不要淡入（`with dissolve` / `with fade`）。

        为什么不是"真播那个转场"：转场是引擎的渲染语义（ATL + shader），
        在 Qt 里重写一遍必然对不上。预览的目标是"让人看出这里有一次
        **渐变**换景"，所以只做淡入这一件事，并且**只对渐变类转场**做 ——
        硬切必须看起来像硬切，否则"我明明写的是硬切"会看不出来。
        """
        if not fade:
            self._fade_alpha = 1.0
            self._fade_timer.stop()
            return
        self._fade_alpha = 0.15
        self._fade_started = time.monotonic()
        self._fade_timer.start()

    def _tick_fade(self) -> None:
        try:
            elapsed = time.monotonic() - self._fade_started
            self._fade_alpha = min(1.0, 0.15 + 0.85 * (elapsed / 0.35))
            if self._fade_alpha >= 1.0:
                self._fade_timer.stop()
            self.update()
        except Exception:                      # noqa: BLE001 - 定时器里不许抛
            self._fade_timer.stop()

    # ------------------------------------------------------------------

    def set_registry(self, registry):
        self._images = getattr(registry, 'images', {}) or {}

    def shutdown(self):
        try:
            self._loader.shutdown()
        except Exception:                    # noqa: BLE001
            swallow('stage_preview.shutdown')

    # ------------------------------------------------------------------

    def show_frame(self, frame, idx: int):
        """切到某一帧：**先清空旧的贴图**，再异步取新的。"""
        self._frame = frame
        self._frame_idx = idx
        self._bg_pix = None
        self._layer_pix = None
        self._sprites = {}
        self._boxes = {}
        self._loading = set()
        self._missing = []
        self.update()
        if frame is None or not self.real_images:
            return
        # 上一帧的贴图不再需要：把"还排在队里、没开始解"的那些丢掉
        # （正在解的会跑完并进缓存，切回去就是命中）。不丢的话，一路播下去
        # 会攒下几百张"没人再看"的图，白占线程池（见 `ThumbLoader` 的调度模型）。
        # **但预取要留着** —— 那是下一两拍的图，丢了等于没预取（翻到那拍又要等）。
        try:
            self._loader.clear_pending(
                keep=lambda slot: not _is_stale_frame_slot(slot, idx))
        except Exception:                     # noqa: BLE001
            swallow('stage_preview.show_frame')
        assets = resolve_frame_assets(
            frame, self._images, self.project_root,
            image_index(self.project_root),
            layer_model=layer_model_for(self.project_root))
        self._missing = list(assets.get('missing') or ())
        if assets.get('bg') is not None:
            self._request(('bg', idx), str(assets['bg']), self._on_bg,
                          (STAGE_W, STAGE_H))
        for i, (s, path) in enumerate(assets.get('sprites') or []):
            if path is not None:
                # 位置按项目 transform 的真实几何算；尺寸问图片的**原始像素**
                # （只读文件头，快）—— 立绘大小 = 原图 × zoom，缺一不可。
                size = natural_size(path, self.project_root)
                box = sprite_box(s, size, self.screen)
                if box is not None:
                    self._boxes[i] = box
                # 先记"正在解"再请求：`request` 在内存命中时会**同步**回调，
                # 那一瞬间就要把它从"正在解"里划掉（见 `_on_sprite`）。
                self._loading.add(i)
                self._request(('sp', idx, i), str(path),
                              self._on_sprite(i),
                              (SPRITE_MAX[0], SPRITE_MAX[1]))
        if assets.get('layers') is not None:
            self._request(('layer', idx), str(assets['layers']), self._on_layer,
                          (STAGE_W, STAGE_H))

    # ------------------------------------------------------------------

    def missing_assets(self) -> list:
        """这一帧**脚本里写了、磁盘上找不到**的素材名（横条上要显眼地写出来）。"""
        return list(self._missing)

    def prefetch_frame(self, frame, limit: int = 6):
        """
        把**下一拍**要用的图解进内存缓存（翻到它时 0 延迟，加载框也就看不见了）。

        优先级是 `PRIORITY_BACKGROUND`：**预取永远不许挡住当前这一帧** ——
        正在解的那几张先出图，空下来的线程才轮到预取。
        """
        if frame is None or not self.real_images:
            return
        try:
            if self._prefetch_queued() > PREFETCH_QUEUE_LIMIT:
                return
        except Exception:                     # noqa: BLE001
            swallow('stage_preview.prefetch_frame')
        try:
            assets = resolve_frame_assets(
                frame, self._images, self.project_root,
                image_index(self.project_root),
            layer_model=layer_model_for(self.project_root))
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.stage_preview.prefetch_frame', e)
            return
        jobs = []
        if assets.get('bg') is not None:
            jobs.append((str(assets['bg']), (STAGE_W, STAGE_H)))
        for _s, path in (assets.get('sprites') or [])[:max(1, int(limit))]:
            if path is not None:
                jobs.append((str(path), (SPRITE_MAX[0], SPRITE_MAX[1])))
        if assets.get('layers') is not None:
            jobs.append((str(assets['layers']), (STAGE_W, STAGE_H)))
        for path, size in jobs:
            try:
                self._loader.prefetch(path, size)
            except Exception:                 # noqa: BLE001
                swallow('stage_preview.prefetch_frame')

    def _prefetch_queued(self) -> int:
        n = 0
        for slot in self._loader.queued():
            if isinstance(slot, tuple) and slot and slot[0] == 'prefetch':
                n += 1
        return n

    def _request(self, slot, path, apply, size):
        # `placeholder` **必须给**：不给的话 `ThumbLoader` 会拿 `apply(None)`
        # 当"先画个骨架"，而这里的 `apply` 语义是"结果到了"（`pixmap=None`
        # = 没解出来）。于是"正在解"会被立刻当成"解完了但没图" ——
        # 加载中的标记一闪就没，画面上就还是那个占位框。
        self._loader.request(slot, path, apply, max_size=size,
                             placeholder=lambda: None)

    def _on_bg(self, pixmap, _path):
        self._bg_pix = pixmap
        self.update()

    def _on_layer(self, pixmap, _path):
        self._layer_pix = pixmap
        self.update()

    def _on_sprite(self, i):
        def apply(pixmap, _path):
            self._loading.discard(i)
            if pixmap is not None:
                self._sprites[i] = pixmap
            self.update()
        return apply

    # ------------------------------------------------------------------
    #  绘制
    # ------------------------------------------------------------------
    #
    #  注意（踩过的坑）：**不要在 paintEvent 里做可能抛异常的事**。
    #  PySide6 的自定义 paint 里抛异常 = 直接 segfault，且没有 traceback。

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            # 转场淡入：整帧按 `_fade_alpha` 铺（硬切时恒为 1.0）
            p.setOpacity(max(0.0, min(1.0, float(self._fade_alpha))))
            w, h = self.width(), self.height()
            f = self._frame

            # 1) 背景：真图 → 纯色 → 写出名字
            if self._bg_pix is not None:
                p.drawPixmap(0, 0, w, h, self._bg_pix)
            else:
                color = resolve_solid_color(getattr(f, 'scene', ''),
                                            self._images,
                                            self.project_root) if f else None
                p.fillRect(0, 0, w, h,
                           QColor(color) if color else QColor(T.t('stage.letterbox')))
                if f is not None and f.scene:
                    p.setPen(QPen(QColor(T.t('stage.label'))))
                    p.setFont(QFont(theme.font_family(),
                                    theme.font_pt('caption')))
                    p.drawText(10, 18, f'背景：{f.scene}')

            # 2) 立绘：真图逐张；没有就占位块
            sprites = getattr(f, 'sprites', []) if f else []
            k = w / max(1.0, float(self.screen[0]))     # 虚拟屏幕 → 控件
            base_y = h - 14
            for i, s in enumerate(sprites):
                pix = self._sprites.get(i)
                box = self._boxes.get(i)
                if box is not None:
                    # **真实几何**（项目 transform 算出来的）：位置与大小都按
                    # 游戏里的像素来，超出画面的部分由控件裁掉（与游戏一致）
                    left, top, bw, bh = box
                    rect = (int(left * k), int(top * k),
                            max(1, int(bw * k)), max(1, int(bh * k)))
                    if pix is not None:
                        p.drawPixmap(rect[0], rect[1], rect[2], rect[3], pix)
                    else:
                        self._draw_block(p, s, rect, loading=i in self._loading)
                    if s.speaking:
                        p.setPen(QPen(QColor(T.t('stage.speakingOutline')), 3))
                        p.drawRect(*rect)
                    continue
                # 没有真实几何（transform 没解析出来 / 找不到原图尺寸）：
                # 退回"按屏幕百分比"的示意图
                x = int(max(0.0, min(1.0, s.x)) * w)
                if pix is not None:
                    ph = int(h * SPRITE_H_RATIO)
                    pw = int(pix.width() * ph / max(1, pix.height()))
                    p.drawPixmap(x - pw // 2, base_y - ph, pw, ph, pix)
                    if s.speaking:
                        p.setPen(QPen(QColor(T.t('stage.speakingOutline')), 3))
                        p.drawRect(x - pw // 2, base_y - ph, pw, ph)
                else:
                    self._draw_block(p, s, (x - BLOCK_W // 2, base_y - BLOCK_H,
                                            BLOCK_W, BLOCK_H),
                                     loading=i in self._loading)

            if f is not None and not sprites:
                p.setPen(QPen(QColor(T.t('stage.textDim'))))
                p.setFont(QFont(theme.font_family(), theme.font_pt('body')))
                p.drawText(0, 0, w, h, Qt.AlignCenter, '（台上没人）')

            # 3) 非角色图层（CG 等）在最上面
            if self._layer_pix is not None:
                p.drawPixmap(0, 0, w, h, self._layer_pix)
            elif f is not None and f.layers:
                color = resolve_solid_color(f.layers, self._images,
                                            self.project_root)
                if color:
                    p.fillRect(0, 0, w, h, QColor(color))
                else:
                    p.setPen(QPen(QColor(T.t('stage.labelAlt'))))
                    p.setFont(QFont(theme.font_family(),
                                    theme.font_pt('caption')))
                    p.drawText(w - 10, 18, f'图层：{f.layers}')
        except Exception:                    # noqa: BLE001 - paint 里绝不外抛
            swallow('stage_preview.paintEvent')
        finally:
            p.end()

    def _draw_block(self, p, s, rect, loading: bool = False):
        """
        占位块：真图不可用时也要让"台上有人"看得出来（`rect` = 框）。

        两种含义**必须分开画**（用户 2026-10-05："每次加载时……总会有一个蓝色框
        当占位符一闪而过，影响观感"）：
          · `loading=True`：图已经解析到、只是在解 —— 画得**越低调越好**
            （深色底 + 几乎看不见的 1px 边，不写字），图到了就自然顶上来；
          · `loading=False`：**磁盘上找不到这张图** —— 那就该显眼（亮蓝框 +
            名字 + 站位），这是要用户去修的，不是"再等等"。
        """
        left, top, bw, bh = [int(v) for v in rect]
        if loading:
            p.setBrush(QColor(T.t('stage.loading')))
            p.setPen(QPen(QColor(T.t('stage.loadingOutline')), 1))
            p.drawRect(left, top, bw, bh)
            return
        speaking = bool(getattr(s, 'speaking', False))
        fill = QColor(T.t('stage.speaking') if speaking
                      else T.t('stage.placeholder'))
        outline = QColor(T.t('stage.speakingOutline') if speaking
                         else T.t('stage.placeholderOutline'))
        p.setBrush(fill)
        p.setPen(QPen(outline, 3 if speaking else 1))
        p.drawRect(left, top, bw, bh)
        p.setPen(QPen(QColor(T.t('stage.text'))))
        p.setFont(QFont(theme.font_family(), theme.font_pt('caption')))
        label = getattr(s, 'label', '') or ''
        p.drawText(left + 4, top + 4, max(8, bw - 8), max(8, bh - 8),
                   Qt.AlignCenter | Qt.TextWordWrap, label)
        dim = getattr(s, 'tag', '') + (f' @ {s.at}' if getattr(s, 'at', '')
                                       else '')
        p.setPen(QPen(QColor(T.t('stage.textDim'))))
        p.drawText(left, top + bh + 2, max(40, bw), 16, Qt.AlignHCenter, dim)


class StagePreviewWindow(QDialog):
    """
    舞台演出预览窗口。

    能力：逐帧播放 / 暂停 / 上一句 / 下一句 / 跳到某拍 /
    「只看有变化的拍」/「真实贴图」。
    """

    def __init__(self, parent=None, frames=None, title: str = '舞台演出预览',
                 registry=None, project_root=None, real_images: bool = True,
                 screen=None, sound: bool = True, player_factory=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        theme.apply_to(self)
        self.frames = list(frames or [])
        self._all = list(frames or [])
        self.idx = 0
        self._playing = False
        self._images = getattr(registry, 'images', {}) or {}
        self._registry = registry
        self._project_root = project_root
        self._real = bool(real_images)
        self._only_changed = False
        #: 真实舞台几何（游戏分辨率）：transform 的像素值按它归一化
        from storygraph.story.atl import DEFAULT_SCREEN, project_screen
        self._screen = tuple(screen) if screen else (
            project_screen(project_root) if project_root else DEFAULT_SCREEN)
        self._audio = StageAudio(project_root, registry=registry,
                                 enabled=sound, player_factory=player_factory)
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(PLAY_MS)
        self._timer.timeout.connect(self.next_frame)
        if self.frames:
            self.show_frame(0)

    # ------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(T.SPACE['sm'], T.SPACE['sm'],
                                T.SPACE['sm'], T.SPACE['sm'])
        root.setSpacing(T.SPACE['xs'])

        self._canvas = StageCanvas(self, project_root=self._project_root,
                                   screen=self._screen)
        self._canvas._images = self._images
        self._canvas.real_images = self._real
        self._canvas.setFixedSize(STAGE_W, STAGE_H)
        root.addWidget(self._canvas, 0, Qt.AlignCenter)

        # 说话人名条
        self._who = QLabel('')
        self._who.setStyleSheet(
            f"background: {T.t('stage.speakerBg')};"
            f"color: {T.t('stage.text')}; padding: 3px 8px;")
        wf = QFont(theme.font_family(), theme.font_pt('bodyStrong'))
        wf.setBold(True)
        self._who.setFont(wf)
        root.addWidget(self._who)

        # 对话框
        self._dlg = QLabel('')
        self._dlg.setWordWrap(True)
        # **RichText**：台词里的 `{color=…}` / `{b}` / `{i}` 要**画出来**
        # （2026-10-18，审计 P1-8"台词标签不解析"）。从前直接 setText 原文，
        # 预览里看到的是 `{color=#ff0000}红{/color}` 这样的源码。
        self._dlg.setTextFormat(Qt.RichText)
        self._dlg.setMinimumHeight(52)
        self._dlg.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._dlg.setStyleSheet(
            f"background: {T.t('stage.dialogueBg')};"
            f"color: {T.t('stage.text')}; padding: 6px 8px;")
        self._dlg.setFont(QFont(theme.font_family(), theme.font_pt('text')))
        root.addWidget(self._dlg)

        # 本拍变化清单
        self._summary = QLabel('')
        self._summary.setProperty('role', 'caption')
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        # —— 控制条 ——
        bar = QHBoxLayout()
        bar.setSpacing(T.SPACE['xs'])
        self._prev = QPushButton('◀ 上一句')
        self._prev.clicked.connect(self.prev_frame)
        bar.addWidget(self._prev)
        self._play = QPushButton('▶ 播放')
        self._play.setProperty('variant', 'primary')
        self._play.clicked.connect(self.toggle_play)
        bar.addWidget(self._play)
        self._next = QPushButton('下一句 ▶')
        self._next.clicked.connect(self.next_frame)
        bar.addWidget(self._next)

        bar.addSpacing(T.SPACE['md'])
        bar.addWidget(QLabel('跳到第'))
        self._jump = QSpinBox()
        self._jump.setRange(1, max(1, len(self.frames)))
        self._jump.setFixedWidth(80)
        # ⚠ **手输数字也要能跳**（用户 2026-10-05："手动输入那个拍无法直接
        # 跳转到对应的拍场面"）。
        #
        # QSpinBox 的 `valueChanged` 在**敲键盘时**只对上下箭头/滚轮发；
        # 手输一个数字后不按回车就什么都不发生（回车在某些焦点下也不触发），
        # 用户看到的就是"输了没反应"。所以三条都接上：
        #   · `editingFinished` —— 输完点别处/失焦/回车；
        #   · `keyboardTracking(False)` + `valueChanged` —— 输完立刻跳（这是
        #     真正常用的路径）；
        #   · 外加"拍"按钮（原来就有）。
        self._jump.setKeyboardTracking(False)
        self._jump.valueChanged.connect(self._on_jump)
        self._jump.editingFinished.connect(self._on_jump)
        bar.addWidget(self._jump)
        jump_btn = QPushButton('拍')
        jump_btn.clicked.connect(self._on_jump)
        bar.addWidget(jump_btn)

        self._real_cb = QCheckBox('真实贴图')
        self._real_cb.setChecked(True)
        self._real_cb.toggled.connect(self._on_real)
        bar.addWidget(self._real_cb)
        # 声音：默认开（用户 2026-10-05："bgm 未被实际播放"）——
        # 但给一个开关，免得预览一开就出声吓人。
        self._sound_cb = QCheckBox('🔊 声音')
        self._sound_cb.setChecked(True)
        self._sound_cb.setToolTip(
            '按这一拍的 BGM / 音效 / 语音真的放出来（走系统音频设备）。\n'
            'BGM 是持续状态：没换曲就继续放；音效/语音换到那一拍响一次。')
        self._sound_cb.toggled.connect(self._on_sound)
        bar.addWidget(self._sound_cb)
        self._only_cb = QCheckBox('只看有变化的拍')
        self._only_cb.toggled.connect(self._on_only)
        bar.addWidget(self._only_cb)
        bar.addStretch(1)
        self._pos = QLabel('')
        self._pos.setProperty('role', 'caption')
        bar.addWidget(self._pos)
        root.addLayout(bar)

    # ------------------------------------------------------------------

    def show_frame(self, idx: int):
        if not self.frames:
            self._canvas.show_frame(None, -1)
            self._who.setText('')
            self._dlg.setText('（没有可预览的内容）')
            self._summary.setText('')
            self._pos.setText('— / —')
            return
        idx = max(0, min(int(idx), len(self.frames) - 1))
        self.idx = idx
        f = self.frames[idx]
        self._canvas.show_frame(f, idx)
        # 声音：BGM 持续、音效/语音一次性（同一帧重画不重放 —— 见 StageAudio）
        cues = {}
        try:
            cues = self._audio.set_frame(
                f, key=(getattr(f, 'line_id', ''), idx)) or {}
        except Exception as e:                     # noqa: BLE001 - 放不出声不影响画面
            swallow('ui_qt.stage_preview.show_frame', e)
            cues = {}
        self._who.setText(f.speaker or '　')
        self._dlg.setText(_dialogue_html(f))
        self._summary.setText(self._summary_text(f, cues))
        self._pos.setText(f'{idx + 1} / {len(self.frames)}')
        # **按拍定速**（2026-10-18，审计 P1-8"固定 900ms"）：
        # 长台词还没看完就跳、纯舞台变化又干等 —— 时长由这一拍自己说了算
        # （`StageFrame.duration_ms`：pause 秒数 → 字数估算 → 600ms）。
        try:
            self._timer.setInterval(int(getattr(f, 'duration_ms', PLAY_MS)))
        except Exception:                     # noqa: BLE001 - 老 frame 没这个属性
            self._timer.setInterval(PLAY_MS)
        # **转场**（P1-8"无转场"）：`with dissolve/fade` 的那一拍淡入，
        # 硬切的不淡 —— 这样"换景"和"换立绘"在预览里看起来不一样。
        try:
            self._canvas.begin_transition(bool(getattr(f, 'fades', False)))
        except Exception:                     # noqa: BLE001
            swallow('ui_qt.stage_preview.show_frame.transition')
        # **姿势改回普通但没 hide** → 在状态行上说清楚（用户 2026-10-05 报的
        # "从 B 改到 A … 工具跟不上"）。只提示，不改脚本。
        try:
            hint = self._pose_hint(idx)
            if hint:
                self._summary.setText(
                    (self._summary.text() + '　⚠ ' + hint).strip())
        except Exception as e:                     # noqa: BLE001 - 提示失败不影响画面
            swallow('ui_qt.stage_preview.show_frame.pose_hint', e)
        self._jump.setValue(idx + 1)
        self._prev.setEnabled(idx > 0)
        self._next.setEnabled(idx < len(self.frames) - 1)
        # 下一两拍的图先解起来（翻过去就是内存命中，不再有"加载框一闪"）
        self._prefetch_neighbours(idx)

    def _prefetch_neighbours(self, idx: int):
        for step in range(1, PREFETCH_AHEAD + 1):
            j = idx + step
            if j >= len(self.frames):
                break
            try:
                self._canvas.prefetch_frame(self.frames[j])
            except Exception:                 # noqa: BLE001
                swallow('stage_preview._prefetch_neighbours')

    def _summary_text(self, f, cues=None) -> str:
        """
        横条上的说明：本拍变化清单（与「舞台快照」表同一份文字）
        + **缺什么**（缺图 / 缺音频 / 音频是近似匹配）。

        用户 2026-10-05：

        > 这个你确定 bgm 导入了吗？背景导入了吗？是否正确呢？查询一下，
        > 因为我这里没有声音……

        以前"没解析出来"是**静默**的（界面上一片黑、一点声都没有，用户只能猜）。
        现在把原因写在脸上：哪张图没找到、哪个音频没找到、近似匹配到了谁。
        """
        from storygraph.app.stage import frame_summary
        try:
            base = frame_summary(f) if f is not None else ''
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.stage_preview._summary_text', e)
            base = ''
        extra = [str(n) for n in ((cues or {}).get('notes') or ())]
        try:
            miss = self._canvas.missing_assets()
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.stage_preview._summary_text', e)
            miss = []
        if miss:
            extra.append('⚠ 缺图：' + '、'.join(miss))
        note = self._audio.note() if self._sound_cb.isChecked() else ''
        if note:
            extra.append(f'（声音：{note}）')
        if not extra:
            return base
        return (base + '\n' if base else '') + '\n'.join(extra)

    # ------------------------------------------------------------------

    def next_frame(self):
        if self.idx < len(self.frames) - 1:
            self.show_frame(self.idx + 1)
        elif self._playing:
            self.stop_play()

    def prev_frame(self):
        if self.idx > 0:
            self.show_frame(self.idx - 1)

    def pose_warnings(self):
        """
        "姿势改回普通但没 hide"的警告（用户 2026-10-05 报的那条）。

        返回 `[(帧下标, problem), …]`；**只报告**，不改用户脚本
        （自动补 `hide` 是设计文档里的 P4，默认不做）。
        """
        try:
            from storygraph.story.layered_model import frame_pose_warnings
            return frame_pose_warnings(
                self.frames, layer_model_for(self.project_root))
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.stage_preview.pose_warnings', e)
            return []

    def _pose_hint(self, idx: int) -> str:
        """这一拍有没有"改回空姿势但没 hide"的提示（没有就空串）。"""
        cache = getattr(self, '_pose_warn_map', None)
        if cache is None:
            cache = {i: p for i, p in (self.pose_warnings() or [])}
            self._pose_warn_map = cache
        problem = cache.get(int(idx))
        if not problem:
            return ''
        return (f'这一拍想把「{problem.get("from_label")}」改回'
                f'「{problem.get("to_label")}」，但 `show` 改不回去 —— '
                f'要先 `hide {problem.get("tag")}`。')

    def toggle_play(self):
        self.stop_play() if self._playing else self.start_play()

    def start_play(self):
        if len(self.frames) < 2:
            return
        self._playing = True
        self._play.setText('⏸ 暂停')
        self._timer.start()

    def stop_play(self):
        self._playing = False
        self._play.setText('▶ 播放')
        self._timer.stop()

    def _on_jump(self):
        """
        跳到输入框里那一拍。

        ⚠ `_jump.setValue()`（`show_frame` 里同步显示用）会**反过来**
        触发 `valueChanged` → 又调到这里。目标拍与当前拍相同时**什么都不做**
        （幂等），递归自然断掉 —— 不加这一句就是无限递归。
        """
        target = int(self._jump.value()) - 1
        if target == int(getattr(self, 'idx', -1)):
            return
        self.show_frame(target)

    def _on_real(self, checked: bool):
        self._canvas.real_images = bool(checked)
        self.show_frame(self.idx)

    def _on_sound(self, checked: bool):
        """
        声音开关：开=开始按拍放；关=立刻停（不残留上一首 BGM）。

        注意：**关掉声音不等于"没有缺资源"** —— 所以这一拍该有的提示
        （缺文件 / 近似匹配）照样算出来写在横条上：那是脚本的问题，
        跟这个开关无关（用户 2026-10-05 就是靠这条才发现 BGM 名字对不上）。
        """
        self._audio.set_enabled(bool(checked))
        if not self.frames:
            return
        f = self.frames[self.idx]
        if checked:
            try:                              # 重新放当前这一拍该有的声音
                cues = self._audio.set_frame(f, key=None) or {}
            except Exception as e:                 # noqa: BLE001
                swallow('ui_qt.stage_preview._on_sound', e)
                cues = {}
        else:
            cues = self._cues_for(f)
        self._summary.setText(self._summary_text(f, cues))

    def _cues_for(self, f) -> dict:
        """**只算**这一拍的声音提示（不放声）—— 给横条显示用。"""
        try:
            return stage_audio_cues(f, self._registry, self._project_root) or {}
        except Exception as e:                     # noqa: BLE001
            swallow('ui_qt.stage_preview._cues_for', e)
            return {}

    def _on_only(self, checked: bool):
        """
        「只看有变化的拍」：在几百拍里只留有真正变化的那几拍。

        切回来时要能从原始列表重建 —— 所以永远保留 `self._all`。
        """
        self._only_changed = bool(checked)
        if self._only_changed:
            self.frames = [f for f in self._all if self._has_change(f)]
            if not self.frames:
                self.frames = list(self._all)
        else:
            self.frames = list(self._all)
        self._jump.setRange(1, max(1, len(self.frames)))
        self.show_frame(0)

    @staticmethod
    def _has_change(f) -> bool:
        from storygraph.app.stage import frame_summary
        try:
            return bool(frame_summary(f))
        except Exception as e:                    # noqa: BLE001
            swallow('ui_qt.stage_preview._has_change', e)
            return True

    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Right:
            self.next_frame()
        elif key == Qt.Key_Left:
            self.prev_frame()
        elif key == Qt.Key_Space:
            self.toggle_play()
        elif key == Qt.Key_Home:
            self.show_frame(0)
        elif key == Qt.Key_End:
            self.show_frame(len(self.frames) - 1)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop_play()
        try:
            self._audio.stop()             # 关窗必须停声（否则 BGM 会一直响）
        except Exception:                   # noqa: BLE001
            swallow('stage_preview.closeEvent')
        try:
            self._canvas.shutdown()
        except Exception:                    # noqa: BLE001
            swallow('stage_preview.closeEvent')
        super().closeEvent(event)


def _dialogue_html(frame) -> str:
    """
    这一拍的台词 → 预览用的 HTML（**解析 Ren'Py 文字标签**）。

    2026-10-18，审计 P1-8："台词标签不解析" —— 从前 `QLabel.setText(原文)`，
    预览里看到的是 `{color=#ff0000}红{/color} 色的字` 这样的**源码**。

    转换器与卡片预览**同一份**（`main_window.renpy_text_to_html`）：
    两处各写一套迟早漂移（一处认 `{size}`、另一处不认，同一句话在卡片上
    和预览里长得不一样）。只多一步 `\\n → <br>`：QLabel 的富文本不认换行。
    """
    text = str(getattr(frame, 'text', '') or '')
    if not text:
        return '（这一拍没有台词）'
    try:
        from storygraph.ui_qt.main_window import renpy_text_to_html
        html, _has = renpy_text_to_html(text)
    except Exception as e:               # noqa: BLE001 - 转不了就显示原文
        swallow('ui_qt.stage_preview._dialogue_html', e)
        return text
    return html.replace('\n', '<br>')


def open_preview(parent, frames, registry=None, project_root=None,
                 real_images: bool = True, title: str = '舞台演出预览',
                 sound: bool = True):
    """打开预览窗口（非模态，开着也能回去改卡片）。"""
    win = StagePreviewWindow(parent, frames, title=title, registry=registry,
                             project_root=project_root,
                             real_images=real_images, sound=sound)
    win.show()
    return win


__all__ = ['StageAudio', 'StageCanvas', 'StagePreviewWindow', 'open_preview']
