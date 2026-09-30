"""
storygraph.ui.sprite_card —— 立绘卡（SpriteCard）与添加卡（AddCard）

为什么重做（2026-09-29 设计评审结论）
-------------------------------------
旧实现是"一个立绘 = 一整行 grid"：图像名输入框 + 150×210 缩略图 + 🔍 +
表情框 + 位置框 + 删除，**行高由缩略图决定（≈210px）**。三个立绘就 630px，
把面板顶穿、底部翻页被挤出可视区；而且表情/位置在**面板级**和**行内**
各有一套入口，多立绘时用户不知道自己在改谁。

新实现把"一张立绘"变成一张**卡片**（100×176）：

    ┌──────────────┐
    │  [图 88×118] │  ← 尺寸恒定（骨架同尺寸，图晚到也不跳）
    │  ling   ▶说话 │
    │  [开心] [左]  │  ← 点 chip 直接换（1 次点击）
    └──────────────┘
     悬停时右上角出现 ✕

网格排布后，3 个立绘只占 1 行 ≈176px；表情/位置**只在卡内**编辑，
面板级不再有同名输入框（单一入口）。

实现说明：tkinter 没有圆角，所以卡片用 `Canvas` + 平滑多边形画圆角，
事件用 `tag_bind` 绑在 Canvas item 上（不用嵌套 Frame，避免布局抖动）。
"""
from __future__ import annotations

import tkinter as tk

from storygraph.ui import tokens as T


def round_points(x1, y1, x2, y2, r):
    """圆角矩形的多边形顶点（配合 `create_polygon(smooth=True)` 使用）。"""
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def ellipsize(text: str, limit: int) -> str:
    """超长截断（保留尾部信息用不上，名字看重的是开头）。"""
    text = str(text or '')
    return text if len(text) <= limit else text[:max(1, limit - 1)] + '…'


