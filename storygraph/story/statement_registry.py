"""
`storygraph.story.statement_registry`
=====================================
**一句话的"被管理等级"从哪来**（M3，2026-10-07）。

设计文档 §3.2 的三来源优先级：

    项目清单声明（storygraph.rules.json）  ← 最强，能覆盖
    > 宿主注册表（renpy.register_statement）
    > 形状识别（不做，见 `project_conventions`）

这一层把它落成一个**查表对象**：给一个语句头（`mycue`），回答
"它是谁声明的、带不带块、人话说明是什么"。

为什么要有优先级（而不是谁先扫到算谁）
--------------------------------------
`storygraph.rules.json` 是**用户手写的意图**，`register_statement` 是
**程序写的实现**。两者冲突时（例如程序把 `block=False` 改了，而用户
在清单里写了 `block: true`）应当听用户的 —— 这是"让用户能覆盖工具"
的最后一根杠杆，也是本项目"声明覆盖识别"一贯的口径
（`project_conventions.detect_conventions` 同一条规矩）。

⚠ 它与 `cds_registry` 的分工：`cds_registry` 只负责**读**宿主注册；
这里负责**合并**两个来源并回答查询。谁都不做"猜"。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.atomicio import write_text_atomic as _write_text_atomic
from storygraph.core.filelock import DEFAULT_TIMEOUT as DEFAULT_LOCK_WAIT
from storygraph.core.filelock import LockTimeout, file_lock
from storygraph.core.safeguard import swallow
from storygraph.core.threeway import merge_by_key as _merge_by_key

#: 清单里声明自定义语句的键名（`storygraph.rules.json` 的顶层键）
#: `storygraph.rules.json` 里放"语句声明"列表的键
MANIFEST_KEY = 'statements'
#: 清单里放**词组声明**的键（"服装 / 表情"这类分栏；见 `authoring.spec_groups`）
ATTR_GROUPS_KEY = 'attr_groups'
#: 清单里放**技术调用**声明的键（`$ 项目自己的包装函数(...)`；2026-10-30）
CALLS_KEY = 'calls'


#: 清单文件名。与 `project_conventions.MANIFEST_NAMES` 是同一份约定
#: （这里再写一遍是为了不让 `story` 内部互相 import；两处都有测试钉着）。
MANIFEST_NAMES = ('storygraph.rules.json',)

#: 扩展点清单（`storygraph.ext.json`）—— 与 `ext.registry.MANIFEST` 同一个名字。
#:
#: 它在这个模块里的角色很窄：**只当"声明来源"**（`RegisteredStatement.origin`）。
#: 命名**不另抄一份**会漂，所以由 `tests/test_ext_platform.py` 钉着两处相等。
EXT_MANIFEST = 'storygraph.ext.json'



@dataclass
class RegisteredStatement:
    """一条**被声明过**的自定义语句（T1：工具知道它是什么）。"""

    name: str = ''
    aliases: list = field(default_factory=list)
    #: `True` / `False` / `'possible'` —— 与 `cds_registry` 同一套值
    block: str = 'possible'
    source: str = 'declared'        # 'declared' | 'host-registry'
    origin: str = ''                # `defs.rpy:12` 或清单路径
    docs: str = ''
    #: M4 的投影字段（先占位，声明的照收不误）
    label: str = ''
    columns: list = field(default_factory=list)
    #: **块体允许出现哪些语句**（头 token 列表；空 = 不校验）——`body.allows`。
    #: 这是"结构级"约束（设计文档 §0 的 ②层）：语法形状猜不出来，只能被声明。
    body_allows: list = field(default_factory=list)
    #: **有序格子**（`pattern`：`[{kind,text,column,group,options}…]`）。
    #:
    #: ⚠ 2026-10-12 补：它以前**只在磁盘上**（`rules.json`），没进注册表对象，
    #: 于是"第几格 = 第几个位置参数"这件事在读取侧完全丢了 ——
    #: 实测后果：`swim_suit lyj "泳装" mood=happy` 在**卡片编辑器**里
    #: `角色`/`服装` 两格是**空的**（只有 `mood=happy` 认得出）。
    #: 现在它跟着声明一起流到 `spec_columns`（`_declared_positionals`）。
    pattern: list = field(default_factory=list)
    #: **复杂子句**（`clauses`：`at` / `behind` / `with` 这类关键字后面的
    #: 一段；2026-10-30）。形状见 `story.spec_clauses`。
    clauses: list = field(default_factory=list)
    render: str = 'verbatim'

    @property
    def names(self) -> list:
        return list(dict.fromkeys([self.name, *(self.aliases or [])]))

    @property
    def has_block(self) -> bool:
        return self.block in ('yes', 'possible', True)

    def to_dict(self) -> dict:
        out = {'name': self.name, 'aliases': list(self.aliases),
               'block': self.block, 'source': self.source,
               'origin': self.origin, 'docs': self.docs,
               'label': self.label, 'columns': list(self.columns),
               'body_allows': list(self.body_allows),
               'pattern': [dict(s) for s in (self.pattern or ())],
               'render': self.render}
        # ⚠ 没有子句时**不写这个键**：`describe` 的 golden 是"纯重构不许变"，
        #   给每条老声明都加一个空 `clauses` 只会让金标整份重基（噪声）。
        #   有子句时才出现，且放在最后（老键顺序不动）。
        if self.clauses:
            out['clauses'] = [dict(c) for c in self.clauses]
        return out


# ---------------------------------------------------------------------------
#  两个来源 → 一个注册表
# ---------------------------------------------------------------------------

def _from_manifest(data: dict, manifest_path: str) -> list:
    """
    `storygraph.rules.json` 的 `statements` 段 → `[RegisteredStatement, ...]`。

    形状（宽松，坏条目**跳过**而不是炸）：

        "statements": [
          {"name": "mycue", "aliases": ["cue"], "block": true,
           "docs": "闪电动画指令"},
          "battle"                      ← 也可以只写一个名字
        ]
    """
    out = []
    for item in (data.get(MANIFEST_KEY) or ()):
        if isinstance(item, str):
            item = {'name': item}
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        raw_block = item.get('block', 'possible')
        if raw_block is True:
            block = 'yes'
        elif raw_block is False:
            block = 'no'
        elif str(raw_block).lower() == 'possible':
            block = 'possible'
        else:
            block = 'possible'
        aliases = item.get('aliases')
        if not isinstance(aliases, list):
            aliases = []
        # `name` 允许写成空格分隔的多个词（与 CDS 的 `name="a b"` 同形）
        words = name.split()
        out.append(RegisteredStatement(
            name=words[0], aliases=[*words[1:], *[str(a) for a in aliases]],
            block=block, source='declared',
            # 来处可以说得更细（扩展点会写 `plugin:xxx.json`），没写就退回清单名
            origin=str(item.get('origin')
                       or (Path(manifest_path).name if manifest_path
                           else MANIFEST_KEY)),
            docs=str(item.get('docs') or ''),
            label=str(item.get('label') or ''),
            columns=[c for c in (item.get('columns') or ())
                     if isinstance(c, dict)],
            # `body: {"allows": ["say","show",…]}` —— 结构级约束（②层）：
            # 语法形状猜不出"这个块里允许写什么"，只能被声明。
            body_allows=[str(x) for x in (
                (item.get('body') or {}).get('allows') or ())
                if str(x).strip()],
            # ⚠ 有序格子必须一起收进来（否则读取侧推不出"第几格 = 第几个位置参数"）
            pattern=[dict(s) for s in (item.get('pattern') or ())
                     if isinstance(s, dict)],
            # 复杂子句（2026-10-30）：坏条目在这里**跳过**（与 columns 同一条
            # "坏条目跳过"纪律；界面那一层会出声）。
            clauses=_clauses_from_manifest(item.get('clauses')),
            render=str(item.get('render') or 'verbatim')))
    return out


def _clauses_from_manifest(raw) -> list:
    """清单里的 `clauses` → 规范形状（坏条目跳过，不炸整份清单）。"""
    if not raw:
        return []
    try:
        from storygraph.story.spec_clauses import normalize_clauses
        return normalize_clauses(raw)
    except Exception as e:                       # noqa: BLE001 - 坏条目不影响装配
        swallow('story.statement_registry.clauses', e)
        return []


#: 技术调用的 head：标识符，允许**一个点**（`renpy.transition` / `my_wipe`）。
_CALL_HEAD_RE = re.compile(r'^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?$')
_IDENT_RE = re.compile(r'^[A-Za-z_]\w*$')


def _call_head_ok(head: str) -> bool:
    return bool(_CALL_HEAD_RE.fullmatch(str(head or '').strip()))


def _ident_ok(text: str) -> bool:
    """是不是一个合法标识符（关键字参数名）。"""
    return bool(_IDENT_RE.fullmatch(str(text or '')))


def _calls_from_manifest(raw, origin: str = '') -> list:
    """
    清单里的 `calls` → 规范形状（坏条目跳过）。

    形状：

        {"head": "my_wipe", "label": "擦除",
         "columns": [{"name": "transition", "label": "转场",
                      "position": 0, "kind": "transition"}],
         "effect": "Dissolve", "seconds_arg": 1,
         "origin": "storygraph.rules.json"}

    `effect` / `seconds_arg` 只影响**演出预览**：包装函数本身不是转场，
    但项目可以在声明里说"它等价于哪个效果、时长取第几个参数"。
    认不出/没写 → 预览如实标"认不出"，不假装淡入。

    `origin`（2026-11-07 第四轮）= **这条声明写在哪**（`rules.json` /
    `storygraph.ext.json` / `plugin:xxx.json`）。参数里的同名键优先，
    否则用调用方给的默认值。为什么必须有：同一个 head 可以被两个文件声明，
    而装配顺序是「扩展 > 旧清单」—— 界面要知道"我改的这份会不会被盖住"，
    否则就是"保存成功、实际没变"（见 `save_calls`）。
    """
    out = []
    for item in (raw or ()):
        if isinstance(item, str):
            item = {'head': item}
        if not isinstance(item, dict):
            continue
        head = str(item.get('head') or item.get('name') or '').strip()
        if not _call_head_ok(head):
            continue
        columns = []
        for col in (item.get('columns') or ()):
            if not isinstance(col, dict):
                continue
            name = str(col.get('name') or col.get('label') or '').strip()
            if not name:
                continue
            raw_pos = col.get('position',
                              col.get('positional'))
            kw = str(col.get('kw') or '').strip()
            pos = None
            if kw:
                if not _ident_ok(kw):
                    continue
            else:
                try:
                    pos = int(raw_pos)
                except (TypeError, ValueError):
                    continue
                if pos < 0:
                    continue
            columns.append({
                'name': name,
                'label': str(col.get('label') or name),
                'kind': str(col.get('kind') or 'word'),
                'position': pos,
                'kw': kw,
                'choices': [str(c) for c in (col.get('choices') or ())],
                # 必填（2026-11-07 第五轮）：以前这里**把它丢了**，于是声明里
                # 写 `required: true` 与不写完全一样（`call_columns` 读到的
                # 永远是 False）。只在真写了的时候带上，老文件一个字节不变。
                **({'required': True} if col.get('required') else {}),
                # 提示 / 说明（第七轮）：同上，只在真写了时带 —— 卡片的参数格
                # 靠这两位把"声明里写的提示"显示出来（`_column_entry` 再往上传）。
                **({'placeholder': str(col.get('placeholder'))}
                   if col.get('placeholder') else {}),
                **({'help': str(col.get('help'))} if col.get('help') else {}),
            })
        if not columns:
            continue
        seconds_arg = item.get('seconds_arg')
        try:
            seconds_arg = (int(seconds_arg)
                           if seconds_arg is not None else None)
        except (TypeError, ValueError):
            seconds_arg = None
        out.append({'head': head, 'label': str(item.get('label') or ''),
                    'columns': columns,
                    'effect': str(item.get('effect') or ''),
                    'seconds_arg': seconds_arg,
                    # 来处：写进参数的优先（插件会写 `plugin:闪电动画.json`），
                    # 否则用调用方给的默认（`rules.json` / `storygraph.ext.json`）。
                    # **空就不写这个键** —— 老代码拿它当"普通 dict"逐字比较，
                    # 多一个恒为空的键只会制造噪音。
                    **({'origin': str(item.get('origin') or origin)}
                       if (item.get('origin') or origin) else {})})
    return out


def _from_cds(project_root) -> list:
    """宿主注册表（`renpy.register_statement`）→ `[RegisteredStatement, ...]`。"""
    out = []
    try:
        from storygraph.story.cds_registry import scan_project_cds
        for spec in scan_project_cds(project_root):
            where = (f'{Path(spec.source_file).name}:{spec.source_line}'
                     if spec.source_file else '')
            out.append(RegisteredStatement(
                name=spec.name, aliases=list(spec.aliases[1:]),
                block=spec.block, source='host-registry', origin=where,
                docs=spec.note or ''))
    except (ImportError, OSError, ValueError, KeyError, AttributeError,
            TypeError, RuntimeError) as e:
        # 扫描器是纯 Python（正则 + 读文件），能出的就是这几类；
        # 真出了别的类型，那说明扫描器自身坏了 —— 该炸出来，不该咽掉。
        swallow('story.statement_registry._from_cds', e)
    return out


class StatementRegistry:
    """
    项目的自定义语句查表。`declared`（清单）**覆盖** `host`（CDS）。

    用法：

        reg = StatementRegistry.for_project(project_root)
        hit = reg.match('mycue')      # → RegisteredStatement | None
    """

    def __init__(self, declared=(), host=(), attr_groups=(), calls=()):
        self.declared = list(declared)
        self.host = list(host)
        #: 项目声明的**词组**（`attr_groups`）—— 用于把候选分栏显示
        #: （"服装 / 表情"这类）。见 `authoring.spec_groups`。
        #: 空 = 项目没声明 → 调用方走原来的平铺候选，行为一字不变。
        self.attr_groups = list(attr_groups)
        #: 项目声明的**技术调用**（`calls`：`$ 包装函数(...)` 的参数形状）。
        #: 空 = 只有内置 `renpy.transition` 那一条，行为与从前一致。
        self.calls = [dict(c) for c in (calls or ())
                      if isinstance(c, dict) and c.get('head')]
        self._by_name = {}
        self._calls_by_head = {str(c['head']): c for c in self.calls}
        # **先装 host，再装 declared** —— 后者覆盖前者（优先级就落在这里，
        # 而不是散在调用方的 if 里）。
        for spec in self.host:
            for n in spec.names:
                self._by_name[n] = spec
        for spec in self.declared:
            for n in spec.names:
                self._by_name[n] = spec

    def register(self, spec: RegisteredStatement) -> None:
        """手工登记一条（最高优先级；测试与调用方扩展用）。"""
        self.declared.append(spec)
        for n in spec.names:
            self._by_name[n] = spec

    def match(self, head: str):
        """语句头（`'mycue'`）→ `RegisteredStatement`；没有就是 None。"""
        return self._by_name.get(str(head or '').strip().rstrip(':'))

    def call_spec(self, head: str):
        """技术调用 head（`'my_wipe'`）→ 声明 dict；没有就是 None。"""
        return self._calls_by_head.get(str(head or '').strip())

    def names(self) -> list:
        return sorted(self._by_name)

    def specs(self) -> list:
        """去重后的全部 spec（declared 在前）。"""
        seen, out = set(), []
        for spec in [*self.declared, *self.host]:
            key = spec.name
            if key in seen:
                continue
            seen.add(key)
            out.append(spec)
        return out

    def __bool__(self) -> bool:
        return bool(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    @classmethod
    def for_project(cls, project_root=None, use_cache: bool = True):
        """按优先级装配：清单（declared）→ 宿主注册（host）。

        **带缓存**（与 `project_conventions.conventions_for` 同一套做法）：
        装配要扫一遍全项目 .rpy 找 CDS，而 `beats_from_parse_result` 会被
        逐章调用 —— 不缓存就是 O(章数 × 文件数)。缓存键是项目根，
        用户改了 `storygraph.rules.json` / `storygraph.ext.json` 后清一下即可
        （`clear_cache()`；`EditorSession` 打开项目时、扩展窗口保存后都会清）。

        声明的**四个来源**（优先级从高到低，同名的后面盖前面）：

            扩展点（`storygraph.ext.json` + `extensions/` 插件）
            > `storygraph.rules.json` 的 statements / calls
            > 宿主 `renpy.register_statement`（CDS）

        前两个都在 `declared` / `calls` 里，所以"哪个更强"就落在这里的
        列表顺序上，不散在调用方的 if 里。
        """
        key = str(project_root or '')
        if not key:
            return cls()
        if use_cache:
            hit = _CACHE.get(key)
            if hit is not None:
                return hit
        declared, host, groups = [], [], []
        calls: list = []
        try:
            # 直接取下沉后的 `project_manifest`：`project_conventions` 会
            # import 本模块（`_declared_heads`），走它就是一条环比。
            from storygraph.story.project_manifest import load_manifest
            from storygraph.authoring.spec_groups import parse_attr_groups
            data, path, _warnings = load_manifest(project_root)
            if isinstance(data, dict):
                declared = _from_manifest(data, path)
                # 词组分栏：项目自己声明"有哪几组词"（`attr_groups`），
                # 工具不写死"服装/表情"（用户 2026-10-10 的通用性要求）
                groups = parse_attr_groups(data.get(ATTR_GROUPS_KEY))
                # 技术调用（2026-10-30）：`$ 包装函数(...)` 的参数形状
                # `origin` 记下"写在哪"：清单可能在项目根或 `game/` 下
                # （见 `manifest_path`），界面要照实说，不能一律叫 rules.json。
                calls = _calls_from_manifest(
                    data.get(CALLS_KEY),
                    Path(path).name if path else MANIFEST_NAMES[0])
        except Exception as e:                # noqa: BLE001
            swallow('story.statement_registry.for_project.manifest', e)
        host = _from_cds(project_root)
        # 「是否使用」开关的落地点（用户 2026-11-07）：
        # `storygraph.ext.json` 里 `enabled: false` 的扩展点 → **注册表里没有它**。
        # 于是卡片不出格子、表格不出列、批处理也不会拿它当理由 —— 但声明本身
        # 还在磁盘上，随时能再打开（不是删除）。
        off_st, off_calls = _ext_disabled(project_root)
        if off_st:
            declared = [d for d in declared
                        if str(getattr(d, 'name', '')) not in off_st]
        if off_calls:
            calls = [c for c in calls
                     if str(c.get('head') or '') not in off_calls]
        # 扩展点里**新建**的写法 / 调用（用户 2026-11-07："我想在工具里加个
        # 新写法"）。它们与 `rules.json` 是同一份形状，所以走同一条装配线；
        # 同名的**以扩展点为准** —— 界面上改过的那份比手写清单更新
        # （"改了要生效"，不是"改了没反应"）。
        ext_st, ext_calls = _from_extensions(project_root)
        if ext_st:
            heads = {d.name for d in ext_st}
            declared = [d for d in declared if d.name not in heads] + ext_st
        if ext_calls:
            heads = {str(c.get('head') or '') for c in ext_calls}
            calls = [c for c in calls
                     if str(c.get('head') or '') not in heads] + ext_calls
        reg = cls(declared=declared, host=host, attr_groups=groups,
                  calls=calls)
        if use_cache:
            _CACHE[key] = reg
        return reg


def _ext_disabled(project_root) -> tuple:
    """
    扩展清单里被**关掉**的扩展点 → `(语句名集合, 调用 head 集合)`。

    ⚠ 这里**只读** `storygraph.ext.json`，不调 `ext.registry`：摘掉一个声明
    只需要 id，不需要装配插件 / 校验字段。装配那条重路在 `_from_extensions`
    （**按项目缓存**，一个项目只走一次），两条路分工不同、不许互相替代。
    读不到 / 坏 JSON → 当作"没有关掉任何东西"（**关闭开关不该让工具打不开**）。
    """
    try:
        import json as _json
        from pathlib import Path as _Path
        path = _Path(project_root) / 'storygraph.ext.json'
        if not path.is_file():
            return set(), set()
        data = _json.loads(path.read_text(encoding='utf-8') or '{}')
    except (OSError, ValueError):
        # 读不到 / 坏 JSON → 当作"没有关掉任何东西"：**关闭开关不该让工具打不开**
        return set(), set()
    off_st, off_calls = set(), set()
    if not isinstance(data, dict):
        return off_st, off_calls
    for item in (data.get('extensions') or ()):
        if not isinstance(item, dict) or item.get('enabled') is not False:
            continue
        ident = str(item.get('id') or '').strip()
        if not ident:
            continue
        if str(item.get('kind') or '') == 'call':
            off_calls.add(ident)
        else:
            off_st.add(ident)
    return off_st, off_calls


def _from_extensions(project_root) -> tuple:
    """
    扩展点（`storygraph.ext.json` + `<项目>/extensions/`）里**开着的**写法与
    调用 → 与 `rules.json` 同一份形状的 `([RegisteredStatement], [call dict])`。

    为什么要在这一层接：`spec_columns` / 卡片 / 批处理 / 覆盖率统计全都只问
    `StatementRegistry`，不各自去翻 JSON。**把扩展点变成注册表里的条目**，
    等于让"界面上配出来的写法"与"手写 rules.json 的写法"对工具是同一件事。

    ⚠ 会**装载插件**（`extensions/*.py` 是要执行的）：所以只在 `for_project`
      这一层调，而它按项目根缓存 —— 一个项目只付一次。插件里请别在 import
      时回调工具（那会重入本函数）；正常写法（只放 `EXTENSIONS` 数据）没问题。

    ⚠ 被关掉的（`enabled: false`）在这里**不会出现**（`ext.bridge` 按开关过滤），
      与 `_ext_disabled` 那条"摘掉"的路互为补集，不会互相打架。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return [], []
    #: 绝大多数项目两者都没有 —— 快路走掉，别为了"可能没有扩展点"付一次装配
    #: （装配会读清单、扫插件目录；插件目录里的 `.py` 还要执行）。
    if not (root / EXT_MANIFEST).is_file() and not (root / 'extensions').is_dir():
        return [], []
    try:
        from storygraph.ext import bridge
        raw = bridge.for_project(project_root)
    except ImportError:
        # 后端被裁剪过（与 `_from_cds` 同一条纪律）：没有扩展层就只当没声明过。
        return [], []
    return (_from_manifest({'statements': raw.get('statements') or ()},
                           EXT_MANIFEST),
            _calls_from_manifest(raw.get('calls'), EXT_MANIFEST))


