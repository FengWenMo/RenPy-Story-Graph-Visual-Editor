"""
storygraph.app.stage
====================
**舞台演出推导层**（框架无关 / 无 UI 依赖）。

把 Beat 序列推导成"一帧帧可绘制的舞台画面"：背景 / 立绘 / 图层 /
站位 / 说话人高亮。用户 2026-09-28 的原话：

> 我希望你能够写一个舞台演出核心，让这一章能够实时根据卡片效果跑起来

难点不在画，在**语义同源**：预览必须和生成的 .rpy 说同一件事，
否则"预览在骗人"（比没有预览更糟）。所以这里**不重写舞台推导** ——
舞台状态全部来自 `story.stage_snapshot.snapshot_rows`（与「舞台快照」表、
`render_beat_to_rpy` 同一套语义）。本层只多做两件事：

  1. `transform_x()`：把 transform 名 / 位置词换算成屏幕 x（**位置建议**，
     不是引擎真相；拿不准就居中，不猜）；
  2. 把每拍的最终状态组装成 `StageFrame`，并把素材名解析成真实图片路径。

契约（勿破）：
  · 本模块**禁止** import 任何 UI 框架，也禁止 import `storygraph.ui*`；
  · `ui/stage_preview.py` 作为兼容门面 re-export 这里的一切，
    tkinter 的 `StagePreviewWindow` 仍留在那边。
  · 纯函数可无头测试：`build_frames` / `resolve_frame_assets` 等起 tkinter 也不需要。

分层：
    snapshot_rows(beats)   →  [{...}]            （story 层，已有）
    build_frames(beats)    →  [StageFrame]       （本模块）
    resolve_frame_assets() →  {bg, sprites, layers}（本模块）
    <前端>                 →  画出来 + 播放控制
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.app.beats import lookup_image_id
from storygraph.story.stage_snapshot import snapshot_rows

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
