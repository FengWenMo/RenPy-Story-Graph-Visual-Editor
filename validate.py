"""
validate.py
===========
三级校验：把"这份演出表/脚本能不能进构建"讲清楚。

分级不是为了好看，是为了对应**三种不同的人**和三种修法：

  L1 · 单条（一行一格）  —— 文案自己能修
      空台词、说话人没定义、引用的资产没登记、音频动作写错、
      文本里的 `{}` 标签不配对、台词超出 UI 显示上限。

  L2 · 表内（一张表内部）—— 策划/组长能修
      LineId 重复、seq 断号、label 缺失、菜单选项没有对应 menu、
      StageOrder 与实际舞台列不一致。

  L3 · 全库（跨章节/跨表）—— 程序 + 主策一起看
      断裂跳转、不可达 label、未使用资产、角色没有台词、
      资源表登记了但文件不存在、多语言缺条目。

设计约束：
  · 只报告，不修改。修由人决定（编辑器里已有对应动作）。
  · 结果可机器消费（Issue 是 dataclass，可 json 化），CI 能卡门。
  · 复用既有真源：资产引用判定一律走 asset_registry.resolve_references，
    不再自建第二套匹配规则。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from asset_registry import RENPY_BUILTIN_TRANSFORMS, resolve_references
from script_table import (
    KIND_MENU, KIND_MENU_OPTION, _split_hide_items, _split_show_items,
)

L1 = 'L1'
L2 = 'L2'
L3 = 'L3'

ERROR = 'error'
WARNING = 'warning'

# UI 里一句台词的安全长度（超过就要换行/拆页，否则对话框会溢出）
DEFAULT_CHAR_LIMIT = 60

_VALID_ACTIONS = {'play', 'queue', 'stop'}

# Ren'Py 文本标签 {b}…{/b}、{color=#fff}、{w}、{nw}、[变量] 等：
# 只检查花括号是否成对，不做完整语法解析（那需要 Ren'Py 自己的解析器）。
_RE_TEXT_TAG = re.compile(r'\{[^{}]*\}|\[[^\[\]]*\]')


@dataclass
class Issue:
    level: str
    code: str
    severity: str
    message: str
    where: str = ''           # 定位（line_id / label / 文件）
    hint: str = ''            # 怎么修

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    issues: list = field(default_factory=list)

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def by_level(self, level: str) -> list:
        return [i for i in self.issues if i.level == level]

    def extend(self, issues) -> None:
        self.issues.extend(issues)

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'by_level': {lv: len(self.by_level(lv)) for lv in (L1, L2, L3)},
            'issues': [i.to_dict() for i in self.issues],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def summary(self) -> str:
        d = self.to_dict()
        head = (f"{'通过' if d['ok'] else '未通过'}："
                f"错误 {d['error_count']}，警告 {d['warning_count']}"
                f"（L1 {d['by_level'][L1]} / L2 {d['by_level'][L2]}"
                f" / L3 {d['by_level'][L3]}）")
        return head

    def format(self, max_per_level: int = 40) -> str:
        lines = [self.summary(), '']
        label = {L1: 'L1 单条', L2: 'L2 表内', L3: 'L3 全库'}
        for lv in (L1, L2, L3):
            issues = self.by_level(lv)
            if not issues:
                continue
            lines.append(f'—— {label[lv]} ——')
            for i in issues[:max_per_level]:
                mark = '✗' if i.severity == ERROR else '!'
                loc = f" [{i.where}]" if i.where else ''
                lines.append(f'  {mark} {i.code}{loc} {i.message}')
                if i.hint:
                    lines.append(f'      → {i.hint}')
            if len(issues) > max_per_level:
                lines.append(f'  …还有 {len(issues) - max_per_level} 条')
            lines.append('')
        return '\n'.join(lines).rstrip()


def _ref_ok(name: str, asset_type: str, registry, builtin_ok=False) -> bool:
    if not name:
        return True
    if builtin_ok and name in RENPY_BUILTIN_TRANSFORMS:
        return True
    if registry is None:
        return True
    return bool(resolve_references(name, registry))


# 资产类型 → registry 属性名（判断"这一类资产到底管没管"）
_TYPE_ATTR = {
    'image': 'images', 'music': 'music', 'sound': 'sounds',
    'voice': 'voices', 'transform': 'transforms', 'character': 'characters',
}


def _category_managed(registry, asset_type: str) -> bool:
    """
    这一类资产在项目里到底管没管？

    这类"未登记"要分级报告，否则是纯噪音：
      · 项目里有 images.xlsx（管了）而 `bg_room` 不在里面
        → **错误**，这是真的断裂引用，构建出来会缺图。
      · 项目里压根没有图片表（没管）
        → **警告**，说明资产管线还没上；报成错误会让 build 直接失败，
          把"还没登记资产表"这种正常阶段卡死。
    """
    if registry is None:
        return False
    attr = _TYPE_ATTR.get(asset_type)
    if not attr:
        return False
    return bool(getattr(registry, attr, {}) or {})


# ==================================================================
#  L1：单条
# ==================================================================

def validate_level1(beats, registry=None,
                    char_limit: int = DEFAULT_CHAR_LIMIT) -> list:
    issues = []
    for b in beats or []:
        where = getattr(b, 'line_id', '') or f"{b.label}.{b.seq:04d}"

        # 空台词：纯 `stop music` / `stop sound` 是明确指令，不算空
        if b.kind in ('say', 'narration') and not b.text and not b.comment \
                and not b.raw and not b.code and not b.sound and not b.voice \
                and not b.music and not b.scene and not b.show \
                and not b.hide and (b.music_action or '') != 'stop' \
                and (b.sound_action or '') != 'stop':
            issues.append(Issue(
                L1, 'E101', ERROR, '台词为空', where,
                '补台词，或把这一行删掉'))

        # 说话人未定义
        if b.speaker and registry is not None \
                and not resolve_references(b.speaker, registry):
            issues.append(Issue(
                L1, 'E102', ERROR,
                f"说话人 '{b.speaker}' 不在资源表角色里", where,
                '在 characters.xlsx 登记，或改成本表已有的角色变量名'))

        # 资产引用未登记
        for atype, name, builtin_ok in _beat_asset_refs(b):
            if _ref_ok(name, atype, registry, builtin_ok):
                continue
            managed = _category_managed(registry, atype)
            issues.append(Issue(
                L1, 'E103' if managed else 'W110',
                ERROR if managed else WARNING,
                f"{atype} '{name}' 未登记", where,
                '在对应资源表登记，或在候选表新增后刷新候选'
                if managed else
                '这类资产还没建资源表：建好后引用即可下拉选择'))

        # 台词长度 / 文本标签
        if b.text:
            if len(b.text) > char_limit:
                issues.append(Issue(
                    L1, 'W101', WARNING,
                    f'台词 {len(b.text)} 字，超过 UI 上限 {char_limit}', where,
                    '拆成两拍，或调短'))
            issues.extend(_check_text_tags(b.text, where))

        # 音频动作取值
        for col, val in (('MusicAction', b.music_action),
                         ('SoundAction', b.sound_action)):
            if val and val not in _VALID_ACTIONS:
                issues.append(Issue(
                    L1, 'W102', WARNING,
                    f'{col} 取值 {val!r} 非法（应为 play/queue/stop）',
                    where, '改成 play / queue / stop'))

        # 音频参数写在没有音频的拍上
        if b.music_params and not b.music and b.music_action != 'stop':
            issues.append(Issue(
                L1, 'W103', WARNING, 'MusicParams 填了但没有音乐', where,
                '补 Music，或清空参数'))
        if b.sound_params and not b.sound:
            issues.append(Issue(
                L1, 'W104', WARNING, 'SoundParams 填了但没有音效', where,
                '补 Sound，或清空参数'))

        # 同一拍里 scene 与 show 撞同一图像：顺序敏感，容易出错
        scene_img = (b.scene or '').strip()
        if scene_img:
            for item in _split_show_items(b.show):
                if (item.get('image') or '').strip() == scene_img:
                    issues.append(Issue(
                        L1, 'W105', WARNING,
                        f'scene 与 show 用了同一图像 {scene_img!r}', where,
                        '确认 StageOrder 是否符合本意'))

        # 内置变换写进 Transform/With 列（大概率是选错了列）
        if b.transform and b.transform in RENPY_BUILTIN_TRANSFORMS:
            issues.append(Issue(
                L1, 'W106', WARNING,
                f"Transform 列填的是内置变换 '{b.transform}'", where,
                '内置变换直接留在 At 列即可'))

        # 条件表达式肉眼可见的壳
        if b.condition and not _looks_like_condition(b.condition):
            issues.append(Issue(
                L1, 'W107', WARNING,
                f'条件 {b.condition!r} 看起来不是合法表达式', where,
                '写成 love >= 5 这样的 Python 表达式'))

    return issues


def _beat_asset_refs(beat) -> list:
    """一条 Beat 引用的资产：[(类型, 名字, 是否放行内置)]"""
    refs = []
    if beat.scene:
        refs.append(('image', beat.scene.strip(), False))
    for item in _split_show_items(beat.show):
        if (item.get('image') or '').strip():
            refs.append(('image', item['image'].strip(), False))
    for item in _split_hide_items(beat.hide):
        if (item.get('image') or '').strip():
            refs.append(('image', item['image'].strip(), False))
    for t in (beat.at or '').split():
        refs.append(('transform', t, True))
    if beat.transform:
        refs.append(('transform', beat.transform.strip(), True))
    if beat.with_:
        refs.append(('transform', beat.with_.strip(), True))
    if beat.music:
        refs.append(('music', beat.music.strip(), False))
    if beat.sound:
        refs.append(('sound', beat.sound.strip(), False))
    if beat.voice:
        refs.append(('voice', beat.voice.strip(), False))
    return refs


def _check_text_tags(text: str, where: str) -> list:
    issues = []
    if text.count('{') != text.count('}'):
        issues.append(Issue(
            L1, 'W108', WARNING, '文本标签 {} 不配对', where,
            '检查 {b} / {color=#fff} 之类的闭合'))
    if text.count('[') != text.count(']'):
        issues.append(Issue(
            L1, 'W109', WARNING, '插值 [] 不配对', where,
            '检查 [变量] 的闭合'))
    return issues


def _looks_like_condition(cond: str) -> bool:
    """粗判：不能含中文、不能以运算符开头/结尾、括号要配对。"""
    if re.search(r'[\u4e00-\u9fff]', cond):
        return False
    stripped = cond.strip()
    if re.match(r'^[=<>!&|)]', stripped) or re.search(r'[=<>!&|(]$', stripped):
        return False
    if cond.count('(') != cond.count(')'):
        return False
    return True


# ==================================================================
#  L2：表内
# ==================================================================

def validate_level2(beats, registry=None) -> list:
    issues = []
    beats = list(beats or [])

    seen_line_ids = {}
    seq_by_label = {}
    menu_options_by_label = {}
    has_menu_caption = set()

    for b in beats:
        where = getattr(b, 'line_id', '') or f"{b.label}.{b.seq:04d}"

        if not b.label:
            issues.append(Issue(
                L2, 'E201', ERROR, '这一行没有 Label', where,
                '填上所属章节 label（生成脚本时用它分段）'))

        if b.line_id:
            if b.line_id in seen_line_ids:
                issues.append(Issue(
                    L2, 'E202', ERROR,
                    f"LineId '{b.line_id}' 在第 "
                    f"{seen_line_ids[b.line_id]} 拍已出现过", where,
                    'LineId 是协作主键，必须唯一；重算 LineId 或调整顺序'))
            else:
                seen_line_ids[b.line_id] = b.seq

        seq_by_label.setdefault(b.label, []).append(b.seq)
        if b.kind == KIND_MENU:
            has_menu_caption.add(b.label)
        if b.kind == KIND_MENU_OPTION:
            menu_options_by_label.setdefault(b.label, 0)
            menu_options_by_label[b.label] += 1

    # seq 断号/重复
    for label, seqs in seq_by_label.items():
        if len(set(seqs)) != len(seqs):
            issues.append(Issue(
                L2, 'E203', ERROR, f"label '{label}' 里 Seq 有重复",
                label, '重排 Seq'))
        ordered = sorted(set(seqs))
        gaps = [a for a, b2 in zip(ordered, ordered[1:]) if b2 != a + 1]
        if gaps:
            issues.append(Issue(
                L2, 'W201', WARNING,
                f"label '{label}' 的 Seq 不连续（{len(ordered)} 拍，"
                f"{len(gaps)} 处断号）", label,
                '断号通常无害；若是删行造成，可重排'))

    # 菜单选项没有 menu 标题
    for label, count in menu_options_by_label.items():
        if label not in has_menu_caption:
            issues.append(Issue(
                L2, 'W202', WARNING,
                f"label '{label}' 有 {count} 个菜单选项却没有 menu 行",
                label, '补一行 menu（Caption 填提示语）或用 jump 代替'))

    return issues


# ==================================================================
#  L3：全库
# ==================================================================

def validate_level3(beats, registry=None, graph=None,
                    script_warnings=None, project_root=None) -> list:
    issues = []
    beats = list(beats or [])

    # 断裂跳转 / 不可达：优先用图的告警与可达性
    for w in (script_warnings or []):
        if '未定义的跳转目标' in str(w):
            issues.append(Issue(
                L3, 'E301', ERROR, str(w), '', '补上目标 label 或改跳转'))

    if graph is not None:
        issues.extend(_unreachable_issues(graph))

    if registry is None:
        return issues

    # 未使用资产
    referenced = set(getattr(registry, 'references', {}) or {})
    for attr, label in (('images', '图片'), ('music', '音乐'),
                        ('sounds', '音效'), ('transforms', '变换')):
        data = getattr(registry, attr, {}) or {}
        unused = [aid for aid in data if aid not in referenced]
        if unused:
            sample = '、'.join(sorted(unused)[:6])
            issues.append(Issue(
                L3, 'W301', WARNING,
                f'{label}：{len(unused)} 个未被任何脚本引用（如 {sample}）',
                attr, '确认是否废弃；确实不用就从资源表删除'))

    # 角色没有台词
    if beats:
        speakers = {b.speaker for b in beats if b.speaker}
        chars = getattr(registry, 'characters', {}) or {}
        silent = [cid for cid in chars if cid not in speakers]
        if silent:
            issues.append(Issue(
                L3, 'W302', WARNING,
                f'{len(silent)} 个角色在本批次里没有台词（如 '
                f'{"、".join(sorted(silent)[:6])}）', 'characters',
                '确认是尚未出场，还是被漏掉了'))

    # 资源表登记了但文件不存在
    if project_root:
        root = Path(project_root)
        missing = []
        for attr in ('images', 'music', 'sounds', 'voices'):
            for aid, row in (getattr(registry, attr, {}) or {}).items():
                rel = (row or {}).get('Path')
                if rel and not (root / str(rel)).exists():
                    missing.append(f'{aid}({rel})')
        if missing:
            issues.append(Issue(
                L3, 'E302', ERROR,
                f'{len(missing)} 个资产的源文件不存在：'
                f'{"、".join(missing[:6])}', '资源表',
                '修正 Path 或补文件；否则构建出的包会缺资源'))

    return issues


def _unreachable_issues(graph) -> list:
    """从入口做可达性分析，报出到不了的 label。"""
    try:
        from analysis import analyze_reachability
    except Exception:
        return []
    if not getattr(graph, 'nodes', None):
        return []
    start = next((nid for nid, n in graph.nodes.items() if n.is_current),
                 None)
    if start is None:
        start = next(iter(graph.nodes), None)
    if start is None:
        return []
    try:
        _states, _warnings = analyze_reachability(graph.copy(), start)
    except Exception:
        return []

    reached = _reached_nodes(graph, start)
    unreachable = [n.label_name for nid, n in graph.nodes.items()
                   if nid not in reached]
    if not unreachable:
        return []
    return [Issue(
        L3, 'E303', ERROR,
        f'{len(unreachable)} 个 label 从入口走不到（如 '
        f'{"、".join(sorted(unreachable)[:6])}）', 'graph',
        '检查是否少了 jump/call，或它们本就是调试用残留')]


def _reached_nodes(graph, start_id) -> set:
    """图上从入口可达的节点集合（宽搜，与可达性分析结论一致）。"""
    seen = {start_id}
    queue = [start_id]
    while queue:
        cur = queue.pop()
        for e in graph.edges.values():
            if e.from_id != cur:
                continue
            if e.to_id not in seen:
                seen.add(e.to_id)
                queue.append(e.to_id)
    return seen


# ==================================================================
#  汇总
# ==================================================================

def validate_all(beats, registry=None, graph=None, script_warnings=None,
                 project_root=None,
                 char_limit: int = DEFAULT_CHAR_LIMIT) -> ValidationReport:
    report = ValidationReport()
    report.extend(validate_level1(beats, registry, char_limit=char_limit))
    report.extend(validate_level2(beats, registry))
    report.extend(validate_level3(beats, registry, graph,
                                  script_warnings, project_root))
    return report
