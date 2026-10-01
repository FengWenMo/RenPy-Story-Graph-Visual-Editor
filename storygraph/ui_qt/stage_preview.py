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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from storygraph.app.stage import (BLOCK_H, BLOCK_W, SPRITE_H_RATIO, SPRITE_MAX,
                                  STAGE_H, STAGE_W, image_index,
                                  resolve_frame_assets, resolve_solid_color)
from storygraph.ui import tokens as T
from storygraph.ui_qt import theme
from storygraph.ui_qt.thumb_loader import ThumbLoader

PLAY_MS = 900          # 自动播放间隔；节奏仅示意，不代表真实时长


class StageCanvas(QWidget):
    """
    舞台画布：一帧的画面。

    绘制顺序 = 真实游戏的层级：背景 → 立绘 → 非角色图层 → 说话人角标。
    """

    def __init__(self, parent=None, project_root=None, real_images=True):
        super().__init__(parent)
        self.setMinimumSize(STAGE_W // 2, STAGE_H // 2)
        self.setAutoFillBackground(False)
        self.project_root = project_root
        self.real_images = real_images
        self._images: dict = {}
        self._loader = ThumbLoader(self, max_size=SPRITE_MAX,
                                   project_root=project_root)
        self._frame = None
        self._frame_idx = -1
        self._bg_pix = None
        #: 当前帧的立绘贴图：{序号: QPixmap}
        self._sprites: dict = {}
        self._layer_pix = None

    # ------------------------------------------------------------------

    def set_registry(self, registry):
        self._images = getattr(registry, 'images', {}) or {}

    def shutdown(self):
        try:
            self._loader.shutdown()
        except Exception:                    # noqa: BLE001
            pass

    # ------------------------------------------------------------------

    def show_frame(self, frame, idx: int):
        """切到某一帧：**先清空旧的贴图**，再异步取新的。"""
        self._frame = frame
        self._frame_idx = idx
        self._bg_pix = None
        self._layer_pix = None
        self._sprites = {}
        self.update()
        if frame is None or not self.real_images:
            return
        assets = resolve_frame_assets(
            frame, self._images, self.project_root,
            image_index(self.project_root))
        w, h = self.width(), self.height()
        if assets.get('bg') is not None:
            self._request(('bg', idx), str(assets['bg']), self._on_bg,
                          (STAGE_W, STAGE_H))
        for i, (s, path) in enumerate(assets.get('sprites') or []):
            if path is not None:
                self._request(('sp', idx, i), str(path),
                              self._on_sprite(i),
                              (SPRITE_MAX[0], int(STAGE_H * SPRITE_H_RATIO)))
        if assets.get('layers') is not None:
            self._request(('layer', idx), str(assets['layers']), self._on_layer,
                          (STAGE_W, STAGE_H))
        _ = (w, h)

    def _request(self, slot, path, apply, size):
        self._loader.request(slot, path, apply, max_size=size)

    def _on_bg(self, pixmap, _path):
        self._bg_pix = pixmap
        self.update()

    def _on_layer(self, pixmap, _path):
        self._layer_pix = pixmap
        self.update()

    def _on_sprite(self, i):
        def apply(pixmap, _path):
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
            w, h = self.width(), self.height()
            f = self._frame

            # 1) 背景：真图 → 纯色 → 写出名字
            if self._bg_pix is not None:
                p.drawPixmap(0, 0, w, h, self._bg_pix)
            else:
                color = resolve_solid_color(getattr(f, 'scene', ''),
                                            self._images) if f else None
                p.fillRect(0, 0, w, h,
                           QColor(color) if color else QColor(T.t('stage.letterbox')))
                if f is not None and f.scene:
                    p.setPen(QPen(QColor(T.t('stage.label'))))
                    p.setFont(QFont(theme.font_family(),
                                    theme.font_pt('caption')))
                    p.drawText(10, 18, f'背景：{f.scene}')

            # 2) 立绘：真图逐张；没有就占位块
            sprites = getattr(f, 'sprites', []) if f else []
            base_y = h - 14
            for i, s in enumerate(sprites):
                pix = self._sprites.get(i)
                x = int(max(0.0, min(1.0, s.x)) * w)
                if pix is not None:
                    ph = int(h * SPRITE_H_RATIO)
                    pw = int(pix.width() * ph / max(1, pix.height()))
                    p.drawPixmap(x - pw // 2, base_y - ph, pw, ph, pix)
                    if s.speaking:
                        p.setPen(QPen(QColor(T.t('stage.speakingOutline')), 3))
                        p.drawRect(x - pw // 2, base_y - ph, pw, ph)
                else:
                    self._draw_block(p, s, x, base_y)

            if f is not None and not sprites:
                p.setPen(QPen(QColor(T.t('stage.textDim'))))
                p.setFont(QFont(theme.font_family(), theme.font_pt('body')))
                p.drawText(0, 0, w, h, Qt.AlignCenter, '（台上没人）')

            # 3) 非角色图层（CG 等）在最上面
            if self._layer_pix is not None:
                p.drawPixmap(0, 0, w, h, self._layer_pix)
            elif f is not None and f.layers:
                color = resolve_solid_color(f.layers, self._images)
                if color:
                    p.fillRect(0, 0, w, h, QColor(color))
                else:
                    p.setPen(QPen(QColor(T.t('stage.labelAlt'))))
                    p.setFont(QFont(theme.font_family(),
                                    theme.font_pt('caption')))
                    p.drawText(w - 10, 18, f'图层：{f.layers}')
        except Exception:                    # noqa: BLE001 - paint 里绝不外抛
            pass
        finally:
            p.end()

    def _draw_block(self, p, s, x, base_y):
        """占位块：真图不可用时也要让"台上有人"看得出来。"""
        speaking = bool(getattr(s, 'speaking', False))
        fill = QColor(T.t('stage.speaking') if speaking
                      else T.t('stage.placeholder'))
        outline = QColor(T.t('stage.speakingOutline') if speaking
                         else T.t('stage.placeholderOutline'))
        bw = BLOCK_W // 2
        p.setBrush(fill)
        p.setPen(QPen(outline, 3 if speaking else 1))
        p.drawRect(x - bw, base_y - BLOCK_H, BLOCK_W, BLOCK_H)
        p.setPen(QPen(QColor(T.t('stage.text'))))
        p.setFont(QFont(theme.font_family(), theme.font_pt('caption')))
        label = getattr(s, 'label', '') or ''
        p.drawText(x - bw + 4, base_y - BLOCK_H + 4, BLOCK_W - 8, BLOCK_H - 8,
                   Qt.AlignCenter | Qt.TextWordWrap, label)
        dim = getattr(s, 'tag', '') + (f' @ {s.at}' if getattr(s, 'at', '')
                                       else '')
        p.setPen(QPen(QColor(T.t('stage.textDim'))))
        p.drawText(x - bw, base_y + 2, BLOCK_W, 16, Qt.AlignHCenter, dim)


class StagePreviewWindow(QDialog):
    """
    舞台演出预览窗口。

    能力：逐帧播放 / 暂停 / 上一句 / 下一句 / 跳到某拍 /
    「只看有变化的拍」/「真实贴图」。
    """

    def __init__(self, parent=None, frames=None, title: str = '舞台演出预览',
                 registry=None, project_root=None, real_images: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        theme.apply_to(self)
        self.frames = list(frames or [])
        self._all = list(frames or [])
        self.idx = 0
        self._playing = False
        self._images = getattr(registry, 'images', {}) or {}
        self._project_root = project_root
        self._real = bool(real_images)
        self._only_changed = False
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

        self._canvas = StageCanvas(self, project_root=self._project_root)
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
        bar.addWidget(self._jump)
        jump_btn = QPushButton('拍')
        jump_btn.clicked.connect(self._on_jump)
        bar.addWidget(jump_btn)

        self._real_cb = QCheckBox('真实贴图')
        self._real_cb.setChecked(True)
        self._real_cb.toggled.connect(self._on_real)
        bar.addWidget(self._real_cb)
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
        self._who.setText(f.speaker or '　')
        self._dlg.setText(f.text or '（这一拍没有台词）')
        self._summary.setText(self._summary_text(f))
        self._pos.setText(f'{idx + 1} / {len(self.frames)}')
        self._jump.setValue(idx + 1)
        self._prev.setEnabled(idx > 0)
        self._next.setEnabled(idx < len(self.frames) - 1)

    def _summary_text(self, f) -> str:
        """本拍变化清单（与「舞台快照」表同一份文字）。"""
        from storygraph.app.stage import frame_summary
        try:
            return frame_summary(f)
        except Exception:                    # noqa: BLE001
            return ''

    # ------------------------------------------------------------------

    def next_frame(self):
        if self.idx < len(self.frames) - 1:
            self.show_frame(self.idx + 1)
        elif self._playing:
            self.stop_play()

    def prev_frame(self):
        if self.idx > 0:
            self.show_frame(self.idx - 1)

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
        self.show_frame(self._jump.value() - 1)

    def _on_real(self, checked: bool):
        self._canvas.real_images = bool(checked)
        self.show_frame(self.idx)

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
        except Exception:                    # noqa: BLE001
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
            self._canvas.shutdown()
        except Exception:                    # noqa: BLE001
            pass
        super().closeEvent(event)


def open_preview(parent, frames, registry=None, project_root=None,
                 real_images: bool = True, title: str = '舞台演出预览'):
    """打开预览窗口（非模态，开着也能回去改卡片）。"""
    win = StagePreviewWindow(parent, frames, title=title, registry=registry,
                             project_root=project_root,
                             real_images=real_images)
    win.show()
    return win


__all__ = ['StageCanvas', 'StagePreviewWindow', 'open_preview']
