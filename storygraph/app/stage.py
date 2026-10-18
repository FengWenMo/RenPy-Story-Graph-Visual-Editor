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
  · 本模块**禁止** import 任何 UI 框架，也禁止 import `storygraph.ui_qt*`；
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

from storygraph.core.safeguard import swallow
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
                    except ValueError as e:
                        swallow('stage.transform_x', e)

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
    x: float            # 0..1（归一化中心；没有真实几何时用它）
    speaking: bool = False
    #: 项目 transform 算出来的**真实舞台几何**（见 `story.atl`）。
    #: `None` = 没算出来（那时只按 `x` + 满高画示意图，与老版本一致）。
    place: object = None

    @property
    def zoom(self) -> float:
        return float(getattr(self.place, 'zoom', 1.0) or 1.0)

    @property
    def from_atl(self) -> bool:
        """这个位置是从项目 transform 里真算出来的吗？（决定要不要按真实几何画）"""
        return bool(getattr(self.place, 'from_atl', False))

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
    #: 这一拍的**转场**（`with dissolve` 里的 `dissolve`；空 = 硬切）。
    #: 2026-10-18（审计 P1-8"无转场"）：预览据此做交叉淡入。
    with_: str = ''
    #: 这一拍的**停顿秒数**（`pause 1.5` / `pause 1.5 hard`；0 = 没写）。
    #: 预览据此决定自动播放停多久 —— 从前固定 900ms，长台词一闪而过。
    pause: float = 0.0

    @property
    def duration_ms(self) -> int:
        """
        自动播放时这一拍该停多久（毫秒）。

        判据顺序（都是"脚本里写得出来的东西"，不猜节奏）：
          ① 有 `pause N` → N 秒（那是作者明确写的停顿）；
          ② 有台词 → 按字数估（中文按字、西文按词的速度都落在
             每字 60~100ms 这个量级），夹在 [700, 6000] 之间；
          ③ 纯舞台变化（没有台词）→ 600ms（够看清切了什么）。
        """
        if self.pause and self.pause > 0:
            return max(200, int(self.pause * 1000))
        text = self.text or ''
        if text:
            return max(700, min(6000, 500 + len(text) * 70))
        return 600

    @property
    def fades(self) -> bool:
        """这一拍是不是**渐变**类转场（`dissolve` / `fade` / `Fade(…)`）。"""
        name = str(self.with_ or '').strip().lower()
        if not name:
            return False
        return any(k in name for k in ('dissolve', 'fade', 'dissolve',
                                       'flash', 'pixellate'))

    @property
    def dialogue(self) -> str:
        """这一帧对话框里显示什么。"""
        who = self.speaker or ''
        body = self.text or ''
        if who and body:
            return f'{who}：「{body}」'
        return body or '（没有台词）'


def build_frames(beats: list, registry=None, transform_index=None,
                 screen=None) -> list:
    """
    Beat 序列 → `StageFrame` 列表（**纯函数，不碰 tkinter**）。

    舞台状态全部来自 `snapshot_rows`（与生成/快照表同源）；
    这里只把 transform 换成屏幕位置、并把"谁在说话"标出来。

    `transform_index` / `screen`（用户 2026-10-05："transform 未对应到舞台真实
    位置"）：项目 `transform.rpy` 里解析出来的定义表 + 游戏分辨率，交给
    `story.atl.placement_of` 算出**真实像素位置与缩放**（`SpriteLayout.place`）。
    不传就退回老的启发式（按名字首字母猜个 x），行为与以前完全一致。
    """
    if not beats:
        return []
    from storygraph.story.atl import DEFAULT_SCREEN, placement_of

    size = tuple(screen) if screen else DEFAULT_SCREEN
    rows = snapshot_rows(beats)
    frames = []
    for i, (beat, row) in enumerate(zip(beats, rows)):
        speaker = (getattr(beat, 'speaker', '') or '').strip()
        sprites = []
        for img, at in (row.get('state', {}).get('actors') or []):
            tag = (img or '').split()[0] if img else ''
            place = placement_of(at, transform_index, registry, size)
            sprites.append(SpriteLayout(
                image=img or '', tag=tag, at=at or '',
                x=place.x, speaking=False, place=place))
        # 谁在说话：优先角色变量的 ImageTag，其次说话人本身就是 tag。
        # **不猜**（匹配不上就都不高亮）——高亮打错人比不高亮更糟。
        if speaker:
            spk_tag = ''
            try:
                ch = (getattr(registry, 'characters', {}) or {}).get(speaker) or {}
                spk_tag = str(ch.get('ImageTag') or '').strip()
            except Exception as e:
                swallow('app.stage.build_frames', e)
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
            changes=row.get('display_changes', '') or '',
            with_=str(getattr(beat, 'with_', '') or ''),
            pause=_pause_seconds_of(beat)))
    return frames


