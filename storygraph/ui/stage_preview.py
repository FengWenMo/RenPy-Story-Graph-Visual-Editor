"""
storygraph.ui.stage_preview
===========================
tkinter 版**舞台演出预览窗口**（只管画与控制）。

2026-10-01 拆分：舞台推导（`transform_x` / `build_frames` /
`resolve_frame_assets` …）全部搬去 `storygraph.app.stage`（框架无关）。
本模块只剩：

  · `StagePreviewWindow` —— tkinter 的画布 + 播放控制；
  · **兼容门面**：把 `app.stage` 的名字 re-export 出来，
    老的 `from storygraph.ui.stage_preview import build_frames` 继续可用。

Qt 前端请直接用 `storygraph.app.stage`，**不必 import tkinter**。
"""

from __future__ import annotations

import tkinter as tk

from storygraph.ui import tokens as T


# ===================== 兼容门面：舞台推导的唯一真源在 app.stage =====================
#
# 这里只是把名字搬出来（不是第二套实现）。前端与测试都应当直接
# import `storygraph.app.stage`；保留绑定只为不破坏既有 import。
from storygraph.app import stage as _stage

CENTER_X = _stage.CENTER_X
_POSITION_X = _stage._POSITION_X
_LETTER_X = _stage._LETTER_X
SpriteLayout = _stage.SpriteLayout
StageFrame = _stage.StageFrame
transform_x = _stage.transform_x
build_frames = _stage.build_frames
frame_summary = _stage.frame_summary
resolve_stage_asset = _stage.resolve_stage_asset
_SOLID_RE = _stage._SOLID_RE
resolve_solid_color = _stage.resolve_solid_color
_path_from_rel = _stage._path_from_rel
image_index = _stage.image_index
image_index_lookup = _stage.image_index_lookup
resolve_frame_assets = _stage.resolve_frame_assets
SPRITE_H_RATIO = _stage.SPRITE_H_RATIO
SPRITE_MAX = _stage.SPRITE_MAX
_screen_x = _stage._screen_x
# 窗口自己的画布/方块尺寸（原文件的模块级常量，不属于推导层）
STAGE_W, STAGE_H = _stage.STAGE_W, _stage.STAGE_H
BLOCK_W, BLOCK_H = _stage.BLOCK_W, _stage.BLOCK_H


