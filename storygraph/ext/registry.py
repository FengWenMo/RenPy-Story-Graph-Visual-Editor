"""
storygraph.ext.registry
=======================
**一个项目里所有扩展点，装在一个注册表里**（四个入口合成一份）。

这一层只做三件事：

  1. **装载**：内置（`renpy.transition` 这类引擎自带 API）→ 项目清单
     `storygraph.ext.json` → 插件目录 `extensions/*.json|*.py` → 旧的
     `storygraph.rules.json`（`statements` / `calls`，只读兼容）；
  2. **去重与覆盖**：同 id 后者覆盖前者，但**记住它来自哪**（`source`），
     界面上要看得出"这条是插件加的 / 这条是旧清单里来的"；
  3. **落盘**：`storygraph.ext.json`（唯一写入口，坏 JSON → 拒绝覆盖）。

⚠ **旧的 `rules.json` 永远不被改写**。它仍然是语句声明与批处理规则的
**运行时真源**（`statement_registry` 直接读它）；这里只是把它的内容
**投影**成扩展点，让人在一个界面里看得全。要改，写进 `storygraph.ext.json`，
由调用方决定什么时候把它回写进 `rules.json`（P1 再做，见设计文档）。
"""
from __future__ import annotations

import json
from pathlib import Path

from storygraph.core.filelock import DEFAULT_TIMEOUT, LockTimeout, file_lock
from storygraph.core.threeway import compose_rows, merge_by_key
from storygraph.ext.schema import (EXT_SCHEMA, ExtError, ExtensionPoint,
                                   field_of_column, parse_extension)

#: 扩展点清单（新格式）。`rules.json` 仍是语句/批处理的运行时真源。
MANIFEST = 'storygraph.ext.json'

#: 插件目录（项目根下）
PLUGIN_DIR = 'extensions'

#: 能**整条**写进 `storygraph.ext.json` 的来源。别的来源（`builtin` /
#: `rules.json` / `plugin:xxx.json`）形状在别处，文件里最多留一条
#: 「只带开关」的记录（`override`）。
_WRITABLE_SOURCES = ('project', 'plugin')


def _builtin_points() -> list:
    """
    工具**自带**的扩展点（不是项目声明的）。

    只放"引擎自己就有、但项目可能想关掉或想批处理"的那几条。
    `renpy.transition` 的参数形状**不在这里重复写一遍**：它来自
    `story.spec_args.BUILTIN_CALL_SCHEMAS`（唯一真源），这里只是把它
    翻成扩展点的样子。
    """
    out = []
    try:
        from storygraph.story.spec_args import BUILTIN_CALL_SCHEMAS
    except ImportError:                         # 后端被裁剪过：就只列空的
        BUILTIN_CALL_SCHEMAS = {}
    for head, cols in (BUILTIN_CALL_SCHEMAS or {}).items():
        fields = []
        for col in (cols or ()):
            spec = field_of_column(col) if isinstance(col, dict) else None
            if spec is None or not str(spec.key or '').strip():
                continue
            # 不是声明、只是给人看的一句说明（内置形状不可改，见 override）
            spec.help = '关键字参数' if spec.kw else '位置参数'
            fields.append(spec)
        out.append(ExtensionPoint(
            id=str(head), kind='call',
            label=f'{head}（引擎自带）',
            docs='Ren\'Py 自带的 API；参数形状由工具内置，不需要项目声明。',
            enabled=True, batchable=False, fields=fields, source='builtin'))
    return out


def _from_ext_manifest(path: Path) -> tuple:
    """`storygraph.ext.json` → `([ExtensionPoint], problems)`。坏条目跳过并出声。"""
    points, problems = [], []
    try:
        data = json.loads(path.read_text(encoding='utf-8') or '{}')
    except (OSError, ValueError) as e:
        return [], [f'{path.name} 读不出来：{e}']
    if not isinstance(data, dict):
        return [], [f'{path.name} 顶层不是对象']
    for raw in (data.get('extensions') or ()):
        try:
            points.append(parse_extension(raw, source='project'))
        except ExtError as e:
            problems.append(str(e))
    return points, problems