def _pause_seconds_of(beat) -> float:
    """
    这一拍有没有 `pause N`（秒）。

    为什么从 `raw` 里读而不是加字段：`pause` 现在走的是"原生语句"通道
    （`raw` 里逐字存着），加一个 Beat 字段会动表契约（列/指纹/快照）。
    这里只取第一个 `pause` 的数字 —— 拍里写第二条 pause 的情况极少，
    而"停多久"只是预览节奏，不影响产物。
    """
    import re as _re
    text = str(getattr(beat, 'raw', '') or '')
    m = _re.search(r'(?m)^\s*pause\s+(\d+(?:\.\d+)?)', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


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


def placement_notes(frame: StageFrame) -> list:
    """
    这一拍里**位置是猜的**有哪些 → `['ling happy：这个 transform 含 parallel，位置是猜的']`。

    用户 2026-10-19（B1）：

    > 现在的问题不是"算不出"，是"算不出还不说"，那比算错更糟。

    只报"不可靠"的那些（`Placement.uncertain` 非空）；有把握时返回空表 ——
    到处标警告等于没警告。纯函数（不碰 Qt），横条与测试共用这一份文字。
    """
    out = []
    for s in list(getattr(frame, 'sprites', ()) or ()):
        note = str(getattr(getattr(s, 'place', None), 'uncertain', '') or '').strip()
        if not note:
            continue
        who = str(getattr(s, 'image', '') or getattr(s, 'tag', '') or '').strip()
        out.append(f'{who}：{note}' if who else note)
    return out


def has_stage_change(frame: StageFrame) -> bool:
    """
    这一拍的**舞台**真的变了什么吗（预览的「只看有变化的拍」判据）。

    ⚠ 2026-10-19（审计 P1-8 收尾）：判据从前是 `bool(frame_summary(f))`，
    而摘要**永远**至少含 `#N · line_id` —— 于是恒为 True，那个勾选框
    什么也没做（审计原话："仍是空实现"）。

    现在只看 `changes` —— 它和「舞台快照」表的「本拍变化」是**同一份文字**
    （`stage_snapshot._render_changes`），所以"预览里留下的拍"与"表里标了
    变化的拍"必然一致。

    刻意**不看台词**：一句纯对话没有改变舞台，它不是这一项要留下的东西；
    这也正是这张表存在的意义（几百拍里只看真正换景/换人/换声的那几拍）。
    """
    try:
        return bool(str(getattr(frame, 'changes', '') or '').strip())
    except Exception as e:                   # noqa: BLE001 - 判不了就别藏拍
        swallow('app.stage.has_stage_change', e)
        return True


# ===================== 真实素材解析（纯函数，可无头测） =====================
#
# 为什么要单独一层：预览要画真图，就必须回答"这个图像名对应磁盘上哪张图"。
# 这件事**必须和编辑器/Excel 那条路同源**（都走"按 tag 收窄"的解析），
# 否则预览会把 `ling oh` 画成 lan 的立绘 —— 用户 2026-09-30 实测的串图事故。


# —— 脚本里的 `image` 声明：**引擎的真源** ——
#
# 用户 2026-10-05：
# > 这个你确定 bgm 导入了吗？背景导入了吗？是否正确呢？
#
# 实测真实项目：预览里 `bg bg11_0` **一片黑**，而资源表里只有磁盘扫描出来的
# `bg11`（`images/bg/bg11.png`）。"`bg11_0` = `bg11.png`"这条对应关系**只写在
# 脚本里**：
#
#     definitions.rpy:146   image bg bg11_0 = "images/bg/bg11.png"
#
# Ren'Py 是照脚本认的（`show` / `scene` 里的名字查的就是这张声明表），所以预览
# 也必须照脚本认 —— 否则"表里没登记的写法"就永远画不出来（表是编辑器的东西，
# 脚本才是游戏的东西）。`image black = "#000000"` 这种纯色声明也一并收下。
#
# 只扫 `*.rpy` 的顶层 `image` 行（实测真实项目 41 个文件 / 104 条声明 / 70ms），
# 结果按项目缓存；**不做全量语法解析**（那是导入章节的活，这里只要声明）。

#: `image <名字> = <表达式>`（名字允许带空格：`bg bg1_1`）；与解析层同一套写法
_RE_IMAGE_DECL = re.compile(
    r'^image\s+(?P<name>[^\s=][^=]*?)\s*=\s*(?P<rhs>.+)$')
#: 扫描时跳过的目录（缓存/存档/翻译/构建产物里没有演出用的图）
_RPY_SKIP_DIRS = {'cache', 'saves', 'tl', '.git', 'build', '__pycache__'}

#: 脚本声明缓存：`{项目根: {'names':…, 'attrs':…}}`。**每项目只扫一次**。
_SCRIPT_IMAGE_CACHE: dict = {}


def script_images(project_root) -> dict:
    """
    扫项目脚本里的 `image` 声明 → `{'names': {名字: info}, 'attrs': {属性串: info}}`。

    `info = {'path': 脚本里的相对路径|'', 'solid': '#rrggbb'|''}`。
    同一张图会有两种写法（`bg bg11_0` 与只有属性段的 `bg11_0`），两份索引都给，
    免得调用方自己去切字符串。
    """
    empty = {'names': {}, 'attrs': {}}
    if not project_root:
        return empty
    key = str(project_root)
    got = _SCRIPT_IMAGE_CACHE.get(key)
    if got is not None:
        return got
    try:
        from storygraph.parsing.renpy_parser import (image_path_from_expr,
                                                     image_solid_from_expr)
    except Exception as e:                         # noqa: BLE001 - 桩环境
        swallow('app.stage.script_images', e)
        image_path_from_expr = image_solid_from_expr = lambda _e: None

    names: dict = {}
    attrs: dict = {}
    seen = set()
    roots = [Path(project_root), Path(project_root) / 'game']
    for root in roots:
        try:
            if not root.is_dir():
                continue
            files = sorted(root.rglob('*.rpy'))
        except OSError:
            continue
        for f in files:
            if _RPY_SKIP_DIRS & set(f.parts) or f in seen:
                continue
            seen.add(f)
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for line in text.splitlines():
                # 只认顶层（缩进的都块内语句）；`# image …` 是注释
                if not line.startswith('image '):
                    continue
                m = _RE_IMAGE_DECL.match(line)
                if not m:
                    continue
                name = ' '.join(m.group('name').split())
                if not name:
                    continue
                rhs = m.group('rhs').strip()
                info: dict = {'path': '', 'solid': ''}
                path = image_path_from_expr(rhs)
                if path:
                    info['path'] = path
                else:
                    solid = image_solid_from_expr(rhs)
                    if not solid:
                        continue      # 既不是文件也不是纯色（layeredimage…）
                    info['solid'] = solid
                names.setdefault(name, info)
                parts = name.split()
                if len(parts) > 1:
                    attrs.setdefault(' '.join(parts[1:]), info)
    out = {'names': names, 'attrs': attrs}
    _SCRIPT_IMAGE_CACHE[key] = out
    return out


def script_image(spec, project_root):
    """
    图像名 → 脚本里的声明 `{'path':…, 'solid':…}`；查不到返回 None。

    匹配顺序：整名 → 逐级去掉尾属性（`ye base_handdown sideeye` 落到
    `image ye base_handdown`）→ 只有属性段的写法（`bg11_0`）。长名优先，
    **不猜**（实在没有就返回 None，交给调用方的磁盘回退）。
    """
    decls = script_images(project_root)
    names, attrs = decls['names'], decls['attrs']
    if not names and not attrs:
        return None
    parts = str(spec or '').split()
    if not parts:
        return None
    for i in range(len(parts), 0, -1):
        cand = ' '.join(parts[:i])
        info = names.get(cand) or attrs.get(cand)
        if info:
            return info
    return None


def resolve_stage_asset(name: str, images: dict, project_root=None,
                        index: dict = None):
    """
    图像名（`bg bg6_2` / `ling disappointed`）→ 磁盘上的图片路径。

    找不到（没登记 / 文件不在）返回 `None` —— 调用方应当**降级画示意图**，
    绝不让画面空着（"图没找到 → 一片空白"是这个功能最容易踩的坑）。

    解析**先按 tag 收窄**（复用 `lookup_image_id`）：真实项目里
    `lan` 与 `ling` 都有 `oh` 这个表情，用裸属性名查必然张冠李戴。

    三级顺序（**越来越粗**，每级都要求文件真的存在）：
      ① 资源表（表里的 `Path`，先按项目根、再按 `game/` 解释）；
      ② 脚本里的 `image` 声明（引擎的真源 —— `image bg bg11_0 = "…"` 这种
         别名只写在脚本里，表里查不到；见 `script_images`）；
      ③ 磁盘按名字回退（`bg6_2` ↔ `bg6.2.png` 这种点/下划线差异）。
    """
    spec = str(name or '').strip()
    if not spec:
        return None
    parts = spec.split()
    tag, rest = parts[0], parts[1:]
    if images:
        # ⚠ **多属性时先试"表情件"**（2026-10-05 修"无头骑士"）：
        #
        # `show ye base_handdown happy` 里 `base_handdown` 是**身体件**
        # （只有身体没有头），`lookup_image_id` 只看第一个属性 → 画出无头身体。
        # 按"非身体件优先（从后往前）"逐个试，第一个查得到的才是完整立绘。
        try:
            from storygraph.app.beats import (lookup_image_id,
                                              preferred_image_attrs)
            wants = preferred_image_attrs(rest) if len(rest) > 1 else []
            for attr in wants:
                aid = lookup_image_id(images, tag, [attr])
                if not aid:
                    continue
                row = images.get(aid) or {}
                hit = _path_from_rel(str(row.get('Path') or '').strip(),
                                     project_root)
                if hit is not None:
                    return hit
        except Exception as e:                    # noqa: BLE001 - 桩环境
            swallow('app.stage.resolve_stage_asset.order', e)
        try:
            aid = lookup_image_id(images, tag, rest)
        except Exception as e:                    # noqa: BLE001 - 桩环境
            swallow('app.stage.resolve_stage_asset', e)
            aid = None
        if aid:
            row = images.get(aid) or {}
            rel = str(row.get('Path') or '').strip()
            hit = _path_from_rel(rel, project_root)
            if hit is not None:
                return hit
    # ② 脚本声明（用户 2026-10-05："背景导入了吗？" —— `bg bg11_0` 就是这一级
    #    救回来的：表里只有 `bg11`，别名 `bg11_0` 是脚本里的声明）
    decl = script_image(spec, project_root)
    if decl and decl.get('path'):
        hit = _path_from_rel(decl['path'], project_root)
        if hit is not None:
            return hit
    # ③ 有界磁盘回退（按名字找文件）。
    #    真实数据实测：脚本声明 `image bg bg6_2 = "images/bg/bg6.2.png"`
    #    的文件名带点、而脚本里写的是下划线，靠表查必然落空。
    return image_index_lookup(spec, index)


_SOLID_RE = re.compile(
    r'^\s*(?:Solid\s*\(\s*)?["\']?(#[0-9A-Fa-f]{3,8})\b')


def resolve_solid_color(name: str, images: dict, project_root=None):
    """
    图像名 → **纯色**（`image black = "#000000"` / `Solid("#000")`）。

    真实项目里 `scene black` 出现 50 次 —— 它是纯色、不是图片文件。
    不认这一档的话，预览会把它当"找不到图"（其实游戏里就是一块黑）。

    表里查不到时同样看**脚本声明**（`definitions.rpy` 里 `image black = "#000000"`
    根本没进表 —— 用户 2026-10-05 那次排查的背景别名是同一个成因）。
    """
    spec = str(name or '').strip()
    if not spec:
        return None
    parts = spec.split()
    aid = None
    if images:
        try:
            aid = lookup_image_id(images, parts[0], parts[1:])
        except Exception as e:                    # noqa: BLE001
            swallow('app.stage.resolve_solid_color', e)
            aid = None
    if aid:
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
        if m:
            return m.group(1)
    decl = script_image(spec, project_root)
    return (decl or {}).get('solid') or None


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
    except OSError as e:
        swallow('stage.image_index', e)
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


def sprite_plan(frame, sprite, layer_model, images: dict, project_root=None):
    """
    一个立绘 → **合成计划**（`LayerPlan`）或 None。

    设计文档：`docs/archive/设计_合成立绘合成与姿势切换_2026-10-05.md`（P2）。
    没有 `layeredimage` 声明 / 认不出 → 返回 None（调用方走今天的单图路径）。
    """
    if layer_model is None or not getattr(layer_model, 'images', None):
        return None
    try:
        from storygraph.story.layered_model import resolve_layers
        spec = ''
        if isinstance(sprite, (list, tuple)) and len(sprite) >= 2:
            spec = str(sprite[1] or '')
        else:
            spec = str(getattr(sprite, 'image', '') or '')
        parts = spec.split()
        if not parts:
            return None
        tag, attrs = parts[0], parts[1:]
        if not layer_model.has(tag):
            return None
        plan = resolve_layers(layer_model, tag, attrs, project_root)
        return plan if getattr(plan, 'layers', None) else None
    except Exception as e:                        # noqa: BLE001 - 认不出就走老路
        swallow('app.stage.sprite_plan', e)
        return None


def resolve_frame_assets(frame, images: dict, project_root=None,
                         index: dict = None, layer_model=None) -> dict:
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
        if resolve_solid_color(spec, images, project_root):
            return None                  # 有解，只是不是文件 → 不算"找不到"
        return None

    sprites = []
    missing = []
    composed = {}
    for s in getattr(frame, 'sprites', []) or []:
        spec = getattr(s, 'image', '')
        # ① **合成立绘优先**（有 layeredimage 声明的项目）：
        #    按项目自己的 group/attribute + AlphaMask 蒙版叠出完整立绘。
        #    合成不出来就往下走老路（单图），**绝不画错也不留白**。
        path = None
        plan = sprite_plan(frame, s, layer_model, images, project_root)
        if plan is not None and getattr(plan, 'ok', False):
            try:
                from storygraph.app.layer_compose import compose_sprite
                path = compose_sprite(plan)
            except Exception as e:                # noqa: BLE001 - 合成失败走老路
                swallow('app.stage.resolve_frame_assets.compose', e)
                path = None
            if path is not None:
                composed[spec] = plan
        if path is None:
            path = resolve(spec)
        if path is None and spec and not resolve_solid_color(spec, images,
                                                             project_root):
            missing.append(spec)
        sprites.append((s, path))
    bg = None
    if getattr(frame, 'scene', ''):
        bg = resolve(frame.scene)
        if bg is None and not resolve_solid_color(frame.scene, images,
                                                 project_root):
            missing.append(frame.scene)
    layers = None
    if getattr(frame, 'layers', ''):
        layers = resolve(frame.layers)
        if layers is None and not resolve_solid_color(frame.layers, images,
                                                     project_root):
            missing.append(frame.layers)
    return {'bg': bg, 'layers': layers, 'sprites': sprites,
            'missing': missing, 'composed': composed}


# ===================== 画出来（tkinter） =====================
#
# 上面全是纯函数；这一层只负责"把帧画到画布上 + 播放控制"。
#
# 两种画法，**都能用**：
#   · 真实贴图（默认）：背景 + 立绘，走 `ui_qt.thumb_loader` 异步解码
#     （禁止在 UI 线程同步解码大图 —— 立绘常见 1500×2200）
#   · 示意图：找不到图 / 关掉真实贴图时，画方块 + 名字 + 站位
# 缺图时**同一帧里两种混着画**：有图的贴图、没图的落在方块位置上，
# 并在底部明确写出"缺哪几张"，不让用户对着空白猜。
#
# 画面尺寸用 16:9（`STAGE_W × STAGE_H`）：和游戏屏幕同比例，
# 站位才看得出来对不对。

#: 舞台画布尺寸（16:9）
STAGE_W, STAGE_H = 896, 504
#: 立绘占舞台高度的比例（**只在没有真实几何时**用；Ren'Py 立绘通常接近满高）
SPRITE_H_RATIO = 0.92
#: 立绘解码目标尺寸（真实几何下按 zoom×原图算；够清晰又不至于解整张大图）
SPRITE_MAX = (640, 1140)
#: 示意图方块的尺寸
BLOCK_W, BLOCK_H = 92, 232


def _screen_x(x: float, width: int) -> int:
    return int(max(0.0, min(1.0, x)) * width)


def sprite_box(layout, natural, screen=None):
    """
    一个立绘在**虚拟屏幕**上的位置与大小：`(left, top, w, h)`；算不出来返回 None。

    `natural` 是图片的原始像素尺寸 `(w, h)`，`layout.place` 是
    `story.atl` 从项目 transform 解析出来的几何。Ren'Py 的规则照抄：

      · 大小 = 原图尺寸 × `zoom`；
      · `xcenter` / `xpos` / `xalign` + `xanchor` / `xoffset` 定横向；
      · `ypos`（>1 当像素）/ `yalign` / `yanchor` / `yoffset` 定纵向。

    为什么必须带上原图尺寸：真实项目立绘是 4320×7680，`zoom 0.30` 之后仍有
    1296×2304（1920×1080 屏幕的两倍高）—— 只按"屏幕百分比"画必然错位。
    `layout` 没有真实几何（`from_atl` 为假）或 `natural` 缺失时返回 None，
    调用方按老办法画示意图。
    """
    place = getattr(layout, 'place', None)
    if place is None or not getattr(place, 'from_atl', False):
        return None
    if not natural or len(natural) < 2:
        return None
    nw, nh = float(natural[0] or 0), float(natural[1] or 0)
    if nw <= 0 or nh <= 0:
        return None
    sw, sh = (tuple(screen) + (1920, 1080))[:2] if screen else (1920, 1080)
    sw, sh = max(1.0, float(sw)), max(1.0, float(sh))
    zoom = float(getattr(place, 'zoom', 1.0) or 1.0)
    w, h = nw * zoom, nh * zoom
    xoffset = float(getattr(place, 'xoffset', 0.0) or 0.0)
    xanchor = float(getattr(place, 'xanchor', 0.5) or 0.0)
    x_px = getattr(place, 'x_px', None)
    kind = getattr(place, 'x_kind', 'center')
    if x_px is None:
        left = float(getattr(layout, 'x', 0.5) or 0.5) * sw - w / 2.0
    elif kind == 'align':
        left = float(x_px) * sw + xoffset - xanchor * w
    elif kind == 'left':
        left = float(x_px) + xoffset - xanchor * w
    else:                                     # center
        left = float(x_px) + xoffset - w / 2.0
    yoffset = float(getattr(place, 'yoffset', 0.0) or 0.0)
    yanchor = float(getattr(place, 'yanchor', 1.0) or 0.0)
    y_px = getattr(place, 'y_px', None)
    ykind = getattr(place, 'y_kind', 'bottom')
    if y_px is None:
        bottom = sh                              # 默认贴屏幕底
    elif ykind == 'align':
        bottom = float(y_px) * sh + yoffset + (1.0 - yanchor) * h
    elif ykind == 'top':
        bottom = float(y_px) + yoffset + h
    else:                                        # bottom
        bottom = float(y_px) + yoffset
    return left, bottom - h, w, h


# ===================== 声音：素材名 → 真实音频文件 =====================
#
# 用户 2026-10-05：
# > 演出的预览界面可能仍旧有点问题，第一个是 bgm 未被实际播放
#
# 预览要出声，就必须回答"这个 BGM 名对应磁盘上哪个 mp3"。规则与图片那条路
# 同源：**先查资源表**（Path 列），查不到再按名字在已知音频目录里找。

#: 音频目录（Ren'Py 惯例 + 这个项目的实际布局）
_AUDIO_DIRS = ('game/bgm', 'game/audio', 'game/sound', 'game/voice',
               'game/music', 'bgm', 'audio', 'sound', 'voice')
_AUDIO_EXT = ('.mp3', '.ogg', '.wav', '.opus', '.flac', '.m4a')


def _audio_index(project_root) -> dict:
    """`game/**` 下的音频文件：`{小写名字(含/不含扩展名): [路径, …]}`（有界）。"""
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return {}
    out: dict = {}
    for rel in _AUDIO_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        try:
            files = sorted(base.rglob('*'))
        except OSError:
            continue
        for path in files:
            if path.suffix.lower() not in _AUDIO_EXT or not path.is_file():
                continue
            out.setdefault(path.stem.lower(), []).append(path)
            out.setdefault(path.name.lower(), []).append(path)
    return out


def audio_spec_name(spec) -> str:
    """
    从各种写法里取出"曲名/文件名"：`[音乐] bgm/x.mp3 fadein 1.0` → `bgm/x.mp3`；
    `bgm_Empt1y` → 原样。卡片上那一格是**带前缀的整行文本**，而快照里拿到的是
    资产 ID —— 两种都要认。
    """
    text = str(spec or '').strip()
    if not text:
        return ''
    text = re.sub(r'^\s*\[[^\]]*\]\s*', '', text)          # 去掉 `[音乐]` 前缀
    text = re.sub(r'\s+(fadein|fadeout|loop|volume|noloop)\b.*$', '', text,
                  flags=re.IGNORECASE)
    parts = text.split()
    for token in parts:
        if token.lower().endswith(_AUDIO_EXT):
            return token
    return parts[0] if parts else ''


def _norm_audio_stem(text) -> str:
    """
    近似比对用的"曲子名"：去扩展名、去尾部省略号、小写。

    为什么专门去省略号：真实项目里脚本写的是
    `play music "bgm/夜晚，断电之后…….mp3"`，而磁盘上是
    `夜晚，断电之后，不存在的教室.mp3` —— 作者当初把长名字省略着写，
    后来文件改名了，脚本没跟着改（游戏自己也会找不到这个文件）。
    """
    s = str(text or '').strip().replace('\\', '/').split('/')[-1]
    s = re.sub(r'\.(mp3|ogg|wav|opus|flac|m4a)$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s.…·]+$', '', s)
    return s.lower()


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


#: 近似匹配的最低要求：至少这么长的公共前缀（少了就是瞎猜）
MIN_APPROX_PREFIX = 4


def _approx_audio_pool(registry, table: str, project_root, index: dict) -> list:
    """
    近似匹配的候选池：`[(比对用曲名, 真实路径, 显示名), …]`。

    两个来源（都是**表里的真路径**优先，再退到磁盘上真实存在的文件）：
      · 资源表那一张（`music` / `sounds` / `voices`）的每一行；
      · 磁盘音频索引（去重）。
    """
    out: list = []
    seen: set = set()
    rows = getattr(registry, table, None) or {}
    for key, row in (rows.items() if hasattr(rows, 'items') else ()):
        row = row or {}
        raw = str(row.get('Path') or row.get('File') or '').strip()
        path = _audio_path_from_raw(raw, project_root) if raw else None
        if path is None:
            continue
        stem = _norm_audio_stem(Path(raw).name)
        if not stem:
            continue
        p = str(path)
        if p in seen:
            continue
        seen.add(p)
        out.append((stem, p, Path(raw).name))
        # 表里的 ID 常常是 `bgm_夜晚，断电之后，不存在的教室` —— 也拿来比一次
        rid = _norm_audio_stem(re.sub(r'^(bgm|music|snd|sfx|voice)[_-]', '',
                                      str(key), flags=re.IGNORECASE))
        if rid and rid != stem:
            out.append((rid, p, Path(raw).name))
    for hits in index.values():
        for path in hits:
            p = str(path)
            if p in seen:
                continue
            seen.add(p)
            out.append((_norm_audio_stem(path.stem), p, path.name))
    return out


def _audio_path_from_raw(raw: str, project_root):
    """
    表里的 `Path` → 真实文件：**先按"相对当前项目"解释，再按原样**。

    真实项目里有一层**重复嵌套的 `sr_general/` 目录**，于是
    `sr_general/game/bgm/x.mp3` 两套基准都能命中（`…/sr_general/sr_general/…`
    与 `…/sr_general/game/…`）。先试去掉前缀的那一套，命中的就是**引擎真正
    会用的那一份**（外层短路径），不是嵌套副本。
    """
    raw = str(raw or '').replace('\\', '/').strip()
    if not raw:
        return None
    parts = raw.split('/')
    for cand in ['/'.join(parts[i:]) for i in range(1, len(parts))] + [raw]:
        path = _path_from_rel(cand, project_root)
        if path is not None:
            return path
    return None


def resolve_audio_path_info(spec, registry=None, project_root=None,
                            kind: str = 'music', allow_approx: bool = True,
                            index: dict = None) -> dict:
    """
    音频名 → `{'path', 'query', 'exact', 'approx', 'matched'}`（**详细版**）。

    用户 2026-10-05：

    > 这个你确定 bgm 导入了吗？……是否正确呢？查询一下，因为我这里没有声音……

    精确解析的候选顺序（**先按"相对当前项目"解释，再按表里原样**）：
      ① 名字本身（`bgm/615.mp3` 这种脚本写法）；
      ② 资源表 `Path` 去掉前面的目录段（真实项目的表里存的是
         `sr_general/game/bgm/x.mp3`，而我们打开的是 `…/sr_general`）；
      ③ 资源表 `Path` 原样；
      ④ 按名字在 `game/bgm|audio|sound|voice` 里找同名文件（**有界**扫一遍）。

    每一步都用"文件真的存在"来验收（`_path_from_rel` 自己会查）——
    绝不返回一个不存在的路径去骗播放器。

    精确都落空且 `allow_approx` 时，再按**公共前缀**找一次近似（真实项目实测：
    `bgm/夜晚，断电之后…….mp3` 在磁盘上根本不存在，同名的真文件叫
    `夜晚，断电之后，不存在的教室.mp3`）。近似结果 `approx=True` 且带着
    `matched` 真名 —— 界面必须把它**说出来**，绝不悄悄放一首别的曲子。
    并列第一（前缀一样长的两个候选）时**不猜**，返回找不到。
    """
    query = audio_spec_name(spec)
    out = {'path': None, 'query': query, 'exact': False, 'approx': False,
           'matched': ''}
    if not query:
        return out
    table = {'music': 'music', 'sound': 'sounds', 'sfx': 'sounds',
             'voice': 'voices'}.get(kind, 'music')
    rows = getattr(registry, table, None) or {}
    row = rows.get(query) or rows.get(str(spec or '').strip()) or {}
    raw = str(row.get('Path') or row.get('File') or '').strip()
    candidates = [query]
    parts = raw.replace('\\', '/').split('/')
    candidates.extend('/'.join(parts[i:]) for i in range(1, len(parts)))
    if raw:
        candidates.append(raw)
    for cand in candidates:
        path = _path_from_rel(cand, project_root)
        if path is not None:
            out.update(path=str(path), exact=True, matched=Path(cand).name)
            return out
    index = _audio_index(project_root) if index is None else index
    for key in (query.lower(), Path(query).name.lower(),
                Path(query).stem.lower()):
        hits = index.get(key) or []
        if hits:
            out.update(path=str(hits[0]), exact=True, matched=hits[0].name)
            return out
    # ①b 表里**文件同名**的行（表的 ID 可能跟文件名不一样：真实项目里
    #     `guitar.ogg` 挂在 `bgm_…` 之类的 ID 下）。同名就是同一个文件，
    #     这不是"猜"。
    base = Path(query).name.lower()
    for _key, row in (rows.items() if hasattr(rows, 'items') else ()):
        raw = str((row or {}).get('Path') or (row or {}).get('File') or '')
        raw = raw.strip()
        if not raw or Path(raw.replace('\\', '/')).name.lower() != base:
            continue
        path = _audio_path_from_raw(raw, project_root)
        if path is not None:
            out.update(path=str(path), exact=True, matched=Path(raw).name)
            return out
    if not allow_approx:
        return out
    stem = _norm_audio_stem(query)
    if len(stem) < MIN_APPROX_PREFIX:
        return out
    scored = []
    for cand_stem, path, label in _approx_audio_pool(registry, table,
                                                     project_root, index):
        score = _common_prefix_len(stem, cand_stem)
        if score >= MIN_APPROX_PREFIX:
            scored.append((score, path, label))
    if not scored:
        return out
    scored.sort(key=lambda t: -t[0])
    top = scored[0][0]
    winners = [s for s in scored if s[0] == top]
    if len({_norm_audio_stem(w[2]) for w in winners}) > 1:
        return out                    # 并列第一但是**不同的曲子**：不猜
    # 同名的那几份是同一首歌（真实项目里有一层重复嵌套的 `sr_general/`
    # 目录，同一首曲子会在两处；同一首也常有 mp3/wav 两种格式）：
    # 先挑**跟脚本写法同一个扩展名**的，再挑路径最短的（离项目根最近的那份
    # 就是引擎真正会用的那份）。
    ext = Path(query).suffix.lower()
    winners.sort(key=lambda w: (Path(str(w[2])).suffix.lower() != ext,
                                str(w[1]).count('\\') + str(w[1]).count('/'),
                                len(str(w[1]))))
    out.update(path=winners[0][1], approx=True, matched=winners[0][2])
    return out


def resolve_audio_path(spec, registry=None, project_root=None,
                       kind: str = 'music', allow_approx: bool = False):
    """
    音频名 → 磁盘路径（**字符串**；找不到返回 None）。

    默认**只要精确匹配**（`allow_approx=False`）——"这个名字到底有没有文件"
    是一个可以拿去写进校验报告的事实，不能被近似匹配污染。要那份"顺带告诉你
    真名是什么"的详细信息，用 `resolve_audio_path_info`。
    """
    info = resolve_audio_path_info(spec, registry, project_root, kind,
                                   allow_approx=allow_approx)
    return info.get('path')


def stage_audio_cues(frame, registry=None, project_root=None,
                     allow_approx: bool = True) -> dict:
    """
    一帧的**声音提示**。

    返回：
      · `music` / `music_name`：这一拍该放的 BGM（持续状态）；
      · `music_exact` / `music_matched`：精确命中吗 / 实际命中的文件名；
      · `once`：`[(kind, 路径)]` —— 音效与语音（换到这一拍响一次）；
      · `missing`：**脚本里写了、磁盘上找不到**的那些名字（用户 2026-10-05
        "我这里没有声音"就是这个：脚本引用的文件名不存在）；
      · `notes`：给界面直接显示的一句话（缺文件 / 近似匹配）。

    纯函数（只有磁盘查找，没有 Qt）—— 预览拿它决定"该放什么"，测试直接钉这里。
    BGM 是**持续状态**（这一拍没换就继续放），音效 / 语音是**一次性**的
    （换到这一拍就响一次）。
    """
    out = {'music': None, 'music_name': '', 'music_exact': True,
           'music_matched': '', 'once': [], 'missing': [], 'notes': []}
    if frame is None:
        return out
    spec = str(getattr(frame, 'music', '') or '').strip()
    out['music_name'] = spec
    if spec:
        info = resolve_audio_path_info(spec, registry, project_root, 'music',
                                       allow_approx=allow_approx)
        out['music'] = info['path']
        out['music_exact'] = bool(info['exact'])
        out['music_matched'] = info['matched']
        if info['path'] and info['approx']:
            out['notes'].append(
                f'♪ {spec} → 近似匹配「{info["matched"]}」'
                f'（脚本里写的这个文件名不存在，游戏里也会找不到）')
        elif not info['path']:
            out['missing'].append(spec)
            out['notes'].append(f'♪ {spec}（磁盘上找不到这个音频文件）')
    for kind in ('sound', 'voice'):
        raw = str(getattr(frame, kind, '') or '').strip()
        if not raw:
            continue
        info = resolve_audio_path_info(raw, registry, project_root, kind,
                                       allow_approx=allow_approx)
        if info['path'] is not None:
            out['once'].append((kind, info['path']))
            if info['approx']:
                out['notes'].append(
                    f'{"♫" if kind == "sound" else "🎙"} {raw} → '
                    f'近似匹配「{info["matched"]}」')
        else:
            out['missing'].append(raw)
            out['notes'].append(
                f'{"♫" if kind == "sound" else "🎙"} {raw}（磁盘上找不到）')
    return out