#: 项目根 → `StatementRegistry`（见 `for_project` 的说明）
_CACHE: dict = {}


def clear_cache() -> None:
    """丢掉装配缓存（测试、以及"用户刚改完 rules.json"时用）。"""
    _CACHE.clear()


def manifest_path(project_root) -> Path:
    """
    清单**实际所在**的路径（项目根或 `game/`，已有的那份优先）。

    为什么要有（2026-10-18 审计 P1-5）：读走 `find_manifest`（两处都认），
    写却一律 `root / 'storygraph.rules.json'` —— 清单在 `game/` 下的项目
    能看见声明，但删/清/重置/写入全部失败或写到**另一个文件**。
    新建仍默认项目根（与历史行为一致）。
    """
    root = Path(project_root) if project_root else Path('.')
    for p in (root / MANIFEST_NAMES[0], root / 'game' / MANIFEST_NAMES[0]):
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return root / MANIFEST_NAMES[0]


#: 往上找项目根时**永远跳过**的目录名（构建/缓存/版本库/临时目录）。
_NEVER_A_PROJECT_ROOT = frozenset({
    '.pip-tmp', '.git', '.hg', '.svn', '__pycache__',
    'node_modules', '.venv', 'venv', '.tox', '.mypy_cache',
    '.pytest_cache', 'dist', 'build',
})


def root_of(script_path) -> str:
    """
    从脚本路径反推**项目根**。**推不出来返回 `''`**（不是"退回父目录"）。

    一个"项目"的标志只有两条：**有 `game/` 目录**，或者**有
    `storygraph.rules.json`**。两者都没有就不是项目 —— 这时返回父目录会让
    上层去 `rglob` 一个根本不是项目的地方，实测过代价：一个用临时目录的
    用例因此从 0.6 秒涨到 **227 秒**（`tmp_path` 上层是系统临时目录，
    里面成百上千个目录）。
    """
    try:
        p = Path(script_path)
    except TypeError:
        return ''
    # **字符串层面先判一次 `/game/`**（2026-10-22，CI 的 Linux 上抓到）：
    # POSIX 上 `Path(r'D://p//game//a.rpy')` 是**单段路径**（反斜杠不是分隔符），
    # `p.parents` 是空的 —— 于是 Windows 路径一条也推不出来。
    # 而路径可能从 `--sdk`、日志、剪贴板粘过来，分隔符不一定和本机一致。
    # 这里只看"路径里有没有 `/game/` 这一段"，纯字符串、与平台无关。
    raw = str(script_path).replace('\\', '/')
    if '/game/' in raw:
        head = raw.split('/game/', 1)[0]
        if head:
            return head
    chain = [p.parent, *p.parents]
    for parent in chain:
        if parent.name == 'game':
            return str(parent.parent)
    for parent in chain:
        # **这些目录永远不是项目根**（2026-11-01 实测的一次真事故）：
        # `.pip-tmp/storygraph.rules.json` 是某次测试/探针留下的，而 pytest 的
        # 临时目录就在 `.pip-tmp/` 下 —— 于是**每一条**从 tmp 路径出发的
        # root_of() 都反推出 `.pip-tmp`，把那份带 `show` 声明的清单套到
        # 所有用例的导出上：Beats 多出 3 列、`show` 往返变成 `show show show`，
        # 30 条用例一起红。根因不是那份文件，是"往上找"没有边界。
        if parent.name in _NEVER_A_PROJECT_ROOT:
            continue
        try:
            if (parent / 'game').is_dir():
                return str(parent)
            if (parent / MANIFEST_NAMES[0]).is_file():
                return str(parent)
        except OSError:
            continue
    return ''


def registry_for_parse_result(parse_result, use_cache: bool = True):
    """
    从 `ParseResult` 反推项目根 → `StatementRegistry`（推不出来返回 None）。

    为什么**反推**而不是让调用方传：`beats_from_parse_result` 有 8 个调用点
    （build / compile / app / cli / patcher / script_table…），一处漏传就是
    "这条路上的自定义语句又变成不认识" —— 而那正是最容易被忽略的错
    （本项目已有"三处调用方漏交 `beats`"的前科）。语句自带 `source`
    （文件路径），信息本来就在手上。
    """
    root = ''
    for st in (getattr(parse_result, 'statements', None) or ()):
        src = getattr(st, 'source', None)
        if isinstance(src, (tuple, list)) and src and src[0]:
            root = root_of(src[0])
            break
    if not root:
        for b in (getattr(parse_result, 'file_blocks', None) or ()):
            if getattr(b, 'source_file', ''):
                root = root_of(b.source_file)
                break
    if not root:
        return None
    return StatementRegistry.for_project(root, use_cache=use_cache)


def annotate_spec_statements(statements, registry) -> int:
    """
    就地给"被声明过的"语句打上 `payload['spec_name']`；返回命中条数。

    为什么是**后置标注**而不是改解析器：依赖方向（契约 H1）不许
    `parsing` import `story`，而"被声明过"这件事只有 story 层知道
    （清单在项目根、CDS 扫描也在 story）。所以解析器照旧只回答
    "这是未建模块"，谁来认领由这一层说了算。

    只标注**未建模**那三类（`unmodeled` / `unknown_block` / `known_block`）：
    内置建模过的语句（say/show/menu…）本来就归工具管，不该被 spec 抢走。
    """
    if not registry:
        return 0
    from storygraph.core.vocabulary import statement_head_token
    hit = 0
    for st in (statements or ()):
        payload = getattr(st, 'payload', None)
        if not isinstance(payload, dict):
            continue
        if (payload.get('raw_kind') or '') not in (
                'unmodeled', 'unknown_block', 'known_block'):
            continue
        spec = registry.match(statement_head_token(payload.get('text') or ''))
        if spec is not None:
            payload['spec_name'] = spec.name
            hit += 1
    return hit


#: 解析器把它"没被任何分支接住"的语句记成这几个 `raw_kind`
#: （与 `beat_build._UNMODELED_KINDS` 是同一份约定）。
UNMODELED_KINDS = frozenset({'unmodeled', 'unknown_block', 'known_block'})


def capability_of(head: str, registry=None, *, modeled: bool = False,
                  raw_kind: str = '', structured: bool = False) -> 'Capability':
    """
    （语句头，注册表）→ `Capability`（**唯一一处**把事实译成能力等级）。

    调用方给"工具知不知道它"（`modeled` / `raw_kind` / `structured`），
    这一层给"几级"。判定顺序与 `core.capability.state_of` 一致，
    不在这里另立一套。

    `structured` 只对"块体逐字"那两级有意义（循环 / 相机）：块体已经
    **作为独立语句进了语句流**（可以逐句编辑）→ `T0`；仍是一坨逐字原文
    → `T0b`。**这一位必须由调用方给**，不许在这里"看头行猜" ——
    猜错就是把"接住了"当成"完全理解了"（`core.capability` 的 T0/T0b 分界）。
    """
    from storygraph.core.capability import (PROV_BUILTIN, PROVENANCE_NAMES, T0,
                                            T0B, T2, T3, Capability,
                                            escape_for, provenance_of,
                                            state_of)

    if (raw_kind or '') == 'loop':
        if structured:
            # 循环块（D2 + 2026-10-07 第三轮）：**块体也结构化了**（作用域），
            # 于是它真的是"结构化列全功能编辑" → T0。
            return Capability(head=head, level=T0, provenance=PROV_BUILTIN,
                              evidence='工具内置的循环块：头行结构化（可编辑'
                                       '可校验），块体已按作用域拆成普通语句，'
                                       '可以逐句编辑',
                              escape=escape_for(T0))
        # 老形态（整块逐字躺在 `Opaque` 里）→ T0b。
        return Capability(head=head, level=T0B, provenance=PROV_BUILTIN,
                          evidence='工具内置的循环块：头行结构化（可编辑'
                                   '可校验），块体逐字保真',
                          escape=escape_for(T0B))

    if (raw_kind or '') == 'camera':
        # 3D 舞台的 `camera` 块（2026-10-07）：头行（层名）结构化，
        # 块体（ATL 属性）逐字 —— 它**没有** `structured` 那条路（ATL 不是
        # 演出流语句，工具不解释它），所以恒为 T0b。
        return Capability(head=head, level=T0B, provenance=PROV_BUILTIN,
                          evidence='工具内置的相机块：头行（层名）结构化'
                                   '（可编辑可校验），块体（ATL 属性）逐字保真',
                          escape=escape_for(T0B))

    if modeled or (raw_kind or '') not in UNMODELED_KINDS:
        lv = T0
        return Capability(head=head, level=lv, provenance=PROV_BUILTIN,
                          evidence='工具内置语法（有专门的解析分支）',
                          escape=escape_for(lv))

    spec = registry.match(head) if registry else None
    known = bool(head) and head in _known_heads()
    src = (spec.source if spec is not None else '')
    projected = bool(spec is not None and (spec.columns or spec.label))
    lv = state_of(declared=spec is not None, projected=projected,
                  known_head=known)
    prov = provenance_of(declared_source=src, known_head=known)

    if lv == T2:
        ev = '工具认识它的形状（Ren\'Py 标准语句），但还没有结构化建模'
    elif lv == T3:
        ev = '工具完全不知道它是什么'
    elif lv == 'T1a':
        ev = f'项目声明过（{PROVENANCE_NAMES.get(prov, prov)}），并给了参数形状'
    else:
        ev = f'项目声明过（{PROVENANCE_NAMES.get(prov, prov)}），只逐字透传'
    return Capability(head=head, level=lv, provenance=prov, evidence=ev,
                      escape=escape_for(lv))


