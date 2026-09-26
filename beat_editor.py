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

from asset_completion import AssetCompletion
from asset_registry import describe_asset
from script_table import (
    Beat, _assign_identity, beats_from_parse_result,
    render_beats_to_rpy, write_beats_to_rpy,
)
from asset_registry import add_asset_to_registry
from renpy_parser import RenPyParser, ParseResultBuilder
from stage_snapshot import stage_digest
from validate import validate_all


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


def beat_to_fields(beat: Beat) -> dict:
    """Beat → 面板字段字典（界面与数据之间的中性中间层）。"""
    return {
        'Label': beat.label,
        'Kind': beat.kind,
        'Speaker': beat.speaker,
        'Text': beat.text,
        'Scene': beat.scene,
        'Show': beat.show,
        'Hide': beat.hide,
        'At': beat.at,
        'With': beat.with_,
        'Transform': beat.transform,
        'Comment': beat.comment,
        'Raw': beat.raw,
        'CodeBlock': beat.code,
        'Music': beat.music,
        'MusicParams': beat.music_params,
        'MusicAction': beat.music_action,
        'Sound': beat.sound,
        'SoundParams': beat.sound_params,
        'SoundAction': beat.sound_action,
        'Voice': beat.voice,
        'Condition': beat.condition,
        'Notes': beat.notes,
    }


