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

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 清单里声明自定义语句的键名（`storygraph.rules.json` 的顶层键）
#: `storygraph.rules.json` 里放"语句声明"列表的键
MANIFEST_KEY = 'statements'
#: 清单里放**词组声明**的键（"服装 / 表情"这类分栏；见 `authoring.spec_groups`）
ATTR_GROUPS_KEY = 'attr_groups'


#: 清单文件名。与 `project_conventions.MANIFEST_NAMES` 是同一份约定
#: （这里再写一遍是为了不让 `story` 内部互相 import；两处都有测试钉着）。
MANIFEST_NAMES = ('storygraph.rules.json',)


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
    render: str = 'verbatim'

    @property
    def names(self) -> list:
        return list(dict.fromkeys([self.name, *(self.aliases or [])]))

    @property
    def has_block(self) -> bool:
        return self.block in ('yes', 'possible', True)

    def to_dict(self) -> dict:
        return {'name': self.name, 'aliases': list(self.aliases),
                'block': self.block, 'source': self.source,
                'origin': self.origin, 'docs': self.docs,
                'label': self.label, 'columns': list(self.columns),
                'body_allows': list(self.body_allows),
                'render': self.render}


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
            origin=Path(manifest_path).name if manifest_path else MANIFEST_KEY,
            docs=str(item.get('docs') or ''),
            label=str(item.get('label') or ''),
            columns=[c for c in (item.get('columns') or ())
                     if isinstance(c, dict)],
            # `body: {"allows": ["say","show",…]}` —— 结构级约束（②层）：
            # 语法形状猜不出"这个块里允许写什么"，只能被声明。
            body_allows=[str(x) for x in (
                (item.get('body') or {}).get('allows') or ())
                if str(x).strip()],
            render=str(item.get('render') or 'verbatim')))
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
    except Exception as e:                    # noqa: BLE001 - 读不出来不该炸
        swallow('story.statement_registry._from_cds', e)
    return out