#: 「工具认识的语句头」全集（含流程块）—— 唯一真源在 `core.vocabulary`。
#: 这里做成**懒加载**（导入期不拉 vocabulary），失败就退化成空集：
#: 那时所有未建模语句都会判成 T3 —— **宁可报"不认识"，也不要假装认识**。
_KNOWN_HEADS: frozenset = frozenset()


def _known_heads() -> frozenset:
    global _KNOWN_HEADS
    if not _KNOWN_HEADS:
        try:
            from storygraph.core.vocabulary import RENPY_KNOWN_HEADS
            _KNOWN_HEADS = RENPY_KNOWN_HEADS
        except Exception as e:                # noqa: BLE001 - 退化成空集，但要说一声
            # 空集 = 一个宿主 statement 头都不认 → 语句覆盖率 / 校验基线会整体变。
            # 缓存只在**成功**时写入（空集就是"没加载过"的哨兵，下次还会重试）。
            _KNOWN_HEADS = frozenset()
            swallow('story.statement_registry.known_heads', e)
    return _KNOWN_HEADS


def coverage_of_statements(statements, registry=None,
                           samples_limit: int = 24) -> 'Coverage':
    """
    语句流 → `Coverage`（项目级可编辑覆盖率，CAP 视角）。

    只统计**需要判断能力等级**的语句：内置建模的（say/show/menu…）
    一律记 T0 —— 它们本来就是工具管的，把它们算进"覆盖率"是对的
    （覆盖率回答的是"整份脚本有多少是工具替得了的"）。
    """
    from storygraph.core.capability import T0, Coverage
    from storygraph.core.vocabulary import statement_head_token

    cov = Coverage()
    seen = set()
    for st in (statements or ()):
        payload = getattr(st, 'payload', None)
        payload = payload if isinstance(payload, dict) else {}
        raw_kind = payload.get('raw_kind') or ''
        # ⚠ 循环块（D2）**没有** `raw_kind`（它走的是自己的 `kind='loop'`），
        # 只按 `raw_kind` 判会把它算成"内置完全建模"（T0）—— 那就等于
        # 拿"接住了"冒充"完全理解了"。这里显式把它认成 `loop`。
        # `camera`（3D 舞台）是同一个坑的第二例：`kind='camera'` 也没有
        # `raw_kind`，不显式认就会当成 T0（它其实是 T0b）。
        if getattr(st, 'kind', '') in ('loop', 'camera'):
            raw_kind = getattr(st, 'kind')
        head = statement_head_token(payload.get('text') or '')
        cap = capability_of(
            head, registry, raw_kind=raw_kind,
            # 循环的块体是不是**已经进语句流**了（作用域）—— 只有
            # `walked=True` 才算"结构化列全功能编辑"（T0）。
            structured=bool(payload.get('walked')) if raw_kind == 'loop'
            else False)
        cov.counts[cap.level] = cov.count_of(cap.level) + 1
        # 样例只留"需要动作"的那几级，且每个头只留一条
        key = (cap.level, cap.head)
        if (cap.level != T0 and cap.head and key not in seen
                and len(cov.samples) < samples_limit):
            seen.add(key)
            cov.samples.append(cap)
    return cov


def coverage_of_beats(beats, registry=None, samples_limit: int = 24):
    """
    **拍** → `Coverage`。给"手里只有拍、没有语句流"的调用方用
    （构建报告、编辑器）。

    判据与 `coverage_of_statements` 同源：一张拍要么是内置建模的
    （没有 `opaque`）→ T0；要么带 `opaque` → 按注册表 / 形状判
    （`spec_name` 非空 = 已声明；`while` 这类头 = 形状）。
    """
    from storygraph.core.capability import T0, Coverage
    from storygraph.core.vocabulary import statement_head_token

    cov = Coverage()
    seen = set()
    for b in (beats or ()):
        text = str(getattr(b, 'opaque', '') or '')
        # **ATL 附件**（2026-10-31）：`show …:` + 缩进块现在拆成两半 ——
        # 头行（谁/什么/在哪）结构化进 `Beat.show`，块体原文进 `Beat.opaque`。
        #
        # 于是这里不能再用"opaque 第一行是什么语句"来判级：块体首行是
        # `    zoom 0.15`，取出来的 head 是 `zoom` —— 一个不存在的语句头，
        # 于是 33 处本已接住的立绘被记成 T3「工具完全不知道它是什么」。
        #
        # 判据改成**结构**：opaque 首行有缩进（说明它是挂在上一行语句上的
        # 块体，不是自带头行的整块）+ 这一拍的 `show` 有值（头行真的接住了）
        # → 结构已知，T0；块体逐字保真，工具**不解释也不需要解释** ATL。
        _first = next((ln for ln in text.split('\n') if ln.strip()), '')
        if (_first[:1] in (' ', '\t')
                and str(getattr(b, 'show', '') or '').strip()):
            cov.counts[T0] = cov.count_of(T0) + 1
            continue
        if not text.strip():
            # 没有 `opaque` = 这一拍是**内置建模**的（台词 / 舞台 / 代码…），
            # 或者是**结构化块的头拍**（循环 / 相机：块体各自成拍）。
            # 两者都是"工具替你管得了" → T0。
            # ⚠ `T0b` 只留给"整块逐字躺在 opaque 里"的老形态 —— 上面对
            # 相机/循环的分支就是那条线。
            cov.counts[T0] = cov.count_of(T0) + 1
            continue
        head = statement_head_token(
            next((ln for ln in text.split('\n') if ln.strip()), ''))
        # 分类**只**交给 `capability_of`（查注册表 + 形状规则），
        # 这里不自己看 `b.spec_name` —— 两处判据就是两套真相。
        # 块体逐字但**头行内置建模**的两种（D2 循环 / 2026-10-07 相机）：
        # 有 `loop_header` / `camera_header` 就说明是**内置建模**的，
        # 而不是"未识别" —— 分类仍然只由 `capability_of` 说了算。
        kind = 'unknown_block'
        if str(getattr(b, 'loop_header', '') or ''):
            kind = 'loop'
        elif str(getattr(b, 'camera_header', '') or ''):
            kind = 'camera'
        cap = capability_of(head, registry, raw_kind=kind,
                            # 走到这里说明**有** `opaque` —— 也就是"整块逐字"
                            # 那条路，`structured` 恒为 False（循环 → T0b）。
                            structured=False)
        cov.counts[cap.level] = cov.count_of(cap.level) + 1
        key = (cap.level, cap.head)
        if (cap.level != T0 and cap.head and key not in seen
                and len(cov.samples) < samples_limit):
            seen.add(key)
            cov.samples.append(cap)
    return cov


def format_capability_summary(statements=None, registry=None,
                              beats=None) -> str:
    """一步到位：语句流（或拍）→ 人话报告（报告/体检用）。"""
    from storygraph.core.capability import format_capability_report
    if beats is not None:
        return format_capability_report(coverage_of_beats(beats, registry))
    return format_capability_report(
        coverage_of_statements(statements, registry))


def spec_arg_choices(registry, head: str, column: str = '') -> list:
    """
    M4（投影）：这条声明语句的某个参数**有哪些候选值**。

    声明形状（`storygraph.rules.json`）：

        {"name": "mycue", "columns": [
            {"name": "intensity", "choices": ["1", "2", "3"]},
            {"name": "target", "choices": ["ling", "lan"]}]}

    给 UI / Excel 数据验证用。**没有声明就返回空列表**（不是"猜一组"）——
    猜出来的下拉会诱导人写出脚本里根本没有的值。
    """
    spec = registry.match(head) if registry else None
    if spec is None:
        return []
    out = []
    for col in (spec.columns or ()):
        if not isinstance(col, dict):
            continue
        if column and str(col.get('name') or '') != column:
            continue
        for c in (col.get('choices') or ()):
            if str(c) not in out:
                out.append(str(c))
    return out


def _declared_kinds(spec) -> dict:
    """
    格子名 → 格子类型（`keyword` / `variable` / `choice` / `optional` / …）。

    为什么要单独一步（2026-11-07）：**类型只存在 `pattern` 的格子上**，列描述里
    原本没有它。于是卡片只能"看有没有候选"猜一个 —— `keyword`（固定几个字，
    剧本里必须原样出现）那一格成了**可编辑的输入框**，人改一次就把语句名改掉
    （写回时按位置替换第 0 个词）。带上类型，界面才有东西可以分派。

    ⚠ 只用**声明的原始格子**（`spec.pattern`），不走 `parse_pattern` ——
    后者会把格子规范化成另一套键，而这里要的是"原始声明说了什么"。
    """
    kinds: dict = {}
    for slot in (getattr(spec, 'pattern', None) or ()):
        if not isinstance(slot, dict):
            continue
        kind = str(slot.get('kind') or '')
        for key in (slot.get('column'), slot.get('name'), slot.get('text')):
            text = str(key or '').strip()
            if text and text not in kinds:
                kinds[text] = kind
    return kinds


def _column_entry(col: dict, *, name: str, pos, multi: bool, kinds: dict) -> dict:
    """
    一条列描述（`spec_columns` 里那 8 个键的**唯一出处**）。

    抽出来是因为 `spec_columns` 已经顶到复杂度棘轮了（它做"声明优先 / 内置表 /
    从格子推列"三件事），而这段键表纯属拼装 —— 拼装就该有自己的名字。
    """
    return {'name': name,
            'label': str(col.get('label') or ''),
            'choices': [str(c) for c in (col.get('choices') or ())],
            'required': bool(col.get('required')),
            'positional': pos,
            # "这一格能装好几个词"（多音轨 / 多声部）：卡片据此给提示
            'multi': multi,
            # 声明里这一格的类型（可能空 → 界面按"有没有候选"兜底）
            'kind': str(col.get('kind') or kinds.get(name) or ''),
            'choices_from': str(col.get('choices_from') or ''),
            # 声明里写的提示 / 说明（2026-11-07 第七轮）：卡片参数格的灰字与
            # tooltip 直接读这两位 —— 从前它们到不了界面（只有按类型的默认值）。
            'placeholder': str(col.get('placeholder') or ''),
            'help': str(col.get('help') or '')}