def fields_to_beat(fields: dict, base: Optional[Beat] = None) -> Beat:
    """面板字段字典 → Beat。base 非空时在其上覆盖，保留 seq 等元信息。"""
    b = base or Beat()
    b.label = (fields.get('Label') or '').strip()
    b.kind = (fields.get('Kind') or 'say').strip() or 'say'
    b.speaker = (fields.get('Speaker') or '').strip()
    b.text = (fields.get('Text') or '').strip()
    b.scene = (fields.get('Scene') or '').strip()
    b.show = (fields.get('Show') or '').strip()
    b.hide = (fields.get('Hide') or '').strip()
    b.at = (fields.get('At') or '').strip()
    b.with_ = (fields.get('With') or '').strip()
    b.transform = (fields.get('Transform') or '').strip()
    b.comment = (fields.get('Comment') or '').strip()
    b.raw = (fields.get('Raw') or '').strip()
    b.code = (fields.get('CodeBlock') or '').strip()
    b.music = (fields.get('Music') or '').strip()
    b.music_params = (fields.get('MusicParams') or '').strip()
    b.music_action = (fields.get('MusicAction') or 'play').strip() or 'play'
    b.sound = (fields.get('Sound') or '').strip()
    b.sound_params = (fields.get('SoundParams') or '').strip()
    b.sound_action = (fields.get('SoundAction') or 'play').strip() or 'play'
    b.voice = (fields.get('Voice') or '').strip()
    b.condition = (fields.get('Condition') or '').strip()
    b.notes = (fields.get('Notes') or '').strip()
    return b


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
    """选中一拍后的快速编辑面板。"""

    # 字段 → 是否走资产选择器（及其资产类型）
    _ASSET_FIELDS = {
        'Speaker': ('character',),
        'Scene': ('image',),
        'Show': ('image',),
        'Hide': ('image',),
        'At': ('transform',),
        'With': ('transform',),
        'Transform': ('transform',),
        'Music': ('music',),
        'Sound': ('sound',),
        'Voice': ('voice',),
    }

    # 只读 / 特殊字段
    _TEXT_FIELDS = ['Label', 'Kind', 'Text', 'Condition', 'Comment',
                    'CodeBlock', 'Raw', 'Notes']
    _ACTION_FIELDS = {'MusicAction': ['play', 'queue', 'stop'],
                     'SoundAction': ['play', 'queue', 'stop']}
    # 音频参数（fadein/loop 等）——自由文本，导出时原样接到 play 后面
    _PARAM_FIELDS = ['MusicParams', 'SoundParams']

    def __init__(self, parent, engine, **kw):
        super().__init__(parent, **kw)
        self.engine = engine
        self._beat = None
        self._widgets = {}
        self._note_var = tk.StringVar(value='')
        self._build()

    def _build(self):
        row = 0
        for name in self._TEXT_FIELDS:
            tk.Label(self, text=name).grid(row=row, column=0, sticky='w')
            var = tk.StringVar()
            tk.Entry(self, textvariable=var).grid(row=row, column=1,
                                                  sticky='we')
            self._widgets[name] = var
            row += 1

        for name, _types in self._ASSET_FIELDS.items():
            tk.Label(self, text=name).grid(row=row, column=0, sticky='w')
            var = tk.StringVar()
            tk.Entry(self, textvariable=var).grid(row=row, column=1,
                                                  sticky='we')
            tk.Button(self, text="🔍", width=2,
                      command=lambda n=name, t=_types: self._pick(n, t)
                      ).grid(row=row, column=2)
            tk.Button(self, text="?说明", width=5,
                      command=lambda n=name, t=_types: self._explain(n, t)
                      ).grid(row=row, column=3)
            self._widgets[name] = var
            row += 1

        for name, values in self._ACTION_FIELDS.items():
            tk.Label(self, text=name).grid(row=row, column=0, sticky='w')
            var = tk.StringVar(value='play')
            tk.Entry(self, textvariable=var).grid(row=row, column=1,
                                                  sticky='we')
            self._widgets[name] = var
            row += 1

        for name in self._PARAM_FIELDS:
            tk.Label(self, text=name).grid(row=row, column=0, sticky='w')
            var = tk.StringVar()
            tk.Entry(self, textvariable=var).grid(row=row, column=1,
                                                  sticky='we')
            self._widgets[name] = var
            row += 1

        self.columnconfigure(1, weight=1)

        # 说明区：选中/填好一个变换、立绘、音乐后，这里给出"它是什么"
        tk.Label(self, text="说明").grid(row=row, column=0, sticky='nw')
        tk.Label(self, textvariable=self._note_var, justify='left',
                 anchor='nw', wraplength=320, fg='#444'
                 ).grid(row=row, column=1, columnspan=3, sticky='we')
        row += 1

        # 舞台快照：这一拍播出来玩家看见什么（背景/立绘/音乐）
        tk.Label(self, text="舞台快照").grid(row=row, column=0, sticky='nw')
        self._stage_var = tk.StringVar(value='')
        tk.Label(self, textvariable=self._stage_var, justify='left',
                 anchor='nw', wraplength=320, fg='#0A5'
                 ).grid(row=row, column=1, columnspan=3, sticky='we')

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
        if not hasattr(self._widgets[field_name], 'get'):
            return
        value = (self._widgets[field_name].get() or '').strip()
        if not value:
            self._set_note(f'{field_name}：还没填。点 🔍 从资源表里选。')
            return
        # 值可能是 `图像@变换` / 多个值（; 分隔），逐个解释
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
            self._widgets[field_name].set(aid)
            detail = asset_detail(self.engine, _atype, aid)
            self._set_note(f'{field_name} = {aid}\n{detail}')

    def load_beat(self, beat: Beat):
        self._beat = beat
        fields = beat_to_fields(beat)
        for name, var in self._widgets.items():
            val = fields.get(name, '')
            if hasattr(var, 'set'):
                var.set(val)
        # 载入时就把变换说明摆出来——文案最常问的就是"这个变换是干嘛的"
        for fname in ('Transform', 'At', 'With'):
            types = self._ASSET_FIELDS.get(fname)
            if types:
                self._explain(fname, types)
                note = self._note_var.get() if hasattr(self._note_var, 'get') \
                    else ''
                if note and '还没填' not in note:
                    break

    def collect_beat(self) -> Beat:
        fields = {name: (var.get() if hasattr(var, 'get') else '')
                 for name, var in self._widgets.items()}
        return fields_to_beat(fields, base=self._beat)


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
        self.title("剧本表编辑器")
        self._build()
        if script_path:
            self.load_script(script_path)

    def _build(self):
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

    def _selected_index(self):
        if not hasattr(self._list, 'curselection'):
            return -1
        sel = self._list.curselection()
        return sel[0] if sel else -1

    def _on_select(self, event=None):
        idx = self._selected_index()
        if 0 <= idx < len(self.beats):
            self._panel.load_beat(self.beats[idx])
            self._panel.set_stage_snapshot(stage_digest(self.beats, idx))

    def next_beat(self):
        """保存当前拍 → 选下一拍 → 说话人按轮转前进（乒乓）。"""
        idx = self._selected_index()
        if idx < 0:
            return
        self.apply_current_beat()
        nxt = idx + 1
        if nxt >= len(self.beats):
            return
        cur_speaker = self.beats[idx].speaker
        speaker = next_speaker(self.rotation, cur_speaker)
        if speaker:
            self.beats[nxt].speaker = speaker
        if hasattr(self._list, 'selection_clear'):
            self._list.selection_clear(0, 'end')
            self._list.selection_set(nxt)
            self._list.see(nxt)
        self._panel.load_beat(self.beats[nxt])
        self._panel.set_stage_snapshot(stage_digest(self.beats, nxt))

    # ---------- 载入 / 导出 / 体检 ----------

    def load_script(self, script_path):
        nodes = RenPyParser().parse(Path(script_path))
        result = ParseResultBuilder().build(nodes)
        self.beats = beats_from_parse_result(result)
        if hasattr(self._list, 'delete'):
            self._list.delete(0, 'end')
        if hasattr(self._list, 'insert'):
            for i, b in enumerate(self.beats):
                label = b.text or b.kind or '(空)'
                self._list.insert('end', f"#{i + 1} [{b.label}] {label}")
        self._refresh_participants()
        self.health = None

    def _on_load(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="选择 .rpy",
            filetypes=[("Ren'Py 脚本", "*.rpy"), ("所有文件", "*.*")])
        if p:
            self.load_script(p)

    def _on_export(self):
        from tkinter import filedialog
        self.apply_current_beat()
        out = filedialog.asksaveasfilename(
            title="导出 .rpy",
            defaultextension=".rpy",
            filetypes=[("Ren'Py 脚本", "*.rpy")])
        if not out:
            return
        write_beats_to_rpy(self.beats, out, registry=self._registry())
        messagebox.showinfo("导出完成", f"已写入：\n{out}")

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
        """把面板当前编辑写回 self.beats（选中项）。"""
        idx = self._selected_index()
        if 0 <= idx < len(self.beats):
            self.beats[idx] = self._panel.collect_beat()
            _assign_identity([self.beats[idx]])
