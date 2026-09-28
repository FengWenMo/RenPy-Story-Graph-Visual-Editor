"""
beat_editor.py
==============
应用内的剧本表编辑 UI（M5 / M6）。

与 Excel 双向路线并行，这里提供一个"不离开编辑器"的轻量编辑界面：
  · AssetPickerDialog   通用资产选择器（复用 AssetCompletion 的前缀/模糊搜索）
  · BeatEditorPanel     选中一拍后的快速编辑面板（角色/台词/语音/音乐/立绘…）
  · ScriptTableEditorWindow  载入一个 .rpy → 列出所有 Beat → 编辑 → 导出 .rpy

纯逻辑（搜索、Beat↔字段映射、新建资产回写）抽成函数，便于无头测试；
tkinter 只负责把数据摆到界面上。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import messagebox, simpledialog

from storygraph.assets.asset_completion import AssetCompletion
from storygraph.assets.asset_registry import describe_asset
from storygraph.story.beat_model import (
    Beat,
    KIND_EXTEND,
    KIND_NARRATION,
    _assign_identity,
    _join_show_items,
    _split_show_items,
    compose_stage,
    compose_audio_text,
    compose_code,
)
from storygraph.story.beat_build import (
    beats_from_parse_result,
    hydrate_from_dsl,
)
from storygraph.story.generation_guard import (
    generation_guard,
    occupied_labels,
    write_beats_to_rpy,
)
from storygraph.story.beat_render import render_beats_to_rpy, make_symbols
from storygraph.assets.asset_registry import add_asset_to_registry
from storygraph.parsing.renpy_parser import RenPyParser, ParseResultBuilder
from storygraph.story.stage_snapshot import stage_digest
from storygraph.pipeline.validate import validate_all


# ===================== 纯逻辑（可单测） =====================

def search_assets(engine, query: str = '', asset_types=None, limit: int = 50):
    """按前缀/模糊搜索资产，返回 [(asset_type, asset_id), ...]。"""
    comp = AssetCompletion(engine)
    return comp.complete(query, asset_types=asset_types, limit=limit)


def asset_row(engine, asset_type: str, asset_id: str):
    """取一条资产的原始行数据（找不到返回 None）。"""
    if engine is None or not asset_id:
        return None
    try:
        registry = engine.get_registry()
    except Exception:
        return None
    if registry is None:
        return None
    attr = AssetCompletion.TYPE_MAP.get(asset_type)
    if not attr:
        return None
    return (getattr(registry, attr, {}) or {}).get(asset_id)


def asset_detail(engine, asset_type: str, asset_id: str) -> str:
    """
    一条资产的人话说明。

    重点是 transform：选中 `leftish` 时要能看到"靠左站"那句注释，
    而不是只有一串 xpos——不然文案根本不知道该选哪个变换。
    """
    row = asset_row(engine, asset_type, asset_id)
    if row is None:
        return ''
    described = describe_asset(asset_type, row)
    if asset_type == 'transform':
        atl = (row.get('ATL') or '').strip()
        if atl:
            described += '\n\nATL:\n' + '\n'.join(
                '    ' + l.strip() for l in atl.splitlines() if l.strip())
    return described


def asset_preview_path(engine, asset_type: str, asset_id: str):
    """
    返回可用于预览的图片绝对路径（仅 image 类型且文件存在时）。

    资产表的 Path 是相对项目根目录的，所以这里要靠 engine.project_root
    拼回绝对路径；拼不出来就给 None，让调用方退化显示文字。
    """
    if asset_type != 'image':
        return None
    row = asset_row(engine, asset_type, asset_id)
    if not row:
        return None
    rel = (row.get('Path') or '').strip()
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        root = getattr(engine, 'project_root', None)
        if root:
            p = Path(root) / rel
    try:
        return p if p.exists() else None
    except OSError:
        return None


def load_thumbnail(path, max_size=(220, 140)):
    """
    读一张图并缩成缩略图，返回 tk PhotoImage（失败返回 None）。

    优先 Pillow（png/jpg/webp 通吃），没装则退到 tk.PhotoImage
    （只能读 png/gif，且只能整数倍缩小）。两条路都走不通就返回 None，
    调用方显示文字说明即可——预览是锦上添花，不能让它把功能挡住。
    """
    path = Path(path)
    if not path.exists():
        return None

    try:
        from PIL import Image, ImageTk      # type: ignore
        img = Image.open(path)
        img.thumbnail(max_size)
        return ImageTk.PhotoImage(img)
    except Exception:
        pass

    try:
        if not hasattr(tk, 'PhotoImage'):
            return None
        photo = tk.PhotoImage(file=str(path))
        w = max(photo.width(), 1)
        h = max(photo.height(), 1)
        factor = max(1, (w + max_size[0] - 1) // max_size[0],
                     (h + max_size[1] - 1) // max_size[1])
        if factor > 1:
            photo = photo.subsample(factor, factor)
        return photo
    except Exception:
        return None


def beat_to_fields(beat: Beat, symbols=None) -> dict:
    """
    Beat → 面板字段字典（界面与数据之间的中性中间层）。

    面板把**全部舞台指令投影进 Stage 一格**（给了符号表就合成中文写法
    `[显示] 凌跃捷 开心 左`，合不成就写原生写法 `show ling close_happy
    at left`——两种都能被编译器吃回去），另有 Scene / Voice 两个能点选的
    结构化列。Excel 那面则是 20 列结构化列 + Stage 高级通道；两边通过
    `compose_stage` / `hydrate_from_dsl` 互为投影，不会各输出一次。

    2026-09-28 新增 **Expression / Position 两个派生字段**：
    它们是"卡片上 4 个可编辑项"里的后两项（前两项是 Speaker / Text）。
    见 `beat_expression_word` / `beat_position_word` 的说明——
    这两个字段没有对应的 Beat 属性，是 `show` 的投影。
    """
    return {
        'Label': beat.label,
        'Kind': beat.kind,
        'Speaker': beat.speaker,
        'Text': beat.text,
        'Expression': beat_expression_word(beat, symbols),
        'Position': beat_position_word(beat, symbols),
        'Scene': beat.scene,
        'Stage': beat.stage or compose_stage(beat, symbols),
        'BGM': beat.bgm or compose_audio_text(beat, 'music'),
        'SFX': beat.sfx or compose_audio_text(beat, 'sound'),
        'Voice': beat.voice,
        'CodeBlock': compose_code(beat),
        'Comment': beat.comment,
        'Notes': beat.notes,
    }


def fields_to_beat(fields: dict, base: Optional[Beat] = None) -> Beat:
    """面板字段字典 → Beat。base 非空时在其上覆盖，保留 seq 等元信息。

    顺序很讲究：先把结构化字段写进 Beat，再用 Expression / Position
    改写 `beat.show`。**Stage 单元格不在这里重算**——面板显示 Stage 时
    会拿改好的 Beat 调 `compose_stage`（见 `BeatEditorPanel.collect`），
    否则 4 项卡片里改的表情/位置会在 Stage 里显示成旧值，
    正是"我表里看到的和生成的不一样"。
    """
    b = base or Beat()
    b.label = (fields.get('Label') or '').strip()
    b.kind = (fields.get('Kind') or 'say').strip() or 'say'
    b.speaker = (fields.get('Speaker') or '').strip()
    b.text = (fields.get('Text') or '').strip()
    b.scene = (fields.get('Scene') or '').strip()
    b.stage = (fields.get('Stage') or '').strip()
    b.bgm = (fields.get('BGM') or '').strip()
    b.sfx = (fields.get('SFX') or '').strip()
    b.voice = (fields.get('Voice') or '').strip()
    b.code = (fields.get('CodeBlock') or '').strip()
    # raw 已经并入 CodeBlock（唯一的"保真后门"），这里不再单独维护
    b.raw = ''
    b.comment = (fields.get('Comment') or '').strip()
    # 「备注」列已经取消（并入注释，见 Beat.from_row 的老表迁移）。
    # 这里再兜一次：旧 Beat / 旧表带来的 notes 绝不能静默丢掉。
    b.notes = (fields.get('Notes') or '').strip()
    if b.notes:
        b.comment = f'{b.comment}\n{b.notes}'.strip() if b.comment else b.notes
        b.notes = ''

    # —— 4 项卡片的派生两项：写回 show ——
    # 只有表单里真的带这两个键时才改写，避免"直接构造 Beat 的调用方"
    # （单测、CLI）在没碰表情/位置的情况下被清空立绘。
    if 'Expression' in fields:
        b.show = apply_expression(b.show, (fields.get('Expression') or '').strip())
    if 'Position' in fields:
        b.show = apply_position(b.show, (fields.get('Position') or '').strip())
    return b


# ===================== 4 项卡片：表情 / 位置的投影 =====================
#
# 为什么要有这一层：策划在卡片上真正会改的只有 **4 样**——
#   说话人 / 台词 / 表情 / 位置
# 而 Beat 上只有前两样有独立属性（speaker / text）；表情和位置是
# `beat.show`（`ling happy@left`）这一格的投影。直接让策划编辑 show 字符串
# 等于要求他记住 `图@位置` 这种语法——那正是要消灭的负担。
#
# 所以这里做一对纯函数（读出来 / 写回去），UI 只负责摆控件；
# 无头测试可以直接验它们，不必起 tkinter。


def _first_show_item(beat: Beat) -> dict:
    """取第一个「一般立绘」项（跳过 clause 形态）。没有就返回空 dict。"""
    for item in _split_show_items(getattr(beat, 'show', '') or ''):
        if item.get('clause') or not (item.get('image') or '').strip():
            continue
        return item
    return {}


def _show_items(beat: Beat) -> list:
    return [i for i in _split_show_items(getattr(beat, 'show', '') or '')
            if not i.get('clause') and (i.get('image') or '').strip()]


def beat_sprite_rows(beat: Beat, symbols=None) -> list:
    """
    Beat → **立绘数组**（每张立绘一行），供卡片显示/编辑。

    用户 2026-09-28 的设计意见：
    > 立绘是数组，不是单数 —— 一张卡片可以同时有多个立绘：
    > 主角在左、女主在右、路人居中。每个立绘独立控制位置。

    数据层**本来就支持**（`beat.show` 是 `;` 分隔的列表，渲染成多条 show），
    但卡片只显示第一张 —— 这里把它摊成数组。
    返回 `[{'image':..., 'tag':..., 'expression':..., 'position':...}, ...]`，
    顺序即出场顺序（不丢信息）。
    """
    rows = []
    for item in _split_show_items(getattr(beat, 'show', '') or ''):
        if item.get('clause') or not (item.get('image') or '').strip():
            # 复杂子句（behind/as/onlayer）不在这套结构里表达，跳过
            continue
        parts = (item.get('image') or '').split()
        tag = parts[0] if parts else ''
        attrs = parts[1:]
        one = Beat(seq=1, label='', text='', show=item.get('image') or '')
        one.at = item.get('at') or ''
        rows.append({
            'tag': tag,
            'image': item.get('image') or '',
            'expression': beat_expression_word(one, symbols),
            'position': beat_position_word(one, symbols),
            'at': item.get('at') or '',
            'clause': '',
        })
    return rows


def apply_sprite_rows(show: str, rows: list) -> str:
    """
    立绘数组 → `beat.show` 字符串（`a x@left; b y@right`）。

    **复杂子句原样保留**：它们结构化列表达不了，重建时不能丢。
    """
    kept = [i for i in _split_show_items(show or '') if i.get('clause')]
    out = []
    for row in rows or ():
        image = (row.get('image') or '').strip()
        if not image:
            continue
        at = (row.get('at') or '').strip()
        out.append({'clause': '', 'image': image, 'at': at})
    return _join_show_items(out + kept)


def inherited_state_hint(beats: list, idx: int) -> str:
    """
    「继承自上一拍」的提示文本（背景 / 立绘 / 音乐这类**持续状态**）。

    用户 2026-09-28 的设计意见：
    > 背景、BGM、环境层这类持续状态，默认从上一张卡片继承。
    > 卡片上显示"继承：教室-黄昏"，点击才覆盖。

    重要前提：**Ren'Py 本身就是继承语义** —— 背景/立绘/BGM 是状态，
    不写就是沿用上一拍。所以这一条**不需要改数据模型**，
    只要把"当前继承到什么"**显示出来**，创作者就不必每拍重填。

    这里复用 `stage_snapshot` 的同一套推导（预览与生成必须同源），
    返回一行人类可读的说明；第 0 拍或没有任何继承时返回 ''。
    """
    if not beats or not (0 < idx < len(beats)):
        return ''
    try:
        from storygraph.story.stage_snapshot import snapshot_rows
        rows = snapshot_rows(beats)
    except Exception:
        return ''
    if idx >= len(rows):
        return ''
    before, cur = rows[idx - 1], rows[idx]

    parts = []
    # 只有"这一拍自己没写"的才算继承
    if not (beats[idx].scene or '').strip() and before.get('scene'):
        parts.append(f'背景 {before["scene"]}')
    if not (beats[idx].show or '').strip() and before.get('actors'):
        parts.append(f'立绘 {before["actors"]}')
    if not (beats[idx].music or '').strip() and before.get('music'):
        parts.append(f'音乐 {before["music"]}')
    if not parts:
        return ''
    return '继承上一拍：' + '　·　'.join(parts)


def position_choices(symbols, limit: int = 40) -> list:
    """
    位置词候选：内置/词表位置 + 项目里真实存在的自定义 transform。

    自定义 transform 一定要带上——真实项目里 `i11`、`leftish` 这种
    比 `left` 用得多，策划找不到它们就只能手打（用户抱怨过"变换选不到"）。
    """
    out = []
    for word in (getattr(symbols, 'positions', {}) or {}):
        if word not in out:
            out.append(word)
    reg = getattr(symbols, 'registry', None)
    for name in sorted((getattr(reg, 'transforms', {}) or {})):
        if name not in out:
            out.append(name)
    return out[:limit]


def _ask_choice(parent, title, prompt, choices):
    """
    一句话选择框：候选少时用按钮列表，多时用列表 + 确定。

    tkinter 没有 combobox 的稳定跨平台实现（ttk 在无头测试里是桩），
    所以这里自己用 Toplevel + Listbox 做一个，行为可预期。
    """
    import tkinter as tk

    win = tk.Toplevel(parent)
    win.title(title)
    tk.Label(win, text=prompt).pack(anchor='w', padx=8, pady=(8, 2))
    box = tk.Listbox(win, height=min(12, max(3, len(choices))), width=34)
    for c in choices:
        box.insert('end', c)
    box.pack(fill='both', expand=True, padx=8)
    if hasattr(box, 'selection_set') and choices:
        box.selection_set(0)
    picked = {'v': None}

    def ok(_e=None):
        sel = box.curselection() if hasattr(box, 'curselection') else ()
        if sel:
            picked['v'] = choices[sel[0]]
        win.destroy()

    def cancel(_e=None):
        win.destroy()

    btns = tk.Frame(win)
    btns.pack(fill='x', padx=8, pady=8)
    tk.Button(btns, text='确定', command=ok).pack(side='left')
    tk.Button(btns, text='取消', command=cancel).pack(side='left')
    if hasattr(box, 'bind'):
        box.bind('<Double-Button-1>', ok)
    try:
        win.grab_set()
    except Exception:
        pass
    parent.wait_window(win) if hasattr(parent, 'wait_window') else None
    return picked['v']


def beat_expression_word(beat: Beat, symbols=None) -> str:
    """
    Beat → 表情词（给策划看的那个词）。

    **必须保证"翻成人话再翻回来"是逐字无损的**，做不到就显示原属性名。
    理由（2026-09-28 实测到的事故）：

        show ling close_happy@left
          → 显示成「开心」（`expression_word_for` 靠子串把 close_happy 匹配到 happy）
          → 写回时「开心」→ `happy`，**close_ 丢了**
          → `show ling happy at left`  ← 演出变了，而且是静默变的

    这跟 `dsl.stage_word_if_lossless` 是同一条原则：**猜错一个表情就是演出事故**，
    所以宁可给策划看 `close_happy`（丑但准），也不给人话（好看但会改演出）。

    无损标准：反推出的词经 `attr_for` 翻回来，必须**逐字等于**原属性。
    """
    item = _first_show_item(beat)
    if not item:
        return ''
    parts = (item.get('image') or '').split()
    if len(parts) < 2:
        return ''
    tag, attrs = parts[0], parts[1:]
    words = []
    for attr in attrs:
        word = ''
        if symbols is not None:
            try:
                candidate = symbols.expression_word_for(tag, attr)
            except Exception:
                candidate = ''
            # 自证无损：翻回来必须是同一个属性
            if candidate:
                try:
                    if normalize_expression(candidate, beat, symbols) == attr:
                        word = candidate
                except Exception:
                    word = ''
        words.append(word or attr)
    return ' '.join(words)


def beat_position_word(beat: Beat, symbols=None) -> str:
    """Beat → 位置词（如「左」）。立绘没写 `@位置` 时退回 Beat 的 `at`。"""
    item = _first_show_item(beat)
    if not item:
        return ''
    at = (item.get('at') or '').strip() or (getattr(beat, 'at', '') or '').strip()
    if not at or symbols is None:
        return at
    try:
        return symbols.position_word_for(at) or at
    except Exception:
        return at


def expression_choices(beat: Beat, symbols, limit: int = 200) -> list:
    """
    当前立绘的**可选表情词**清单（给下拉/选择器用）。

    来源是 `symbols.attrs_of_tag(tag)`——即资源表里这个 tag 真正声明过的属性。
    这比 `DEFAULT_EXPRESSIONS` 准：项目自己的表情命名才算数，
    而且不会给出"这个角色根本没有的表情"。
    """
    item = _first_show_item(beat)
    if not item or symbols is None:
        return []
    tag = (item.get('image') or '').split()[0] if item.get('image') else ''
    try:
        attrs = symbols.attrs_of_tag(tag)
    except Exception:
        return []
    out = []
    for attr in attrs[:limit]:
        try:
            word = symbols.expression_word_for(tag, attr)
        except Exception:
            word = ''
        out.append(word or attr)
    return out


def normalize_expression(word: str, beat: Beat, symbols) -> str:
    """
    表情词 → 图像属性。**这一步必须在写回 `show` 之前做。**

    策划在卡片上看到/填写的是中文（「开心」），但脚本里必须是属性
    （`happy` / `close_happy`）——否则生成的就是 `show eileen 开心`，
    一个 Ren'Py 语法上不存在的图像。这条曾经只是"编译期才报错"，
    现在在写回的那一刻就落地。

    解析不到就**原样返回**：策划可能就是在写英文属性名（`close_happy`），
    那种写法本来就合法，不该被改写。
    """
    word = (word or '').strip()
    if not word or symbols is None:
        return word
    item = _first_show_item(beat)
    tag = (item.get('image') or '').split()[0] if item.get('image') else ''
    if not tag:
        return word
    try:
        return symbols.attr_for(tag, word) or word
    except Exception:
        return word


def normalize_position(word: str, symbols) -> str:
    """
    位置词 → transform（`左` → `left`）。

    解析不到就原样返回：项目自定义 transform（`i11`、`leftish`）
    本来就是英文/自定义名。
    """
    word = (word or '').strip()
    if not word or symbols is None:
        return word
    try:
        return symbols.position_for(word) or word
    except Exception:
        return word


def apply_expression(show: str, word: str) -> str:
    """
    把 `show` 里**第一个立绘**的表情换成 `word`，其余项原样保留。

    语义：
      · `word` 为空 → 去掉表情（只留 tag）；
      · 不改位置，不动其他立绘，**不碰 clause 形态的项**（它们结构化列
        表达不了，改了会丢修饰符）。

    `word` 会先经 `attribute_for` 归一：策划写中文就落成英文属性，
    写 `close_happy` 就原样——与 DSL 编译期同一套规则，避免"卡片上写中文、
    脚本里出中文"。
    """
    items = _split_show_items(show or '')
    if not items:
        # 没有立绘可改：不凭空造一个（那会改变演出）
        return show or ''
    out = []
    done = False
    for item in items:
        if item.get('clause') or done:
            out.append(item)
            continue
        parts = (item.get('image') or '').split()
        if not parts:
            out.append(item)
            continue
        tag = parts[0]
        new = dict(item)
        new['image'] = ' '.join([p for p in ([tag] + ([word] if word else []))
                                 if p])
        out.append(new)
        done = True
    return _join_show_items(out)


def apply_position(show: str, word: str) -> str:
    """
    把位置写进 `show` 的**每一个普通立绘**项（`图@位置`）。

    为什么是"每一个"而不是第一个：位置是舞台级的观感，同一拍里两张立绘
    站不同位置时，策划改的是"这句台词时谁在左谁在右"——这里给的是
    统一位置，单张要精细控制在「高级」区改 `图@位置`。
    """
    items = _split_show_items(show or '')
    if not items:
        return show or ''
    out = []
    for item in items:
        if item.get('clause'):
            out.append(item)
            continue
        new = dict(item)
        new['at'] = word or ''
        out.append(new)
    return _join_show_items(out)


# ===================== 资产选择器 =====================

class AssetPickerDialog(tk.Toplevel):
    """
    通用资产选择弹窗：搜索 → 列表 → 说明/预览 → 选中；支持"新建资产"回写。

    比旧版多了两块信息，专门解决"只看文字根本不清楚效果"：
      · 说明面板：显示这条资产的人话描述（transform 会带上它的注释与 ATL）
      · 预览图：图片资产直接显示缩略图，不用靠脑补
    """

    def __init__(self, parent, engine, asset_types=None, title='选择资产',
                 allow_new: bool = True, on_new_asset=None):
        super().__init__(parent)
        self.engine = engine
        self.asset_types = asset_types or list(AssetCompletion.TYPE_MAP.keys())
        self.title(title)
        self.result = None
        self._items = []
        self._photo = None          # 必须留引用，否则被 GC 掉图就没了
        self._allow_new = allow_new
        self._on_new_asset = on_new_asset
        self._build()

    def _build(self):
        top = tk.Frame(self)
        top.pack(fill='x', padx=8, pady=(8, 0))
        tk.Label(top, text="搜索：").pack(side='left')
        self._query = tk.StringVar()
        entry = tk.Entry(top, textvariable=self._query)
        entry.pack(side='left', fill='x', expand=True)

        body = tk.Frame(self)
        body.pack(fill='both', expand=True, padx=8, pady=8)

        left = tk.Frame(body)
        left.pack(side='left', fill='both', expand=True)
        self._list = tk.Listbox(left, height=14, width=34)
        self._list.pack(fill='both', expand=True)
        if hasattr(self._list, 'bind'):
            self._list.bind('<<ListboxSelect>>', self._on_select)

        right = tk.Frame(body, width=280)
        right.pack(side='right', fill='y', padx=(8, 0))
        self._preview = tk.Label(right, text='（无预览）', height=8,
                                 width=34, anchor='center',
                                 bg='#f0f0f0', fg='#666')
        self._preview.pack(fill='x')
        self._detail = tk.Label(right, text='', justify='left', anchor='nw',
                                wraplength=280, height=8, fg='#222')
        self._detail.pack(fill='both', expand=True, pady=(6, 0))

        # 输入即过滤
        if hasattr(self._query, 'trace_add'):
            self._query.trace_add('write', lambda *a: self._populate(
                self._query.get()))

        self._populate('')

        btn = tk.Frame(self)
        btn.pack(fill='x', padx=8, pady=(0, 8))
        tk.Button(btn, text="确定", command=self._on_ok).pack(side='left')
        tk.Button(btn, text="取消", command=self._on_cancel).pack(side='left')
        if self._allow_new:
            tk.Button(btn, text="＋ 新建资产…",
                      command=self._on_new).pack(side='right')
        if hasattr(self._list, 'bind'):
            self._list.bind('<Double-Button-1>', lambda e: self._on_ok())

    def _populate(self, query: str = ''):
        self._items = search_assets(self.engine, query, self.asset_types)
        if hasattr(self._list, 'delete'):
            self._list.delete(0, 'end')
        if hasattr(self._list, 'insert'):
            for atype, aid in self._items:
                self._list.insert('end', f"[{atype}] {aid}")
        self._show_detail(None)

    def _selected(self):
        if not hasattr(self._list, 'curselection'):
            return None
        sel = self._list.curselection()
        if not sel:
            return None
        return self._items[sel[0]] if sel[0] < len(self._items) else None

    def _on_select(self, event=None):
        self._show_detail(self._selected())

    def _show_detail(self, item):
        """更新右侧说明 + 预览。item 为 None 时清空。"""
        if item is None:
            self._set_text(self._detail, '')
            self._set_photo(None)
            self._set_text(self._preview, '（无预览）')
            return
        atype, aid = item
        self._set_text(self._detail, asset_detail(self.engine, atype, aid))

        path = asset_preview_path(self.engine, atype, aid)
        if path is None:
            self._set_photo(None)
            self._set_text(
                self._preview,
                '（该类型无预览图）' if atype != 'image'
                else '（找不到图片文件）')
            return
        photo = load_thumbnail(path)
        if photo is None:
            self._set_photo(None)
            self._set_text(self._preview, f'无法预览：{Path(path).name}')
        else:
            self._photo = photo
            self._set_photo(photo)

    @staticmethod
    def _set_text(widget, text):
        if hasattr(widget, 'config'):
            try:
                widget.config(text=text)
            except Exception:
                pass

    def _set_photo(self, photo):
        if hasattr(self._preview, 'config'):
            try:
                self._preview.config(image=photo if photo is not None else '')
            except Exception:
                pass

    def _on_ok(self):
        sel = self._selected()
        if sel is not None:
            self.result = sel
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()

    def _on_new(self):
        asset_type = simpledialog.askstring("新建资产", "资产类型：")
        if not asset_type or asset_type not in AssetCompletion.TYPE_MAP:
            messagebox.showerror("类型无效",
                                 "请输入合法类型："
                                 + "/".join(AssetCompletion.TYPE_MAP))
            return
        asset_id = simpledialog.askstring("新建资产", "资产 ID：")
        if not asset_id:
            return
        path = simpledialog.askstring("新建资产", "资源路径（可选）：") or ''
        if self._on_new_asset is not None:
            self._on_new_asset(asset_type.strip(), asset_id.strip(), path.strip())
        # 选中刚新建的，方便直接填回
        self.result = (asset_type.strip(), asset_id.strip())
        self.destroy()

    def pick(self):
        """模态打开，返回 (asset_type, asset_id) 或 None。"""
        self.grab_set()
        self.wait_window(self)
        return self.result


# ===================== Beat 快速编辑面板 =====================

class BeatEditorPanel(tk.Frame):
    """
    选中一拍后的快速编辑面板 —— **4 项卡片**。

    策划真正会改的只有四样（`PRIMARY_FIELDS`）：

        说话人 / 台词 / 表情 / 位置

    前两样是 Beat 的真实属性；后两样是 `show`（`ling happy@left`）的投影
    （见 `apply_expression` / `apply_position`）。**不能让策划直接编辑
    `图@位置` 这种语法**——那正是要消灭的负担。

    其余全部进「高级」区（`ADVANCED_FIELDS`）并**默认收起 + 灰显只读**：
    不是删掉（程序与老策划还要用），而是不让它们占第一眼的注意力。
    收起时**不会清空**里面的值——`collect_beat` 从当前 Beat 出发，
    只覆盖真正在编辑的字段（曾经整体覆盖会把 Stage/音频/注释清掉）。

    与 Excel 那面的关系：Excel 是 20 列结构化列 + Stage 高级通道，
    两边通过 `compose_stage` / `hydrate_from_dsl` 互为投影，不会各输出一次。
    """

    #: **4 项卡片**：策划真正会改的就这四样。
    #: 前两项是 Beat 的真实属性，后两项是 `show` 的投影（见 apply_expression /
    #: apply_position）。其余全部挤进「高级」区并**灰显**——
    #: 不是删掉（程序与老策划还要用），而是不让它们占第一眼的注意力。
    PRIMARY_FIELDS = ['Speaker', 'Text', 'Expression', 'Position']

    #: 高级区（默认灰显、可展开）：Stage/音频/场景/语音/注释/代码/章节/类型
    ADVANCED_FIELDS = ['Scene', 'Stage', 'BGM', 'SFX', 'Voice',
                       'Comment', 'CodeBlock', 'Label', 'Kind', 'Notes']

    # 走资产选择器的字段（一格一个值，能点选）
    _ASSET_FIELDS = {
        'Speaker': ('character',),
        'Scene': ('image',),
        'Voice': ('voice',),
    }

    # 单行字段（按界面上从上到下的顺序）
    _SINGLE_FIELDS = ['Speaker', 'Scene', 'Voice', 'Comment',
                      'Label', 'Kind', 'Notes', 'Expression', 'Position']
    # 多行字段 → 行高（DSL 与长文本要能换行写）
    _MULTI_FIELDS = {'Text': 4, 'Stage': 3, 'BGM': 2, 'SFX': 2,
                     'CodeBlock': 3}
    # 真正的展示顺序：先 4 项卡片，再高级区
    _ORDER = PRIMARY_FIELDS + ADVANCED_FIELDS

    _HINTS = {
        'Expression': '开心 / 难过 / 生气……（从该立绘声明过的表情里选）',
        'Position': '左 / 中 / 右 / 特写……（不填＝原地）',
        'Stage': '[显示] 凌跃捷 开心 左 ; [转场] dissolve',
        'BGM': '[音乐] 春天花瓣 fadein 1.0　/　[停止音乐]',
        'SFX': '[音效] 开门',
    }

    def __init__(self, parent, engine, on_new_asset=None, **kw):
        super().__init__(parent, **kw)
        self.engine = engine
        self.on_new_asset = on_new_asset
        self._beat = None
        self._widgets = {}
        self._multi = set()
        #: 单行字段的 StringVar（Entry 的读写只能通过它，见 _wset）
        self._vars = {}
        self._note_var = tk.StringVar(value='')
        self._stage_var = tk.StringVar(value='')
        self._adv_open = False
        self._advanced_rows = []
        #: 立绘数组的每行控件（见 add_sprite_row / _collect_sprites）
        self._sprite_widgets = []
        self._build()

    # ---------- 控件读写（StringVar 与 Text 统一） ----------

    def _wget(self, name: str) -> str:
        if name in self._multi:
            w = self._widgets.get(name)
            if w is None:
                return ''
            return w.get('1.0', 'end-1c') if hasattr(w, 'get') else ''
        var = self._vars.get(name)
        if var is None:
            return ''
        return var.get() if hasattr(var, 'get') else ''

    def _wset(self, name: str, value: str):
        """
        把一个字段写进控件。

        **单行字段走 StringVar，多行字段走 Text 的 delete/insert。**
        不能对控件本身调 `set()`——`tk.Entry` 没有这个方法，
        以前那样写会让单行字段**静默写不进去**（用户 2026-09-28 实测：
        卡片上只有台词有值，说话人/表情/位置全空）。
        """
        if name in self._multi:
            w = self._widgets.get(name)
            if w is None or not hasattr(w, 'delete'):
                return
            w.delete('1.0', 'end')
            if value:
                w.insert('1.0', value)
            return
        var = self._vars.get(name)
        if var is not None and hasattr(var, 'set'):
            var.set(value or '')

    def _build(self):
        row = 0

        # ---- 4 项卡片（永远可见、可编辑） ----
        tk.Label(self, text='这一拍', font=('微软雅黑', 10, 'bold')
                 ).grid(row=row, column=0, columnspan=5, sticky='w')
        row += 1
        # 先说话人与台词；立绘用下面的「立绘数组」独立编辑
        row = self._build_field('Speaker', row, enabled=True)
        row = self._build_field('Text', row, enabled=True)

        # ---- 立绘（数组，不是单数） ----
        # 用户 2026-09-28：一张卡片可以同时有多个立绘，各自独立控制位置。
        # 数据层本来就支持（`show` 是 `;` 分隔列表），但以前卡片只显示第一张。
        tk.Label(self, text='立绘（可多个）',
                 font=('微软雅黑', 9, 'bold')).grid(
                     row=row, column=0, columnspan=5, sticky='w', pady=(8, 0))
        row += 1
        self._sprites_frame = tk.Frame(self)
        self._sprites_frame.grid(row=row, column=0, columnspan=5, sticky='we')
        self._sprites_frame.columnconfigure(1, weight=1)
        row += 1
        tk.Button(self, text='＋ 加一张立绘', command=self.add_sprite_row
                  ).grid(row=row, column=0, sticky='w', pady=(2, 0))
        row += 1

        # ---- 继承提示（持续状态默认继承，这里只是把它显示出来） ----
        self._inherit_var = tk.StringVar(value='')
        tk.Label(self, textvariable=self._inherit_var, justify='left',
                 anchor='w', wraplength=520, fg='#0A5',
                 font=('微软雅黑', 8)).grid(
                     row=row, column=0, columnspan=5, sticky='we', pady=(6, 0))
        row += 1

        # ---- 条件显示（台词/立绘/背景都能挂，统一一个入口） ----
        cond = tk.Frame(self)
        cond.grid(row=row, column=0, columnspan=5, sticky='we', pady=(6, 0))
        tk.Button(cond, text='＋ 条件', command=self._focus_condition
                  ).pack(side='left')
        self._condition_var = tk.StringVar(value='')
        tk.Entry(cond, textvariable=self._condition_var,
                 font=('Consolas', 9)).pack(side='left', fill='x',
                                            expand=True, padx=(6, 0))
        tk.Label(cond, text='（留空＝无条件；如 love >= 5）',
                 font=('微软雅黑', 8), fg='#888').pack(side='left')
        row += 1

        # ---- 高级区（默认隐藏 + 灰显） ----
        self._adv_toggle = tk.Button(
            self, text='▸ 高级（背景 / 音乐 / 音效 / 语音 / 注释 / 代码）',
            command=self.toggle_advanced, anchor='w',
            relief='flat', fg='#555')
        self._adv_toggle.grid(row=row, column=0, columnspan=5,
                              sticky='we', pady=(8, 0))
        row += 1

        self._adv_frame = tk.Frame(self)
        self._adv_frame.grid(row=row, column=0, columnspan=5, sticky='we')
        self._adv_frame.grid_remove()          # 默认收起
        self._adv_row = 0
        for name in self.ADVANCED_FIELDS:
            self._adv_row = self._build_field(
                name, self._adv_row, enabled=False, parent=self._adv_frame)
        self._adv_frame.columnconfigure(1, weight=1)
        row += 1

        self.columnconfigure(1, weight=1)

        # 动作条：预览编译结果 / 看词表 / 解释舞台
        act = tk.Frame(self)
        act.grid(row=row, column=0, columnspan=5, sticky='we', pady=(6, 0))
        tk.Button(act, text="预览编译结果",
                  command=self.preview_compiled).pack(side='left')
        tk.Button(act, text="舞台词表",
                  command=self.show_keywords).pack(side='left')
        tk.Button(act, text="解释舞台列",
                  command=self.explain_stage).pack(side='left')
        row += 1

        # 说明区：选中/填好一个变换、立绘、音乐后，这里给出"它是什么"
        tk.Label(self, text="说明").grid(row=row, column=0, sticky='nw')
        tk.Label(self, textvariable=self._note_var, justify='left',
                 anchor='nw', wraplength=380, fg='#444'
                 ).grid(row=row, column=1, columnspan=4, sticky='we')
        row += 1

        # 舞台快照：这一拍播出来玩家看见什么（背景/立绘/音乐）
        tk.Label(self, text="舞台快照").grid(row=row, column=0, sticky='nw')
        tk.Label(self, textvariable=self._stage_var, justify='left',
                 anchor='nw', wraplength=380, fg='#0A5'
                 ).grid(row=row, column=1, columnspan=4, sticky='we')

    def _build_field(self, name, row, enabled=True, parent=None):
        """建一个字段行。`enabled=False` 时**灰显且只读**（高级区用）。"""
        host = parent if parent is not None else self
        label = name
        hint = self._HINTS.get(name, '')
        col0 = tk.Frame(host)
        col0.grid(row=row, column=0, sticky='nw')
        tk.Label(col0, text=label,
                 fg='#222' if enabled else '#999').pack(anchor='w')
        if hint:
            tk.Label(col0, text=hint, font=('微软雅黑', 7),
                     fg='#888' if enabled else '#bbb',
                     wraplength=120, justify='left').pack(anchor='w')

        if name in self._MULTI_FIELDS:
            import tkinter.scrolledtext as st
            widget = st.ScrolledText(host, height=self._MULTI_FIELDS[name],
                                     wrap='word', font=('Consolas', 9),
                                     background='white' if enabled else '#f2f2f2')
            widget.grid(row=row, column=1, sticky='we')
            self._multi.add(name)
            if not enabled and hasattr(widget, 'configure'):
                widget.configure(state='disabled')
        else:
            var = tk.StringVar()
            widget = tk.Entry(host, textvariable=var,
                              state='normal' if enabled else 'disabled',
                              disabledbackground='#f2f2f2',
                              disabledforeground='#999')
            widget.grid(row=row, column=1, sticky='we')
            # **必须自己记住 StringVar**：`tk.Entry` 没有 `set()` 方法，
            # 读写只能通过 textvariable。以前 `_wset` 判 `hasattr(w, 'set')`
            # 对 Entry 恒为 False，于是单行字段**静默**写不进去
            # （多行的 ScrolledText 有 insert()，所以只有 Entry 受影响）。
            self._vars[name] = var
        self._widgets[name] = widget

        types = self._ASSET_FIELDS.get(name)
        if types and enabled:
            tk.Button(host, text="🔍", width=2,
                      command=lambda n=name, t=types: self._pick(n, t)
                      ).grid(row=row, column=2)
            tk.Button(host, text="?说明", width=5,
                      command=lambda n=name, t=types: self._explain(n, t)
                      ).grid(row=row, column=3)
        return row + 1

    def toggle_advanced(self):
        """展开/收起高级区。收起时字段**灰显且只读**，数据照旧保留。"""
        self._adv_open = not self._adv_open
        if self._adv_open:
            self._adv_frame.grid()
            self._adv_toggle.config(
                text='▾ 高级（背景 / 音乐 / 音效 / 语音 / 注释 / 代码）')
        else:
            self._adv_frame.grid_remove()
            self._adv_toggle.config(
                text='▸ 高级（背景 / 音乐 / 音效 / 语音 / 注释 / 代码）')

    # ---------- 立绘数组（可增删，每张独立表情/位置） ----------

    def _clear_sprite_rows(self):
        for w in list(getattr(self, '_sprite_widgets', []) or []):
            for sub in (w.get('frame'),):
                if sub is not None and hasattr(sub, 'destroy'):
                    sub.destroy()
        self._sprite_widgets = []

    def add_sprite_row(self, row: dict = None):
        """
        加一行立绘编辑（tag / 选图 / 表情 / 位置 / 删除）。

        `row` 为 None 时是"用户点＋"——给一张空行，让他用 🔍 选图。
        """
        row = dict(row or {})
        host = getattr(self, '_sprites_frame', None)
        if host is None:
            return
        n = len(getattr(self, '_sprite_widgets', []) or [])
        frame = tk.Frame(host)
        frame.grid(row=n, column=0, columnspan=5, sticky='we', pady=1)

        img_var = tk.StringVar(value=row.get('image') or '')
        expr_var = tk.StringVar(value=row.get('expression') or '')
        pos_var = tk.StringVar(value=row.get('position') or '')
        entry = tk.Entry(frame, textvariable=img_var, width=18,
                         font=('Consolas', 9))
        entry.grid(row=0, column=0, sticky='we')
        tk.Button(frame, text='🔍', width=2,
                  command=lambda: self._pick_sprite_image(img_var)
                  ).grid(row=0, column=1)
        tk.Entry(frame, textvariable=expr_var, width=10
                 ).grid(row=0, column=2, padx=(4, 0))
        tk.Entry(frame, textvariable=pos_var, width=8
                 ).grid(row=0, column=3, padx=(4, 0))
        tk.Button(frame, text='✕', width=2,
                  command=lambda f=frame: self._remove_sprite_row(f)
                  ).grid(row=0, column=4, padx=(4, 0))

        self._sprite_widgets.append({'frame': frame, 'image': img_var,
                                     'expression': expr_var,
                                     'position': pos_var})

    def _remove_sprite_row(self, frame):
        kept = []
        for w in self._sprite_widgets:
            if w.get('frame') is frame:
                if hasattr(frame, 'destroy'):
                    frame.destroy()
                continue
            kept.append(w)
        self._sprite_widgets = kept

    def _pick_sprite_image(self, img_var):
        """给某一行立绘选图。"""
        dlg = AssetPickerDialog(self, self.engine, ['image'],
                                title='选择立绘')
        result = dlg.pick()
        if result is not None:
            _atype, aid = result
            if hasattr(img_var, 'set'):
                img_var.set(aid)

    def _focus_condition(self):
        """「＋ 条件」——高级区里的 Kind/Label 旁边给不了条件，这里只提示用法。"""
        self._set_note(
            '条件写在这一行的输入框里（如 `love >= 5`）。\n'
            '生成脚本时这一拍会被包在 `if <条件>:` 里；'
            '留空表示无条件。')

    def _collect_sprites(self) -> str:
        """
        立绘数组 → `show` 字符串（`ling happy@left; mary smile@right`）。

        **复杂子句（behind/as/onlayer）原样保留** —— 它们不出现在数组里，
        重建时要带回去，否则一编辑就丢。
        """
        symbols = self._symbols()
        rows = []
        for w in getattr(self, '_sprite_widgets', []) or []:
            image = (w['image'].get() or '').strip()
            if not image:
                continue
            expr = (w['expression'].get() or '').strip()
            pos = (w['position'].get() or '').strip()
            probe = Beat(seq=1, label='', text='', show=image)
            tag = image.split()[0]
            # 表情：把中文词归一成属性（不写就保留原属性）
            if expr:
                parts = image.split()
                attrs = [normalize_expression(expr, probe, symbols)]
                image = ' '.join([tag] + [a for a in attrs if a])
            rows.append({'image': image, 'at': normalize_position(pos, symbols),
                         'clause': ''})
        kept = [i for i in _split_show_items(
            getattr(self._beat, 'show', '') or '') if i.get('clause')]
        return _join_show_items(rows + kept)

    # ---------- 符号表 / 审计 ----------

    def _symbols(self):
        reg = None
        try:
            reg = self.engine.get_registry() if self.engine else None
        except Exception:
            reg = None
        return make_symbols(reg)

    # ---------- 交互 ----------

    def set_stage_snapshot(self, text: str):
        """外部（选中某拍时）灌入这一拍的舞台状态文本。"""
        if hasattr(self._stage_var, 'set'):
            self._stage_var.set(text or '')

    def _set_note(self, text: str):
        if hasattr(self._note_var, 'set'):
            self._note_var.set(text or '')

    def _field_asset_type(self, field_name):
        types = self._ASSET_FIELDS.get(field_name) or ()
        return types[0] if types else None

    def _explain(self, field_name, asset_types):
        """点「?说明」：解释当前字段里填的值是什么。"""
        value = self._wget(field_name).strip()
        if not value:
            self._set_note(f'{field_name}：还没填。点 🔍 从资源表里选。')
            return
        texts = []
        for token in re.split(r'[;\s]+', value):
            if not token or token == '@':
                continue
            detail = ''
            for atype in list(asset_types) + ['image', 'transform', 'music',
                                              'sound', 'voice', 'character']:
                detail = asset_detail(self.engine, atype, token)
                if detail:
                    break
            texts.append(f'{token}：{detail}' if detail
                         else f'{token}：（资源表里没有这条，可能是内置或未登记）')
        self._set_note('\n'.join(texts))

    def _pick(self, field_name, asset_types):
        dlg = AssetPickerDialog(self, self.engine, list(asset_types),
                                title=f"选择 {field_name}")
        result = dlg.pick()
        if result is not None:
            _atype, aid = result
            self._wset(field_name, aid)
            detail = asset_detail(self.engine, _atype, aid)
            self._set_note(f'{field_name} = {aid}\n{detail}')
            if field_name == 'Speaker':
                # 换了说话人 → 表情候选跟着换（旧表情可能属于上一个角色）
                self._refresh_expression_hint()

    def pick_expression(self):
        """
        从**该立绘声明过的表情**里挑（不靠记词，也不给不存在的表情）。

        候选来自 `expression_choices`（资源表里这个 tag 的 Attributes）；
        一个都没有时退回"手填 + 词表提示"，绝不因为查不到就让人没法写。
        """
        choices = expression_choices(self.collect(), self._symbols())
        if not choices:
            self._set_note(
                '这个立绘在资源表里没有声明过表情属性。\n'
                '可以直接手填（如 开心 / close_happy），或先在资源表里补属性。')
            return
        picked = _ask_choice(self, '选表情', '这一拍用哪个表情？', choices)
        if picked is not None:
            self._wset('Expression', picked)
            self._set_note(f'表情 = {picked}')

    def pick_position(self):
        """从位置词里挑（内置位置 + 项目自定义 transform）。"""
        choices = position_choices(self._symbols())
        picked = _ask_choice(self, '选位置', '这一拍站哪里？', choices)
        if picked is not None:
            self._wset('Position', picked)
            self._set_note(f'位置 = {picked}')

    def _refresh_expression_hint(self):
        choices = expression_choices(self.collect(), self._symbols())
        if choices:
            self._set_note('这个立绘可选的表情：' + '、'.join(choices[:20]))

    def explain_stage(self):
        """把 Stage / BGM / SFX 逐条解释：会被翻译成什么，或缺什么。"""
        from storygraph.story.dsl import compile_stage, compile_audio, Audit, TODO

        symbols = self._symbols()
        audit = Audit(symbols)
        lines = []
        stage = self._wget('Stage').strip()
        if stage:
            lines.append('【舞台】')
            for intent, code, status in compile_stage(stage, symbols, audit):
                mark = '⚠ ' if status == TODO else '  '
                lines.append(f'{mark}{intent}  →  {code}')
        for name, channel, label in (('BGM', 'music', '音乐'),
                                     ('SFX', 'sound', '音效')):
            text = self._wget(name).strip()
            if not text:
                continue
            lines.append(f'【{label}】')
            for intent, code, status in compile_audio(text, channel, symbols,
                                                      audit):
                mark = '⚠ ' if status == TODO else '  '
                lines.append(f'{mark}{intent}  →  {code}')
        self._set_note('\n'.join(lines) or '舞台 / BGM / SFX 都还没填。')

    def preview_compiled(self):
        """把这一拍编译成 Ren'Py 代码看一眼——所见即所得。"""
        beat = self.collect_beat()
        text = render_beats_to_rpy([beat], label_name=beat.label or 'preview',
                                   registry=self._registry())
        win = tk.Toplevel(self)
        win.title('编译预览（这一拍会生成什么）')
        import tkinter.scrolledtext as st
        box = st.ScrolledText(win, width=84, height=22,
                              font=('Consolas', 10))
        box.pack(fill='both', expand=True, padx=8, pady=8)
        box.insert('1.0', text)
        box.configure(state='disabled')
        tk.Button(win, text='关闭', command=win.destroy).pack(pady=(0, 8))

    def show_keywords(self):
        """把 Stage 里能写的词摊出来（角色/位置/表情/转场）。"""
        from storygraph.story.dsl import DEFAULT_POSITIONS, DEFAULT_EXPRESSIONS, NON_ACTOR_TAGS  # noqa

        symbols = self._symbols()
        lines = ['【角色】可写显示名或变量名，后面跟表情/位置']
        for var, row in (symbols._by_var or {}).items():
            name = (row.get('Name') or var)
            tag = row.get('ImageTag') or var
            lines.append(f'  {name}（变量 {var}，立绘 {tag}）')
        lines.append('')
        lines.append('【位置】' + '、'.join(DEFAULT_POSITIONS))
        lines.append('【表情】' + '、'.join(
            w for w, m in DEFAULT_EXPRESSIONS.items() if m))
        lines.append('')
        lines.append('写法：[显示] 角色 表情 位置　例：[显示] 凌跃捷 开心 左')
        self._set_note('\n'.join(lines))

    def _registry(self):
        try:
            return self.engine.get_registry() if self.engine else None
        except Exception:
            return None

    # ---------- 载入 / 回写 ----------

    def load_beat(self, beat: Beat, inherit_hint: str = ''):
        self._beat = beat
        symbols = self._symbols()
        fields = beat_to_fields(beat, symbols)
        for name in self._ORDER:
            self._wset(name, fields.get(name, ''))

        # —— 立绘数组 ——
        self._clear_sprite_rows()
        for row in beat_sprite_rows(beat, symbols):
            self.add_sprite_row(row)

        # —— 条件 ——
        cond_var = getattr(self, '_condition_var', None)
        if cond_var is not None and hasattr(cond_var, 'set'):
            cond_var.set(getattr(beat, 'condition', '') or '')

        # —— 继承提示（持续状态默认继承，只是显示出来） ——
        inh = getattr(self, '_inherit_var', None)
        if inh is not None and hasattr(inh, 'set'):
            inh.set(inherit_hint or '')

        self._set_note('')
        # 载入时就把舞台列解释一遍——文案最常问的就是"这行会变成什么"
        if any((beat.stage, beat.bgm, beat.sfx)):
            self.explain_stage()
        # 防御性警告：**会变成旁白**的拍必须显形
        # （用户 2026-09-28 报"幽灵旁白"时，卡片上什么都没提示，
        #  只看得出 Speaker 是空的——而空着也能点"下一句"，错误就被带过去了）
        self._warn_if_speaker_missing(beat)

    def _warn_if_speaker_missing(self, beat: Beat):
        """
        有台词、却没有任何说话人 → 导出后就是旁白，在这里说清楚。

        `extend` 例外：它本来就不带说话人（续上一句），渲染成 `extend "…"`。
        旁白（KIND_NARRATION）也例外——它**就是**旁白。
        """
        if not (beat.text or '').strip():
            return
        if beat.speaker:
            return
        if beat.kind in (KIND_NARRATION, KIND_EXTEND):
            return
        self._set_note(
            '⚠ 这一拍有台词但没有说话人 —— 导出后会变成**旁白**。\n'
            f'   kind={beat.kind!r}，请确认这句是谁说的；'
            '若确实是旁白，把「类型」改成 narration。')

    def collect_beat(self) -> Beat:
        """
        表单 → Beat。

        关键：**从当前 Beat 出发，只覆盖表单里真正在编辑的字段**。
        不能把所有 widget 都读一遍再整体覆盖——高级区收起时那些控件是
        只读的（灰显），整体覆盖会把里面的 Stage / 音频 / 注释**清空**，
        也就是"改一句台词把这一拍的 BGM 弄没了"。
        """
        b = self._beat or Beat()
        b.speaker = self._wget('Speaker').strip()
        b.text = self._wget('Text').strip()

        # —— 条件 ——
        cond_var = getattr(self, '_condition_var', None)
        if cond_var is not None and hasattr(cond_var, 'get'):
            b.condition = (cond_var.get() or '').strip()

        # —— 立绘数组 → show ——
        # 每张立绘的表情/位置在各自那一行里编辑（见 _collect_sprites），
        # 而且**行内已经做过归一**（中文词 → 属性），所以这里不再调用
        # apply_expression/apply_position —— 否则会二次改写第一张。
        #
        # **数组为空 ≠ 清空立绘**：只有"表单里真的没有立绘行"时才不动
        # `b.show`。否则 `show=...` 会被静默清成空
        # （实测：`_collect_sprites()` 在没有行时返回 ''，直接把立绘抹掉）。
        # 要删立绘请用「✕」删掉那一行，再配合「退场」列。
        if getattr(self, '_sprite_widgets', None):
            new_show = self._collect_sprites()
            if new_show:
                b.show = new_show
                rows = beat_sprite_rows(b, self._symbols())
                # 位置列同步到 `at`（舞台快照/兼容旧逻辑会读它）
                b.at = rows[0]['at'] if rows else ''

        if self._adv_open:
            # 展开时才让高级区接管（这时它的控件是可编辑的）
            b.stage = self._wget('Stage').strip()
            b.bgm = self._wget('BGM').strip()
            b.sfx = self._wget('SFX').strip()
            b.scene = self._wget('Scene').strip()
            b.voice = self._wget('Voice').strip()
            b.code = self._wget('CodeBlock').strip()
            b.comment = self._wget('Comment').strip()
            b.label = self._wget('Label').strip() or b.label
            b.kind = self._wget('Kind').strip() or b.kind

        # 立刻把 DSL 解释成结构化字段：舞台快照/校验才看得见这一拍的内容
        try:
            hydrate_from_dsl([b], self._symbols())
        except Exception:
            pass
        return b

    def collect(self) -> dict:
        """表单 → 字段字典（`beat_to_fields` 的逆，供选择器等只读用途）。"""
        return beat_to_fields(self.collect_beat(), self._symbols())