def spec_columns(registry, head: str) -> list:
    """
    这条声明的**列描述**（M4）：
    `[{name, label, choices, required, positional, choices_from}]`。

    `choices_from`（可选）：候选来自**资源表的某一类**（`'transform'` /
    `'image'` / …），而不是内联的一串值 —— `with` 这种语句的候选就是项目里
    真实存在的变换，让 Excel 直接绑到那张候选表上（自伸缩、没有 255 字符上限，
    也不会因为项目加了一个变换就过时）。

    ⚠⚠ **位置参数号从声明自己的格子顺序推出来**（2026-10-12 修的实测 bug）：

        `swim_suit lyj "泳装" mood=happy`      ← 作者写的（前两个是位置参数）
        卡片编辑器里：`角色` 空、`服装` **空**、`神态` = happy

    根因：列描述里的 `positional` **只有显式写了才有**，而界面（弹窗）
    存下来的是 `pattern`（有序格子），没人把"第几格 = 第几个位置参数"写进列里。
    于是 `Beat.spec_arg_cells` 只能去 `key=value` 里找值 —— 位置参数全部落空。
    这里按**声明自己的顺序**补上位置号（第 0 个可填格 = 位置 0，依此类推）；
    显式写了 `positional` 的仍然以它为准。
    """
    spec = registry.match(head) if registry else None
    if spec is None:
        return []
    positions = _declared_positionals(spec)
    multi = _declared_multi(spec)
    kinds = _declared_kinds(spec)
    cols = [c for c in (spec.columns or ()) if isinstance(c, dict)]
    if not cols:
        # ⚠⚠ **只有格子、没有列**的声明也要能编辑（2026-10-18）。
        #
        # 界面（`build_declaration`）同时写 `columns` 与 `pattern`，但手写的
        # / 早期版本的 `storygraph.rules.json` 可能只写了 `pattern` ——
        # 那时卡片上**一格都不出现**（声明明明说了"后面跟这几个词"），
        # 而人只会看到"我声明了，参数却不存在"。
        # 从格子推列是**机械**的（格子本来就是这条语句的形状声明）：
        # 每一格只要有列名（`column`）就是一列，候选直接照抄。
        cols = []
        try:
            from storygraph.authoring.pattern import parse_pattern
            slots = parse_pattern(getattr(spec, 'pattern', None) or ()) or ()
        except Exception as e:                   # noqa: BLE001 - 认不出就不建列
            swallow('story.statement_registry.spec_columns.pattern', e)
            slots = ()
        for slot in slots:
            # ⚠ **关键字格不建列**（2026-10-18）：它是"固定几个字"，
            # 值就是那句话本身 —— 建一列等于让改参数的人**把语句名改掉**
            # （折回时按位置号替换第 0 个词 → `sfx_mix` 变了，整条语句废掉）。
            if str(slot.get('kind') or '') == 'keyword':
                continue
            # ⚠ **可选段用"脚本里那个词"当列名**（2026-10-18）：`gain=-6` 里
            #   写回时用的是 `gain`（`set_spec_arg` 按列名拼 `<名>=<值>`），
            #   而「音量」只是给人看的标签。用列名拼就会写出 `音量=-6`
            #   —— 一个脚本里根本不存在的参数（引擎当场报错）。
            _kind = str(slot.get('kind') or '')
            name = str((slot.get('text') if _kind == 'optional'
                        else slot.get('column')) or '').strip()
            label = str(slot.get('column') or name).strip()
            if not name or any(str(c.get('name')) == name for c in cols):
                continue
            item = {'name': name, 'label': label,
                    'choices': [str(c) for c in (slot.get('options') or ())],
                    'placeholder': str(slot.get('placeholder') or ''),
                    'help': str(slot.get('help') or '')}
            if slot.get('multi'):
                item['multi'] = True
            cols.append(item)
    out = []
    for col in cols:
        name = str(col.get('name') or '').strip()
        if not name:
            continue
        pos = col.get('positional')
        if pos is None:
            pos = positions.get(name)
        out.append(_column_entry(col, name=name, pos=pos,
                                 multi=bool(col.get('multi')) or name in multi,
                                 kinds=kinds))
    # ---- 复杂子句（2026-10-30）------------------------------------------
    # 子句**不是位置参数**：它由关键字引入、可以不出现、顺序也可变。
    # 所以 `positional=None`，并带上 `clause` 元数据，让读值/折回知道
    # "这一列要按子句处理，不能写成 `位置=…`"。
    seen = {str(c.get('name')) for c in out}
    for clause in (getattr(spec, 'clauses', None) or ()):
        if not isinstance(clause, dict):
            continue
        name = str(clause.get('name') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({'name': name,
                    'label': str(clause.get('label') or name),
                    'choices': [str(c) for c in (clause.get('choices') or ())],
                    'required': not bool(clause.get('optional', True)),
                    'positional': None,
                    # 类型只为提示（`transition` 会让预览消费它）
                    'multi': str(clause.get('kind') or '') == 'words',
                    'choices_from': '',
                    'clause': dict(clause)})
    return out


def _declared_multi(spec) -> set:
    """
    `{列名}` —— 哪些格子在声明里标了"可以有多个"（`pattern[].multi`）。

    为什么要单独算：`columns[].` 里没有这一位（界面把它存在格子上），
    而卡片/表格要据此决定"这一格能不能连写多个词"。
    认不出（没有 pattern）→ 空集，行为与从前一样。
    """
    try:
        from storygraph.authoring.pattern import parse_pattern
    except Exception as e:                       # noqa: BLE001 - 认不出就不标
        swallow('story.statement_registry.declared_multi.import', e)
        return set()
    out: set = set()
    for slot in (parse_pattern(getattr(spec, 'pattern', None) or ()) or ()):
        if not isinstance(slot, dict) or not slot.get('multi'):
            continue
        name = str(slot.get('column') or '').strip()
        if name:
            out.add(name)
    return out


def _declared_positionals(spec) -> dict:
    """
    `{列名: 位置号}` —— 按声明的**格子顺序走一遍**，数出每一格是第几个位置参数。

    ⚠⚠ **2026-10-13 第三次修正：位置号必须"走格子数"，不能"只数可填格"。**

    前两版都栽在同一件事上：`keyword` 格（固定几个字，如 `swim_suit`）
    **不占位置号、却确实吃掉一个词**。只数可填格的话：

        show lan swim_suit happy
        [人物=任意] [swim_suit=固定] [happy=从这些选]
        → 人物@0、happy@1      ← 数出来的
        → 实际读值：pos=[lan, swim_suit, happy]
          · 人物@0 → lan          ✅
          · happy@1 → swim_suit   ❌（把关键字本身当成了值，"三格只对一格"）

    现在按**与读值同一套**的走法编号（`spec_args.WALK_KINDS`）：
    每吃一个词就 +1，关键字格也吃词、也占号。于是 happy 会拿到 @2，
    读值就是 `happy`。

    认不出（没有 `pattern` / 旧形状）就返回 `{}`，调用方退回旧规则（**不猜**）。
    """
    try:
        from storygraph.authoring.pattern import parse_pattern
        from storygraph.story.spec_args import WALK_KINDS
    except Exception as e:                       # noqa: BLE001 - 认不出就不补
        swallow('story.statement_registry.declared_positionals.import', e)
        return {}
    slots = [s for s in parse_pattern(getattr(spec, 'pattern', None) or ())
             if isinstance(s, dict)]
    if not slots:
        return {}
    cols = [c for c in (getattr(spec, 'columns', None) or ())
            if isinstance(c, dict) and str(c.get('name') or '').strip()]
    if not cols:
        # 只有格子、没有列的声明（2026-10-18）：位置号照样要算 —— 否则
        # `spec_arg_cells` 只能退回"按 key=value 找"，位置参数**整排空着**。
        # 与 `spec_columns` 的推导同源：格子上的列名就是列名。
        seen_cols: set = set()
        for slot in slots:
            name = str(slot.get('column') or '').strip()
            if name and name not in seen_cols:
                seen_cols.add(name)
                cols.append({'name': name, 'label': name})
    out: dict = {}
    used = set()
    token_index = 0
    for slot in slots:
        kind = str(slot.get('kind') or '')
        if kind not in WALK_KINDS:
            continue                             # optional 之类：跳过（不算词）
        keys = {str(slot.get('column') or '').strip(),
                str(slot.get('text') or '').strip()} - {''}
        hit = None
        for col in cols:
            if id(col) in used:
                continue
            if keys & {str(col.get('name') or '').strip(),
                       str(col.get('label') or '').strip()}:
                hit = col
                break
        if hit is None and kind != 'keyword':
            # ⚠⚠ **固定字格不许"顺序兜底"拿走一列**（2026-10-18 第五轮攻击修，P0）。
            #
            # 声明里 `pattern` 有固定字格（`swim_suit` / `mood`），而 `columns`
            # 只列取值列（这是**最常见**的写法：固定字不是参数、不该有列）时，
            # 兜底会把这个固定字格跟「人物」配成一对 —— 于是：
            #
            #     人物@0（本该 @1）、服装@1、心情@2
            #
            # `@N` 又按"第几格"去用 → 改「人物」时改的是**语句名本身**：
            # 实测 `swim_suit lyj "泳装" mood=happy` → `lyj happy "泳装" mood=happy`
            # （动作词没了，整条语句变成一个不存在的语句）。
            # 固定字只有**名字对得上**时才认领一列（人真的给它建了列）。
            rest = [c for c in cols if id(c) not in used]
            if rest:
                hit = rest[0]                    # 顺序兜底（1:1 对位）
        if hit is not None:
            used.add(id(hit))
            name = str(hit.get('name') or '').strip()
            if name and name not in out:
                out[name] = token_index
        token_index += 1
    return out


#: **内置语句的参数形状**在 `story/spec_args.py`（唯一真源）——
#: 放那里是因为读表那一侧（`Beat.from_row`）也要用「位置参数」这回事，
#: 而 `beat_model` 是很底层的模块，不该认识"项目声明"这一套。
#: 这里只做一层转发，免得两个模块各存一份表。
def _builtin_schemas() -> dict:
    from storygraph.story.spec_args import BUILTIN_ARG_SCHEMAS
    return BUILTIN_ARG_SCHEMAS


def merge_builtin_columns(builtin, declared) -> list:
    """
    把**声明**的列合到**内置**的形状上：同名的按声明，**别的全都留着**。

    用户 2026-10-13（原话）：

    > 连 show 这种内置语句也能在卡片上长出参数格，具体做法是这样的：
    > 本来就默认存在且有的，那就保留，只改后面加进去的，加了什么，就长个什么
    > 参数，没必要全部都更新

    所以规则只有两条，**没有任何"全量更新"**：

      1. **保留**：内置形状里的每一列都在（顺序也不动）；
      2. **追加 / 就地改**：声明里同名的列**替换**那一列（于是"就是我加的那一格"
         可以一直改）；新名字的列**追加在后面**（加了什么就长什么）。

    ⚠ 为什么不能"声明在场就整个换成声明的"（那正是修之前的行为）：
    内置形状是**工具对官方语法的既有认识**（`camera` 的层名、`with` 的转场、
    `window` 的动作、`pause` 的秒数）—— 只因为你想多一个格子就把它抹掉，
    等于"加一列"顺手"删三列"，而界面不会提醒你。
    """
    out = [dict(c) for c in (builtin or ())]
    index = {str(c.get('name') or ''): i for i, c in enumerate(out)}
    for col in (declared or ()):
        if not isinstance(col, dict):
            continue
        name = str(col.get('name') or '').strip()
        if not name:
            continue
        item = dict(col)
        item['name'] = name
        if name in index:
            out[index[name]] = item
        else:
            index[name] = len(out)
            out.append(item)
    return out


def call_columns(registry, head: str) -> list:
    """
    技术调用 head 的**参数列**：项目声明优先，其次内置 `renpy.transition`。

    返回形状与 `spec_columns` 同族，多一个 `call` 元数据：
    `{'name','label','choices','required','positional','multi',
      'call': {'head','kind':'pos'|'kw','position','kw'}}`。
    """
    from storygraph.story.spec_args import BUILTIN_CALL_SCHEMAS
    name = str(head or '').strip()
    if not name:
        return []
    raw = []
    spec = registry.call_spec(name) if registry is not None else None
    if spec:
        raw = [dict(c) for c in (spec.get('columns') or ())
               if isinstance(c, dict)]
    elif name in BUILTIN_CALL_SCHEMAS:
        raw = [dict(c) for c in BUILTIN_CALL_SCHEMAS[name]]
    out = []
    for col in raw:
        param = str(col.get('name') or '').strip()
        if not param:
            continue
        kw = str(col.get('kw') or '').strip()
        position = col.get('position', col.get('positional'))
        if kw:
            position = None
        else:
            try:
                position = int(position)
            except (TypeError, ValueError):
                position = 0
        out.append({'name': param,
                    'label': str(col.get('label') or param),
                    'choices': [str(c) for c in (col.get('choices') or ())],
                    'required': bool(col.get('required')),
                    'positional': position,
                    'multi': False,
                    'choices_from': '',
                    'kind': str(col.get('kind') or 'word'),
                    # 提示 / 说明跟着列走（第七轮）：卡片参数格的灰字与 tooltip
                    'placeholder': str(col.get('placeholder') or ''),
                    'help': str(col.get('help') or ''),
                    'call': {'head': name,
                             'kind': 'kw' if kw else 'pos',
                             'position': position,
                             'kw': kw}})
    return out


def arg_columns(registry, head: str) -> list:
    """
    这个语句头的**参数列形状**：项目声明优先，其次内置表，都没有就空。

    与 `spec_columns` 的区别：那个只认**声明过**的自定义语句（契约是
    "没声明就返回空"，M4 的判据钉着）；这里多一档"标准语句由**我们**声明形状"
    （`camera` 的层名 —— 官方语法确定，不是猜）。

    ⚠ **两条路合并，不是二选一**（2026-10-13 用户要求）：内置形状里本来就有的
    列一律保留，声明里多出来的**追加**在后面（见 `merge_builtin_columns`）。
    所以"给 `camera` 再加一格"不会把它的「层名」弄丢。
    """
    declared = spec_columns(registry, head)
    from storygraph.story.spec_args import builtin_columns
    calls = call_columns(registry, head)
    if calls:
        # 技术调用是**代码**，没有"语句参数形状"可言；同名 head 也不该同时
        # 是语句（`renpy.transition` 不是语句）。所以这里直接返回调用列。
        return calls
    builtin = builtin_columns(head)
    if not declared:
        return builtin
    return merge_builtin_columns(builtin, declared)


def dynamic_spec_columns(beats, registry) -> list:
    """
    「**每参数一列**」（M4 第二阶段）：这批拍需要哪些动态列。

    返回 `[{'header': 'mycue·intensity', 'name': 'mycue', 'param': 'intensity',
            'label': ..., 'choices': [...], 'required': bool,
            'positional': int|None}]`，**保序去重**。

    口径（四条，都是刻意收窄的）：

      · 只给**这批拍里真的出现过**的语句建列：声明过的是 `Beat.spec_name`，
        标准语句是它的头行（`camera_header` / `loop_header` / `opaque` 首行）；
      · 只给**有参数形状**的语句建列：项目声明了 `columns`，或者它在
        `BUILTIN_ARG_SCHEMAS` 里。都没有 → 走「头行」那一格的候选注释
        （见 `excel_export._annotate_spec_heads`）；
      · 列名 = `语句名·参数名`（位置参数多一个 `@<下标>`，见
        `spec_args.dynamic_column_name`）—— 拼法与解析**只有那一处**；
      · `positional` 原样带出去：Excel 那侧不关心，**折回头行时要靠它**
        （位置参数不能被当成 `key=value` 去写）。
    """
    from storygraph.story.spec_args import dynamic_column_name

    heads: list = []
    for b in (beats or ()):
        for name in _beat_statement_heads(b, registry):
            if name and name not in heads:
                heads.append(name)

    out: list = []
    seen: set = set()
    for name in heads:
        for col in arg_columns(registry, name):
            clause = col.get('clause') if isinstance(col, dict) else None
            call = col.get('call') if isinstance(col, dict) else None
            header = dynamic_column_name(
                name, col['name'], col.get('positional'),
                clause=((clause or {}).get('keyword') or ''),
                call_kw=((call or {}).get('kw') or ''))
            if not header or header in seen:
                continue
            seen.add(header)
            out.append({'header': header, 'name': name, 'param': col['name'],
                        'label': col['label'], 'choices': list(col['choices']),
                        'required': col['required'],
                        # 声明里这一格的类型（`keyword` / `choice` / `number` …）：
                        # 卡片据此选控件，不再"所有格子都是输入框"。
                        'kind': str(col.get('kind') or ''),
                        'positional': col.get('positional'),
                        # "这一格能装好几个词"（2026-10-18）：卡片据此在那一格
                        # 旁边写"可写多个（空格分隔）"，Excel 侧目前不看它。
                        'multi': bool(col.get('multi')),
                        # ⚠ **把声明的格子也带出去**（2026-10-13 实测加）：
                        #   读值那一侧（`Beat.spec_arg_cells`）要靠它"按格子认领词"。
                        #   位置号那套数不准 —— `keyword` 格（固定几个字）**不占
                        #   位置号却吃掉一个词**，于是「人物/服装/神态」三格里
                        #   只对一格。每一条列都带同一份（读值只取第一份）。
                        'pattern': _declared_pattern(registry, name),
                        # 复杂子句：这一列是哪一句（没有 → 空）、这张表里
                        # 全部子句（插到没有出现过的位置时要用声明顺序）。
                        'clause': dict(clause) if clause else {},
                        'clauses': _declared_clauses(registry, name),
                        # 技术调用（2026-10-30）：这一列读/写哪个实参。
                        'call': dict(call) if call else {},
                        # 列的值类型（`transition` = 过场表达式，读值时保留引号）
                        'kind': str(col.get('kind') or ''),
                        # ⚠ **候选来自哪一类资产**（2026-10-31 补）：
                        #   从前这里把它丢了，于是 `with` 的「转场」那一格
                        #   **一个候选都没有**（`spec_arg_choices_for` 只能看
                        #   到 `choices: []`），更别说按词汇表显示中文了。
                        #   项目声明的列本来就有这个键（`arg_columns` 带出来）。
                        'choices_from': str(col.get('choices_from') or '')})
    return out


def _declared_clauses(registry, head: str) -> list:
    """这条声明里**全部**复杂子句（按声明顺序）；没有 → `[]`。"""
    try:
        spec = registry.match(head) if registry is not None else None
        return [dict(c) for c in (getattr(spec, 'clauses', None) or ())
                if isinstance(c, dict)]
    except Exception as e:                      # noqa: BLE001 - 认不出就不带
        swallow('story.statement_registry.declared_clauses', e)
        return []


def _declared_pattern(registry, head: str) -> list:
    """
    这条声明在清单里存的**有序格子**（`pattern`）—— 没有就返回 `[]`。

    为什么要有它（2026-10-13 实测修）：读值那一侧要靠"格子顺序"认领词
    （见 `spec_args.slot_value_map`），而列描述此前**不带格子** →
    卡片上「人物/服装/神态」三格只对一格（关键字格吃掉的那个词被算到隔壁）。
    认不出就返回 `[]`（调用方退回位置号，**不猜**）。
    """
    try:
        spec = registry.match(head) if registry is not None else None
        return [dict(s) for s in (getattr(spec, 'pattern', None) or ())
                if isinstance(s, dict)]
    except Exception as e:                      # noqa: BLE001 - 认不出就不带
        swallow('story.statement_registry.declared_pattern', e)
        return []


def _registered_call_heads(registry) -> list:
    """内置 + 项目声明的技术调用 head（保序去重）。"""
    from storygraph.story.spec_args import BUILTIN_CALL_SCHEMAS
    out = list(BUILTIN_CALL_SCHEMAS)
    if registry is not None:
        for spec in (getattr(registry, 'calls', None) or ()):
            head = str((spec or {}).get('head') or '').strip()
            if head and head not in out:
                out.append(head)
    return out


def _beat_statement_heads(beat, registry=None) -> list:
    """
    这一拍贡献的**语句头**（保序去重）—— 一个拍可能有多个：

      · **头行**那一个（声明的自定义语句 / 循环 / 相机）；
      · 「代码」列里那些**叶语句**（`with` / `pause` / `window`，见
        `beat_model.LEAF_ARG_HEADS`）—— 它们的参数列折的是 raw 里那一行，
        不是头行（走 `opaque` 会**换渲染通道、动顺序**）。

    `spec_name`（声明来源）优先：它才是"这条语句被谁声明过"的权威答案。
    """
    out: list = []
    name = str(getattr(beat, 'spec_name', '') or '').strip()
    if name:
        out.append(name)
    else:
        head = _beat_statement_head(beat)
        if head:
            out.append(head)
    from storygraph.story.beat_model import _leaf_heads_of
    for head in _leaf_heads_of(beat):
        if head not in out:
            out.append(head)
    # 技术调用（2026-10-30）：`$ renpy.transition(...)` / 项目包装函数。
    # **只在这一拍恰好一次时建列**（同一 head 出现两次 = 一格装不下两条，
    # 不猜改哪条；见 `call_expr` 的纪律）。
    code = str(getattr(beat, 'code', '') or '')
    if code.strip():
        from storygraph.story.call_expr import find_calls
        for head in _registered_call_heads(registry):
            if head in out or head not in code:
                continue
            if len(find_calls(code, head)) == 1:
                out.append(head)
    return out


#: `_beat_statement_head` 依次看的字段：**头行原文住在这里**。
#:
#: ⚠ `show` 是 2026-10-13 加进来的（用户："连 show 这种内置语句也能在卡片上长出
#: 参数格"）。`show` 是**被显式建模**的内置语句 —— 它的行进的是 `Beat.show`
#: （`'lan swim_suit happy'`，**不含动作名**），既不在 `opaque` 里、也没有
#: `spec_head`。所以在那之前，"你声明了 show"**永远建不出列**：
#: `_beat_statement_head` 返回空 → `arg_columns` 拿不到头 → 卡片一个格子都不长。
#: 字段名与顺序照 `Beat._head_field()` 与 `LEAF_ARG_HEADS` 的既有口径。
_HEAD_FIELDS = ('spec_head', 'loop_header', 'camera_header', 'show')


def _beat_statement_head(beat) -> str:
    """这一拍的**语句头**（从它的头行 / 整块原文里取，不含「代码」列）。"""
    from storygraph.core.vocabulary import statement_head_token
    for attr in _HEAD_FIELDS:
        text = str(getattr(beat, attr, '') or '')
        if not text.strip():
            continue
        if attr == 'show':
            # ⚠ `Beat.show` 存的是**动作名后面**那一串（见 `Beat.statement_head`），
            #   所以它的头就是字面量 `show`，不能拿内容去 `token` 化
            #   （那会把 `lan` 当成语句名）。
            #
            # ⚠⚠ **多值拍（`show a; show b`）不给头**（2026-10-13 实测修）：
            #   那种拍一格对多行，参数格怎么映射都是猜 —— 按单行去折会**静默改
            #   数据**（实测：`'lan swim_suit happy; ling uniform calm'` 改了第一格
            #   之后第二行的 `ling` 没了）。判断交给 `Beat.statement_head()`
            #   （那里是唯一真源，别在这边另写一份），空 → 这一拍不长参数格。
            if not str(beat.statement_head() or '').strip():
                return ''
            return 'show'
        return statement_head_token(text)
    first = next((ln for ln in str(getattr(beat, 'opaque', '') or '').split('\n')
                  if ln.strip()), '')
    return statement_head_token(first)


def classify_head(text: str, registry) -> str:
    """
    一句话 → 它的**被管理等级**：`''` | `'declared'` | `'host-registry'`。

    `registry` 为 None 时一律返回 `''`（工具没被告知过任何东西）——
    调用方据此退回 M1 的"未建模块"报告口径。
    """
    if registry is None:
        return ''
    from storygraph.core.vocabulary import statement_head_token
    spec = registry.match(statement_head_token(text))
    return spec.source if spec is not None else ''


def as_registered(item) -> 'RegisteredStatement | None':
    """
    `RegisteredStatement` / 它的 `to_dict()` 形态 → `RegisteredStatement`。

    与 `cds_registry.as_spec` 同一个理由：报告是**跨层传递**的
    （`EditorSession` 交 dict 给 `app.asset_report` 那个纯函数）。
    """
    if isinstance(item, RegisteredStatement):
        return item
    if isinstance(item, dict):
        try:
            return RegisteredStatement(
                name=item.get('name') or '',
                aliases=list(item.get('aliases') or []),
                block=item.get('block') or 'possible',
                source=item.get('source') or 'declared',
                origin=item.get('origin') or '',
                docs=item.get('docs') or '',
                label=item.get('label') or '',
                columns=list(item.get('columns') or []),
                render=item.get('render') or 'verbatim')
        except (TypeError, ValueError):
            return None
    return None


def _specs_of(registry) -> list:
    """注册表 / spec 列表 → `[RegisteredStatement, ...]`（去重、保序）。"""
    if isinstance(registry, StatementRegistry):
        return registry.specs()
    if registry is None:
        return []
    out, seen = [], set()
    for item in registry:
        spec = as_registered(item)
        if spec is None or not spec.name or spec.name in seen:
            continue
        seen.add(spec.name)
        out.append(spec)
    return out


def _describe_rows(counts, seen, reg, by_name, known_builtin,
                  cov) -> list:
    """
    `describe_statements` 的**报告行**（M4 从主函数搬出）。

    每条头一行：来源四档（declared / host-registry / builtin /
    shape-guess）、参数列、保真度、出现次数、原文样例、告警。
    顺手把**能力覆盖**累加进 `cov`（调用方在返回里用它）。

    ⚠ 这里的判定细节（为什么官方语句也算 builtin、为什么只对
    `shape-guess` 提示"去声明"）见行内注释 —— 一个字都没改。
    """
    rows = []
    for head, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if head in known_builtin:
            continue
        raw_kind = (seen.get(head) or {}).get('raw_kind') or ''
        cap = capability_of(head, reg, raw_kind=raw_kind)
        cov.counts[cap.level] = cov.count_of(cap.level) + cnt
        sp = by_name.get(head)
        # 来源三档 + 一档"官方/工具自己的"（2026-10-07）：
        #   · 项目声明（rules / CDS）= 主路径；
        #   · **官方语句**（`show` / `hide` / `call` …）与**工具自己建模**的
        #     （`loop` / `camera`）以及**内置形状表**（`with`/`pause`/`window`）
        #     → 'builtin'：它们不是"项目自定义结构"，**不该提示人去声明**
        #     （第一版把 `show` 也提示成"只靠猜，请写进 rules.json" —— 那是噪声）；
        #   · 剩下的才真是"只靠形状猜"。
        builtin = (head in _builtin_schemas() or head in _known_heads()
                   or head in ('loop', 'camera'))
        if sp is not None:
            source = sp.source                      # 'declared' | 'host-registry'
        elif builtin:
            source = 'builtin'
        else:
            source = 'shape-guess'
        cols = []
        for c in arg_columns(reg, head):
            cols.append({'name': c.get('param') or c.get('name') or '',
                         'label': c.get('label') or '',
                         'positional': c.get('positional'),
                         'choices': list(c.get('choices') or ()),
                         'choices_from': c.get('choices_from') or ''})
        warnings = []
        if source == 'shape-guess':
            warnings.append('只靠形状猜出来的：参数名/约束工具不知道'
                            '（要让它知道，写进 storygraph.rules.json 的 '
                            'statements，或让代码里有 renpy.register_statement）')
        elif sp is not None and not cols:
            warnings.append('已声明但没有参数形状 → 表里不会有"每参数一列"')
        rows.append({
            'name': head,
            'label': (sp.label if sp is not None else '') or '',
            'source': source,
            'origin': (sp.origin if sp is not None else ''),
            'declared': sp is not None,
            'block': bool(sp.has_block) if sp is not None else None,
            'columns': cols,
            'fidelity': cap.level,
            'fidelity_name': cap.level_name,
            'provenance': cap.provenance,
            'evidence_note': cap.evidence,
            'escape': cap.escape,
            'occurrences': cnt,
            'file': (seen.get(head) or {}).get('file') or '',
            # 原文样例（CST 真迹优先）—— 见循环里 `statement_text` 的说明。
            'sample': (seen.get(head) or {}).get('sample') or '',
            'warnings': warnings,
        })
    return rows

def describe_statements(project_root=None, statements=None, registry=None,
                        builtin_heads=None) -> dict:
    """
    **工具"以为"的自定义结构** → 可 diff 的 dict（设计文档 §2.1 第一档接口）。

    为什么先要它（`docs/archive/设计_自定义结构发现与交互_2026-10-07.md`）：
    所有"人机对齐"的前提是**工具能把自己以为的东西摊开**。没有这一份，
    人只能猜"工具到底认不认我的 `mycue`、认成什么档、凭什么"。

    输出三段：

      · `declared` / `cds` / `shape_guess`：**来源**（声明 = 主路径；
        CDS = 反射防漂移；shape_guess = 形状检测，只做分类，**不当结构真源**）；
      · `statements[]`：每个见过的头一条——名字/标签/有无块体/参数表
        （`arg_columns`）/保真档（`capability_of`）/证据（文件:行 + 出现次数）/
        告警（"只靠猜" / "声明与 CDS 不一致"）；
      · `capability`：这一份脚本流的分级计数（与 `coverage_of_statements` 同源）。

    ⚠ 它**只读**：不改任何数据、不写文件。要"调节"就改声明文件，然后重跑它。
    """
    from collections import defaultdict

    from storygraph.core.capability import Coverage
    from storygraph.core.vocabulary import statement_head_token

    reg = registry
    if reg is None and project_root:
        reg = StatementRegistry.for_project(str(project_root))
    if statements is None and project_root:
        from storygraph.parsing.discover import discover_scripts
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        pr, _extra = IncrementalRenPyAnalyzer().analyze(
            files=discover_scripts(str(project_root)), graph=None)
        statements = list(getattr(pr, 'statements', ()) or ())
    statements = list(statements or ())

    specs = _specs_of(reg)
    by_name = {}
    for sp in specs:
        for nm in sp.names:
            by_name[nm] = sp

    #: **只统计"自定义结构"那几类**（缺口 4/CST 的范围）：
    #: `raw`（工具不认识的原生语句）/ `loop` / `camera`。
    #:
    #: ⚠ 第一版把**所有**语句都算进来，于是台词也成了"语句头"
    #: （真机跑出 1634 个"头"，里面全是 `兄弟，再别说了。` 这种台词、
    #: `#` 注释、`$` 代码行）—— 那是把"工具不认识的东西"与"本来就归工具管的
    #: 东西"混成一锅。台词/舞台/代码/注释是**内置建模**的，不是自定义结构。
    cst_kinds = ('raw', 'loop', 'camera')
    seen: dict = {}
    counts: dict = defaultdict(int)
    # 每条"头"留一份**原文样例**（CST 真迹优先）—— `statement_text()` 是
    # CST 的正式消费者（从前生产零调用）：报告里给人看"工具眼里的这一行
    # 到底长什么样"，比只给一个头 token 有用得多。
    from storygraph.core.cst import statement_text
    for st in statements:
        kind = getattr(st, 'kind', '') or ''
        if kind not in cst_kinds:
            continue
        payload = getattr(st, 'payload', None)
        payload = payload if isinstance(payload, dict) else {}
        # 循环/相机没有"头行 token"（它们走自己的 kind），头就是 kind 本身。
        head = (kind if kind in ('loop', 'camera')
                else statement_head_token(payload.get('text') or ''))
        if not head:
            continue
        counts[head] += 1
        if head not in seen:
            src = getattr(st, 'source', None)
            loc = ''
            if isinstance(src, (tuple, list)) and len(src) > 1:
                loc = f'{Path(str(src[0])).name}:{src[1]}'
            raw = statement_text(st) or str(payload.get('text') or '')
            sample = next((ln.strip() for ln in raw.split('\n') if ln.strip()),
                          '')
            seen[head] = {'file': loc,
                          'raw_kind': payload.get('raw_kind') or kind,
                          'sample': sample[:120]}

    cov = Coverage()
    known_builtin = set(builtin_heads or ())
    rows = _describe_rows(counts, seen, reg, by_name, known_builtin,
                              cov)

    return {
        'project': str(project_root or ''),
        'registry': {
            'declared': [s.to_dict() for s in specs],
            'cds': [s.to_dict() for s in specs if s.source == 'host-registry'],
        },
        'statements': rows,
        'capability': dict(cov.counts),
        'summary': {
            'heads': len(rows),
            'declared_heads': sum(1 for r in rows if r['declared']),
            'builtin_heads': sum(1 for r in rows if r['source'] == 'builtin'),
            'guessed_heads': sum(1 for r in rows
                                 if r['source'] == 'shape-guess'),
            'needs_action': sum(1 for r in rows
                                if r['source'] == 'shape-guess'),
        },
    }


def spec_body_allows(registry, head: str) -> list:
    """
    某个**声明过的**自定义语句，它的块体里**允许出现哪些语句头**
    （声明 `body: {"allows": [...]}`；没声明就返回空 = **不校验**）。

    为什么这条只能是"声明"（设计文档 §0）：语法形状能看出"有块体"，
    但看不出"这个块里允许写 `say`/`show` 还是只允许 `python`" ——
    那是项目自己的约定，工具猜就是编。空列表 = 少报胜过乱报。
    """
    for sp in _specs_of(registry):
        if head in sp.names:
            return list(getattr(sp, 'body_allows', None) or ())
    return []


class _ManifestRefused(Exception):
    """`_edit_manifest` 的 `edit` 用它表示"这次拒绝写"（带人话原因）。"""

    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__(self.reason)


class _ManifestConflict(_ManifestRefused):
    """拒绝写的另一种：**两边都改过同一条**（把 id 带回去，界面要列出来）。"""

    def __init__(self, conflicts, reason: str):
        self.conflicts = list(conflicts or ())
        super().__init__(reason)


def _dump_manifest(data: dict) -> str:
    """清单 → 落盘文本（**唯一**排版口径：2 空格缩进 + 结尾一个换行）。"""
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def _edit_outcome(outcome) -> tuple:
    """
    `edit` 的返回 → `(result, changed, delete)`。

    `edit` 可以返回 2 元组 `(result, changed)`，也可以返回 3 元组
    `(result, changed, delete)` —— 最后那个 `True` = "这次的结果是**把清单
    整个删掉**"（什么都不剩时才这样；`remove_statement` / `clear_attr_groups`
    / `reset_declarations` 用）。
    """
    if not isinstance(outcome, tuple) or not 2 <= len(outcome) <= 3:
        raise TypeError('edit 要返回 (result, changed) 或 (result, changed, delete)，'
                        f'收到 {outcome!r}')
    delete = bool(outcome[2]) if len(outcome) == 3 else False
    return outcome[0], bool(outcome[1]), delete


def _edit_manifest(project_root, edit, *, timeout: float = DEFAULT_LOCK_WAIT,
                   backup: bool = False, refuse_word: str = '拒绝覆盖') -> dict:
    """
    在**跨进程锁**里：读 `storygraph.rules.json` → `edit(path, data)` → 原子写
    （或者把文件删掉）。

    `edit` 返回 `(result, changed)` / `(result, changed, delete)`；
    `changed=False` = 这次什么都没改（不写盘、不备份、不清缓存）。它也可以抛
    `_ManifestRefused(reason)` —— 那就不写，把 reason 原样带回去（"清单坏了 /
    这条本来就不在"这一类都是它）。

    `backup=True`：**真的改了**才备份，且备份的是**这次在锁里读到的那份文本**
    （不是再读一遍 —— 再读一次可能读到别人刚写的版本，撤销回去就错位了）。
    备份失败 = 整次拒绝，一个字节都不写（没有备份就不许删东西）。

    `refuse_word` 只改"坏清单"那句人话的动词：新增/覆盖是 `拒绝覆盖`，
    删除/清空是 `拒绝改动`（对用户来说那确实是两件事）。

    返回 `{'ok','path','reason','result','locked','backup'}`：

      · `locked=True` —— 等锁超时（**没写**，但这不是程序错：界面要说
        "另一个进程正在存，稍后再来"，而不是报"保存失败"让人去查）；
      · `ok=False` 且 `locked=False` —— 真写不了（坏 JSON / 权限 / 盘满）；
      · `backup` —— 这次改动之前那一份的备份路径（没备份就是空串）。

    为什么读也要在锁里（2026-11-07 第七轮）：只锁"写"等于没锁 ——
    甲读、乙读、甲写、乙写，乙写的是**甲写之前**看到的世界。读→改→写
    必须是一段，这跟 `ext.registry.save()` 是同一条理由
    （`core.filelock` 的模块文档里有那张时序图）。

    为什么删文件与备份也在这里（第八轮）：`remove_statement` 从前是
    "锁外读 → 锁外备份 → 再读一遍写回去"，读到的、备份的、被删的可以是
    **三个不同版本**；而且两个进程同时删两条时，后删的那次会把先删的
    结果覆盖回去（先删的那条复活）。现在"备份哪一份、删不删、写什么"
    全在锁里的同一份 `data` 上决定。
    """
    path = manifest_path(project_root)
    try:
        with file_lock(path, timeout=timeout):
            text = ''
            data = {}
            if path.is_file():
                text = path.read_text(encoding='utf-8')
                try:
                    data = json.loads(text or '{}')
                except Exception as e:          # noqa: BLE001 - 坏清单不覆盖
                    raise _ManifestRefused(
                        f'{MANIFEST_NAMES[0]} 不是合法 JSON，{refuse_word}（{e}）')
                if not isinstance(data, dict):
                    raise _ManifestRefused(f'清单顶层不是对象，{refuse_word}')
            result, changed, delete = _edit_outcome(edit(path, data))
            saved = ''
            if changed:
                if backup and text:
                    try:
                        saved = _backup_manifest(path.parent, path, text=text)
                    except Exception as e:      # noqa: BLE001 - 备不了就不写
                        raise _ManifestRefused(
                            f'备份失败，为安全起见没有改动：{e}')
                try:
                    if delete:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        _write_text_atomic(path, _dump_manifest(data))
                except OSError as e:
                    raise _ManifestRefused(
                        f'写不进去：{e}'
                        + (f'（备份在 {saved}）' if saved else ''))
                clear_cache()                   # 刚改完清单：装配缓存作废
            return {'ok': True, 'path': str(path), 'reason': '',
                    'result': result, 'locked': False, 'backup': saved}
    except LockTimeout as e:
        return {'ok': False, 'path': str(path), 'result': None, 'locked': True,
                'conflicts': [], 'backup': '',
                'reason': f'{MANIFEST_NAMES[0]} 由另一个进程在写（{e}），'
                          f'这次**没有保存**，请稍后再存'}
    except _ManifestConflict as e:
        return {'ok': False, 'path': str(path), 'result': None, 'locked': False,
                'conflicts': list(e.conflicts), 'backup': '', 'reason': e.reason}
    except _ManifestRefused as e:
        return {'ok': False, 'path': str(path), 'result': None, 'locked': False,
                'conflicts': [], 'backup': '', 'reason': e.reason}
    except OSError as e:
        return {'ok': False, 'path': str(path), 'result': None, 'locked': False,
                'conflicts': [], 'backup': '', 'reason': f'写不进去：{e}'}


def declare_statement(project_root, name: str, label: str = '',
                      columns=None, block=None, docs: str = '',
                      pattern=None, clauses=None) -> dict:
    """
    **把一条"教给它"的结构落成声明**（设计文档 §5 第 3 步：
    "教一次"入口 —— 人答的当场落成 `rules.json` 的 `statements`）。

    为什么落盘才算数（设计文档 §1 那条最重要的约束）：只存在内存里的
    "工具知道"不算知道 —— 程序员改代码、工具改内存，第二天就对不上。
    这个函数是**唯一的写入口**：CLI（`storygraph.cli learn`）与表/卡片
    入口都调它，所以"教一次"的结果永远是可评审、可 diff、可回滚的。

    行为（都写进判据）：

      · **同名 = 更新**，不追加第二条（教两次不会变成两份声明）；
      · 已有的其它键（`aliases` / `docs` / 顶层别的段）**原样保留**；
      · `storygraph.rules.json` **不存在就新建**；
      · ⚠ 文件存在但 **JSON 坏了 → 拒绝写**（返回 `ok=False`），
        绝不拿我们这份覆盖掉用户手写的清单（那是不可逆的破坏）；
      · `name` 为空 / 不是字符串 → 拒绝。

    返回 `{'ok': bool, 'path': str, 'action': 'added'|'updated'|'refused',
          'reason': str, 'entry': dict}`。

    ⚠ 写回用 `json.dumps(indent=2, ensure_ascii=False)` —— 也就是说
    **整份清单会被重新排版**（键顺序按原 dict 顺序保留）。值一个不改。

    2026-11-07 第七轮：读→改→写整段进了 `core.filelock` 的跨进程锁，
    写盘走 `write_text_atomic`（从前是裸 `Path.write_text`：两个人同时教
    两条语句时，后写的会把先写的**静默抹掉**，而两次都返回 `ok: True`）。
    """
    name = str(name or '').strip()
    if not name:
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': '语句名不能为空', 'entry': {}}
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': '没有项目根目录', 'entry': {}}
    entry = {'name': name}
    if label:
        entry['label'] = str(label)
    if block is not None:
        entry['block'] = bool(block)
    if docs:
        entry['docs'] = str(docs)
    cols = []
    for c in (columns or ()):
        if isinstance(c, dict) and str(c.get('name') or '').strip():
            cols.append(c)
    if cols:
        entry['columns'] = cols
    # ⭐ **位置序列**（2026-10-11）：格子有顺序、有类型 —— 那是"代码里长什么样"的
    #   唯一依据（用户撞墙记录："竖着排的三张卡凭什么对应那个顺序"）。
    #   ⚠ 只增不改：没给 `pattern` 时，清单产物与以前**逐字节一样**。
    #
    #   ⚠⚠ **`_required_set` 不许进清单**（2026-10-18 第五轮）：
    #     它只是 `pattern.parse_pattern` 用来区分"人说的 required"与"默认补的
    #     required"的内部标记（见 `with_optional_defaults`）。清单里存的是**具体的**
    #     `required` 值，读回来时"键在 = 说过的"，语义自动成立 ——
    #     把内部标记写进用户要读、要 diff、要手改的 `storygraph.rules.json`
    #     只是噪声。
    slots = [{k: v for k, v in s.items() if k != '_required_set'}
             for s in (pattern or ())
             if isinstance(s, dict) and s.get('kind')]
    if slots:
        entry['pattern'] = slots
    # ⭐ **复杂子句**（2026-10-30）：`clauses is not None` 才算"这次编辑过
    #   这一段"—— 于是 UI 能清空它（`[]`），而 CLI/MCP 不传时保留旧值
    #   （`merged.update(entry)` 不会碰没写的键）。
    if clauses is not None:
        from storygraph.story.spec_clauses import normalize_clauses
        entry['clauses'] = normalize_clauses(clauses)

    def edit(path, data):
        """锁里那一步：**这一条**的增/改（其它条目原样保留）。"""
        items = data.get(MANIFEST_KEY)
        items = items if isinstance(items, list) else []
        out, replaced, merged_entry = [], False, dict(entry)
        for it in items:
            same = (isinstance(it, dict)
                    and str(it.get('name') or '').strip() == name)
            if same and not replaced:
                merged_entry = dict(it)
                merged_entry.update(entry)
                out.append(merged_entry)
                replaced = True
                continue
            out.append(it)
        if not replaced:
            out.append(entry)
        data[MANIFEST_KEY] = out
        return ({'action': 'updated' if replaced else 'added',
                 'entry': merged_entry}, True)

    res = _edit_manifest(root, edit)
    if not res['ok']:
        got = (res.get('result') or {}).get('entry') or entry
        return {'ok': False, 'path': res['path'], 'action': 'refused',
                'reason': res['reason'], 'entry': got}
    got = res['result']
    return {'ok': True, 'path': res['path'], 'action': got['action'],
            'reason': '', 'entry': got['entry']}


def _call_body(call) -> dict:
    """
    一条调用声明里**属于文件的那部分**（去掉 `origin`）。

    `origin` 是"这条是从哪个清单读进来的"，不是声明内容。合并时必须先摘掉：
    界面那边读到的条目全带着 `origin`，而文件里新读出来的条目没有 ——
    带着它比，**每一条都会被算成"被人改过"**，于是全是假冲突（判据当场抓到）。
    """
    return {k: v for k, v in (call or {}).items() if k != 'origin'}


def declare_calls(project_root, calls, *, replace: bool = True, base=None,
                  on_conflict: str = 'abort') -> dict:
    """
    **技术调用**（`$ 项目包装函数(...)`）→ 写进 `storygraph.rules.json` 的
    `calls` 段（唯一写入口；与 `declare_statement` 同一条纪律）。

    `calls` 的形状见 `_calls_from_manifest`。`replace=True` = 这一份就是全部
    声明（UI 保存时用）；`False` = 按 head 合并（CLI 逐个装包时用）。

    **三方合并**（2026-11-07 第七轮，同一个药方见 `ext.registry.save`）：

        base   = 我装载那一刻清单里的 calls（`None` = 不知道 → 按"整份替换"）
        mine   = 这次要写的（`calls`）
        theirs = 锁里读到的、此刻清单里的

    按 `head` 逐条判（表见 `core.threeway.merge_by_key`）。`base=None` 时
    `base := theirs`，于是退化成本轮之前的行为（"这一份就是全部"）——
    **老调用方一个字不用改**，而界面那条路（`save_calls`）会传 `base`。

    返回 `{'ok','path','count','reason','conflicts','deleted','locked'}`。
    坏 JSON / 顶层不是对象 → 拒绝写，绝不覆盖用户手写的清单。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'count': 0, 'conflicts': [],
                'deleted': [], 'reason': '没有项目根目录'}
    clean = _calls_from_manifest(calls)

    def edit(path, data):
        theirs_list = _calls_from_manifest(data.get(CALLS_KEY))
        theirs = {str(c['head']): _call_body(c) for c in theirs_list}
        mine = {str(c['head']): _call_body(c) for c in clean}
        if not replace:
            merged = dict(theirs)
            merged.update(mine)                 # 逐个装包：同名覆盖，其余留着
            data[CALLS_KEY] = list(merged.values())
            return ({'conflicts': [], 'deleted': []}, True)
        had = theirs if base is None else {
            str(c['head']): _call_body(c)
            for c in _calls_from_manifest(base)}
        picks, conflicts, _absorbed = _merge_by_key(had, mine, theirs,
                                                    on_conflict)
        if conflicts and on_conflict == 'abort':
            raise _ManifestConflict(
                conflicts,
                f'{MANIFEST_NAMES[0]} 里这些调用也有人改过：'
                + '、'.join(conflicts[:4])
                + ('…' if len(conflicts) > 4 else '') + '（这次没有保存）')
        order = [*theirs, *(h for h in mine if h not in theirs)]
        data[CALLS_KEY] = [picks[h] for h in order if picks.get(h) is not None]
        deleted = [h for h in had if h not in mine and picks.get(h) is None]
        return ({'conflicts': list(conflicts), 'deleted': deleted}, True)

    res = _edit_manifest(root, edit)
    if not res['ok']:
        return {'ok': False, 'path': res['path'], 'count': 0,
                'conflicts': list(res.get('conflicts') or ()),
                'deleted': [], 'locked': res['locked'],
                'reason': res['reason']}
    got = res['result']
    return {'ok': True, 'path': res['path'], 'count': len(clean),
            'conflicts': got['conflicts'], 'deleted': sorted(got['deleted']),
            'reason': ''}


def call_kw_ok(text) -> bool:
    """`layer` 这样的**关键字参数名**合不合法（唯一一份规则：`_IDENT_RE`）。

    公开它（2026-11-07 第四轮）是为了让"参数编辑窗口"和"清单读取"用同一条
    规则：界面报"这个名字不能当关键字"，存盘时那边就真的会跳过它 ——
    两边各写一遍正则，迟早出现"界面说行、存进去没了"。
    """
    return _ident_ok(text)


#: 一条调用声明**归谁管**（`call_owner` 的返回值）→ 界面上的说法。
#:
#: 为什么要按"归谁"分流：同一个 head 可以被 `storygraph.ext.json` 与
#: `storygraph.rules.json` 各声明一次，而装配顺序是**扩展优先**
#: （`for_project` 里 `ext_calls` 盖掉同名条目）。于是写错文件 = 写的这份
#: 永远读不到 —— 界面还回一句"已保存"，这就是最坏的一种"能骗人"。
CALL_OWNER_LABELS = {
    'rules': '规则文件',
    'ext': '扩展',
    'plugin': '插件',
    'builtin': '工具自带',
}


def call_owner(origin) -> str:
    """
    调用声明的 `origin` → 管辖区（`'rules'` / `'ext'` / `'plugin'` / `'builtin'`）。

    没有 `origin`（老数据、或手写的 rules.json）→ `'rules'`：在 2026-11-07
    之前，调用声明只有 rules.json 这一条路，所以这是唯一安全的默认。
    """
    text = str(origin or '').strip()
    if not text:
        return 'rules'
    if text.startswith('plugin:'):
        return 'plugin'
    if text == 'builtin':
        return 'builtin'
    # 清单可能在项目根或 `game/` 下（`manifest_path`），所以按**文件名**认。
    base = text.replace('\\', '/').rsplit('/', 1)[-1]
    if base == EXT_MANIFEST or text == 'project':
        return 'ext'
    return 'rules'


#: 语句声明的来处用**同一套判据**：`origin` 只看文件名，与"调用还是语句"无关。
#:
#: `host-registry`（脚本里的 `renpy.register_statement`）落在 `rules`：写进
#: `rules.json` 会**覆盖** CDS —— 那正是"教工具认出脚本里已经注册的写法"该
#: 有的行为（`declared` 盖 `host`）。只有扩展 / 插件 / 内置那三种写了会被盖住。
statement_owner = call_owner


def _split_call_owners(calls) -> tuple:
    """
    一整份调用表 → `(能写进规则文件的, 被挡住的)`（纯函数）。

    `origin` 是"来处"，**不是**声明内容：可写的那份先把它摘掉，
    否则它会被原样写进 `rules.json`（读回来又被当成参数里的 origin）。
    """
    writable, blocked = [], []
    for call in (calls or ()):
        if not isinstance(call, dict):
            continue
        head = str(call.get('head') or '').strip()
        if not head:
            continue
        origin = str(call.get('origin') or '')
        owner = call_owner(origin)
        if owner != 'rules':
            blocked.append({'head': head, 'owner': owner, 'origin': origin})
            continue
        writable.append({k: v for k, v in call.items() if k != 'origin'})
    return writable, blocked


def _call_heads_in_manifest(project_root) -> set:
    """规则文件里现在有哪些调用 head（读不出来 / 没有文件 → 空集合）。"""
    import json as _json
    path = manifest_path(project_root)
    if not path.is_file():
        return set()
    try:
        data = _json.loads(path.read_text(encoding='utf-8') or '{}')
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(c.get('head') or '') for c in
            _calls_from_manifest(data.get(CALLS_KEY))}


def save_calls(project_root, calls, *, base=None,
               on_conflict: str = 'abort') -> dict:
    """
    界面上**一整份**调用表 → 落到**该落的文件**里（2026-11-07 第四轮）。

    为什么不能直接 `declare_calls`（探针 `.pip-tmp/probe_call_owner.py`）：

        项目里扩展声明了 my_wipe（方向 / 秒数）
          → 在看板上改名字、点保存 → 写进 rules.json（`ok: True`，"已保存 1 条"）
          → 重新装配：认到的**还是扩展那份**（改了个寂寞）
          → 再把扩展那条关掉：rules.json 里那份**跟着消失**（按 head 摘掉）

    所以这里按 `origin` 分流，并且**不假装**：

      · `rules`   → 写 `storygraph.rules.json`（`declare_calls` 那一份）；
      · `ext`     → **不写**：声明住在 `storygraph.ext.json`，写规则文件会被
                    它盖住。返回里说清"去扩展窗口改同一条"；
      · `plugin`  → **不写**：插件文件是输入（可能还是别人的），就地改会被
                    下次加载冲掉；
      · `builtin` → **不写**：形状是工具内置的（引擎 API）。

    `replace` 语义保留（这一份就是规则文件里的全部）：于是"把被扩展盖住的
    旧条目顺手清掉"是自动的 —— 那些行不在可写集合里，写盘时就不见了。

    **三方合并**（2026-11-07 第七轮）：界面打开时读到的调用表要作为 `base`
    传进来。否则"我打开窗口 → 别人加了 wipe → 我存一次"会把 wipe 整条删掉
    （`replace=True` 的语义就是"这一份是全部"），而界面上只会说"已保存"。
    传了 `base` 之后：别人加的我没见过的照留；我删掉的、别人也动过的 →
    **停下来报冲突**（不写）。

    返回 `{'ok','path','count','written','deleted','blocked','reason','locked'}`：

      · `written`  —— 真写进规则文件的 head；
      · `deleted`  —— 从规则文件里**清掉**的旧 head（被扩展 / 插件接管、
                      或我在界面里删掉的）；
      · `blocked`  —— 这次**没写**的 `[{'head','owner','origin'}]`（界面照着说话）。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'count': 0, 'written': [],
                'deleted': [], 'blocked': [],
                'reason': '没有项目根目录'}
    writable, blocked = _split_call_owners(calls)
    old_heads = _call_heads_in_manifest(root)
    # 没有任何可写的、规则文件里也没有 calls → 不建一个空文件（写盘也要有理由）
    if not writable and not old_heads:
        return {'ok': True, 'path': str(manifest_path(root)), 'count': 0,
                'written': [], 'deleted': [], 'blocked': blocked,
                'reason': ''}
    res = declare_calls(root, writable, replace=True, base=base,
                        on_conflict=on_conflict)
    if not res.get('ok'):
        return {'ok': False, 'path': str(res.get('path') or ''), 'count': 0,
                'written': [], 'deleted': [], 'blocked': blocked,
                'conflicts': list(res.get('conflicts') or ()),
                'locked': bool(res.get('locked')),
                'reason': str(res.get('reason') or '')}
    return {'ok': True, 'path': str(res.get('path') or ''),
            'count': int(res.get('count') or 0),
            'written': [str(c.get('head') or '') for c in writable],
            'deleted': list(res.get('deleted') or []), 'blocked': blocked,
            'reason': ''}