class StagePreviewWindow(tk.Toplevel):
    """
    舞台演出预览窗口：把这一章按卡片效果"播"出来。

    能力（对齐用户诉求）：
      · 逐帧播放 / 暂停 / 上一句 / 下一句 / 跳到某拍；
      · **真实贴图**：背景 + 立绘（异步解码），缺图自动降级成方块；
      · 画面：按 transform 推出的站位 + 说话人高亮 + 说话人名条；
      · 底栏：这一拍的变化清单（与「舞台快照」表同一份文字）；
      · 「只看有变化的拍」——几百拍里快速过一遍真正切了景/换了人的地方。
    """

    WIDTH = STAGE_W
    HEIGHT = STAGE_H
    PLAY_MS = 900          # 自动播放间隔；节奏仅示意，不代表真实时长

    def __init__(self, parent, frames: list, title: str = '舞台演出预览',
                 registry=None, project_root=None, real_images: bool = True):
        super().__init__(parent)
        self.frames = list(frames or [])
        self.idx = 0
        self._playing = False
        self._after_id = None
        self._images = getattr(registry, 'images', {}) or {}
        self._project_root = project_root
        self._real = bool(real_images)
        self._photos: dict = {}          # 持有引用，否则 PhotoImage 被 GC → 图变空白
        self._loader = None              # 惰性建（没画过图就不建线程）
        self.title(title)
        self._build()
        self.show_frame(0)

    # ---------- 界面 ----------

    def _build(self):
        self._canvas = tk.Canvas(self, width=self.WIDTH, height=self.HEIGHT,
                                 bg=T.t('stage.letterbox'), highlightthickness=0)
        self._canvas.pack(padx=8, pady=(8, 0))

        # 说话人名条 + 对话框（分开两块：名条是"谁"，对话框是"说什么"）
        self._who_var = tk.StringVar(value='')
        tk.Label(self, textvariable=self._who_var, anchor='w',
                 bg=T.t('stage.speakerBg'), fg=T.t('stage.text'),
                 font=T.FONTS['bodyStrong'], padx=8, pady=2
                 ).pack(fill='x', padx=8)
        self._dlg_var = tk.StringVar(value='')
        tk.Label(self, textvariable=self._dlg_var, wraplength=self.WIDTH - 20,
                 justify='left', anchor='w', bg=T.t('stage.dialogueBg'),
                 fg=T.t('stage.text'), font=T.FONTS['text'], padx=8, pady=6
                 ).pack(fill='x', padx=8)

        # 状态栏（本拍变化 + 缺图提示）
        self._status_var = tk.StringVar(value='')
        tk.Label(self, textvariable=self._status_var, wraplength=self.WIDTH - 20,
                 justify='left', anchor='w', fg=T.t('text.tertiary'),
                 font=T.FONTS['caption']).pack(fill='x', padx=8, pady=(2, 0))

        bar = tk.Frame(self)
        bar.pack(fill='x', padx=8, pady=8)
        tk.Button(bar, text='◀ 上一句',
                  command=self.prev_frame).pack(side='left')
        self._play_btn = tk.Button(bar, text='▶ 播放', command=self.toggle_play)
        self._play_btn.pack(side='left', padx=(T.SPACE['xs'], 0))
        tk.Button(bar, text='下一句 ▶',
                  command=self.next_frame).pack(side='left',
                                                padx=(T.SPACE['xs'], 0))
        tk.Label(bar, text='跳到第').pack(side='left', padx=(T.SPACE['md'], 2))
        self._jump_var = tk.StringVar(value='1')
        tk.Entry(bar, textvariable=self._jump_var, width=5
                 ).pack(side='left')
        tk.Button(bar, text='拍', command=self._jump).pack(side='left')
        self._real_var = tk.BooleanVar(value=self._real)
        if hasattr(bar, 'winfo_children'):
            tk.Checkbutton(bar, text='真实贴图', variable=self._real_var,
                           command=self._toggle_real).pack(
                               side='left', padx=(T.SPACE['sm'], 0))
            self._only_var = tk.BooleanVar(value=False)
            tk.Checkbutton(bar, text='只看有变化的拍',
                           variable=self._only_var, command=self._retarget
                           ).pack(side='left', padx=(T.SPACE['xs'], 0))
        self._pos_var = tk.StringVar(value='')
        tk.Label(bar, textvariable=self._pos_var,
                 font=T.FONTS['caption'],
                 fg=T.t('text.tertiary')).pack(side='right')
        if hasattr(self, 'bind'):
            self.bind('<Left>', lambda e: self.prev_frame())
            self.bind('<Right>', lambda e: self.next_frame())
            self.bind('<space>', lambda e: self.toggle_play())
            self.bind('<Home>', lambda e: self.show_frame(0))
            self.bind('<End>', lambda e: self.show_frame(len(self.frames) - 1))

    # ---------- 素材加载 ----------

    def _ensure_loader(self):
        if self._loader is None:
            from storygraph.ui.thumb_loader import ThumbLoader
            self._loader = ThumbLoader(self, max_size=SPRITE_MAX,
                                       project_root=self._project_root)
        return self._loader

    def _request(self, slot, path, apply, size=None):
        """
        异步取一张图。**当前帧已经换了就丢弃结果** ——
        否则快速翻页时，上一帧的图会贴到这一帧上（"预览在骗人"）。
        """
        try:
            self._ensure_loader().request(slot, path, apply, max_size=size)
        except Exception:                    # noqa: BLE001 - 桩环境
            pass

    def shutdown(self):
        """关窗时收线程池（不然进程退出要等它）。"""
        loader, self._loader = self._loader, None
        if loader is not None:
            try:
                loader.shutdown()
            except Exception:                # noqa: BLE001
                pass

    def destroy(self):
        self.stop_play()
        self.shutdown()
        super().destroy()

    # ---------- 绘制 ----------

    def _draw_block(self, c, s, base_y, ph_index=None):
        """示意图方块（真实贴图不可用时的降级，也让"台上有人"看得出来）。"""
        x = _screen_x(s.x, self.WIDTH)
        fill = T.t('stage.speaking') if s.speaking else T.t('stage.placeholder')
        outline = (T.t('stage.speakingOutline') if s.speaking
                   else T.t('stage.placeholderOutline'))
        width = 3 if s.speaking else 1
        tags = ('ui',) if ph_index is None else ('ui', f'ph{ph_index}')
        c.create_rectangle(x - BLOCK_W // 2, base_y - BLOCK_H,
                           x + BLOCK_W // 2, base_y,
                           fill=fill, outline=outline, width=width, tags=tags)
        c.create_text(x, base_y - BLOCK_H // 2, fill=T.t('stage.text'),
                      font=T.FONTS['caption'], text=s.label,
                      width=BLOCK_W - 8, tags=tags)
        c.create_text(x, base_y + 2, anchor='n', fill=T.t('stage.textDim'),
                      font=T.FONTS['caption'],
                      text=(s.tag + (f' @ {s.at}' if s.at else '')), tags=tags)

    def _raise_ui(self):
        """文字/角标永远在最上层（异步贴图是后画的，会盖住它们）。"""
        try:
            self._canvas.tag_raise('ui')
        except Exception:                    # noqa: BLE001
            pass

    def show_frame(self, idx: int):
        if not self.frames:
            return
        idx = max(0, min(idx, len(self.frames) - 1))
        self.idx = idx
        f = self.frames[idx]
        c = self._canvas
        c.delete('all')
        # 只持有**当前帧**的图片引用（否则 305 拍 × 大图会吃光内存）；
        # 换帧后重新取图会命中加载器的内存缓存，是瞬时的。
        self._photos.clear()
        w, h = self.WIDTH, self.HEIGHT

        c.create_rectangle(0, 0, w, h, fill=T.t('stage.letterbox'), outline='')

        assets = resolve_frame_assets(
            f, self._images, self._project_root,
            image_index(self._project_root) if self._real else None)
        base_y = h - 18

        # 背景：真图 → 纯色（`scene black`）→ 明确写出名字（不留一片黑）
        bg_color = resolve_solid_color(f.scene, self._images)
        if assets['bg'] is not None and self._real:
            self._request(('bg', idx), str(assets['bg']),
                          self._on_bg(idx), size=(w, h))
        elif bg_color:
            c.create_rectangle(0, 0, w, h, fill=bg_color, outline='')
        elif f.scene:
            c.create_text(10, 12, anchor='nw', fill=T.t('stage.label'),
                          font=T.FONTS['caption'], text=f'背景：{f.scene}',
                          tags=('ui',))

        # 立绘：先都摆占位块（绝不出现"空白等图"），真图到了替换那一块
        for i, (s, path) in enumerate(assets['sprites']):
            self._draw_block(c, s, base_y, ph_index=i)
            if path is not None and self._real:
                self._request(('sp', idx, i), str(path), self._on_sprite(idx, i, s),
                              size=(SPRITE_MAX[0], int(h * SPRITE_H_RATIO)))

        if not f.sprites:
            c.create_text(w // 2, h // 2, fill=T.t('stage.textDim'),
                          font=T.FONTS['body'], text='（台上没人）', tags=('ui',))

        # 非角色图层（CG 等）在最上面
        layer_color = resolve_solid_color(f.layers, self._images)
        if assets['layers'] is not None and self._real:
            self._request(('layer', idx), str(assets['layers']),
                          self._on_layer(idx), size=(w, h))
        elif layer_color and self._real:
            c.create_rectangle(0, 0, w, h, fill=layer_color, outline='')
        elif f.layers:
            c.create_text(w - 10, 12, anchor='ne', fill=T.t('stage.labelAlt'),
                          font=T.FONTS['caption'], text=f'图层：{f.layers}',
                          tags=('ui',))

        # 音频状态
        audio = []
        if f.music:
            audio.append(f'♪ {f.music}')
        if f.sound:
            audio.append(f'♬ {f.sound}')
        if f.voice:
            audio.append(f'🎙 {f.voice}')
        if audio:
            c.create_text(10, h - 4, anchor='sw', fill=T.t('stage.textDim'),
                          font=T.FONTS['caption'], text='  '.join(audio),
                          tags=('ui',))

        # 文字区
        self._who_var.set(f.speaker or '（旁白）')
        self._dlg_var.set(f.dialogue)
        bits = [f.changes.replace('\n', '　|　') if f.changes else '（本拍无变化）']
        if assets['missing']:
            bits.append('⚠ 找不到图：' + '、'.join(sorted(set(assets['missing']))))
        self._status_var.set('　'.join(bits))
        self._pos_var.set(f'{idx + 1} / {len(self.frames)}　{f.line_id}')
        jv = getattr(self, '_jump_var', None)
        if jv is not None and hasattr(jv, 'set'):
            jv.set(str(idx + 1))

    # 异步回调：**只贴"还是当前这一帧"的图**（防止快速翻页贴错）

    def _on_bg(self, idx):
        def apply(photo, _path):
            if photo is None or idx != self.idx:
                return
            self._photos[('bg', idx)] = photo
            try:
                self._canvas.create_image(self.WIDTH // 2, self.HEIGHT // 2,
                                          image=photo, tags=('bgimg',))
                self._canvas.tag_lower('bgimg')
                self._raise_ui()
            except Exception:                # noqa: BLE001
                pass
        return apply

    def _on_layer(self, idx):
        def apply(photo, _path):
            if photo is None or idx != self.idx:
                return
            self._photos[('layer', idx)] = photo
            try:
                self._canvas.create_image(self.WIDTH // 2, self.HEIGHT // 2,
                                          image=photo, tags=('layerimg',))
                self._raise_ui()
            except Exception:                # noqa: BLE001
                pass
        return apply

    def _on_sprite(self, idx, i, sprite):
        """
        立绘到位：**只把那一块占位替换掉**。

        早先这里是"重新整帧渲染"，于是刚存进 `_photos` 的引用被 `clear()`
        清掉 → 图永远贴不上（实测画布上 image 元素恒为 0）。
        """
        def apply(photo, _path):
            if photo is None or idx != self.idx:
                return
            key = ('sprite', sprite.image, round(sprite.x, 4), i, idx)
            self._photos[key] = photo
            c = self._canvas
            try:
                c.delete(f'ph{i}')
                x = _screen_x(sprite.x, self.WIDTH)
                c.create_image(x, self.HEIGHT - 18, anchor='s', image=photo,
                               tags=('sprimg',))
                if sprite.speaking:
                    c.create_text(x, self.HEIGHT - 16, anchor='n',
                                  fill=T.t('stage.speakingOutline'),
                                  font=T.FONTS['captionStrong'], text='▶ 说话中',
                                  tags=('ui',))
                self._raise_ui()
            except Exception:                # noqa: BLE001
                pass
        return apply

    # ---------- 播放控制 ----------

    def _visible_indices(self) -> list:
        """
        当前"可以翻到"的帧序号（「只看有变化的拍」会把它筛掉一批）。

        为什么值得做：一章几百拍，真正切景/换人/换音乐的只有几十拍。
        逐句翻太慢，按序号跳又不知道跳到哪 —— 筛出来最省事。
        """
        only = getattr(self, '_only_var', None)
        if only is None or not getattr(only, 'get', lambda: False)():
            return list(range(len(self.frames)))
        idxs = [i for i, f in enumerate(self.frames) if (f.changes or '').strip()]
        return idxs or list(range(len(self.frames)))

    def _retarget(self):
        """切换筛选后，停在最近的一个可见帧上（不跳回开头）。"""
        visible = self._visible_indices()
        if not visible:
            return
        self.show_frame(min(visible, key=lambda i: abs(i - self.idx)))

    def next_frame(self):
        visible = self._visible_indices()
        after = [i for i in visible if i > self.idx]
        if after:
            self.show_frame(after[0])

    def prev_frame(self):
        visible = self._visible_indices()
        before = [i for i in visible if i < self.idx]
        if before:
            self.show_frame(before[-1])

    def _toggle_real(self):
        v = getattr(self, '_real_var', None)
        if v is not None and hasattr(v, 'get'):
            self._real = bool(v.get())
        self.show_frame(self.idx)

    def _jump(self):
        jv = getattr(self, '_jump_var', None)
        if jv is None or not hasattr(jv, 'get'):
            return
        try:
            n = int((jv.get() or '1').strip())
        except ValueError:
            return
        self.show_frame(n - 1)

    def toggle_play(self):
        if self._playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        if not self.frames:
            return
        self._playing = True
        btn = getattr(self, '_play_btn', None)
        if btn is not None and hasattr(btn, 'config'):
            btn.config(text='⏸ 暂停')
        self._tick()

    def stop_play(self):
        self._playing = False
        btn = getattr(self, '_play_btn', None)
        if btn is not None and hasattr(btn, 'config'):
            btn.config(text='▶ 播放')
        if self._after_id is not None and hasattr(self, 'after_cancel'):
            try:
                self.after_cancel(self._after_id)
            except Exception:                # noqa: BLE001
                pass
        self._after_id = None

    def _tick(self):
        if not self._playing:
            return
        visible = self._visible_indices()
        after = [i for i in visible if i > self.idx]
        if not after:
            self.stop_play()
            return
        self.show_frame(after[0])
        if hasattr(self, 'after'):
            self._after_id = self.after(self.PLAY_MS, self._tick)