# ===================== 对话轮转（说话人"乒乓"） =====================

def participants(engine, limit: int = 8) -> list:
    """
    取"本项目的常驻角色"，按台词量排序。

    参考 Ren'Py Story Architect 最受好评的那个小功能：写双人对话时，
    预选参与角色后每敲一句回车就自动轮换到下一个说话人，
    文案不用反复去点/输入角色名——视觉小说台词密集，这个节省非常可观。
    这里只做纯逻辑：谁能参与、下一个是谁。
    """
    if engine is None:
        return []
    try:
        registry = engine.get_registry()
    except Exception:
        return []
    if registry is None:
        return []
    chars = getattr(registry, 'characters', {}) or {}
    ranked = sorted(
        chars.items(),
        key=lambda kv: (-(kv[1] or {}).get('_dialogue_count', 0) or 0,
                        str(kv[0])))
    return [cid for cid, _row in ranked[:limit]]


def next_speaker(rotation, current: str) -> str:
    """
    轮转里的下一个说话人。

    rotation 为空 → 返回 ''（表示"不自动切换"，退化成手动）。
    当前说话人不在轮转里 → 从第一个开始，而不是莫名其妙跳到第二个。
    """
    rotation = [r for r in (rotation or []) if r]
    if not rotation:
        return ''
    if current in rotation:
        return rotation[(rotation.index(current) + 1) % len(rotation)]
    return rotation[0]