def is_known_head(head: str, registry=None) -> bool:
    """
    工具**认不认识**这个语句头（内置 ∪ 已声明 ∪ 宿主 CDS）。

    为什么单开一个公开函数（2026-11-07）：界面上要决定"这一行值不值得
    教一句"——右键菜单不能对一个 `show` 说"教工具认识它"，那是骂人。
    判据必须和 `discover_undeclared` **同一份**（它用的是 `_is_builtin_head`
    + `reg.match`），否则会出现"列表里不显示、右键却说可以教"。
    """
    name = str(head or '').strip().rstrip(':')
    if not name:
        return True
    if _is_builtin_head(name):
        return True
    try:
        return bool(registry is not None and registry.match(name) is not None)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _is_builtin_head(head: str) -> bool:
    """
    这个语句头是**工具已经会**的吗（不该叫用户去声明）。

    三类都算"会了"（用户 2026-10-10 截图里 `with`(39) / `window`(9) / `pause`
    全出现在"待声明"里，把这条闸门补齐）：

      ① Ren'Py 内置语句头（词表 `RENPY_KNOWN_HEADS`：`show/scene/with/play/…`）；
      ② 流程块头（`if/menu/jump/…`，同上表里也有，但显式写清楚）；
      ③ **工具自己声明过参数**的叶语句（`beat_model.LEAF_ARG_HEADS` =
         `with/pause/window`）—— 它们连参数列都早就有了，更不该叫用户声明。
    """
    head = str(head or '').strip()
    if not head:
        return True
    try:
        from storygraph.core.vocabulary import (RENPY_CONTAINER_HEADS,
                                                RENPY_KNOWN_HEADS)
        from storygraph.story.beat_model import LEAF_ARG_HEADS
    except Exception as e:                       # noqa: BLE001 - 判不出来就当不认识
        swallow('story.statement_registry._is_builtin_head', e)
        return False
    return head in RENPY_KNOWN_HEADS or head in RENPY_CONTAINER_HEADS \
        or head in LEAF_ARG_HEADS