class SpriteCard(tk.Canvas):
    """
    一张立绘卡。**只负责显示与上报点击**，改数据由面板处理
    （卡片不碰 `beat.show`，避免视图层与数据层耦合）。
    """

    def __init__(self, parent, loader, image_var, expr_var, pos_var, *,
                 on_select=None, on_open_picker=None, on_delete=None,
                 on_cycle_expression=None, on_cycle_position=None,
                 speaking=False, compact=False, path_resolver=None):
        self.geo = T.CARD_COMPACT if compact else T.CARD
        super().__init__(parent, width=self.geo['w'], height=self.geo['h'],
                         highlightthickness=0, bd=0,
                         background=T.t('bg.canvas'),
                         takefocus=1)
        self.loader = loader
        self.image_var = image_var
        self.expr_var = expr_var
        self.pos_var = pos_var
        self._on_select = on_select
        self._on_open_picker = on_open_picker
        self._on_delete = on_delete
        self._on_cycle_expression = on_cycle_expression
        self._on_cycle_position = on_cycle_position
        #: 图像名 → 磁盘路径的解析回调（由面板注入，见 thumb_loader）
        self._path_resolver = path_resolver

        self._speaking = bool(speaking)
        self._selected = False
        self._hover = False
        self._photo = None                  # 必须留引用，否则图被 GC
        self._state = 'loading'             # loading / ok / missing
        self._message = ''
        self._warn = ''                     # 图区上沿的警告（如"本拍出现 2 次"）
        self._expressions = []
        self._positions = []

        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        # 注意是 `_draw`（早先这里是 `_redraw`——那个方法名不存在，
        # 一旦卡片拿到焦点就 AttributeError，属于自伤的隐藏 bug）
        self.bind('<FocusIn>', lambda e: self._draw())
        self.bind('<FocusOut>', lambda e: self._draw())
        # 悬停提示：名字被截断时看全名，顺带说明预览状态
        from storygraph.ui.tooltip import Tooltip
        self._tip = Tooltip(self)
        self._tip.set_provider(self._tip_text)
        #: 键盘在「表情 / 位置」两个 chip 之间游走（-1 = 没停在 chip 上）
        self._chip_focus = -1
        self.bind('<FocusOut>', lambda e: self._reset_chip_focus())
        for seq, handler in (('<Left>', lambda e: self._move_chip(-1)),
                             ('<Right>', lambda e: self._move_chip(1)),
                             ('<Return>', lambda e: self._activate_chip()),
                             ('<space>', lambda e: self._activate_chip()),
                             ('<Delete>', lambda e: self._delete_self())):
            try:
                self.bind(seq, handler)
            except Exception:                # noqa: BLE001 - 桩环境
                pass
        self._draw()
        self.refresh_thumb()

    # ---------- chip 的键盘操作 ----------

    def _reset_chip_focus(self):
        self._chip_focus = -1
        self._draw()

    def _chip_slots(self) -> list:
        """当前**有内容**的 chip 槽位（空 chip 不参与键盘游走）。"""
        slots = []
        if self._expr_text():
            slots.append('expr')
        if self._pos_text():
            slots.append('pos')
        return slots

    def _move_chip(self, delta):
        """
        `←` `→` 在 chip 之间移动。

        为什么值得做：换表情是这个界面最高频的动作之一，手不离开键盘
        能省下大量"摸鼠标"的时间；同时也是无障碍的一块（键盘可完成主干操作）。
        """
        slots = self._chip_slots()
        if not slots:
            return 'break'
        self._chip_focus = (0 if self._chip_focus < 0
                            else (self._chip_focus + delta) % len(slots))
        self._draw()
        return 'break'

    def _activate_chip(self):
        """`Enter` / `空格`：切换当前聚焦的 chip（等同点它一下）。"""
        slots = self._chip_slots()
        if not slots or self._chip_focus < 0:
            return 'break'
        kind = slots[min(self._chip_focus, len(slots) - 1)]
        if kind == 'expr' and self._on_cycle_expression:
            self._on_cycle_expression()
        elif kind == 'pos' and self._on_cycle_position:
            self._on_cycle_position()
        return 'break'

    def _delete_self(self):
        if self._on_delete:
            self._on_delete()
        return 'break'

    def _tip_text(self) -> str:
        """
        卡片悬停提示：一次把三件事说清楚。

          · **全名**：名字条会截断（超 12 字），长名看不全
          · **预览状态**：还在加载 / 找不到文件（用户不用猜"为什么没图"）
          · 出场用的**真实属性**（chip 上显示的是策划词）
        """
        spec = ''
        try:
            spec = (self.image_var.get() or '').strip()
        except Exception:                    # noqa: BLE001
            spec = ''
        if not spec:
            return '这一格还没有立绘：双击卡片，或点选图按钮挑一张'
        notes = []
        if self._state == 'loading':
            notes.append('预览加载中…')
        elif self._state != 'ok':
            notes.append('⚠ 找不到图片文件（资源表里有、磁盘上没有）')
        if len(spec) > 12:
            notes.append('名称较长，卡片上做了截断')
        return spec + (('\n' + '；'.join(notes)) if notes else '')

    # ---------- 对外 ----------

    def set_speaking(self, flag, redraw=True):
        self._speaking = bool(flag)
        if redraw:
            self._draw()

    def set_selected(self, flag, redraw=True):
        self._selected = bool(flag)
        if redraw:
            self._draw()

    def set_candidates(self, expressions=None, positions=None):
        """面板告诉卡片"这个立绘可选哪些表情/位置"（用于 chip 循环）。"""
        if expressions is not None:
            self._expressions = [e for e in expressions if e]
        if positions is not None:
            self._positions = [p for p in positions if p]
        self._draw()

    def set_warn(self, text):
        """
        在卡片上挂一条警告（画在图区上沿）。

        用途：同一角色在本拍出现多次（换表情）—— **如实告诉用户**，
        而不是偷偷把多余条目合并掉（那会改变写回的数据）。
        """
        self._warn = str(text or '')
        self._draw()

    def set_photo(self, photo, state='ok'):
        """加载器回调：`photo=None` 时进入占位/缺图态。"""
        self._photo = photo
        self._state = state if photo is not None else 'missing'
        self._draw()

    def refresh_thumb(self):
        """按当前图像名重新请求缩略图（防抖 + 后台解码）。"""
        spec = ''
        try:
            spec = (self.image_var.get() or '').strip()
        except Exception:                    # noqa: BLE001
            spec = ''
        if not spec:
            self._photo = None
            self._state = 'missing'
            self._message = '（无图）'
            self._draw()
            return
        self._state = 'loading'
        self._message = ''
        self._draw()
        try:
            path = self._resolve_path(spec)
            self.loader.request(self, path, self._on_thumb)
        except Exception:                    # noqa: BLE001
            # 预览失败**绝不能影响卡片本身**：宁可显示占位，也不能让
            # 这张卡（乃至整个面板刷新）因为"图上出不来"而崩掉
            self._photo = None
            self._state = 'missing'
            self._message = '（预览不可用）'
            self._draw()

    # ---------- 内部 ----------

    def _resolve_path(self, spec):
        """图像名 → 图片路径（借助面板给的解析回调，没有就用名字本身）。"""
        if self._path_resolver is not None:
            try:
                return self._path_resolver(spec) or ''
            except Exception:                # noqa: BLE001
                return spec
        return spec

    def _on_thumb(self, photo, _path):
        self.set_photo(photo)

    def _enter(self, _e=None):
        self._hover = True
        self._draw()

    def _leave(self, _e=None):
        self._hover = False
        self._draw()

    def _focused(self) -> bool:
        try:
            return self.focus_get() is self
        except Exception:                    # noqa: BLE001 - 桩环境
            return False

    def _redraw_needed(self):
        self._draw()

    def _draw(self):
        """重绘整张卡（元素少，重绘比增量更新更不容易出错）。"""
        g = self.geo
        try:
            self.delete('all')
        except Exception:                    # noqa: BLE001
            return

        w, h, pad = g['w'], g['h'], g['pad']
        focus = self._focused()

        # 1) 卡片底
        if self._selected:
            fill, outline, width = T.t('brand.subtle'), T.t('brand.primary'), 2
        elif self._hover:
            fill, outline, width = T.t('bg.cardHover'), T.t('border.strong'), 1
        else:
            fill, outline, width = T.t('bg.card'), T.t('border.default'), 1
        self.create_polygon(round_points(0.5, 0.5, w - 0.5, h - 0.5, g['radius']),
                            smooth=True, fill=fill, outline=outline,
                            width=width, tags=('card',))
        if focus:
            self.create_polygon(
                round_points(1, 1, w - 1, h - 1, g['radius']),
                smooth=True, fill='', outline=T.t('brand.primary'),
                width=T.STROKE['focus'], tags=('card',))

        # 2) 说话态：左侧 3px 主色条（不靠颜色单编码，同时有 ▶ 文字）
        if self._speaking:
            self.create_rectangle(1, 4, 4, h - 4, fill=T.t('brand.primary'),
                                  outline='', tags=('card',))

        # 3) 图区（尺寸恒定 —— 骨架与成品同尺寸，加载不跳）
        tx, ty, tw, th = pad, pad, g['thumbW'], g['thumbH']
        self.create_rectangle(tx, ty, tx + tw, ty + th,
                              fill=(T.t('bg.skeleton') if self._state == 'loading'
                                    else T.t('bg.canvas')),
                              outline=T.t('border.default'), width=1,
                              tags=('card',))
        if self._photo is not None:
            try:
                self.create_image(tx + tw / 2, ty + th / 2, image=self._photo,
                                  tags=('card',))
            except Exception:                # noqa: BLE001 - 桩环境
                pass
        else:
            label = self._placeholder_text()
            if label:
                self.create_text(tx + tw / 2, ty + th / 2, text=label,
                                 fill=T.t('text.graphic'),
                                 font=(T.FONTS['body'][0], 16),
                                 tags=('card',))

        # 3b) 警告条（画在图区上沿，覆盖在图之上）
        if self._warn:
            self.create_rectangle(tx, ty, tx + tw, ty + 15,
                                  fill=T.t('state.warningBg'), outline='',
                                  tags=('card',))
            self.create_text(tx + tw / 2, ty + 8,
                             text=ellipsize('⚠ ' + self._warn, 16),
                             fill=T.t('state.warning'),
                             font=T.FONTS['caption'], tags=('card',))

        # 4) 名字条（12px；说话中加粗）
        name = ''
        try:
            name = (self.image_var.get() or '').strip()
        except Exception:                    # noqa: BLE001
            name = ''
        shown = ellipsize(name or '（未命名）', 12)
        font = (T.FONTS['bodyStrong'] if self._speaking else T.FONTS['body'])
        self.create_text(tx, ty + th + T.SPACE['xs'] + g['nameH'] / 2,
                         text=shown, anchor='w', fill=T.t('text.primary'),
                         font=font, tags=('card',))
        if self._speaking:
            self.create_text(tx + tw, ty + th + T.SPACE['xs'] + g['nameH'] / 2,
                             text='▶ 说话', anchor='e',
                             fill=T.t('brand.primary'),
                             font=T.FONTS['captionStrong'], tags=('card',))

        # 5) 徽标行：表情 chip（前）+ 位置 chip（后）
        row_y = ty + th + T.SPACE['xs'] + g['nameH'] + 2
        slots = self._chip_slots()
        x = tx
        x = self._chip(x, row_y, self._expr_text(), tags=('expr',),
                       selected=bool(self._expr_text()),
                       focused=(self._chip_focus == 0 and 'expr' in slots))
        if x is not None:
            self._chip(x + T.SPACE['xs'], row_y, self._pos_text(),
                       tags=('pos',), selected=bool(self._pos_text()),
                       focused=(self._chip_focus == 1 and 'pos' in slots))

        # 6) 关闭（悬停才显示 —— 密集网格里防误触）
        if self._hover:
            cx = w - g['closeW'] - 6
            self.create_rectangle(cx, 6, cx + g['closeW'], 6 + g['closeW'],
                                  fill=T.t('bg.canvas'),
                                  outline=T.t('state.error'), width=1,
                                  tags=('close',))
            self.create_text(cx + g['closeW'] / 2, 6 + g['closeW'] / 2,
                             text='×', fill=T.t('state.error'),
                             font=T.FONTS['caption'], tags=('close',))

        # 7) 选中标记（形状 + 颜色，不只靠颜色）
        if self._selected:
            cxx, cyy, r = w - 16, h - 16, 8
            self.create_oval(cxx - r, cyy - r, cxx + r, cyy + r,
                             fill=T.t('brand.primary'), outline='',
                             tags=('card',))
            self.create_text(cxx, cyy, text='✓', fill='#FFFFFF',
                             font=T.FONTS['caption'], tags=('card',))

        self._bind_items()

    def _placeholder_text(self):
        if self._state == 'loading':
            return '…'
        spec = ''
        try:
            spec = (self.image_var.get() or '').strip()
        except Exception:                    # noqa: BLE001
            spec = ''
        if spec:
            return spec.split()[0][:1].upper()      # tag 首字，便于辨认
        return '无图'

    def _expr_text(self):
        try:
            return (self.expr_var.get() or '').strip()
        except Exception:                    # noqa: BLE001
            return ''

    def _pos_text(self):
        try:
            return (self.pos_var.get() or '').strip()
        except Exception:                    # noqa: BLE001
            return ''

    def _chip(self, x, y, text, tags, selected=False, focused=False):
        """画一个 chip，返回它的右边界（空文本不画，返回 None）。"""
        g = self.geo
        text = text or ''
        if not text:
            return x
        ch = g['chipH']
        cw = max(28, len(text) * 8 + 18)
        fill = T.t('brand.subtle') if selected else T.t('bg.canvas')
        # 键盘焦点：加粗描边（键盘用户看得见"现在停在哪个 chip 上"）
        outline = (T.t('brand.primary') if (selected or focused)
                   else T.t('border.default'))
        width = 2 if focused else 1
        self.create_polygon(round_points(x, y, x + cw, y + ch, ch / 2),
                            smooth=True, fill=fill, outline=outline,
                            width=width, tags=tags)
        # 选中态加 ✓ 前缀：**不靠颜色单编码**
        body = f'✓ {text}' if selected else text
        self.create_text(x + cw / 2, y + ch / 2, text=body,
                         fill=(T.t('brand.primary') if selected
                               else T.t('text.secondary')),
                         font=T.FONTS['caption'], tags=tags)
        return x + cw

    def _bind_items(self):
        """重绘后要重新绑定（Canvas 的 tag 绑定随 item 一起消失）。"""
        def clicked(event, action):
            if action:
                action()
            return 'break'

        for tag, action in (('expr', self._on_cycle_expression),
                            ('pos', self._on_cycle_position),
                            ('close', self._on_delete)):
            for item_tags in (tag,):
                try:
                    self.tag_bind(item_tags, '<Button-1>',
                                  lambda e, a=action: clicked(e, a))
                except Exception:            # noqa: BLE001
                    pass
        try:
            self.tag_bind('card', '<Button-1>',
                          lambda e: self._on_select and self._on_select())
            self.tag_bind('card', '<Double-Button-1>',
                          lambda e: (self._on_open_picker
                                     and self._on_open_picker()))
        except Exception:                    # noqa: BLE001
            pass