# ===================== 编辑器主窗口 =====================

class ScriptTableEditorWindow(tk.Toplevel):
    """
    载入 .rpy → 列出 Beat → 编辑 → 导出 .rpy。

    三处针对实操痛点的设计（来自对同类工具的调研）：
      · 说话人轮转：预选参与角色，改完一句按回车就轮到下一个人。
      · 舞台快照：选中一拍直接看到"这时台上是谁、在哪、放什么音乐"，
        不用在脑子里做叠加运算。
      · 体检入口：随时跑三级校验，问题清单里点一条就跳到那一拍。
    """

    def __init__(self, parent, engine, script_path=None, asset_dir=None,
                 on_new_asset=None):
        super().__init__(parent)
        self.engine = engine
        self.asset_dir = asset_dir
        self.on_new_asset = on_new_asset
        self.beats = []
        self.rotation = []
        #: 面板里**当前显示的是哪一拍**（唯一真源，见 _show_index）
        self._loaded_idx = -1
        self.title("剧本表编辑器")
        self._build()
        if script_path:
            self.load_script(script_path)

    def _build(self):
        # ============ 顶部：这一章是哪个文件 + 换一章 ============
        # 以前窗口打开是**空的**，「载入 .rpy」还在底部——用户根本不知道
        # 该干什么（反馈："我不知道如何打开你说的卡片入口"）。
        # 现在顶部第一行就说清楚"正在编哪个文件"，旁边就是换文件按钮。
        top = tk.Frame(self)
        top.pack(side='top', fill='x', padx=8, pady=(6, 0))
        self._file_var = tk.StringVar(value='（还没打开章节）')
        tk.Label(top, textvariable=self._file_var,
                 font=('微软雅黑', 9, 'bold')).pack(side='left')
        tk.Button(top, text='换一章…', command=self._on_load).pack(side='right')
        tk.Label(top, text='每拍只有 4 样要改：说话人 / 台词 / 表情 / 位置',
                 font=('微软雅黑', 8), fg='#666').pack(side='right', padx=(0, 10))

        left = tk.Frame(self)
        left.pack(side='left', fill='y')
        tk.Label(left, text="演出单元（Beat）").pack(anchor='w')
        self._list = tk.Listbox(left, width=40, height=25)
        self._list.pack(fill='y')
        self._list.bind('<<ListboxSelect>>', self._on_select)

        # 参与角色（说话人轮转）
        tk.Label(left, text="参与角色（回车自动轮流说话）").pack(anchor='w',
                                                     pady=(6, 0))
        self._rotation_list = tk.Listbox(left, width=40, height=5,
                                         selectmode=tk.EXTENDED
                                         if hasattr(tk, 'EXTENDED') else None)
        self._rotation_list.pack(fill='x')
        self._refresh_participants()

        right = tk.Frame(self)
        right.pack(side='right', fill='both', expand=True, padx=8)
        self._panel = BeatEditorPanel(right, self.engine,
                                      on_new_asset=self.on_new_asset)
        self._panel.pack(fill='both', expand=True)

        bar = tk.Frame(self)
        bar.pack(side='bottom', fill='x')
        tk.Button(bar, text="载入 .rpy",
                  command=self._on_load).pack(side='left')
        tk.Button(bar, text="导出 .rpy",
                  command=self._on_export).pack(side='left')
        tk.Button(bar, text="下一句 ▶（回车）",
                  command=self.next_beat).pack(side='left')
        tk.Button(bar, text="◀ 上一句",
                  command=self.prev_beat).pack(side='left')
        tk.Button(bar, text="项目体检",
                  command=self.show_health).pack(side='left')

        if hasattr(self, 'bind'):
            self.bind('<Return>', lambda e: self.next_beat())

    def _refresh_participants(self):
        self.rotation = participants(self.engine)
        if hasattr(self._rotation_list, 'delete'):
            self._rotation_list.delete(0, 'end')
        if hasattr(self._rotation_list, 'insert'):
            for cid in self.rotation:
                self._rotation_list.insert('end', cid)

    # ---------- 选择与轮转 ----------
    #
    # **唯一真源 = self._loaded_idx（面板里现在显示的是哪一拍）。**
    #
    # 为什么不直接用列表的 curselection：用户 2026-09-28 截图暴露
    # "列表高亮第 N 行、面板显示第 N-1 拍"。一旦两者错开，
    # `apply_current_beat` 会把**面板里那一拍的内容写进列表选中的另一拍**
    # ——这不是显示问题，是**改错数据**。
    # 所以：载入一律走 `_show_index`，回写一律用 `_loaded_idx`。

    def _selected_index(self):
        """列表当前高亮的行（只用于响应用户点击）。"""
        if not hasattr(self._list, 'curselection'):
            return -1
        sel = self._list.curselection()
        return sel[0] if sel else -1

    def _show_index(self, idx: int):
        """
        把第 idx 拍载入面板（**唯一的载入入口**），并让列表高亮跟着走。

        顺序很关键：先记 `_loaded_idx`，再 sync 列表，最后 load 面板——
        这样即使 `selection_set` 触发了 `<<ListboxSelect>>` 事件，
        重入时 idx 已经是同一个，不会加载到别的拍。
        """
        if not (0 <= idx < len(self.beats)):
            return
        self._loaded_idx = idx
        if hasattr(self._list, 'selection_clear'):
            self._list.selection_clear(0, 'end')
            self._list.selection_set(idx)
            if hasattr(self._list, 'see'):
                self._list.see(idx)
        self._panel.load_beat(self.beats[idx],
                              inherited_state_hint(self.beats, idx))
        self._panel.set_stage_snapshot(stage_digest(self.beats, idx))

    def _on_select(self, event=None):
        """用户点了列表某一行 → 载入那一拍。"""
        self._show_index(self._selected_index())

    def next_beat(self):
        """
        保存当前拍 → 前进到下一拍。

        **回写用 `_loaded_idx` 而不是列表高亮**：面板里显示哪一拍，
        改动就属于哪一拍。以前两者错开时会把内容写错行。
        """
        idx = self._loaded_idx
        if not (0 <= idx < len(self.beats)):
            return
        self.apply_current_beat()
        nxt = idx + 1
        if nxt >= len(self.beats):
            return
        cur_speaker = self.beats[idx].speaker
        speaker = next_speaker(self.rotation, cur_speaker)
        if speaker:
            self.beats[nxt].speaker = speaker
        self._show_index(nxt)

    def prev_beat(self):
        """保存当前拍 → 后退一拍（与「下一句」对称，回车流程也常用）。"""
        idx = self._loaded_idx
        if not (0 <= idx < len(self.beats)):
            return
        self.apply_current_beat()
        if idx - 1 >= 0:
            self._show_index(idx - 1)

    # ---------- 载入 / 导出 / 体检 ----------

    def load_script(self, script_path):
        script_path = Path(script_path)
        nodes = RenPyParser().parse(script_path)
        result = ParseResultBuilder().build(nodes)
        self.beats = beats_from_parse_result(result)
        # 顶栏显示"在编哪个文件"——用户要随时知道自己在改哪一章
        var = getattr(self, '_file_var', None)
        if var is not None and hasattr(var, 'set'):
            var.set(f'正在编辑：{script_path.name}（{len(self.beats)} 拍）')
        # 合成中文 DSL：能无损反推就显示 `[显示] 凌跃捷 开心 左`，
        # 反推不了就保留原生写法（绝不猜错一个表情）
        symbols = make_symbols(self._registry())
        for b in self.beats:
            b.stage = compose_stage(b, symbols) or b.stage
        if hasattr(self._list, 'delete'):
            self._list.delete(0, 'end')
        if hasattr(self._list, 'insert'):
            for i, b in enumerate(self.beats):
                label = b.text or b.kind or '(空)'
                self._list.insert('end', f"#{i + 1} [{b.label}] {label}")
        self._refresh_participants()
        self.health = None
        # 载入后**直接选中第一拍并显示卡片**，不要让人对着空面板发呆。
        # 走 `_show_index`（唯一载入入口），它会同时管列表高亮与面板。
        if self.beats:
            self._show_index(0)

    def _on_load(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="选择 .rpy",
            filetypes=[("Ren'Py 脚本", "*.rpy"), ("所有文件", "*.*")])
        if p:
            self.load_script(p)

    def _on_export(self):
        """
        编译导出：先生成脚本，再写「编译结果表」。

        找不到资源**不会**中断导出——只在脚本里留一行
        `# TODO: 缺少资源 …`，然后把数量报给策划。这是刻意的：
        一个 2600 张图的项目，没人会去看 1073 条红字；
        但 12 处"待补资源"他会去改。
        """
        from tkinter import filedialog
        from storygraph.pipeline.compile import compile_beats, write_export_sheet

        self.apply_current_beat()
        out = filedialog.asksaveasfilename(
            title="导出 .rpy",
            defaultextension=".rpy",
            filetypes=[("Ren'Py 脚本", "*.rpy")])
        if not out:
            return
        registry = self._registry()
        result = compile_beats(self.beats, registry=registry)

        # 生成安全门禁：与「从剧本表生成 .rpy」同一条闸。
        # 这里也要过——否则应用内编辑这条路会绕过保护，
        # 把中文图像 tag / 重名 label 的脚本写进项目。
        occupied = occupied_labels(
            project_root=getattr(self.engine, 'project_root', None),
            exclude={Path(out)})
        issues = generation_guard(
            result.rpy_text, Path(out),
            project_root=getattr(self.engine, 'project_root', None),
            occupied_labels=occupied)
        blocking = [i for i in issues if i.is_blocking]
        if blocking:
            messagebox.showerror(
                '拒绝导出：生成的脚本会让游戏跑不起来',
                '发现 %d 条致命问题，已取消写入：\n\n%s'
                % (len(blocking),
                   '\n'.join('· ' + i.format() for i in blocking[:12])))
            return

        Path(out).write_text(result.rpy_text, encoding='utf-8')
        sheet = None
        try:
            sheet = write_export_sheet(out, result)
        except Exception:
            sheet = None

        msg = [f'已写入：{out}', '', result.summary()]
        if sheet:
            msg += ['', f'编译结果表：{sheet}']
        todos = [r for r in result.rows if r.get('status') == 'todo']
        if todos:
            msg += ['', '待补资源（已作为 # TODO 注释写进脚本）：']
            for r in todos[:8]:
                msg.append(f"  · {r['code']}")
            if len(todos) > 8:
                msg.append(f'  …还有 {len(todos) - 8} 处')
        messagebox.showinfo("编译导出完成", '\n'.join(msg))

    def _registry(self):
        if self.engine is None:
            return None
        try:
            return self.engine.get_registry()
        except Exception:
            return None

    def show_health(self):
        """
        项目体检：三级校验 + 报告窗口。点一条问题跳到对应拍。

        调研里给这类视图的定位很准：把结构性问题集中一处，别让程序在
        跑游戏时一个个撞见。这里复用 build.collect_report 的同一套规则。
        """
        self.apply_current_beat()
        registry = self._registry()
        report = validate_all(self.beats, registry=registry)
        text = report.format(max_per_level=60)

        dlg = tk.Toplevel(self)
        dlg.title(f"项目体检 · {report.summary()}")
        box = tk.Listbox(dlg, width=110, height=24)
        box.pack(fill='both', expand=True, padx=8, pady=8)
        self._health_issues = list(report.issues)
        index_of = {}
        for i, issue in enumerate(report.issues):
            index_of[len(index_of)] = issue
            mark = '✗' if issue.severity == 'error' else '!'
            box.insert('end', f"{mark} {issue.level} {issue.code} "
                              f"[{issue.where}] {issue.message}")
        self._health_index = index_of

        def jump(_event=None):
            if not hasattr(box, 'curselection'):
                return
            sel = box.curselection()
            if not sel:
                return
            issue = index_of.get(sel[0])
            if issue is None:
                return
            for i, b in enumerate(self.beats):
                if getattr(b, 'line_id', '') == issue.where:
                    if hasattr(self._list, 'selection_clear'):
                        self._list.selection_clear(0, 'end')
                        self._list.selection_set(i)
                        self._list.see(i)
                    self._on_select()
                    break

        if hasattr(box, 'bind'):
            box.bind('<<ListboxSelect>>', jump)
        tk.Label(dlg, text=text.split('\n')[0], fg='#666').pack()
        return None

    def apply_current_beat(self):
        """
        把面板当前编辑写回 `self.beats`。

        **写回 `_loaded_idx`（面板显示的那一拍），不是列表高亮的那一行**——
        两者错开时按列表写会把内容写错行。
        """
        idx = self._loaded_idx
        if 0 <= idx < len(self.beats):
            self.beats[idx] = self._panel.collect_beat()
            _assign_identity([self.beats[idx]])
            # 列表里显示的是台词，改完要跟着更新，否则"列表和卡片不一致"
            self._refresh_list_row(idx)

    def _refresh_list_row(self, idx: int):
        """刷新列表某一行的文字（台词改了要立刻反映出来）。"""
        if not (0 <= idx < len(self.beats)):
            return
        if not (hasattr(self._list, 'delete') and hasattr(self._list, 'insert')):
            return
        b = self.beats[idx]
        text = b.text or b.kind or '(空)'
        self._list.delete(idx)
        self._list.insert(idx, f"#{idx + 1} [{b.label}] {text}")
