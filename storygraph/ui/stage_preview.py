"""
storygraph.ui.stage_preview
===========================
**舞台演出预览**：把 Beat 序列渲染成一帧帧舞台画面，让这一章"跑起来"。

用户 2026-09-28：
> 我希望你能够写一个舞台演出核心，让这一章能够实时根据卡片效果跑起来
> （这个非常困难，但这是我希望要做的）

困难的地方不在画，在**语义同源** —— 预览必须和生成的 .rpy 说同一件事，
否则"预览在骗人"（这比没有预览更糟，见用户评审第 6 条）。

所以这里**不重写一套舞台推导**：
  · 舞台状态（背景/立绘/图层/音乐）全部来自 `stage_snapshot.snapshot_rows`
    —— 和「舞台快照」表、`render_beat_to_rpy` 同一套语义；
  · 本模块只多做两件事：
      1. `transform_x()`：把 transform 名/位置词换算成屏幕 x（**位置建议**，
         不是引擎真相；拿不准就居中，不猜）
      2. 把每拍的最终状态组装成可绘制的帧

分层（前两层是纯函数，可无头测试；最后一层才碰 tkinter）：

    snapshot_rows(beats)  →  [{...}]
    build_frames(beats)   →  [StageFrame]
    StagePreviewWindow    →  画出来 + 播放控制
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import tkinter as tk

from storygraph.story.stage_snapshot import snapshot_rows
from storygraph.ui import tokens as T

#: 归一化坐标：0.0 = 屏幕最左，1.0 = 最右（与 Ren'Py 的 xalign 同向）
CENTER_X = 0.5

#: 内置/常见位置词 → xalign。**只收有把握的**，其余走 `_x_from_letters` 或居中。
_POSITION_X = {
    'left': 0.2, 'center': CENTER_X, 'centre': CENTER_X,
    'right': 0.8, 'truecenter': CENTER_X,
    'close': 0.5, 'closeup': 0.5, 'far': 0.5,
    'leftish': 0.2,
}

#: 自定义 transform 常见命名：`i11` / `f11` / `l21` / `r22` / `c11`
#: 首字母表示横向分区（i = inner 偏内、f = far 偏外、l/r/c = 左/右/中），
#: 数字表示深度/档位。这是**启发式**，所以只用来给一个合理默认位。
_LETTER_X = {
    'l': 0.25, 'r': 0.75, 'c': CENTER_X,
    'i': 0.35, 'f': 0.65,
}


def transform_x(name: str, registry=None) -> float:
    """
    transform 名 / 位置词 → 归一化 x（0..1）。

    优先级（从可靠到启发）：
      1. 资源表 transforms 里的 ATL 含 `xpos <数字>` → 直接用它；
      2. 已知位置词（`left` / `右` / `closeup`…）；
      3. 名字首字母启发（`i11` → 偏内、`f11` → 偏外、`l/r/c`）；
      4. 兜底居中 —— **不猜**。

    为什么允许"启发"：预览要让人看出"谁在左谁在右"，全居中就没用了。
    但启发值只影响**画面示意**，不影响生成的代码（那是编译器的活）。
    """
    name = (name or '').strip()
    if not name:
        return CENTER_X
    low = name.lower()

    # 1) 项目 transform 的 ATL 里有 xpos —— 这是最准的
    if registry is not None:
        row = (getattr(registry, 'transforms', {}) or {}).get(name) or {}
        atl = str(row.get('ATL') or '')
        for token in ('xpos', 'xalign'):
            idx = atl.find(token)
            if idx >= 0:
                tail = atl[idx + len(token):].strip().split()
                if tail:
                    try:
                        return max(0.0, min(1.0, float(tail[0])))
                    except ValueError:
                        pass

    # 2) 已知位置词
    if low in _POSITION_X:
        return _POSITION_X[low]

    # 3) 首字母启发（只在"字母 + 数字"这种项目惯例命名上做）
    if low and low[0] in _LETTER_X:
        return _LETTER_X[low[0]]

    # 4) 兜底居中
    return CENTER_X


@dataclass
class SpriteLayout:
    """一帧里的一个立绘（画面示意用）。"""

    image: str          # `ling happy`
    tag: str            # `ling`
    at: str             # `i11` / `left` / ''
    x: float            # 0..1
    speaking: bool = False

    @property
    def label(self) -> str:
        """画在方块上的字（只用 tag + 表情，别太长）。"""
        parts = self.image.split()
        if len(parts) >= 2:
            return f'{parts[0]}\n{" ".join(parts[1:])}'
        return self.image


@dataclass
class StageFrame:
    """
    一拍 = 一帧舞台画面。

    这些字段和 `snapshot_rows` 一一对应，**不新增语义**——
    预览只是把它画出来。
    """

    index: int
    line_id: str
    label: str
    speaker: str
    text: str
    scene: str
    sprites: list = field(default_factory=list)
    layers: str = ''            # CG/UI 等非角色图层
    music: str = ''
    sound: str = ''
    voice: str = ''
    changes: str = ''           # 本拍变化（和快照表同一份文字）

    @property
    def dialogue(self) -> str:
        """这一帧对话框里显示什么。"""
        who = self.speaker or ''
        body = self.text or ''
        if who and body:
            return f'{who}：「{body}」'
        return body or '（没有台词）'


def build_frames(beats: list, registry=None) -> list:
    """
    Beat 序列 → `StageFrame` 列表（**纯函数，不碰 tkinter**）。

    舞台状态全部来自 `snapshot_rows`（与生成/快照表同源）；
    这里只把 transform 换成屏幕 x、并把"谁在说话"标出来。
    """
    if not beats:
        return []
    rows = snapshot_rows(beats)
    frames = []
    for i, (beat, row) in enumerate(zip(beats, rows)):
        speaker = (getattr(beat, 'speaker', '') or '').strip()
        sprites = []
        for img, at in (row.get('state', {}).get('actors') or []):
            tag = (img or '').split()[0] if img else ''
            sprites.append(SpriteLayout(
                image=img or '', tag=tag, at=at or '',
                x=transform_x(at, registry),
                speaking=False))
        # 谁在说话：优先角色变量的 ImageTag，其次说话人本身就是 tag。
        # **不猜**（匹配不上就都不高亮）——高亮打错人比不高亮更糟。
        if speaker:
            spk_tag = ''
            try:
                ch = (getattr(registry, 'characters', {}) or {}).get(speaker) or {}
                spk_tag = str(ch.get('ImageTag') or '').strip()
            except Exception:
                spk_tag = ''
            hit = spk_tag or speaker
            for s in sprites:
                if s.tag and s.tag == hit:
                    s.speaking = True
        # 正在说话的排前面（与卡片一致）
        sprites.sort(key=lambda s: (not s.speaking, s.x))

        frames.append(StageFrame(
            index=i,
            line_id=row.get('line_id', '') or f'{getattr(beat, "label", "")}.{i + 1:04d}',
            label=row.get('label', '') or '',
            speaker=speaker,
            text=row.get('text', '') or '',
            scene=row.get('scene', '') or '',
            sprites=sprites,
            layers=row.get('layers', '') or '',
            music=row.get('music', '') or '',
            sound=row.get('sound', '') or '',
            voice=row.get('voice', '') or '',
            changes=row.get('display_changes', '') or ''))
    return frames


def frame_summary(frame: StageFrame) -> str:
    """一帧的一句话摘要（给状态栏/日志用）。"""
    bits = [f'#{frame.index + 1}', frame.line_id]
    if frame.scene:
        bits.append(frame.scene)
    if frame.sprites:
        bits.append('、'.join(s.image for s in frame.sprites))
    if frame.music:
        bits.append(f'♪ {frame.music}')
    return ' · '.join(b for b in bits if b)


# ===================== 真实素材解析（纯函数，可无头测） =====================
#
# 为什么要单独一层：预览要画真图，就必须回答"这个图像名对应磁盘上哪张图"。
# 这件事**必须和编辑器/Excel 那条路同源**（都走"按 tag 收窄"的解析），
# 否则预览会把 `ling oh` 画成 lan 的立绘 —— 用户 2026-09-30 实测的串图事故。

def resolve_stage_asset(name: str, images: dict, project_root=None,
                        index: dict = None):
    """
    图像名（`bg bg6_2` / `ling disappointed`）→ 磁盘上的图片路径。

    找不到（没登记 / 文件不在）返回 `None` —— 调用方应当**降级画示意图**，
    绝不让画面空着（"图没找到 → 一片空白"是这个功能最容易踩的坑）。

    解析**先按 tag 收窄**（复用 `lookup_image_id`）：真实项目里
    `lan` 与 `ling` 都有 `oh` 这个表情，用裸属性名查必然张冠李戴。
    """
    spec = str(name or '').strip()
    if not spec or not images:
        return None
    parts = spec.split()
    tag, rest = parts[0], parts[1:]
    try:
        from storygraph.ui.beat_editor import lookup_image_id
        aid = lookup_image_id(images, tag, rest)
    except Exception:                        # noqa: BLE001 - 桩环境
        return None
    if not aid:
        return None
    row = images.get(aid) or {}
    rel = str(row.get('Path') or '').strip()
    hit = _path_from_rel(rel, project_root)
    if hit is not None:
        return hit
    # ② 表里没有 / 路径对不上 → 有界磁盘回退（按名字找文件）。
    #    真实数据实测：脚本声明 `image bg bg6_2 = "images/bg/bg6.2.png"`
    #    的文件名带点、而脚本里写的是下划线，靠表查必然落空。
    return image_index_lookup(spec, index)


_SOLID_RE = re.compile(
    r'^\s*(?:Solid\s*\(\s*)?["\']?(#[0-9A-Fa-f]{3,8})\b')


def resolve_solid_color(name: str, images: dict):
    """
    图像名 → **纯色**（`image black = "#000000"` / `Solid("#000")`）。

    真实项目里 `scene black` 出现 50 次 —— 它是纯色、不是图片文件。
    不认这一档的话，预览会把它当"找不到图"（其实游戏里就是一块黑）。
    """
    spec = str(name or '').strip()
    if not spec or not images:
        return None
    parts = spec.split()
    try:
        from storygraph.ui.beat_editor import lookup_image_id
        aid = lookup_image_id(images, parts[0], parts[1:])
    except Exception:                        # noqa: BLE001
        return None
    if not aid:
        return None
    row = images.get(aid) or {}
    # 脚本声明 `image black = "#000000"` 时，合并出来的行把颜色放在
    # `Solid` 字段里（`Path` 是空的，因为没有文件）；旧版/手写的表可能
    # 直接写进 Path，所以两条都认。
    solid = str(row.get('Solid') or '').strip()
    if solid:
        m = _SOLID_RE.match(solid)
        if m:
            return m.group(1)
        if re.match(r'^#[0-9A-Fa-f]{3,8}$', solid):
            return solid
    m = _SOLID_RE.match(str(row.get('Path') or ''))
    return m.group(1) if m else None


def _path_from_rel(rel: str, project_root):
    """
    表里的 Path → 真实文件（不存在返回 None）。

    **两套基准都要试**：真实数据实测（2026-09-30）

      · 扫描出来的资产行：`game/images/characters/ling/oh.png`（相对项目根）
      · 脚本 `image` 声明合并进来的行：`images/bg/bg6.2.png`（相对 `game/`）

    只认前者的话，**所有脚本声明的背景图都解析不出来**
    （chapter1_3 里 305 帧的背景全落空）。
    """
    rel = str(rel or '').strip()
    if not rel or rel.startswith('#'):
        return None                    # 纯色（`image black = "#000000"`）
    from pathlib import Path
    p = Path(rel)
    if p.is_absolute():
        try:
            return p if p.exists() else None
        except OSError:
            return None
    if not project_root:
        return None
    for base in (Path(project_root), Path(project_root) / 'game'):
        cand = base / rel
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return None


#: 磁盘索引缓存：`{项目根: {小写文件名: 路径}}`。**每项目只建一次**。
_IMAGE_INDEX_CACHE: dict = {}


def image_index(project_root) -> dict:
    """
    项目里的图片索引（`bg6.2` / `bg6_2` → 真实文件）。

    **有界**：只在已知的图片目录里递归一次（`game/images/**`），跳过
    `gui/`（界面素材，几千张，与演出无关），结果按项目缓存。
    这不是"猜"，而是"表里没有、但文件确实在"时的兜底 ——
    找不到就老实说找不到，绝不编一个路径出来。
    """
    if not project_root:
        return {}
    key = str(project_root)
    got = _IMAGE_INDEX_CACHE.get(key)
    if got is not None:
        return got
    from pathlib import Path
    idx: dict = {}
    roots = [Path(project_root) / 'game' / 'images',
             Path(project_root) / 'images']
    exts = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
    try:
        for root in roots:
            if not root.is_dir():
                continue
            for f in root.rglob('*'):
                if f.suffix.lower() not in exts:
                    continue
                parts = {x.lower() for x in f.parts}
                if 'gui' in parts or 'screenshots' in parts:
                    continue
                idx.setdefault(f.stem.lower(), str(f))
    except OSError:
        pass
    _IMAGE_INDEX_CACHE[key] = idx
    return idx


def image_index_lookup(spec: str, index: dict):
    """图像名 → 索引里的文件。`bg6_2` 与 `bg6.2` 视为同一个（真实项目两种写法都有人用）。"""
    if not index:
        return None
    parts = str(spec or '').split()
    if not parts:
        return None
    cands = [parts[-1], '_'.join(parts), ' '.join(parts)]
    if len(parts) > 1:
        cands.append(parts[-1].replace('_', '.'))
    for c in cands:
        for key in (c.lower(), c.lower().replace('.', '_'),
                    c.lower().replace('_', '.')):
            hit = index.get(key)
            if hit:
                return hit
    return None


def resolve_frame_assets(frame, images: dict, project_root=None,
                         index: dict = None) -> dict:
    """
    一帧要用到的真实图片路径：`{'bg':..., 'layers':..., 'sprites': [(布局, 路径)]}`。

    纯函数 —— 给真实资源表就能无头跑，也顺手回答了
    "**这一章缺哪几张图**"（`missing` 列表），不用等到画面上空一片才发现。
    查不到的项一律 `None`，由绘制层降级。
    """
    def resolve(spec):
        """真图优先；没有文件但**是纯色**（`scene black`）也算解析成功。"""
        path = resolve_stage_asset(spec, images, project_root, index)
        if path is not None:
            return path
        if resolve_solid_color(spec, images):
            return None                  # 有解，只是不是文件 → 不算"找不到"
        return None

    sprites = []
    missing = []
    for s in getattr(frame, 'sprites', []) or []:
        spec = getattr(s, 'image', '')
        path = resolve(spec)
        if path is None and spec and not resolve_solid_color(spec, images):
            missing.append(spec)
        sprites.append((s, path))
    bg = None
    if getattr(frame, 'scene', ''):
        bg = resolve(frame.scene)
        if bg is None and not resolve_solid_color(frame.scene, images):
            missing.append(frame.scene)
    layers = None
    if getattr(frame, 'layers', ''):
        layers = resolve(frame.layers)
        if layers is None and not resolve_solid_color(frame.layers, images):
            missing.append(frame.layers)
    return {'bg': bg, 'layers': layers, 'sprites': sprites, 'missing': missing}


# ===================== 画出来（tkinter） =====================
#
# 上面全是纯函数；这一层只负责"把帧画到画布上 + 播放控制"。
#
# 两种画法，**都能用**：
#   · 真实贴图（默认）：背景 + 立绘，走 `ui.thumb_loader` 异步解码
#     （禁止在 UI 线程同步解码大图 —— 立绘常见 1500×2200）
#   · 示意图：找不到图 / 关掉真实贴图时，画方块 + 名字 + 站位
# 缺图时**同一帧里两种混着画**：有图的贴图、没图的落在方块位置上，
# 并在底部明确写出"缺哪几张"，不让用户对着空白猜。
#
# 画面尺寸用 16:9（`STAGE_W × STAGE_H`）：和游戏屏幕同比例，
# 站位才看得出来对不对。

#: 舞台画布尺寸（16:9）
STAGE_W, STAGE_H = 896, 504
#: 立绘占舞台高度的比例（Ren'Py 立绘通常接近满高）
SPRITE_H_RATIO = 0.92
#: 立绘解码目标尺寸（够清晰又不至于解整张大图）
SPRITE_MAX = (420, 760)
#: 示意图方块的尺寸
BLOCK_W, BLOCK_H = 92, 232


def _screen_x(x: float, width: int) -> int:
    return int(max(0.0, min(1.0, x)) * width)


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