class AddCard(tk.Canvas):
    """网格末尾的「＋ 添加立绘」卡（虚线框，与立绘卡同尺寸）。"""

    def __init__(self, parent, command=None, compact=False):
        self.geo = T.CARD_COMPACT if compact else T.CARD
        super().__init__(parent, width=self.geo['w'], height=self.geo['h'],
                         highlightthickness=0, bd=0,
                         background=T.t('bg.canvas'), takefocus=1)
        self._command = command
        self._hover = False
        self.bind('<Enter>', lambda e: self._hovered(True))
        self.bind('<Leave>', lambda e: self._hovered(False))
        self.bind('<Button-1>', lambda e: self._command and self._command())
        self._draw()

    def _hovered(self, flag):
        self._hover = flag
        self._draw()

    def _draw(self):
        g = self.geo
        try:
            self.delete('all')
            w, h = g['w'], g['h']
            outline = (T.t('brand.primary') if self._hover
                       else T.t('border.dashed'))
            fill = T.t('brand.subtle') if self._hover else ''
            self.create_polygon(
                round_points(0.5, 0.5, w - 0.5, h - 0.5, g['radius']),
                smooth=True, fill=fill, outline=outline, width=1,
                dash=(4, 3))
            cy = h / 2
            self.create_text(w / 2, cy - 26, text='＋',
                             fill=outline, font=(T.FONTS['panelTitle'][0], 20))
            self.create_text(w / 2, cy + 14, text='添加立绘',
                             fill=(T.t('brand.primary') if self._hover
                                   else T.t('text.secondary')),
                             font=T.FONTS['body'])
        except Exception:                    # noqa: BLE001
            pass


__all__ = ['SpriteCard', 'AddCard', 'round_points', 'ellipsize']
