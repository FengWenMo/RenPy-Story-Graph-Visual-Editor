"""
`storygraph.story.project_conventions`
=====================================
**让工具适配项目自己的约定**（用户 2026-10-05 的核心要求）：

> 若是程序自定义了一个结构，而编剧用这个编辑器编辑时发现：你没有适应到它的
> 自定义结构怎么办？他会立即退出，因为工具没有用！……但很多时候结构是有人
> 自定义的，工具必须能够适应！

设计文档：`docs/设计_合成立绘合成与姿势切换_2026-10-05.md` 第 11 节。

三层机制：

1. **认形状**（能猜就猜）：`detect_conventions()` —— 只看结构形状，
   不认变量名/角色名（合成立绘、姿势循环表、路径基准…）。
2. **让它自己声明**（猜不到就写下来）：项目根或 `game/` 下的
   `storygraph.rules.json`，**声明覆盖自动识别**。
3. **不认识就明说**（`unknown_statements()`）：扫出工具**不认识的语句头** +
   次数 + 例子，写进体检报告。语义是承诺："这些我原样保留，但不替你编辑" ——
   而不是假装懂然后写坏。

这一层的目的不是"支持某一种自定义结构"，而是**把自定义结构变成可教的东西**：
编剧/程序不用改代码，写个清单就能让工具跟上。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.core.vocabulary import (RENPY_CONTAINER_HEADS,
                                        RENPY_KNOWN_HEADS)

#: 清单文件名（两处都找：项目根、`game/` 下）
MANIFEST_NAMES = ('storygraph.rules.json',)

#: 工具**认识**的语句头（用来算"覆盖度"）。宁少不宁多：
#: 少写几个 → 报告里多几条"我不认识"（诚实）；多写 → 假装懂（危险）。
#:
#: ⚠ **它是 `core.vocabulary.RENPY_KNOWN_HEADS` 的别名**（M5 收敛，2026-10-07）：
#: 从前这一份与 `beat_render._RENPY_STATEMENT_HEADS`、下面的 `_CONTAINER_HEADS`
#: 各写一遍，加一种语句要在三处补，迟早在某处漏。现在词表只有一份
#: （放 `core`：`parsing` 不许 import `story`，而这几份声明层与语法层都要用）。
KNOWN_STATEMENT_HEADS = RENPY_KNOWN_HEADS

_RE_STMT_HEAD = re.compile(r'^(\s*)(?:show|hide|scene|play|menu|label|'
                           r'layeredimage|transform|image|screen|style|'
                           r'translate|if|elif|while|for|python|init|define|'
                           r'default|call|jump|return|with|voice|extend|'
                           r'\$\s*)?([A-Za-z_][\w.]*)\b(.*)$')
_RE_TOKEN = re.compile(r'[A-Za-z_][\w.]*')


@dataclass
class Conventions:
    """
    一个项目的"约定集合"：自动识别 + 清单声明的结果。

    `source` 记每个键是**声明**来的还是**认**出来的（报告里要区分：
    "这是你告诉我的" vs "我猜的"）。`enabled_auto_fixes` 是目前**允许自动写**
    的修正（默认空 = 什么都不自动改）。
    """

    poses: dict = field(default_factory=dict)
    pose_labels: dict = field(default_factory=dict)
    path_bases: list = field(default_factory=list)
    auto_fixes: set = field(default_factory=set)
    manifest_path: str = ''
    notes: str = ''
    source: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def pose_options(self, tag: str) -> list:
        return list(self.poses.get(str(tag or '')) or [])

    def hide_before_pose_switch(self) -> bool:
        """允许"改回空姿势时自动补 `hide`"吗（默认**不**允许）。"""
        return 'hide_before_pose_switch' in self.auto_fixes

    def to_dict(self) -> dict:
        return {'poses': self.poses, 'pose_labels': self.pose_labels,
                'path_bases': self.path_bases,
                'auto_fixes': sorted(self.auto_fixes),
                'manifest': self.manifest_path, 'source': self.source,
                'warnings': self.warnings}


# ---------------------------------------------------------------------------
#  第二层：清单
# ---------------------------------------------------------------------------

def find_manifest(project_root) -> object:
    """项目根 / `game/` 下的 `storygraph.rules.json`（没有返回 None）。"""
    root = Path(project_root)
    for base in (root, root / 'game'):
        for name in MANIFEST_NAMES:
            cand = base / name
            try:
                if cand.is_file():
                    return cand
            except OSError as e:                # noqa: BLE001
                swallow('story.project_conventions.find_manifest', e)
    return None


def load_manifest(project_root) -> tuple:
    """
    读清单 → `(data, path, warnings)`。

    坏 JSON **不抛异常**（工具不该因为一个手写错的文件就打不开项目），
    而是记一条警告并当作"没有清单"。
    """
    path = find_manifest(project_root)
    warnings = []
    if path is None:
        return {}, '', warnings
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('顶层必须是对象')
        return data, str(path), warnings
    except Exception as e:                      # noqa: BLE001 - 手写文件容错
        swallow('story.project_conventions.load_manifest', e)
        warnings.append(f'{path.name} 读不出来（{e}）—— 已忽略，'
                        f'按自动识别走')
        return {}, str(path), warnings


# ---------------------------------------------------------------------------
#  第三层：覆盖度（哪些语句头我不认识）
# ---------------------------------------------------------------------------

#: 这些"头"其实是剧本内容，不是结构声明 —— 不算"不认识"。
_CONTENT_HEADS = frozenset({'extend', 'extend_'})


#: 这些头**开一个"工具不编辑的块"**：块里的语句（screen language、
#: 合成立绘层、transform 的 ATL、init python 的代码…）一律**原样保留**，
#: 不算"不认识的结构" —— 否则覆盖度报告会被 `add` / `text` / `vbox`
#: 这类界面语句淹没（实测 203 种里绝大多数是这种噪声）。
#: ⚠ **别名**，同 `KNOWN_STATEMENT_HEADS`：唯一真源在 `core.vocabulary`。
_CONTAINER_HEADS = RENPY_CONTAINER_HEADS


def _declared_heads(project_root) -> set:
    """
    项目**声明过**的语句头（`storygraph.rules.json` + `renpy.register_statement`）。

    为什么要排除它们（2026-10-08）：`unknown_statements` 原来只认内置词表，
    于是**用户已经声明过的**自定义语句照样被报成"工具不认识" —— 报告自己
    打自己脸（`tests/test_build_report_undeclared.py` 抓到的）。声明过就是
    认识，"不认识"必须排除它们。

    走 `StatementRegistry.for_project`（带缓存，一次拿到清单+CDS 两来源）；
    用**延迟 import** 避开与 `statement_registry` 的循环依赖。
    """
    try:
        from storygraph.story.statement_registry import StatementRegistry
        return set(StatementRegistry.for_project(project_root).names())
    except Exception as e:                          # noqa: BLE001 - 读不出来就少排除
        swallow('story.project_conventions._declared_heads', e)
        return set()


def unknown_statements(project_root, limit_examples: int = 3) -> dict:
    """
    扫全项目 `.rpy`，列出**工具不认识的语句头**：`{头: {count, examples}}`。

    只统计**剧情层**的语句：`screen` / `layeredimage` / `transform` /
    `init python` 这些"块"里的内容属于工具的"第三类内容"通道
    （**原样保留、不编辑**），不算"不认识"。

    这样报告回答的才是编剧真正关心的问题：
    "**我剧本里用的哪一种结构，工具管不了？**"
    """
    root = Path(project_root)
    game = root / 'game' if (root / 'game').is_dir() else root
    declared = _declared_heads(project_root)        # 声明过的 = 认识，不算
    out = {}
    for f in sorted(game.rglob('*.rpy')):
        try:
            lines = f.read_text(encoding='utf-8', errors='ignore').splitlines()
        except OSError as e:                    # noqa: BLE001
            swallow('story.project_conventions.unknown_statements', e)
            continue
        skip_indent = None
        depth = 0                               # 上一条语句还没闭合的括号层数
        for ln in lines:
            if depth > 0:
                depth = _bracket_depth(depth, ln)   # 续行：属于上一条语句
                continue
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            ind = _indent(ln)
            if skip_indent is not None:
                if ind > skip_indent:
                    continue                        # 还在"不编辑的块"里
                skip_indent = None
            head = _statement_head(s)
            if head in _CONTAINER_HEADS:
                skip_indent = ind                   # 从这里开始整块跳过
                continue
            # 记下本行留下的未闭合括号 —— 后面的续行不能当成新的语句头。
            # 真实项目实测：`show screen prompt("…", act=[` 的下一行
            # `Call(...)` 曾被误报成"工具不认识的语句"。
            depth = _bracket_depth(0, ln)
            if not head:
                continue
            if (head in KNOWN_STATEMENT_HEADS or head in _CONTENT_HEADS
                    or head in declared):
                continue
            if head[0].isdigit():
                continue
            bucket = out.setdefault(head, {'count': 0, 'examples': []})
            bucket['count'] += 1
            if len(bucket['examples']) < limit_examples:
                bucket['examples'].append(f'{f.name}: {s[:70]}')
    return dict(sorted(out.items(), key=lambda kv: -kv[1]['count']))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


#: 一层括号组（不含嵌套）——`(no_skip=True)`、`(1, 2)` 之类。
_PAREN_GROUP_RE = re.compile(r'\([^()]*\)')


def _strip_paren_groups(s: str) -> str:
    """把 `(...)` 整段挖掉（反复挖到没有为止），用于"只看顶层有没有 `=`"。"""
    prev = None
    out = s
    while out != prev:
        prev = out
        out = _PAREN_GROUP_RE.sub(' ', out)
    return out


def _bracket_depth(depth: int, line: str) -> int:
    """
    本行结束后**未闭合的 `([{` 层数**（跳过字符串与 `#` 注释）。

    用途：多行语句的续行（`show screen prompt("…", act=[` 后面的
    `Call(...)`）**不是新语句头** —— 不认这一点，会把续行里的 Python 调用
    误报成"工具不认识的语句"（真实项目实测 2 条假阳性）。
    """
    quote = ''
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = ''
            i += 1
            continue
        if ch in '"\'':
            quote = ch
        elif ch == '#':
            break
        elif ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth = max(0, depth - 1)
        i += 1
    return depth


def _statement_head(text: str) -> str:
    """
    这一行"像不像一条语句"，是的话返回语句头。

    判据：以已知关键字/`$` 开头；或是 `标识符 ... :`（块语句）；
    或 `标识符(...)` 调用。台词（`"…"`）与中文行直接不算。
    """
    if text.startswith(('"', "'", '「', '（', '(', '[', '{')):
        return ''
    m = re.match(r'^([A-Za-z_][\w.]*)\s*(\([^)]*\))?\s*:?\s*(.*)$', text)
    if not m:
        return ''
    head, call, tail = m.group(1), m.group(2), m.group(3)
    if call is not None:
        return head                       # `foo(...)` 调用式语句
    if text.rstrip().endswith(':') and tail.strip() == '':
        return head                       # 块语句 `foo ...:`
    if head in KNOWN_STATEMENT_HEADS:
        return head
    # 未知的"块语句"（`自定义结构 ...:`）才是我们关心的
    if text.rstrip().endswith(':'):
        return head
    # 未知的"关键字语句"（`mycustomcue 'Thunder' intensity=3`）：
    # 判据是**带具名参数** —— 但 `=` 必须出现在**顶层**（括号之外）。
    # ⚠ 台词可以带 say 参数：`jc "台词" (no_skip=True)` —— 那个 `=` 在
    # `(...)` 里，挖掉括号组后再找，就不会把台词误报成语句头
    # （真实项目实测的 2 条假阳性之一）。
    #
    # ⚠⚠ 2026-10-11 **地狱项目抓到的真缺陷**：这里的参数名原来只认 ASCII
    #   （`\b[A-Za-z_][\w]*\s*=`），于是**中文参数名**的写法被整个丢弃 ——
    #   实测 `weather rain 强度=3` → `_statement_head` 返回 `''` →
    #   `unknown_statements()` 报 **0 种**，而项目里明明有未知写法。
    #   **静默漏报**比误报危险得多（工具会说"你的写法我都认识"）。
    #   中文项目里 `强度=3` 这种参数名太常见了，所以标识符必须认 Unicode 词字符。
    if re.search(r'(?<![\w])\w+\s*=', _strip_paren_groups(tail or '')):
        return head
    return ''


# ---------------------------------------------------------------------------
#  第一层 + 组装
# ---------------------------------------------------------------------------

def detect_conventions(project_root, layer_model=None) -> Conventions:
    """
    自动识别 + 清单声明 → `Conventions`（**声明覆盖识别**）。

    `layer_model` 给了就复用它（省一次全项目扫描）。
    """
    conv = Conventions()
    data, path, warns = load_manifest(project_root)
    conv.manifest_path = path
    conv.warnings.extend(warns)

    # —— 自动识别 ——
    model = layer_model
    if model is None:
        try:
            from storygraph.story.layered_model import load_layer_model
            model = load_layer_model(project_root)
        except Exception as e:                  # noqa: BLE001
            swallow('story.project_conventions.detect_conventions', e)
            model = None
    if model is not None:
        conv.poses = {k: list(v) for k, v in (model.poses or {}).items()}
        conv.source.update({k: 'detected' for k in conv.poses})
        labels = {}
        for _name, table in (model.pose_labels or {}).items():
            labels.update(table or {})
        conv.pose_labels = labels
    # 没有循环表时，从 layeredimage 自己推（`when` 条件里的姿势属性）
    if model is not None:
        for tag in (model.images or {}):
            if tag not in conv.poses:
                opts = model.pose_options(tag)
                if opts:
                    conv.poses[tag] = list(opts)
                    conv.source[tag] = 'derived'

    # —— 清单声明（覆盖） ——
    if isinstance(data.get('poses'), dict):
        for tag, vals in data['poses'].items():
            if isinstance(vals, list):
                conv.poses[str(tag)] = [str(v) for v in vals]
                conv.source[str(tag)] = 'declared'
    if isinstance(data.get('pose_labels'), dict):
        conv.pose_labels = {str(k): str(v)
                            for k, v in data['pose_labels'].items()}
    if isinstance(data.get('path_bases'), list):
        conv.path_bases = [str(v) for v in data['path_bases']]
    for key in ('hide_before_pose_switch',):
        if data.get(key) is True:
            conv.auto_fixes.add(key)
    if isinstance(data.get('notes'), str):
        conv.notes = data['notes']
    # 清单里可以显式关掉自动识别（"别猜我的姿势表"）
    if data.get('detect_poses') is False:
        conv.poses = {k: v for k, v in conv.poses.items()
                      if conv.source.get(k) == 'declared'}
    return conv


def conventions_for(project_root, layer_model=None) -> Conventions:
    """带缓存的入口（同一个项目根只建一次）。"""
    key = str(project_root or '')
    if not key:
        return Conventions()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    conv = detect_conventions(key, layer_model=layer_model)
    _CACHE[key] = conv
    return conv


_CACHE: dict = {}


def clear_cache():
    """测试用：清掉缓存。"""
    _CACHE.clear()


__all__ = ['Conventions', 'MANIFEST_NAMES', 'KNOWN_STATEMENT_HEADS',
           'find_manifest', 'load_manifest', 'unknown_statements',
           'detect_conventions', 'conventions_for', 'clear_cache']