def _read_ext_records(path: Path) -> tuple:
    """
    `storygraph.ext.json` → `([(id, 记录)], 别人的顶层键, problems)`。

    与 `_from_ext_manifest` 的分工：那个是**装载**（读不懂就跳过并出声，
    不在乎原话）；这个是**落盘前对表**（文件里长什么样，就得能原样还回去）——
    所以认不出的条目返回 `id=''` + 原始值，写的时候照抄。

    `rows is None` 表示整份读不出来（坏 JSON / 顶层不是对象 / extensions 不是列表）。
    """
    try:
        data = json.loads(path.read_text(encoding='utf-8') or '{}')
    except (OSError, ValueError) as e:
        return None, {}, [f'{path.name} 读不出来：{e}']
    if not isinstance(data, dict):
        return None, {}, [f'{path.name} 顶层不是对象']
    raw_list = data.get('extensions')
    if raw_list is None:
        raw_list = []
    if not isinstance(raw_list, list):
        return None, {}, [f'{path.name} 的 extensions 不是列表']
    rows = []
    for raw in raw_list:
        ident = str(raw.get('id') or '').strip() \
            if isinstance(raw, dict) else ''
        if not ident:
            rows.append(('', raw))
            continue
        try:
            rows.append((ident, parse_extension(raw, source='project').to_dict()))
        except ExtError:
            rows.append((ident, raw))       # 认得出 id、读不懂形状：原样留着
    extras = {k: v for k, v in data.items() if k not in ('schema', 'extensions')}
    return rows, extras, []


def _statement_point(raw) -> object:
    """清单里的一条 `statements[i]` → `ExtensionPoint`（坏条目返回 None）。"""
    if isinstance(raw, str):
        raw = {'name': raw}
    if not isinstance(raw, dict):
        return None
    name = str(raw.get('name') or '').strip()
    if not name:
        return None
    # 与 `statement_registry._from_manifest` 同一条口径：名字里的第一个词是
    # 语句头，后面的词是别名（`"mycue cue"` → id=`mycue`，别名 `cue`）。
    ident = name.split()[0] if name.split() else name
    from storygraph.ext.schema import field_of_slot

    fields = []
    for slot in (raw.get('pattern') or ()):
        if isinstance(slot, dict):
            fields.append(field_of_slot(slot))
    for clause in (raw.get('clauses') or ()):
        if isinstance(clause, dict):
            fields.append(field_of_slot(clause))
    return ExtensionPoint(id=ident, kind='statement',
                          label=str(raw.get('label') or ident),
                          docs=_statement_notes(raw),
                          enabled=True, batchable=False, fields=fields,
                          source='rules.json')


def _statement_notes(raw: dict) -> str:
    """
    一条声明里**没有变成字段、但确实存在**的部分（块体允许 / 渲染 / 别名）。

    为什么写进 `docs` 而不是做成字段：这几项改了要回写 `storygraph.rules.json`
    才生效（扩展窗口目前只写 `storygraph.ext.json`），做成可编辑的格子会让人
    "改了没反应"。**先让它看得见**，等回写那条路通了再升级成开关
    （用户 2026-11-07："自定义写法能完全被它显示"）。
    """
    notes = []
    head = str(raw.get('name') or '').strip()
    aliases = [str(a) for a in (raw.get('aliases') or ()) if str(a).strip()]
    words = head.split()[1:]
    if words or aliases:
        notes.append('别名：' + '、'.join([*words, *aliases]))
    body = raw.get('body') or {}
    allows = [str(a).strip() for a in (body.get('allows') or ())
              if str(a).strip()] if isinstance(body, dict) else []
    if allows:
        notes.append('块体允许：' + '、'.join(allows))
    render = str(raw.get('render') or '').strip()
    if render and render != 'verbatim':
        notes.append(f'渲染：{render}')
    docs = str(raw.get('docs') or '')
    if not notes:
        return docs
    return (docs + ' ｜ ' if docs else '') + '；'.join(notes)