class StatementRegistry:
    """
    项目的自定义语句查表。`declared`（清单）**覆盖** `host`（CDS）。

    用法：

        reg = StatementRegistry.for_project(project_root)
        hit = reg.match('mycue')      # → RegisteredStatement | None
    """

    def __init__(self, declared=(), host=(), attr_groups=()):
        self.declared = list(declared)
        self.host = list(host)
        #: 项目声明的**词组**（`attr_groups`）—— 用于把候选分栏显示
        #: （"服装 / 表情"这类）。见 `authoring.spec_groups`。
        #: 空 = 项目没声明 → 调用方走原来的平铺候选，行为一字不变。
        self.attr_groups = list(attr_groups)
        self._by_name = {}
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
        用户改了 `storygraph.rules.json` 后清一下即可（`clear_cache()`，
        `EditorSession` 打开项目时也会清）。
        """
        key = str(project_root or '')
        if not key:
            return cls()
        if use_cache:
            hit = _CACHE.get(key)
            if hit is not None:
                return hit
        declared, host, groups = [], [], []
        try:
            from storygraph.story.project_conventions import load_manifest
            from storygraph.authoring.spec_groups import parse_attr_groups
            data, path, _warnings = load_manifest(project_root)
            if isinstance(data, dict):
                declared = _from_manifest(data, path)
                # 词组分栏：项目自己声明"有哪几组词"（`attr_groups`），
                # 工具不写死"服装/表情"（用户 2026-10-10 的通用性要求）
                groups = parse_attr_groups(data.get(ATTR_GROUPS_KEY))
        except Exception as e:                # noqa: BLE001
            swallow('story.statement_registry.for_project.manifest', e)
        host = _from_cds(project_root)
        reg = cls(declared=declared, host=host, attr_groups=groups)
        if use_cache:
            _CACHE[key] = reg
        return reg


#: 项目根 → `StatementRegistry`（见 `for_project` 的说明）
_CACHE: dict = {}


def clear_cache() -> None:
    """丢掉装配缓存（测试、以及"用户刚改完 rules.json"时用）。"""
    _CACHE.clear()


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
    chain = [p.parent, *p.parents]
    for parent in chain:
        if parent.name == 'game':
            return str(parent.parent)
    for parent in chain:
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
        except Exception:                     # noqa: BLE001
            _KNOWN_HEADS = frozenset()
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


def spec_columns(registry, head: str) -> list:
    """
    这条声明的**列描述**（M4）：
    `[{name, label, choices, required, positional, choices_from}]`。

    `choices_from`（可选）：候选来自**资源表的某一类**（`'transform'` /
    `'image'` / …），而不是内联的一串值 —— `with` 这种语句的候选就是项目里
    真实存在的变换，让 Excel 直接绑到那张候选表上（自伸缩、没有 255 字符上限，
    也不会因为项目加了一个变换就过时）。
    """
    spec = registry.match(head) if registry else None
    if spec is None:
        return []
    out = []
    for col in (spec.columns or ()):
        if not isinstance(col, dict):
            continue
        name = str(col.get('name') or '').strip()
        if not name:
            continue
        out.append({'name': name,
                    'label': str(col.get('label') or ''),
                    'choices': [str(c) for c in (col.get('choices') or ())],
                    'required': bool(col.get('required')),
                    'positional': col.get('positional'),
                    'choices_from': str(col.get('choices_from') or '')})
    return out


#: **内置语句的参数形状**在 `story/spec_args.py`（唯一真源）——
#: 放那里是因为读表那一侧（`Beat.from_row`）也要用「位置参数」这回事，
#: 而 `beat_model` 是很底层的模块，不该认识"项目声明"这一套。
#: 这里只做一层转发，免得两个模块各存一份表。
def _builtin_schemas() -> dict:
    from storygraph.story.spec_args import BUILTIN_ARG_SCHEMAS
    return BUILTIN_ARG_SCHEMAS


def arg_columns(registry, head: str) -> list:
    """
    这个语句头的**参数列形状**：项目声明优先，其次内置表，都没有就空。

    与 `spec_columns` 的区别：那个只认**声明过**的自定义语句（契约是
    "没声明就返回空"，M4 的判据钉着）；这里多一档"标准语句由**我们**声明形状"
    （`camera` 的层名 —— 官方语法确定，不是猜）。
    """
    cols = spec_columns(registry, head)
    if cols:
        return cols
    from storygraph.story.spec_args import builtin_columns
    return builtin_columns(head)


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
        for name in _beat_statement_heads(b):
            if name and name not in heads:
                heads.append(name)

    out: list = []
    seen: set = set()
    for name in heads:
        for col in arg_columns(registry, name):
            header = dynamic_column_name(name, col['name'],
                                         col.get('positional'))
            if not header or header in seen:
                continue
            seen.add(header)
            out.append({'header': header, 'name': name, 'param': col['name'],
                        'label': col['label'], 'choices': list(col['choices']),
                        'required': col['required'],
                        'positional': col.get('positional')})
    return out


def _beat_statement_heads(beat) -> list:
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
    return out


def _beat_statement_head(beat) -> str:
    """这一拍的**语句头**（从它的头行 / 整块原文里取，不含「代码」列）。"""
    from storygraph.core.vocabulary import statement_head_token
    for attr in ('spec_head', 'loop_header', 'camera_header'):
        text = str(getattr(beat, attr, '') or '')
        if text.strip():
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

    为什么先要它（`docs/设计_自定义结构发现与交互_2026-10-07.md`）：
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


def declare_statement(project_root, name: str, label: str = '',
                      columns=None, block=None, docs: str = '') -> dict:
    """
    **把一条"教给它"的结构落成声明**（设计文档 §5 第 3 步：
    "教一次"入口 —— 人答的当场落成 `rules.json` 的 `statements`）。

    为什么落盘才算数（设计文档 §1 那条最重要的约束）：只存在内存里的
    "工具知道"不算知道 —— 程序员改代码、工具改内存，第二天就对不上。
    这个函数是**唯一的写入口**：CLI（`storygraph.cli learn`）与将来的
    表/卡片入口都调它，所以"教一次"的结果永远是可评审、可 diff、可回滚的。

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
    """
    import json

    from storygraph.core.safeguard import swallow as _swallow  # noqa: F401

    name = str(name or '').strip()
    if not name:
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': '语句名不能为空', 'entry': {}}
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': '没有项目根目录', 'entry': {}}
    path = root / MANIFEST_NAMES[0]

    data = {}
    action = 'added'
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding='utf-8') or '{}')
        except Exception as e:                  # noqa: BLE001 - 坏清单不覆盖
            return {'ok': False, 'path': str(path), 'action': 'refused',
                    'reason': f'storygraph.rules.json 不是合法 JSON，'
                              f'拒绝覆盖（{e}）', 'entry': {}}
        if not isinstance(data, dict):
            return {'ok': False, 'path': str(path), 'action': 'refused',
                    'reason': '清单顶层不是对象，拒绝覆盖', 'entry': {}}

    items = data.get(MANIFEST_KEY)
    if not isinstance(items, list):
        items = []
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

    out = []
    replaced = False
    for it in items:
        same = (isinstance(it, dict) and str(it.get('name') or '').strip() == name)
        if same and not replaced:
            merged = dict(it)
            merged.update(entry)
            out.append(merged)
            entry = merged
            replaced = True
            action = 'updated'
            continue
        out.append(it)
    if not replaced:
        out.append(entry)

    data[MANIFEST_KEY] = out
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    except Exception as e:                      # noqa: BLE001 - 写不动就如实说
        return {'ok': False, 'path': str(path), 'action': 'refused',
                'reason': f'写不进去：{e}', 'entry': entry}
    clear_cache()                               # 刚改完清单：装配缓存作废
    return {'ok': True, 'path': str(path), 'action': action,
            'reason': '', 'entry': entry}


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
        from storygraph.pipeline.build import load_asset_registry
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
    """
    import json
    from datetime import datetime

    name = str(name or '').strip()
    if not name:
        return {'ok': False, 'path': '', 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': '语句名不能为空'}
    root = Path(project_root) if project_root else None
    if root is None:
        return {'ok': False, 'path': '', 'removed': False,
                'deleted_file': False, 'backup': '', 'reason': '没有项目根目录'}
    path = root / MANIFEST_NAMES[0]
    if not path.is_file():
        return {'ok': False, 'path': str(path), 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': f'没有 {MANIFEST_NAMES[0]}'}
    try:
        data = json.loads(path.read_text(encoding='utf-8') or '{}')
    except Exception as e:                      # noqa: BLE001 - 坏清单不覆盖
        return {'ok': False, 'path': str(path), 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': f'{MANIFEST_NAMES[0]} 不是合法 JSON，拒绝改动（{e}）'}
    if not isinstance(data, dict):
        return {'ok': False, 'path': str(path), 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': '清单顶层不是对象，拒绝改动'}

    items = data.get(MANIFEST_KEY)
    items = items if isinstance(items, list) else []
    kept = [it for it in items
            if not (isinstance(it, dict)
                    and str(it.get('name') or '').strip() == name)]
    if len(kept) == len(items):
        return {'ok': True, 'path': str(path), 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': f'清单里本来就没有 "{name}"'}

    # ① 先备份（改之前的内容）
    backup = ''
    try:
        bdir = root / 'storygraph.rules.backup'
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        bpath = bdir / f'{MANIFEST_NAMES[0].replace(".json", "")}.{stamp}.json'
        bpath.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        backup = str(bpath)
    except Exception as e:                      # noqa: BLE001 - 备不了就不删
        return {'ok': False, 'path': str(path), 'removed': False,
                'deleted_file': False, 'backup': '',
                'reason': f'备份失败，为安全起见没有改动：{e}'}

    # ② 还有别的段 → 保留文件；什么都没了 → 删文件
    rest = {k: v for k, v in data.items() if k != MANIFEST_KEY}
    empty_rest = all(not v for v in rest.values())
    try:
        if not kept and empty_rest:
            path.unlink()
            deleted = True
        else:
            data[MANIFEST_KEY] = kept
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8')
            deleted = False
    except Exception as e:                      # noqa: BLE001 - 写不动就如实说
        return {'ok': False, 'path': str(path), 'removed': False,
                'deleted_file': False, 'backup': backup,
                'reason': f'写不进去：{e}（备份在 {backup}）'}
    clear_cache()
    return {'ok': True, 'path': str(path), 'removed': True,
            'deleted_file': deleted, 'backup': backup, 'reason': ''}


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
        # 名字必须像个标识符（首字符不能是数字）。`$ renpy.notify(...)` 这类
        # 会被切出 `$` 之类的东西，不是"作者自定义的写法"。
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', head):
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


def declare_attr_groups(project_root, groups) -> dict:
    """
    **把"词组"写进清单**（`storygraph.rules.json` 的 `attr_groups`）。

    用户 2026-10-10：

    > 实际上需要分组显示……但因为我这个要求通用！所以一般人其实是没有服装这个
    > 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西分开！

    所以词组和语句声明走**同一份清单、同一个写入口风格**：
      · 同名 = 更新（教两次不会变成两组）；
      · 其它键（`statements` / 用户自己加的）**原样保留**；
      · 清单坏了 → **拒绝写**（绝不覆盖用户手写的东西）；
      · `groups` 传空列表 = **清掉**该段（把分组关掉，回到平铺）。

    返回 `{'ok','path','action','reason','count'}`。
    """
    import json

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
        opts = [str(o).strip() for o in (item.get('options') or ())
                if str(o).strip()]
        if opts:
            entry['options'] = opts
        clean.append(entry)
    if not clean:
        return {'ok': False, 'path': '', 'action': 'refused',
                'reason': '一个词组都没填（要清掉分组请用"清空分组"）',
                'count': 0}

    path = root / MANIFEST_NAMES[0]
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding='utf-8') or '{}')
        except Exception as e:                  # noqa: BLE001 - 坏清单不覆盖
            return {'ok': False, 'path': str(path), 'action': 'refused',
                    'reason': f'storygraph.rules.json 不是合法 JSON，'
                              f'拒绝覆盖（{e}）', 'count': 0}
        if not isinstance(data, dict):
            return {'ok': False, 'path': str(path), 'action': 'refused',
                    'reason': '清单顶层不是对象，拒绝覆盖', 'count': 0}

    existing = data.get(ATTR_GROUPS_KEY)
    if not isinstance(existing, list):
        existing = []
    by_name = {}
    order = []
    for it in existing:                         # 保留原有顺序与其它字段
        if not isinstance(it, dict) or not str(it.get('name') or '').strip():
            continue
        n = str(it['name']).strip()
        if n in by_name:
            continue
        by_name[n] = dict(it)
        order.append(n)
    action = 'updated' if by_name else 'added'
    for entry in clean:                         # 同名 = 合并更新
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    except Exception as e:                      # noqa: BLE001 - 写不动就如实说
        return {'ok': False, 'path': str(path), 'action': 'refused',
                'reason': f'写不进去：{e}', 'count': 0}
    clear_cache()                               # 刚改完清单：装配缓存作废
    return {'ok': True, 'path': str(path), 'action': action, 'reason': '',
            'count': len(data[ATTR_GROUPS_KEY])}


def format_registry_report(registry) -> str:
    """注册表（或 spec 列表）→ 人话（体检报告 / 导出结论用）。结论在第一条。"""
    specs = _specs_of(registry)
    if not specs:
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
    return '\n'.join(lines)


__all__ = ['ATTR_GROUPS_KEY', 'MANIFEST_KEY', 'RegisteredStatement',
           'StatementRegistry',
           'UNMODELED_KINDS', 'annotate_spec_statements', 'arg_columns',
           'as_registered',
           'capability_of', 'classify_head', 'clear_cache',
           'coverage_of_beats', 'coverage_of_statements',
           'declare_attr_groups', 'declare_statement', 'declared_builtins',
           'describe_statements',
           'discover_undeclared',
           'dynamic_spec_columns',
           'format_capability_summary',
           'format_registry_report', 'registry_for_parse_result',
           'remove_statement', 'root_of',
           'spec_arg_choices', 'spec_body_allows', 'spec_columns']
