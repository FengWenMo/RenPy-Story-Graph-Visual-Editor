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

from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 清单里声明自定义语句的键名（`storygraph.rules.json` 的顶层键）
MANIFEST_KEY = 'statements'

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

    def __init__(self, declared=(), host=()):
        self.declared = list(declared)
        self.host = list(host)
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
        declared, host = [], []
        try:
            from storygraph.story.project_conventions import load_manifest
            data, path, _warnings = load_manifest(project_root)
            if isinstance(data, dict):
                declared = _from_manifest(data, path)
        except Exception as e:                # noqa: BLE001
            swallow('story.statement_registry.for_project.manifest', e)
        host = _from_cds(project_root)
        reg = cls(declared=declared, host=host)
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
    """这条声明的**列描述**（M4）：`[{name, label, choices, required}]`。"""
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
                    'required': bool(col.get('required'))})
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
        name = str(getattr(b, 'spec_name', '') or '').strip()
        if not name:
            # 标准语句：头行藏在哪一位由**渲染层那套优先级**决定
            # （`spec_head` → `loop_header` → `camera_header` → `opaque` 首行）。
            name = _beat_statement_head(b)
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


def _beat_statement_head(beat) -> str:
    """这一拍的**语句头**（标准语句那条路：从它的头行/整块原文里取）。"""
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


__all__ = ['MANIFEST_KEY', 'RegisteredStatement',
           'StatementRegistry',
           'UNMODELED_KINDS', 'annotate_spec_statements', 'arg_columns',
           'as_registered',
           'capability_of', 'classify_head', 'clear_cache',
           'coverage_of_beats', 'coverage_of_statements',
           'dynamic_spec_columns',
           'format_capability_summary',
           'format_registry_report', 'registry_for_parse_result', 'root_of',
           'spec_arg_choices', 'spec_columns']