def _call_point(raw) -> object:
    """清单里的一条 `calls[i]` → `ExtensionPoint`（坏条目返回 None）。

    ⚠ 参数那一格走 `schema.field_of_column`（**唯一一处** `columns` → 字段）。
      从前这里只搬 `name` / `label`：`kind` 一律 `variable`、`choices` 丢了、
      `position` / `kw` 根本没有位置可放 —— 于是窗口里看到的是"三条普通参数"，
      照着它再存一份，`layer` 就变成第 3 个位置参数（读错值，不报错）。
      判据 `test_projection_keeps_position_and_kw`。
    """
    if isinstance(raw, str):
        raw = {'head': raw}
    if not isinstance(raw, dict):
        return None
    head = str(raw.get('head') or raw.get('name') or '').strip()
    if not head:
        return None
    fields = []
    for col in (raw.get('columns') or ()):
        if not isinstance(col, dict):
            continue
        spec = field_of_column(col)
        if not str(spec.key or '').strip():
            continue
        fields.append(spec)
    seconds = raw.get('seconds_arg')
    try:
        seconds = int(seconds) if seconds is not None else None
    except (TypeError, ValueError):
        seconds = None
    return ExtensionPoint(id=head, kind='call',
                          label=str(raw.get('label') or head),
                          docs=str(raw.get('docs') or ''), enabled=True,
                          fields=fields, source='rules.json',
                          effect=str(raw.get('effect') or '').strip(),
                          seconds_arg=seconds)


def _from_rules_manifest(path: Path) -> tuple:
    """
    旧的 `storygraph.rules.json` → 扩展点（**只读投影**）。

    `statements` 的每一格（`pattern`）与每一个子句（`clauses`）都变成
    `FieldSpec` —— 这就是"声明图什么、界面就画什么"的那条线。
    """
    try:
        data = json.loads(path.read_text(encoding='utf-8') or '{}')
    except (OSError, ValueError) as e:
        return [], [f'{path.name} 读不出来：{e}']
    if not isinstance(data, dict):
        return [], [f'{path.name} 顶层不是对象']
    points = [p for p in (_statement_point(r)
                          for r in (data.get('statements') or ())) if p]
    points += [p for p in (_call_point(r)
                           for r in (data.get('calls') or ())) if p]
    return points, []


def _borrow_fields(base, point) -> None:
    """
    只改开关的记录（`override: true`）**借用**被覆盖那条的字段来显示。

    为什么要有它（2026-11-07 第二轮）：旧清单那条是**投影**（字段从
    `pattern` / `clauses` 来），而"我在扩展窗口里把它停用"只需要记一个
    `enabled: false`。如果把整条都存下来，就等于把一份**有损**的声明
    盖在真声明上（别名 / 块体 / 子句 / 渲染都在转换里丢了）。
    所以：开关记在 `override` 记录里，**声明仍归旧清单**，
    这里只把字段/说明借过来，让人还能在窗口里看见它的形状。
    """
    if base is None or not getattr(point, 'override', False):
        return
    if not point.fields:
        point.fields = list(base.field_specs())
    if not point.label:
        point.label = base.label
    if not point.docs:
        point.docs = base.docs
    # 演出信息也借来**看**（2026-11-07 第五轮）：override 记录不写这两句
    # （`ExtensionPoint.to_dict` 有闸门），但不显示就等于"窗口里这条调用
    # 没有效果"，人还得去翻 rules.json。
    if not str(getattr(point, 'effect', '') or ''):
        point.effect = str(getattr(base, 'effect', '') or '')
    if getattr(point, 'seconds_arg', None) is None:
        point.seconds_arg = getattr(base, 'seconds_arg', None)


def _source_label(source: str) -> str:
    """一个 `source` 在界面/问题里该叫什么（**一处说法**，别每个窗口各起一个）。"""
    source = str(source or '')
    if source == 'builtin':
        return '工具自带'
    if source == 'rules.json':
        return 'storygraph.rules.json'
    if source.startswith('plugin:'):
        return '插件 ' + source.split(':', 1)[-1]
    return MANIFEST if source in ('', 'project') else source