def _character_names(project_root) -> tuple:
    """`(角色 ID 集合, 显示名集合)`（读不出就两个空集）。"""
    ids, names = set(), set()
    try:
        # 从 `assets.registry_load` 直接取（不是 `pipeline.build` 的门面）：
        # 后者是**上层**，`story` 调它就是反向边、也是大环上的一条环比。
        from storygraph.assets.registry_load import load_asset_registry
        reg = load_asset_registry(project_root)
        for cid, row in (getattr(reg, 'characters', {}) or {}).items():
            ids.add(str(cid))
            if str(cid).lower() not in ('narrator',):
                names.add(str(cid))
            for key in ('Name', 'DisplayName', '显示名', '名字'):
                shown = str((row or {}).get(key) or '').strip()
                if shown:
                    names.add(shown.strip('「」『』'))
    except Exception as e:                       # noqa: BLE001 - 没有角色表也能用
        swallow('story.statement_registry._character_names', e)
    return ids, names


def _looks_like_dialogue(head: str, text: str, roles=()) -> str:
    """
    这行是不是**说话人+台词**（被解析器当成了裸语句）？

    真项目实测（2026-10-10）：`l oh "3"` —— `l` 是角色表里「蓝」的 ID，
    这一行就是台词"3"配表情 `oh`。所以判据**先问角色表**，
    认出来了就**明确说这是台词**（不是"疑似写错"）。

    ⚠ 我第一版只按"名字 ≤2 字符 + 行里有引号"判，于是把 `l oh "3"`
    误诊成"说话人写错了" —— 骂错人比不提示更糟。现在分两层：

      · 命中角色表 → 返回"这是台词"（**确定**，不是猜测）；
      · 没命中、但名字极短且行里有引号 → 返回"像是台词/写法不对"（**存疑**）。
    """
    head = str(head or '').strip()
    text = str(text or '')
    if not head:
        return ''
    ids, names = roles if roles else (set(), set())
    if head in ids or head in names or head.lower() in {i.lower() for i in ids}:
        if '"' in text or "'" in text:
            return (f'这一行是**台词**（"{head}" 是角色表里的角色），'
                    f'不是动作；工具本该把它认成对白')
    if len(head) > 2 or not text:
        return ''
    if '"' not in text and "'" not in text:
        return ''
    return (f'这一行**像是台词/写法不对**（名字 "{head}" 太短、行里有引号'
            f'台词），不像一个动作 —— 也可能是角色表里没有这个角色')