def _note_shadow(reg, incoming, replaced) -> None:
    """
    **两条完整声明撞了同一个 id** —— 出声。

    以前这是完全静默的（2026-11-07 第六轮探针）：项目在 `storygraph.ext.json`
    里声明了 `my_wipe`，插件也声明了 `my_wipe`，插件装在后 → 后者整条覆盖，
    窗口里只剩一条、来源写着插件，**没有一个字**说"你自己那条被盖住了"。
    更糟的是紧接着存一次盘，`payload()` 只收 `project` 来源的点 ——
    项目那条声明**被删掉**（探针里 `count: 0`，整份文件清空）。

    这里只出声（也**不**改谁说了算：装载顺序仍然是 内置 → 旧清单 → 项目 →
    插件）。`override` 记录不算"撞"：它的存在本来就是说"我只想改开关"。
    """
    if replaced is None or replaced is incoming:
        return
    if getattr(incoming, 'override', False) \
            or getattr(replaced, 'override', False):
        return
    if replaced.source == incoming.source:
        return
    reg.problems.append(
        f'「{incoming.id}」被声明了两次：{_source_label(replaced.source)} 和 '
        f'{_source_label(incoming.source)}。**形状**现在以 '
        f'{_source_label(incoming.source)} 为准，'
        f'{_source_label(replaced.source)} 那条**仍在文件里**，'
        f'开关（用不用 / 批不批处理）仍按它算。')


def _apply_switches(reg, records) -> None:
    """
    把 `ext.json` 里那条的**开关三样**贴回最终那条上（插件之后）。

    为什么必须在插件之后（2026-11-07 第五轮探针实测）：插件在 `ext.json`
    **之后**装，`upsert(replace=True)` 会把开关记录整个换掉 —— 于是
    "插件那条的『是否使用』"怎么写都没用：

        插件声明 my_wipe（enabled=true）+ ext.json 记 {"id":"my_wipe",
        "enabled":false,"override":true}
        → 装完：my_wipe 还在、enabled=True（界面上关掉、重启又回来）

    ⚠ 2026-11-07 第六轮：**不再只认 `override` 记录**。项目对"这个 id 用不用"
    的决定跟着 id 走，形状才分来源；否则项目在同名那条上写了
    `enabled: false`（完整声明），被插件盖住之后开关就白写了。

    这里只盖三样：`enabled` / `batchable` / `batch`。**形状仍归插件**
    （字段、说明、标签都是插件的）—— 与 `_borrow_fields` 是同一条分工：
    开关记在项目里，形状住在来源里。
    """
    for rec in records or ():
        base = reg.get(rec.id)
        if base is None or base is rec:
            continue
        base.enabled = bool(rec.enabled)
        base.batchable = bool(rec.batchable)
        if rec.batch:
            base.batch = dict(rec.batch)


def _merge_records(base: dict, mine: dict, theirs: dict,
                   on_conflict: str = 'abort') -> tuple:
    """
    三方合并（**纯函数**，不碰文件）→ `(picks, conflicts, absorbed)`。

    判据表与实现都在 `storygraph.core.threeway.merge_by_key`（第七轮搬到那里：
    `rules.json` 的 `calls` 段要用**同一张表**）。这里只保留本模块的名字 ——
    它说的是"扩展点记录"，`test_ext_merge.py` 的注释也按这个名字讲故事。

        mine == theirs  → 一样，随便取
        mine == base    → 我没动过 → **用文件里的**（收进别人的改动）
        theirs == base  → 别人没动 → 用我的
        两边都变了      → **冲突**：`abort` 时调用方一个字都不写；
                          `mine` / `theirs` 时按它取

    按 id 逐条判，所以"甲窗改 A、乙窗改 B"两边都能落下，只有**同一条**被两边
    改过才停下来问。返回的 `picks` 里 `None` 表示"这条该删"。
    """
    return merge_by_key(base, mine, theirs, on_conflict)


def _compose(rows: list, picks: dict, mine: dict) -> list:
    """合并结果 → 文件里那份列表（实现见 `core.threeway.compose_rows`）。"""
    return compose_rows(rows, picks, mine)


class ExtensionRegistry:
    """一个项目的全部扩展点（有序：内置 → 项目 → 插件 → 旧清单）。"""

    def __init__(self, points=None, problems=None, root=None):
        self._points: list = []
        self.problems: list = list(problems or ())
        self.root = Path(root) if root else None
        #: 装载时 `storygraph.ext.json` 里**原话**（`id → to_dict()`）。
        #:
        #: 落盘要做三方合并，得知道"我打开窗口那一刻文件里是什么"——
        #: 这就是那个 base（2026-11-07 第六轮）。**不是** `self._points` 的
        #: 快照：里面的条目可能已经被插件盖过、被 `_borrow_fields` 借过字段。
        #:
        #: `None` = **不知道**（手工搭出来的注册表，没从项目里装）。这时候
        #: 落盘按"我只对自己写的那几条有主张"处理（见 `save`）。
        self._baseline: dict = None
        for p in (points or ()):
            self.upsert(p, replace=True)

    # ---------- 装载 ----------

    @classmethod
    def for_project(cls, project_root) -> 'ExtensionRegistry':
        """
        读一个项目。**任何一个来源坏了都只是 `problems`，不抛。**

        顺序（后者覆盖前者，所以"谁说了算"就落在这几行上）：

            内置 → 旧清单投影 → `storygraph.ext.json` → 插件

        ⚠ 旧清单在 ext.json **之前**（2026-11-07 第二轮改）：项目里只改开关的
          记录（`override: true`）要能**借用**旧清单那条的字段来显示
          —— 它自己不带字段（见 `_borrow_fields`）。
        """
        root = Path(project_root) if project_root else None
        reg = cls(root=root)
        reg._baseline = {}          # 从项目里装的：文件不存在 = 当时确实是空的
        for p in _builtin_points():
            reg.upsert(p, replace=False)

        if root is not None:
            rules_path = root / 'storygraph.rules.json'
            if rules_path.is_file():
                pts, probs = _from_rules_manifest(rules_path)
                reg.problems.extend(probs)
                for p in pts:
                    # 旧清单是**投影**：同名时不许盖掉内置那几条（`renpy.transition`）
                    reg.upsert(p, replace=False)

            ext_path = root / MANIFEST
            switches: list = []
            if ext_path.is_file():
                pts, probs = _from_ext_manifest(ext_path)
                reg.problems.extend(probs)
                # 文件里的**原话**要在借字段之前记下来：`_borrow_fields` 会往
                # 开关记录上补字段/标签/演出信息，那是**显示用**的，不是声明。
                reg._baseline = {p.id: p.to_dict() for p in pts}
                for p in pts:
                    # **每一条**都要在插件之后重贴一遍开关（不只是 override）：
                    # 项目对某个 id 的"用不用 / 批不批处理"是项目的决定，
                    # 插件只提供形状。所以插件把同名那条盖住之后，
                    # `enabled: false` 仍按 ext.json 里那条算。
                    switches.append(p)
                    _borrow_fields(reg.get(p.id), p)
                    _note_shadow(reg, p, reg.get(p.id))
                    reg.upsert(p, replace=True)

            try:
                from storygraph.ext.plugins import discover
                pts, probs = discover(root)
                reg.problems.extend(probs)
                for p in pts:
                    _note_shadow(reg, p, reg.get(p.id))
                    reg.upsert(p, replace=True)
            except (ImportError, OSError) as e:
                reg.problems.append(f'插件目录读不出来：{e}')
            # 插件之后再把开关贴回去：否则"插件那条的开关"关不掉
            _apply_switches(reg, switches)
        return reg

    # ---------- 查询 ----------

    def all(self) -> list:
        return list(self._points)

    def get(self, ident: str):
        for p in self._points:
            if p.id == ident:
                return p
        return None

    def by_kind(self, kind: str) -> list:
        return [p for p in self._points if p.kind == kind]

    def enabled(self, kind: str = '') -> list:
        return [p for p in self._points
                if p.enabled and (not kind or p.kind == kind)]

    def batchable(self) -> list:
        return [p for p in self._points if p.enabled and p.batchable]

    def cue_rules(self) -> list:
        """所有"开了批处理"的扩展点 → `app.batch_edit` 能吃的规则列表。"""
        from storygraph.ext.schema import to_cue_rule
        out = []
        for p in self.batchable():
            rule = to_cue_rule(p)
            if rule:
                out.append(rule)
        return out

    # ---------- 修改 ----------

    def upsert(self, point: ExtensionPoint, replace: bool = True):
        """同名覆盖 / 新增。`replace=False` 时**只补缺**（已有 id 不动）。"""
        for i, p in enumerate(self._points):
            if p.id == point.id:
                if replace:
                    self._points[i] = point
                return self._points[i]
        self._points.append(point)
        return point

    def remove(self, ident: str) -> bool:
        for i, p in enumerate(self._points):
            if p.id == ident:
                del self._points[i]
                return True
        return False

    def _touch_switch(self, point) -> None:
        """
        改开关的时候，把"形状归别处"的那几条记成 `override` 记录。

        为什么在这里（2026-11-07 第六轮）：开关**要被记住**就得落盘，而这些
        点的完整声明属于插件 / 旧清单 / 引擎 API —— 能写的只有"只带开关"
        那一行。以前 `set_enabled('renpy.transition', False)` 存完再装载就变回
        `True`（`payload()` 直接把内置的点过滤掉了），是"开关看着能关、关不掉"
        的第三例。窗口那条路走 `_apply_to`（自己会置 `override`），这条是它的
        程序化孪生。
        """
        if point.source not in _WRITABLE_SOURCES:
            point.override = True

    def set_enabled(self, ident: str, on: bool) -> bool:
        p = self.get(ident)
        if p is None:
            return False
        p.enabled = bool(on)
        self._touch_switch(p)
        return True

    def set_batchable(self, ident: str, on: bool) -> bool:
        p = self.get(ident)
        if p is None:
            return False
        p.batchable = bool(on)
        self._touch_switch(p)
        return True

    # ---------- 落盘 ----------

    def _record_of(self, point: ExtensionPoint, base) -> dict:
        """
        这一条**该写成什么样**（`base` = 文件里原来那条；`None` = 我新加的）。

        三条口径（2026-11-07 第六轮）：

          · 只带开关的记录（`override`）→ 拿文件里那条做底，**只换开关**
            （`enabled` / `batchable` / `batch`）。形状归原处，这里再抄一份
            就是两份说法；
          · 项目自己的完整声明 → 自己的话（`to_dict`）；
          · 形状被别人盖住的（插件 / 内置 / 旧清单）而且我没在改它 →
            **原样留着**。以前这里一个字都不写：插件声明了同名那条之后，
            在窗口里顺手存一次，项目自己那份声明就**整条消失**了。
        """
        if base is None:
            return point.to_dict()
        if point.override or point.source not in _WRITABLE_SOURCES:
            rec = dict(base)
            rec['enabled'] = bool(point.enabled)
            if point.batchable:
                rec['batchable'] = True
                rec['batch'] = dict(point.batch)
            else:
                rec.pop('batchable', None)
                rec.pop('batch', None)
            return rec
        return point.to_dict()

    def _base_for(self, theirs: dict, mine: dict) -> dict:
        """
        三方合并里的 base：**我装载那一刻**文件里是什么。

        `for_project` 装出来的注册表知道（`_baseline`，文件不存在 = 空）；
        手工搭出来的（`ExtensionRegistry(points=[...])`）不知道 —— 那就只对
        自己写的那几条有主张，文件里别的条目（别人加的）照旧留着，同名的按
        我的来。这是它以前"整份写进去"的语义，只是不再顺手删掉自己不知道的。
        """
        if self._baseline is None:
            return {i: r for i, r in theirs.items() if i in mine}
        return dict(self._baseline)

    def records(self) -> list:
        """我**打算**写进 `storygraph.ext.json` 的那几条（按 id 去重后的）。"""
        live = {p.id: p for p in self._points}
        out: dict = {}
        for ident, base in (self._baseline or {}).items():
            point = live.get(ident)
            if point is None:
                continue                        # 我把它删了
            out[ident] = self._record_of(point, base)
        for p in self._points:
            if p.id in out or p.id in (self._baseline or {}):
                continue
            if p.source in _WRITABLE_SOURCES or p.override:
                out[p.id] = p.to_dict()
        return list(out.values())

    def payload(self) -> dict:
        """要写进 `storygraph.ext.json` 的内容（文件里那几条 + 我改的）。"""
        return {'schema': EXT_SCHEMA, 'extensions': self.records()}

    # ---------- 带走（导出 / 导入一份可移植档案，2026-11-07 第七轮）----------

    def export_archive(self, *, name: str = '', only=None, now: str = '') -> dict:
        """
        当前的声明 → 一份**可移植档案**（纯内存，不写盘）。

        只带走形状真的住在文件里的那些条（`project` / `plugin`），并跳过
        `override`（只带开关的空壳）。被跳过的条目用
        `ext.archive.describe_skipped()` 问，界面上给人一句交代。
        """
        from datetime import datetime

        from storygraph import __version__
        from storygraph.ext.archive import build_archive
        return build_archive(
            self._points, name=name, only=only,
            now=now or datetime.now().isoformat(timespec='seconds'),
            tool=f'storygraph {__version__}')

    def import_archive(self, data, *, on_conflict: str = 'abort') -> dict:
        """
        一份档案 → **装进内存里的注册表**（落盘仍走 `save()`）。

        `data` 可以是档案 dict 或它的 JSON 文本。`on_conflict` 见
        `ext.archive.import_into`（`abort` / `replace` / `keep`）。

        返回 `{'ok','added','same','replaced','conflicts','problems','reason'}`；
        档案里有一处读不懂 → `ok: False` 且**一条都不装**（要么全对、要么不动）。
        """
        from storygraph.ext.archive import import_into, parse_archive
        if isinstance(data, (str, bytes)):
            try:
                data = json.loads(data)
            except ValueError as e:
                return {'ok': False, 'added': [], 'same': [], 'replaced': [],
                        'conflicts': [], 'problems': [f'档案不是合法 JSON：{e}'],
                        'reason': '档案读不出来'}
        incoming, meta, problems = parse_archive(data)
        if problems:
            return {'ok': False, 'added': [], 'same': [], 'replaced': [],
                    'conflicts': [], 'problems': list(problems),
                    'meta': meta,
                    'reason': f'这份档案有 {len(problems)} 处读不懂，'
                              f'整份**没有导入**'}
        res = import_into(self, incoming, on_conflict=on_conflict)
        res['problems'] = []
        res['meta'] = meta
        return res

    def save(self, project_root=None, on_conflict: str = 'abort',
             lock_timeout: float = DEFAULT_TIMEOUT) -> dict:
        """
        写 `<项目>/storygraph.ext.json`（唯一写入口）—— **先对表，再落盘**。

        三方合并（2026-11-07 第六轮，探针 `.pip-tmp/probe_ext_conflict.py`）：

            base   = 我装载那一刻文件里的原话（`self._baseline`）
            mine   = 我现在想写的（`records()`）
            theirs = **此刻**磁盘上文件里的

        按 id 逐条判：

            mine == theirs        → 一样，随便取
            mine == base          → 我没动过 → **用文件里的**（收进别人的改动）
            theirs == base        → 别人没动 → 用我的
            两边都变了            → **冲突**：`on_conflict='abort'`（默认）时
                                    **一个字都不写**，把 id 列回去

        以前是"拿我内存里的整份稿子覆盖文件"：甲窗改名、乙窗改开关，乙一存
        甲的改动就没了（`ok: True`、`count: 2`，界面上什么都看不出来）。

        还守住两条：

          · 文件里**别人加的顶层键**原样留着（只改 `schema` / `extensions`）；
          · 认不出的条目（坏形状 / 没有 id）原样写回去，不删。

        **跨进程锁**（2026-11-07 第七轮）：读 `theirs` → 合并 → 写盘整段都在
        `core.filelock.file_lock` 里。否则两个进程同时点保存时，两边读到的是
        同一份 `theirs`，后写的把先写的**静默抹掉**（合并本身挡不住这个 ——
        它只挡"读得早"，挡不住"读得同时"）。等锁超过 `lock_timeout` 秒
        → `ok: False` + `locked: True`，**不静默跳过**。

        返回 `{'ok','path','count','reason','conflicts','absorbed','conflict'}`：

          · `conflicts` —— 两边都改过的 id（`on_conflict='abort'` 时没写盘）；
          · `absorbed`  —— 文件那边改过、我没动，于是**顺手收进来**的 id。
        """
        root = Path(project_root) if project_root else self.root
        if root is None:
            return {'ok': False, 'path': '', 'count': 0,
                    'reason': '没有项目根目录', 'conflicts': [], 'absorbed': []}
        if on_conflict not in ('abort', 'mine', 'theirs'):
            return {'ok': False, 'path': '', 'count': 0,
                    'reason': f'on_conflict 只认 abort/mine/theirs，收到 '
                              f'{on_conflict!r}', 'conflicts': [], 'absorbed': []}
        path = root / MANIFEST
        try:
            with file_lock(path, timeout=lock_timeout):
                return self._save_locked(path, on_conflict)
        except LockTimeout as e:
            return {'ok': False, 'path': str(path), 'count': 0, 'locked': True,
                    'reason': f'{MANIFEST} 现在由另一个进程在写（{e}），'
                              f'这次**没有保存**，请稍后再存',
                    'conflicts': [], 'absorbed': []}

    def _save_locked(self, path: Path, on_conflict: str) -> dict:
        """`save()` 的主体（**已经在锁里**：读 theirs → 合并 → 写盘）。"""
        rows, extras, problems = [], {}, []
        if path.is_file():
            rows, extras, problems = _read_ext_records(path)
            if rows is None:
                return {'ok': False, 'path': str(path), 'count': 0,
                        'reason': f'{MANIFEST} 不是合法 JSON，拒绝覆盖'
                                  f'（{problems[0] if problems else "?"}）',
                        'conflicts': [], 'absorbed': []}
        theirs = {i: r for i, r in rows if i}
        mine = {str(r.get('id') or ''): r for r in self.records()
                if str(r.get('id') or '').strip()}
        picks, conflicts, absorbed = _merge_records(
            self._base_for(theirs, mine), mine, theirs, on_conflict)

        if conflicts and on_conflict == 'abort':
            shown = '、'.join(conflicts[:4]) + ('…' if len(conflicts) > 4 else '')
            return {'ok': False, 'path': str(path), 'count': 0,
                    'conflict': True, 'conflicts': list(conflicts),
                    'absorbed': list(absorbed),
                    'reason': f'{MANIFEST} 里这些条也有人改过：{shown}'}

        out = _compose(rows, picks, mine)
        payload = {'schema': EXT_SCHEMA}
        payload.update(extras)
        payload['extensions'] = out
        try:
            from storygraph.core.atomicio import write_text_atomic
            write_text_atomic(path,
                              json.dumps(payload, ensure_ascii=False, indent=2)
                              + '\n', backup=True)
        except (OSError, ValueError) as e:
            return {'ok': False, 'path': str(path), 'count': 0,
                    'reason': f'写不进去：{e}', 'conflicts': [], 'absorbed': []}
        self.root = path.parent
        self._baseline = {str(r.get('id')): r for r in out
                          if isinstance(r, dict) and r.get('id')}
        return {'ok': True, 'path': str(path),
                'count': len([r for r in out
                              if isinstance(r, dict) and r.get('id')]),
                'reason': '', 'conflicts': list(conflicts),
                'absorbed': list(absorbed)}