def declared_builtins(registry) -> list:
    """
    清单里**声明了、但它其实自带**的语句 → `[{'name','label','reason'}]`。

    为什么要有：真项目根目录下真有一份被 v1 界面误导着写出来的
    `{"name": "pause", ...}` —— `pause` 是 Ren'Py 内置语句（`pause 1.0`），
    **根本不需要声明**。工具既然教错了，就得能把它指出来（并可一键移除）。
    只读。
    """
    out = []
    for spec in _specs_of(registry):
        if not _is_builtin_head(spec.name):
            continue
        out.append({
            'name': spec.name,
            'label': spec.label or '',
            'reason': f'"{spec.name}" 是 Ren\'Py 自带的语句（工具本来就认识），'
                      f'不用声明；声明了也不会更聪明，只会让清单变难读',
        })
    return out


def remove_statement(project_root, name: str) -> dict:
    """
    **把一条声明从清单里去掉**（先备份，可回退）。

    为什么需要它：v1 界面把人教错了（让用户声明了内置的 `pause`），
    那就要给一条体面的退路 —— 不能只让人手改 JSON。

    契约（都与 `declare_statement` 同源）：
      · **先备份**到 `<项目根>/storygraph.rules.backup/<名字>.<时间戳>.json`，
        内容是**改之前**的整份清单（用户手写的东西不许不可逆地消失）；
      · 去掉之后 `statements` 为空、**且没有别的段** → 删掉整个清单文件
        （回退到"项目没声明"这个干净状态）；
      · 还有别的段（`attr_groups` / 用户自己的键）→ 保留文件，只清那一条；
      · 清单坏了 / 名字不在里面 → **如实说**，不猜、不改。

    返回 `{'ok','path','removed','deleted_file','backup','reason'}`。

    2026-11-07 第八轮：整个"读 → 判断 → 备份 → 写/删"进了 `_edit_manifest`
    的锁。从前的顺序是**锁外读一次、备份时再读一次、写时用第一次那份** ——
    两次读之间别人写进来的东西，备份里有、删掉的却没有，撤销回去对不上；
    两个进程同时删两条时还会互相覆盖（先删的那条复活）。现在备份、删不删、
    写什么，全在锁里的**同一份** `data` 上决定。
    """
    name = str(name or '').strip()
    if not name:
        return {'ok': False, 'path': '', 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': '语句名不能为空'}
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'removed': False,
                'deleted_file': False, 'backup': '', 'reason': '没有项目根目录'}

    def edit(path, data):
        """锁里那一步：删这一条，或者连文件一起删（其它段为空时）。"""
        if not path.is_file():
            raise _ManifestRefused(f'没有 {MANIFEST_NAMES[0]}')
        items = data.get(MANIFEST_KEY)
        items = items if isinstance(items, list) else []
        kept = [it for it in items
                if not (isinstance(it, dict)
                        and str(it.get('name') or '').strip() == name)]
        if len(kept) == len(items):
            return ({'removed': False, 'deleted_file': False,
                     'reason': f'清单里本来就没有 "{name}"'}, False)
        rest = {k: v for k, v in data.items() if k != MANIFEST_KEY}
        if not kept and all(not v for v in rest.values()):
            return ({'removed': True, 'deleted_file': True, 'reason': ''},
                    True, True)                # ← 第三条：整个文件删掉
        data[MANIFEST_KEY] = kept
        return ({'removed': True, 'deleted_file': False, 'reason': ''}, True)

    res = _edit_manifest(root, edit, backup=True, refuse_word='拒绝改动')
    if not res['ok']:
        return {'ok': False, 'path': res['path'], 'removed': False,
                'deleted_file': False, 'backup': res.get('backup') or '',
                'reason': res['reason']}
    body = res['result'] or {}
    return {'ok': True, 'path': res['path'],
            'removed': bool(body.get('removed')),
            'deleted_file': bool(body.get('deleted_file')),
            'backup': res.get('backup') or '',
            'reason': str(body.get('reason') or '')}


def discover_undeclared(project_root, *, limit_files: int = 400,
                        samples_limit: int = 8,
                        include_suspect: bool = False,
                        only_suspect: bool = False) -> list:
    """
    **从项目脚本里找出"工具还不认识的写法"** —— 用户要的那个起点
    （2026-10-10："我并没有找到一个入口可以教这个工具学会我自定义的项目模块"）。

    为什么需要它：`declare_statement()` 是**写**入口，可"该声明哪些名字"以前
    只能靠人自己知道（或去 `describe` 里翻）。这个函数把项目扫一遍，
    把**未建模语句的头**列出来，于是界面可以做成一屏"发现 → 勾选 → 教它"。

    返回 `[{'head','count','samples':[(文件, 行号, 原文)],'block','suspect',
    'files':[文件名…]}]`，**按出现次数降序**（最常写的排最前）。

    ⚠ **闸门**（用户 2026-10-10 截图评审后补的，之前没有 → 把 `with`/`window`/
    `pause` 这些 Ren'Py 内置语句当成"工具还不认识"，甚至教用户写了一份
    `pause` 声明）：

      1. **工具已经会的**（`_is_builtin_head`：词表已知 ∪ 容器头 ∪
         `with/pause/window` 这类工具自己声明过参数的叶语句）→ **不列**；
      2. 语句头必须是合法标识符（首字符不能是数字/符号）；
      3. **疑似写错的行**（`l oh "3"`：名字 ≤2 字符 **且** 行里有引号台词）
         → 默认不列；`include_suspect=True` 时列出并带 `suspect` 原因
         （它们走"疑似写错"那一栏，**不给人"声明"这个动作**）；
      4. 已经在清单 / CDS 里声明过的 → 不算"还不认识"。

    每条都带 `files`（来源文件清单）—— 真机里 `with` 那种高计数大多来自
    Ren'Py 模板/界面文件（`screens.rpy`），要让人一眼看出"这是模板里的"。

    ⚠ 判据**照抄 `coverage_of_statements` 的口径**（不另立一套）：
    `raw_kind` 在 `Statement.payload` 里、不是 beat 属性
    （我第一版按 beat 属性取 → 恒为空 → 一个候选都发现不了，实测踩到）；
    `loop`/`camera` 没有 `raw_kind`，要显式认（它们不是"不认识"）。

    只读：不写盘、不改内存。
    """
    root = Path(project_root) if project_root else None
    if root is None or not root.is_dir():
        return []
    try:
        from storygraph.parsing.discover import discover_scripts
        files = list(discover_scripts(root))[:limit_files]
    except Exception as e:                      # noqa: BLE001
        swallow('statement_registry.discover_undeclared.discover', e)
        return []
    if not files:
        return []
    try:
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        pr, _graph = IncrementalRenPyAnalyzer().analyze(files=files, graph=None)
    except Exception as e:                      # noqa: BLE001
        swallow('statement_registry.discover_undeclared.parse', e)
        return []
    from storygraph.core.vocabulary import statement_head_token
    reg = registry_for_parse_result(pr)
    # 角色表：判"这行其实是台词"要用它（否则会把 `l oh "3"` 误诊成写错）
    roles = _character_names(root)
    found: dict = {}
    for st in (getattr(pr, 'statements', None) or ()):
        payload = getattr(st, 'payload', None)
        payload = payload if isinstance(payload, dict) else {}
        raw_kind = payload.get('raw_kind') or ''
        if getattr(st, 'kind', '') in ('loop', 'camera'):
            raw_kind = getattr(st, 'kind')      # 与 coverage_of_statements 同源
        if raw_kind not in UNMODELED_KINDS:
            continue
        text = str(payload.get('text') or '')
        head = statement_head_token(text)
        if not head or (reg is not None and reg.match(head) is not None):
            continue
        # 闸门①（用户 2026-10-10）：**工具已经会的语句头一律不列** ——
        # `with`/`window`/`pause` 全是 Ren'Py 内置，`with/pause/window` 更是
        # 工具自己声明过参数的叶语句。把它们列出来会把人教错（真项目里就有
        # 一份被误导写出来的 `pause` 声明）。
        if _is_builtin_head(head):
            continue
        # 名字必须像个标识符（首字符不能是数字/符号）。`$ renpy.notify(...)`
        # 这类会被切出 `$` 之类的东西，不是"作者自定义的写法"。
        #
        # ⚠ 2026-11-07：从前这里是 `^[A-Za-z_][A-Za-z0-9_]*$`（**纯 ASCII**）——
        #   于是**中文项目的自定义语句永远发现不了**：`泳装切换 小明 泳装 开心`
        #   是一条合法的 CDS 用法（`renpy.register_statement("泳装切换")`），
        #   模板/界面文件里也常见，但它连"待认领"都进不去，用户看到的是
        #   "这个模块根本不认识我的写法"。
        #   `[^\W\d_]` = **任意 Unicode 字母**（吃中文/日文，不吃数字/下划线），
        #   与 `learn_statement._STMT_RE` 同一口径（那边 2026-10-14 就修了，
        #   这里当时漏了 —— 两处判据不一致本身就是缺陷）。
        if not re.match(r'^[^\W\d_]\w*$', head):
            continue
        suspect = _looks_like_dialogue(head, text, roles)
        if suspect and not include_suspect:
            continue
        if only_suspect and not suspect:
            continue
        rec = found.setdefault(head, {'head': head, 'count': 0, 'samples': [],
                                      'block': False, 'suspect': '',
                                      'reason': '', 'files': []})
        rec['count'] += 1
        if raw_kind in ('unknown_block', 'known_block'):
            rec['block'] = True
        if suspect and not rec['suspect']:
            # `suspect` 与 `reason` 同源：前者给判据（真值），后者给人看（原话）
            rec['suspect'] = suspect
            rec['reason'] = suspect
        # ⚠ `Statement.source` 是 **(路径, 行号)** 元组，不是纯路径 ——
        # 直接 `Path(str(where)).name` 会得到 `ch01.rpy', 2)` 这种垃圾
        # （判据当场抓到）。这里统一拆开：文件名进 `files`，行号进样例。
        where = getattr(st, 'source', '') or ''
        if isinstance(where, (tuple, list)):
            src_file, src_line = (list(where) + ['', ''])[:2]
        else:
            src_file, src_line = where, ''
        name = Path(str(src_file)).name if src_file else ''
        if name and name not in rec['files']:
            rec['files'].append(name)
        if len(rec['samples']) < samples_limit:
            line = f'{name}:{src_line}' if src_line else name
            rec['samples'].append((line, text.split('\n')[0][:80]))
    return sorted(found.values(), key=lambda r: (-r['count'], r['head']))


def declare_attr_groups(project_root, groups, *, replace: bool = False,
                        clear: bool = False) -> dict:
    """
    **把"词组"写进清单**（`storygraph.rules.json` 的 `attr_groups`）。

    用户 2026-10-10：

    > 实际上需要分组显示……但因为我这个要求通用！所以一般人其实是没有服装这个
    > 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西分开！

    所以词组和语句声明走**同一份清单、同一个写入口风格**：
      · 同名 = 更新（教两次不会变成两组）；
      · 其它键（`statements` / 用户自己加的）**原样保留**；
      · 清单坏了 → **拒绝写**（绝不覆盖用户手写的东西）；
      · `groups` 传空列表 = 拒绝（要清空请用 `clear_attr_groups`）。

    `replace=True`（2026-10-18，界面"保存候选分栏"用）：**整段替换** ——
    编辑器里删掉的那一栏真的会从清单消失（默认的 merge 语义做不到删除）。
    replace 会**先备份**。

    ⚠⚠ `replace=True` + 空列表 **默认不清空**：必须再显式传 `clear=True`。
    这是 2026-10-18 审到的事故（主 OK 勾了"把分栏一起写进清单"、而当前没有
    可写分栏时，把项目已有的分组整段删了）—— 清空是破坏性动作，只能由
    明确的按钮/参数发起。

    返回 `{'ok','path','action','reason','count'}`。

    2026-11-07 第八轮：读→改→写整段进了 `_edit_manifest` 的锁。`replace`
    的备份从"再读一遍文件"改成"备份刚才读到的那一份" —— 两次读之间
    别人写进来的东西，备份里有、被整段替换掉的却没有，那就不是"改之前"。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': '没有项目根目录', 'count': 0}
    clean = []
    for g in (groups or ()):
        item = g if isinstance(g, dict) else {}
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        entry = {'name': name}
        if str(item.get('label') or '').strip():
            entry['label'] = str(item['label']).strip()
        if item.get('split'):
            entry['split'] = True
        opts = [str(o).strip() for o in (item.get('options') or ())
                if str(o).strip()]
        if opts:
            entry['options'] = opts
        clean.append(entry)
    if not clean:
        if replace and clear:
            res = clear_attr_groups(project_root)
            if res.get('ok') and not res.get('removed'):
                return {'ok': True, 'path': res.get('path', ''),
                        'action': 'unchanged', 'reason': '', 'count': 0}
            res = dict(res)
            res['action'] = 'cleared'
            return res
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': ('一个词组都没填。要清空已有分栏请用'
                           '「清空候选分栏」（replace 空列表不会清空）'),
                'count': 0}

    def edit(path, data):
        """锁里那一步：整段替换，或者按名字合并（其它段/其它键原样保留）。"""
        if replace:
            # 整段替换时，**同名条目里用户手写的未知字段原样保留**（`when` /
            # 备注 / 以后新增的键）—— 只换编辑器认识的那三样（name/label/options）。
            old_entries = data.get(ATTR_GROUPS_KEY)
            old_by_name = {}
            if isinstance(old_entries, list):
                for it in old_entries:
                    if isinstance(it, dict) and str(it.get('name') or '').strip():
                        old_by_name[str(it['name']).strip()] = it
            merged = []
            for entry in clean:
                old = old_by_name.get(entry['name'], {})
                extra = {k: v for k, v in old.items()
                         if k not in ('name', 'label', 'options')}
                extra.update(entry)
                merged.append(extra)
            data[ATTR_GROUPS_KEY] = merged
            return {'action': 'replaced', 'count': len(merged)}, True

        existing = data.get(ATTR_GROUPS_KEY)
        if not isinstance(existing, list):
            existing = []
        by_name = {}
        order = []
        for it in existing:                     # 保留原有顺序与其它字段
            if not isinstance(it, dict) or not str(it.get('name') or '').strip():
                continue
            n = str(it['name']).strip()
            if n in by_name:
                continue
            by_name[n] = dict(it)
            order.append(n)
        action = 'updated' if by_name else 'added'
        for entry in clean:                     # 同名 = 合并更新
            n = entry['name']
            if n in by_name:
                merged = dict(by_name[n])
                merged.update(entry)
                by_name[n] = merged
                action = 'updated'
            else:
                by_name[n] = entry
                order.append(n)
        data[ATTR_GROUPS_KEY] = [by_name[n] for n in order]
        return {'action': action, 'count': len(data[ATTR_GROUPS_KEY])}, True

    res = _edit_manifest(root, edit, backup=replace)
    if not res['ok']:
        return {'ok': False, 'path': res['path'], 'action': 'refused',
                'reason': res['reason'], 'count': 0}
    body = res['result'] or {}
    return {'ok': True, 'path': res['path'],
            'action': str(body.get('action') or ''), 'reason': '',
            'count': int(body.get('count') or 0)}


def _backup_manifest(root: Path, path: Path, text: str | None = None) -> str:
    """
    把清单**改之前**的整份内容备份到 `storygraph.rules.backup/`。

    `text` 给了就备份它（`_edit_manifest` 在锁里读到的那一份）—— 不要再读
    一遍：两次读之间别人写了新版本的话，备份里是新的、被改掉的是旧的，
    "回退"回去就等于把别人的改动也一起回退了。
    """
    from datetime import datetime

    bdir = root / 'storygraph.rules.backup'
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    bpath = bdir / f'{path.stem}.{stamp}.json'
    blob = text if text is not None else path.read_text(encoding='utf-8')
    bpath.write_text(blob, encoding='utf-8')
    return str(bpath)


def clear_attr_groups(project_root) -> dict:
    """
    **把「候选分栏」整段清掉**（先备份，可回退）。

    为什么需要它（用户 2026-10-18）：
    "你给了个 rules.json 建立，却又不给删除重置" —— 分栏页可以把每一栏删掉，
    但写回时 `declare_attr_groups([])` 会拒绝，于是**删不干净**。这个入口是
    "清空分组"（不是"删掉整个清单"：`statements` / `host_hooks` 等其它段原样
    保留）。

    返回 `{'ok','path','removed','deleted_file','backup','count','reason'}`。

    2026-11-07 第八轮：读 → 判断 → 备份 → 写/删全在 `_edit_manifest` 的锁里。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'removed': False,
                'deleted_file': False, 'backup': '', 'count': 0,
                'reason': '没有项目根目录'}

    def edit(path, data):
        """锁里那一步：清掉 `attr_groups`，别的段为空就整份删掉。"""
        if not path.is_file():
            raise _ManifestRefused(f'没有 {MANIFEST_NAMES[0]}（本来就没有分组）')
        old = data.get(ATTR_GROUPS_KEY)
        if not isinstance(old, list) or not old:
            return ({'removed': False, 'deleted_file': False, 'count': 0,
                     'reason': '清单里本来就没有 attr_groups'}, False)
        rest = {k: v for k, v in data.items() if k != ATTR_GROUPS_KEY}
        if all(not v for v in rest.values()):
            return ({'removed': True, 'deleted_file': True,
                     'count': len(old), 'reason': ''}, True, True)
        data.clear()
        data.update(rest)
        return ({'removed': True, 'deleted_file': False,
                 'count': len(old), 'reason': ''}, True)

    res = _edit_manifest(root, edit, backup=True, refuse_word='拒绝改动')
    if not res['ok']:
        return {'ok': False, 'path': res['path'], 'removed': False,
                'deleted_file': False, 'backup': res.get('backup') or '',
                'count': 0, 'reason': res['reason']}
    body = res['result'] or {}
    return {'ok': True, 'path': res['path'],
            'removed': bool(body.get('removed')),
            'deleted_file': bool(body.get('deleted_file')),
            'backup': res.get('backup') or '',
            'count': int(body.get('count') or 0),
            'reason': str(body.get('reason') or '')}


def reset_declarations(project_root) -> dict:
    """
    **重置项目清单里的自定义声明**（`statements` + `attr_groups` 两段）。

    ⚠ 不是删整个文件：`host_hooks.trust` 或用户自己加的键**原样保留**；
    只有"删完什么都不剩"时才删文件（回到"项目没声明"的干净状态）。
    改之前整份备份到 `storygraph.rules.backup/`。

    返回 `{'ok','path','statements','groups','deleted_file','backup','reason'}`。

    2026-11-07 第八轮：读 → 判断 → 备份 → 写/删全在 `_edit_manifest` 的锁里。
    """
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'statements': 0, 'groups': 0,
                'deleted_file': False, 'backup': '',
                'reason': '没有项目根目录'}

    def edit(path, data):
        """锁里那一步：两段都清掉，别的键为空就整份删掉。"""
        if not path.is_file():
            raise _ManifestRefused(f'没有 {MANIFEST_NAMES[0]}')
        st = data.get(MANIFEST_KEY)
        gr = data.get(ATTR_GROUPS_KEY)
        n_st = len(st) if isinstance(st, list) else 0
        n_gr = len(gr) if isinstance(gr, list) else 0
        if not n_st and not n_gr:
            return ({'statements': 0, 'groups': 0, 'deleted_file': False,
                     'reason': '清单里没有 statements / attr_groups'}, False)
        rest = {k: v for k, v in data.items()
                if k not in (MANIFEST_KEY, ATTR_GROUPS_KEY)}
        if all(not v for v in rest.values()):
            return ({'statements': n_st, 'groups': n_gr,
                     'deleted_file': True, 'reason': ''}, True, True)
        data.clear()
        data.update(rest)
        return ({'statements': n_st, 'groups': n_gr,
                 'deleted_file': False, 'reason': ''}, True)

    res = _edit_manifest(root, edit, backup=True, refuse_word='拒绝改动')
    if not res['ok']:
        return {'ok': False, 'path': res['path'], 'statements': 0, 'groups': 0,
                'deleted_file': False, 'backup': res.get('backup') or '',
                'reason': res['reason']}
    body = res['result'] or {}
    return {'ok': True, 'path': res['path'],
            'statements': int(body.get('statements') or 0),
            'groups': int(body.get('groups') or 0),
            'deleted_file': bool(body.get('deleted_file')),
            'backup': res.get('backup') or '',
            'reason': str(body.get('reason') or '')}


def format_registry_report(registry) -> str:
    """注册表（或 spec 列表）→ 人话（体检报告 / 导出结论用）。结论在第一条。"""
    specs = _specs_of(registry)
    groups = list(getattr(registry, 'attr_groups', ()) or ())
    if not specs:
        if groups:
            lines = [f'项目没有语句声明；候选分栏（attr_groups）有 '
                     f'{len(groups)} 栏：']
            for g in groups:
                words = (f'{len(g.options)} 个词' if g.options else '兜底栏')
                lines.append(f'  · {g.title()}（{g.name}）：{words}')
            return '\n'.join(lines)
        return ('项目**没有声明**任何自定义语句：既没有 `renpy.register_statement`'
                '（CDS），`storygraph.rules.json` 里也没有 `statements` 段。'
                '自定义结构若想被工具管理，从这两处任选一处写下来即可。')
    lines = [f'项目声明了 {len(specs)} 个自定义语句（工具据此把它们当 T1 通用行处理）：']
    for s in specs:
        how = '清单声明' if s.source == 'declared' else '宿主注册'
        extra = [how, '带块' if s.has_block else '不带块']
        if s.aliases:
            extra.append('别名 ' + '/'.join(s.aliases))
        if s.docs:
            extra.append(s.docs)
        where = f'  {s.origin}' if s.origin else ''
        lines.append(f'  · {s.name}（{"；".join(extra)}）{where}')
    if groups:
        lines.append('')
        lines.append(f'候选分栏（attr_groups）：{len(groups)} 栏')
        for g in groups:
            words = (f'{len(g.options)} 个词' if g.options else '兜底栏')
            lines.append(f'  · {g.title()}（{g.name}）：{words}')
    return '\n'.join(lines)


__all__ = ['ATTR_GROUPS_KEY', 'CALL_OWNER_LABELS', 'MANIFEST_KEY',
           'RegisteredStatement',
           'StatementRegistry',
           'UNMODELED_KINDS', 'annotate_spec_statements', 'arg_columns',
           'as_registered',
           'call_kw_ok', 'call_owner',
           'capability_of', 'classify_head', 'clear_attr_groups', 'clear_cache',
           'coverage_of_beats', 'coverage_of_statements',
           'declare_attr_groups', 'declare_calls', 'declare_statement',
           'declared_builtins',
           'describe_statements',
           'discover_undeclared',
           'dynamic_spec_columns', 'format_capability_summary',
           'format_registry_report', 'manifest_path',
           'registry_for_parse_result',
           'remove_statement', 'reset_declarations', 'root_of',
           'save_calls',
           'spec_arg_choices', 'spec_body_allows', 'spec_columns',
           'statement_owner']
