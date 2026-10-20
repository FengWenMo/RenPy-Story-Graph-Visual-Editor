"""
storygraph.pipeline.ir_validate
===============================
**导出之前先自检：这份中立 IR 自己是不是自相矛盾的。**

用户 2026-10-20（本轮目标）：

> 增强多引擎 IR 与适配（校验 / 损失报告 / 更多引擎）

§18 的损失报告回答"目标引擎少拿了什么"；这个模块回答另一个问题：
**"交出去的这一份本身对不对"**。一份 `story_ir.json` 里三十来个行级字段，
下游（Unity 导入器、翻译公司、埋点脚本、存档迁移）只认 `line_id` 和
`stats` 这类"看起来像真值"的东西 —— 一旦它们自相矛盾，
**没有任何一处会报错**：

  · `line_id` 重复 → 存档按 id 存，两个不同拍被当成同一句；
  · `stats.lines` 与 `lines` 长度不符 → 产品按统计做进度条，差一截；
  · `stage[]` 里有一个不存在的 `line_id` → 段错误或不静默跳过；
  · `timeline[].order` 不是 0..n-1 → 适配器按 order 排序，顺序全乱；
  · `structure.known=false` 却带着非空 `path` → 两处互相打脸。

这些**不是目标引擎的损失**（§18 管不了，它只对账字段在不在），
而是"这份 IR 生成完就坏了"。所以放在导出之前跑，产出 `IR_CHECK.md`，
并把错误/警告数写进导出回执与状态栏。

判据在 `tests/test_ir_validate.py`：

  · 一份干净 IR 必须**零错误零警告**（否则检查本身在制造噪音）；
  · 上面每一条坏数据都有对应 `E-IR-*`；
  · `to_unity()` 的产物里出现嵌套字典（JsonUtility 反序列化不了）
    必须被 `check_adapter()` 抓到；
  · `IR_CHECK.md` 真的落在导出目录里，且 `expected_files()` 与之一致。

边界：**非阻塞**。错误只报告，不拦导出 —— 用户可能就是要拿这份数据去
排查；但回执会写"自检 2 错"，不会让它悄悄出去。

**坏数据是自检要诊断的对象，不是它的崩溃点**（2026-10-20 补）：
`lines` / `assets` 形状不对时报告照样生成（`E-IR-LINES-SHAPE` 等）；
适配产物检查崩了记一条 `E-IR-CHECK-CRASH`；连自检自己都崩了，
`export_ir` 也会写一份 `crash_report`——"自检没跑成"和"零问题"必须
长得不一样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from storygraph.core.safeguard import swallow
from storygraph.story.paths import write_text_atomic

#: 自检报告文件名。**每个引擎都会写**：中立 IR 是每次导出都有的产物，
#: 它坏了，目标引擎拿到的那一份也好不到哪里去。
CHECK_REPORT_NAME = 'IR_CHECK.md'

#: 认得的 schema 版本。改 `engine_exporters.SCHEMA_VERSION` 时必须同步
#: （`tests/test_ir_validate.py` 有漂移守卫，不同步会红）。
KNOWN_SCHEMA_VERSIONS = ('1.1',)
SCHEMA_PREFIX = 'renpy-story-ir/'

#: 一条 line 至少得有这些键，否则下游没法安全取值。
REQUIRED_LINE_KEYS = ('line_id', 'seq', 'label', 'text', 'structure',
                      'timeline')

#: `text` 里出现这些字符说明还有 Ren'Py 标记（{color=…} / {w} / [name]），
#: 中立 IR 只保留原文 —— 这不是错，但要提醒目标引擎"别原样显示"。
MARKUP_CHARS = ('{', '[')

#: 低于这个置信度的维度算"猜的"（§19 的刻度：explicit 1.0 / unknown 0.15）。
LOW_CONFIDENCE = 0.7

#: 自检**自己**崩了时用的代码（不是 IR 的问题，是自检的问题）。
#: 与 `export_ir` 写的 `crash_report` 是同一件事的两个入口。
CRASH_CODE = 'E-IR-CHECK-CRASH'

_SCHEMA_RE = re.compile(r'^' + re.escape(SCHEMA_PREFIX) + r'(\d+\.\d+)$')

_SCALARS = (str, int, float, bool, type(None))


@dataclass
class Issue:
    """一条自检结论（同代码多条会聚合成一条，带数量与例子）。"""

    level: str              # 'error' / 'warning'
    code: str               # 'E-IR-…' / 'W-IR-…' / 'E-AD-…'
    message: str            # 给人看的一句话
    scope: str = ''         # schema / line / stage / asset / stats / adapter
    count: int = 1
    examples: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {'level': self.level, 'code': self.code,
                'message': self.message, 'scope': self.scope,
                'count': self.count, 'examples': list(self.examples)}


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


class _Issues:
    """按 (level, code, message) 聚合，保持第一次出现的顺序。"""

    def __init__(self):
        self._rows: dict = {}

    def add(self, level, code, message, scope='', example='', weight=1):
        key = (level, code, message)
        row = self._rows.get(key)
        if row is None:
            # count 从 0 起：默认的 1 会让"第一次出现"直接变成 2。
            row = Issue(level, code, message, scope, count=0)
            self._rows[key] = row
        row.count += int(weight)
        if example and len(row.examples) < 8:
            row.examples.append(str(example))

    def rows(self) -> list:
        return list(self._rows.values())


def _check_schema(ir, out: _Issues) -> None:
    schema = ir.get('schema')
    match = _SCHEMA_RE.match(str(schema or ''))
    if match is None:
        out.add('error', 'E-IR-SCHEMA',
                f'缺少可识别的 schema（应为 {SCHEMA_PREFIX}<版本>）',
                'schema', schema or '（空）')
        return
    if match.group(1) not in KNOWN_SCHEMA_VERSIONS:
        out.add('warning', 'W-IR-SCHEMA-VERSION',
                'schema 版本比本工具认识的新，未知字段会被当成空',
                'schema', schema)


def _check_stats(ir, lines, out: _Issues) -> None:
    stats = ir.get('stats')
    if not isinstance(stats, dict):
        out.add('error', 'E-IR-STATS', '没有 stats，下游无法相信统计值',
                'stats')
        return
    text_lines = [l for l in lines if isinstance(l, dict)]
    want = {
        'lines': len(text_lines),
        'labels': len({str(l.get('label') or '')
                       for l in text_lines if l.get('label')}),
        'characters': len(ir.get('characters') or []),
        'spoken_lines': sum(1 for l in text_lines if l.get('text')),
        'chars': sum(len(str(l.get('text') or '')) for l in text_lines),
    }
    for key, expected in want.items():
        got = stats.get(key)
        if got == expected:
            continue
        out.add('error', 'E-IR-STATS', 'stats 与内容对不上（导出前就已不一致）',
                'stats', f'{key}: 报 {got!r}，实际 {expected}')


def _check_lines(lines, char_ids, image_tags, out: _Issues) -> list:
    """逐行检查；返回见过的 line_id 集合（给 stage 对账用）。"""
    seen = set()
    dup_reported = set()
    for idx, line in enumerate(lines):
        if not isinstance(line, dict):
            out.add('error', 'E-IR-LINE-SHAPE', 'lines 里有不是对象的条目',
                    'line', f'#{idx}')
            continue
        missing = [k for k in REQUIRED_LINE_KEYS if k not in line]
        if missing:
            out.add('error', 'E-IR-LINE-KEYS',
                    'line 缺少下游要用的键', 'line', '、'.join(missing))
        lid = str(line.get('line_id') or '')
        if not lid:
            out.add('error', 'E-IR-ID-EMPTY', 'line_id 为空（主键不能空）',
                    'line', f'#{idx}')
        elif lid in seen:
            # 第一次发现重复时算 2 行（这一行 + 之前那行）；再出现只加 1。
            weight = 1 if lid in dup_reported else 2
            dup_reported.add(lid)
            out.add('error', 'E-IR-ID-DUP', 'line_id 重复（存档/埋点会串）',
                    'line', lid, weight=weight)
        else:
            seen.add(lid)
        if not isinstance(line.get('seq'), int):
            out.add('warning', 'W-IR-SEQ', 'seq 不是整数，排序可能不稳定',
                    'line', lid or f'#{idx}')
        _check_structure(line, lid or f'#{idx}', out)
        _check_timeline(line, lid or f'#{idx}', out)
        _check_actors(line, lid or f'#{idx}', image_tags, out)
        _check_speaker(line, lid or f'#{idx}', char_ids, out)
        text = str(line.get('text') or '')
        if any(ch in text for ch in MARKUP_CHARS):
            out.add('warning', 'W-IR-MARKUP',
                    "台词里还有 Ren'Py 标记，目标引擎别原样显示", 'line',
                    lid or text[:24])
    return seen


def _check_structure(line, lid, out) -> None:
    structure = line.get('structure')
    if not isinstance(structure, dict):
        out.add('error', 'E-IR-STRUCTURE-SHAPE',
                'structure 不是对象（跨引擎靠它重建菜单树）', 'line', lid)
        return
    known = structure.get('known')
    path = str(structure.get('path') or '')
    if not isinstance(known, bool):
        out.add('warning', 'W-IR-STRUCTURE-SHAPE',
                'structure.known 不是布尔值', 'line', lid)
    elif known is False and path:
        out.add('error', 'E-IR-STRUCTURE',
                'known=false 却带着 path，两处互相打脸', 'line', lid)
    elif known is False:
        out.add('warning', 'W-IR-STRUCTURE-UNKNOWN',
                '这些拍不知道嵌套位置，目标引擎只能当顶层', 'line', lid)


def _check_timeline(line, lid, out) -> None:
    timeline = line.get('timeline')
    if not isinstance(timeline, list):
        out.add('error', 'E-IR-TIMELINE-SHAPE', 'timeline 不是数组',
                'line', lid)
        return
    for pos, event in enumerate(timeline):
        if not isinstance(event, dict):
            out.add('error', 'E-IR-TIMELINE-SHAPE',
                    'timeline 里有不是对象的条目', 'line', lid)
            continue
        if event.get('order') != pos:
            out.add('error', 'E-IR-TIMELINE-ORDER',
                    'timeline.order 不是 0..n-1（按它排序会乱）', 'line',
                    f'{lid}#{pos}={event.get("order")!r}')
        if _blank(event.get('op')):
            out.add('error', 'E-IR-TIMELINE-OP', 'timeline 事件没有 op',
                    'line', lid)
        bad = [k for k, v in event.items() if not isinstance(v, _SCALARS)]
        if bad:
            out.add('error', 'E-IR-TIMELINE-VALUE',
                    'timeline 事件里出现非标量（适配器只认标量）', 'line',
                    f'{lid}: {",".join(bad)}')


def _check_actors(line, lid, image_tags, out) -> None:
    actors = line.get('actors')
    if actors is None:
        return
    if not isinstance(actors, list):
        out.add('error', 'E-IR-ACTORS-SHAPE', 'actors 不是数组', 'line', lid)
        return
    for actor in actors:
        name = str(actor or '')
        if ';' in name:
            out.add('error', 'E-IR-ACTOR-JOINED',
                    'actors 里有多张图挤在一个字符串（应逐条拆开）',
                    'line', lid)
        if not name.strip():
            out.add('warning', 'W-IR-ACTOR-BLANK',
                    'actors 里有空字符串', 'line', lid)
            continue
        tag = name.split()[0]
        if image_tags and tag not in image_tags:
            out.add('warning', 'W-IR-ACTOR-UNKNOWN',
                    '这些立绘在资源清单里找不到对应图', 'line', tag)


def _check_speaker(line, lid, char_ids, out) -> None:
    speaker = str(line.get('speaker') or '')
    if speaker and char_ids and speaker not in char_ids:
        out.add('warning', 'W-IR-SPEAKER-UNKNOWN',
                '角色变量不在 characters 里（跨章导出时正常，但要说）',
                'line', speaker)


def _check_stage(ir, line_ids, out) -> None:
    stage = ir.get('stage')
    if stage is None:
        out.add('warning', 'W-IR-STAGE-MISSING',
                '没有 stage 快照，目标引擎得自己叠加 show/hide', 'stage')
        return
    if not isinstance(stage, list):
        out.add('error', 'E-IR-STAGE-SHAPE', 'stage 不是数组', 'stage')
        return
    seen = set()
    covered = set()          # **落在 line_ids 里**的快照（才算"这一拍有快照"）
    dup_reported = set()
    for idx, row in enumerate(stage):
        if not isinstance(row, dict):
            out.add('error', 'E-IR-STAGE-SHAPE',
                    'stage 里有不是对象的条目', 'stage', f'#{idx}')
            continue
        sid = str(row.get('line_id') or '')
        if not sid:
            out.add('error', 'E-IR-STAGE-ID', 'stage 条目的 line_id 为空',
                    'stage', f'#{idx}')
        elif sid in seen:
            weight = 1 if sid in dup_reported else 2
            dup_reported.add(sid)
            out.add('error', 'E-IR-STAGE-DUP',
                    '同一拍有两份 stage 快照', 'stage', sid, weight=weight)
        else:
            seen.add(sid)
            if line_ids and sid not in line_ids:
                out.add('error', 'E-IR-STAGE-ORPHAN',
                        'stage 指向一个不存在的 line_id', 'stage', sid)
            else:
                covered.add(sid)
    # 覆盖率只数**真对上**的：从前数的是 `seen`，孤儿 id 也算一"拍"，
    # 于是"2 拍 + 1 合法 + 1 孤儿"会因为 `len(seen) == len(line_ids)` 而
    # **漏报**覆盖率不足（2026-10-20 修，P3）。
    missing = sorted(set(line_ids or ()) - covered) if line_ids else []
    if missing:
        out.add('warning', 'W-IR-STAGE-MISSING',
                '有拍没有 stage 快照（跨章合并时正常，但目标引擎得兜底）',
                'stage', f'{len(missing)} 拍：' + '、'.join(missing[:3]))


def _check_assets(ir, out) -> None:
    assets = ir.get('assets')
    if assets is None:
        return
    if not isinstance(assets, dict):
        out.add('error', 'E-IR-ASSETS-SHAPE', 'assets 不是对象', 'asset')
        return
    for group, items in assets.items():
        if not isinstance(items, list):
            out.add('error', 'E-IR-ASSETS-SHAPE',
                    'assets 的每个分组都该是数组', 'asset', str(group))
            continue
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                out.add('error', 'E-IR-ASSET-SHAPE',
                        '资源条目不是对象', 'asset', str(group))
                continue
            aid = str(item.get('id') or '')
            if not aid:
                out.add('error', 'E-IR-ASSET-ID', '资源条目没有 id',
                        'asset', str(group))
            elif aid in seen:
                out.add('error', 'E-IR-ASSET-DUP',
                        '同一分组里资源 id 重复', 'asset', aid)
            else:
                seen.add(aid)
            _check_dimensions(item, aid or str(group), out)


def _check_dimensions(item, aid, out) -> None:
    """§19 的置信度：带结构化维度却没有元数据 / 元数据是猜的 / 两列打架。"""
    meta = item.get('attribute_meta')
    attrs = item.get('attributes')
    if attrs and not meta:
        out.add('warning', 'W-IR-DIM-NO-META',
                '有维度结构但没有来源/置信度（下游分不清谁可信）',
                'asset', aid)
        return
    if not isinstance(meta, list):
        return
    low, conflicts = [], []
    for row in meta:
        if not isinstance(row, dict):
            continue
        try:
            confidence = float(row.get('confidence'))
        except (TypeError, ValueError):
            confidence = 0.0
        source = str(row.get('source') or '')
        here = f'{row.get("group") or "?"}={row.get("value") or "?"}'
        # `conflict`（0.5）不是"猜的"：表里两列**确实写着**两个值，
        # 只是不一致。混进 W-IR-DIM-GUESSED 会让人以为是"猜错了"。
        if source == 'conflict':
            conflicts.append(here)
        elif confidence < LOW_CONFIDENCE or source == 'unknown':
            low.append(here)
    if conflicts:
        out.add('warning', 'W-IR-DIM-CONFLICT',
                '`Attributes` 与 `Value` 两列写的不是同一个值'
                '（两条都留着，但分不清哪条是真的）',
                'asset', f'{aid}: {", ".join(conflicts[:3])}')
    if low:
        out.add('warning', 'W-IR-DIM-GUESSED',
                '这些维度是猜的（低置信度），排版/筛选别当确定值',
                'asset', f'{aid}: {", ".join(low[:3])}')


def check_ir(ir) -> list:
    """
    中立 IR 自检 → `[Issue]`（错误在前，警告在后）。

    **不修改** `ir`，也不依赖磁盘。干净数据必须返回空列表 ——
    这条由 `test_clean_ir_has_no_issues` 守着。
    """
    out = _Issues()
    if not isinstance(ir, dict):
        out.add('error', 'E-IR-SHAPE', 'IR 不是对象', 'schema')
        return out.rows()
    _check_schema(ir, out)
    lines = ir.get('lines')
    if not isinstance(lines, list):
        out.add('error', 'E-IR-LINES-SHAPE', 'lines 不是数组', 'line')
        lines = []
    elif not lines:
        out.add('error', 'E-IR-EMPTY', 'lines 为空（导出的是空章节）', 'line')
    characters = ir.get('characters')
    if characters is not None and not isinstance(characters, list):
        out.add('error', 'E-IR-CHARACTERS-SHAPE', 'characters 不是数组',
                'schema')
        characters = []
    char_ids = {str(c.get('id') or '') for c in (characters or [])
                if isinstance(c, dict)}
    image_tags = set()
    assets = ir.get('assets') if isinstance(ir.get('assets'), dict) else {}
    for item in (assets.get('images') or []):
        if isinstance(item, dict) and item.get('tag'):
            image_tags.add(str(item['tag']))
    line_ids = _check_lines(lines, char_ids, image_tags, out)
    _check_stage(ir, line_ids, out)
    _check_assets(ir, out)
    _check_stats(ir, lines, out)
    rows = out.rows()
    rows.sort(key=lambda r: (r.level != 'error', r.code))
    return rows


# ==================================================================
#  适配器约束：Unity 的 JsonUtility 吃不下嵌套字典
# ==================================================================

def _nested_dict_paths(value, path='$', depth=0) -> list:
    """找 `{键: {…}}` 形态（数组里的结构体是允许的）。"""
    found = []
    if depth > 6:
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            here = f'{path}.{key}'
            if isinstance(item, dict):
                found.append(here)
            else:
                found.extend(_nested_dict_paths(item, here, depth + 1))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            found.extend(_nested_dict_paths(item, f'{path}[{idx}]', depth + 1))
    return found


def check_adapter(engine: str, payload) -> list:
    """
    检查**适配产物**是否满足目标引擎的硬约束。

    目前只有 Unity 有硬约束：`JsonUtility.FromJson<T>` **不支持 Dictionary**，
    即 `{"a": {"b": 1}}` 这种字段会让整份数据解析失败。`to_unity()` 靠
    "把 structure 摊成三个标量、timeline 用扁平对象数组"绕开它 ——
    这个检查守的就是"以后别有人往里塞嵌套字典"。
    """
    engine = str(engine or '').lower()
    out = _Issues()
    if engine != 'unity':
        return []
    if not isinstance(payload, dict):
        out.add('error', 'E-AD-SHAPE', 'Unity 数据不是对象', 'adapter')
        return out.rows()
    for idx, line in enumerate(payload.get('lines') or []):
        if not isinstance(line, dict):
            out.add('error', 'E-AD-UNITY-LINE',
                    'Unity lines 里有不是对象的条目', 'adapter', f'#{idx}')
            continue
        missing = [k for k in ('lineId', 'speakerId', 'text') if k not in line]
        if missing:
            out.add('error', 'E-AD-UNITY-KEYS',
                    'Unity line 缺少 C# 结构体要的键', 'adapter',
                    '、'.join(missing))
        nested = _nested_dict_paths(line, f'line[{idx}]')
        for here in nested:
            out.add('error', 'E-AD-UNITY-NESTED',
                    'JsonUtility 反序列化不了嵌套字典（要用结构体数组）',
                    'adapter', here)
    return out.rows()


# ==================================================================
#  汇总与报告
# ==================================================================

def summarize(ir, engine: str = '') -> dict:
    """
    自检结果的可回执形态：`{error_count, warning_count, issues, by_code}`。

    `engine` 给了就顺带检查适配产物（Unity 才会真的多出结论）。

    **适配产物检查自己崩了要算一条错误**（2026-10-20 修，P1）：从前这里
    直接调 `to_unity(ir)`，坏形状的 IR 会抛出去 —— 而 `session_export`
    外面包着 `try`，异常一吞，回执里 `error_count` 就是 0。
    "没跑成"和"干净"在回执上长得一样，这是这个仓库最忌讳的一类谎。
    """
    issues = check_ir(ir)
    if engine:
        try:
            from storygraph.pipeline.engine_exporters import (to_godot,
                                                              to_unity)
            if str(engine).lower() == 'unity':
                issues += check_adapter('unity', to_unity(ir))
            elif str(engine).lower() == 'godot':
                issues += check_adapter('godot', to_godot(ir))
        except Exception as e:                # noqa: BLE001 - 崩了也算一条错误
            swallow('pipeline.ir_validate.summarize.adapter', e)
            issues.append(Issue(
                'error', CRASH_CODE,
                f'适配产物检查没跑成（{type(e).__name__}：{e}）', 'adapter'))
    errors = [i for i in issues if i.level == 'error']
    warnings = [i for i in issues if i.level != 'error']
    by_code = {}
    for issue in issues:
        by_code[issue.code] = by_code.get(issue.code, 0) + issue.count
    return {
        'error_count': sum(i.count for i in errors),
        'warning_count': sum(i.count for i in warnings),
        'issue_count': len(issues),
        'issues': [i.as_dict() for i in issues],
        'by_code': by_code,
    }


def crash_report(engine: str, exc: BaseException) -> str:
    """
    自检崩了时写的报告（保证 `IR_CHECK.md` **一定**存在）。

    为什么要有它：`export_ir` 的最后一步是写这份报告。从前那一步会抛 ——
    于是坏 IR 的目录里**没有** `IR_CHECK.md`，用户只看到"导出失败"，
    而真正的原因（哪一段数据形状不对）谁也没说。
    这份报告说三件事：自检没跑成、崩在哪、以及"别把它当成零问题"。
    """
    title = f'# 中立 IR 自检 · {engine}' if engine else '# 中立 IR 自检'
    detail = f'{type(exc).__name__}：{exc}'
    return '\n'.join([
        title, '',
        '- 错误 1 · 警告 0',
        '',
        '> **自检没跑成**：这一份报告不是"数据没问题"，而是"检查这一份的'
        '时候崩了"。在修好之前，别把这份 IR 当成已校验的产物。', '',
        '## 错误', '',
        '| 代码 | 说明 | 数量 | 例子 |', '|---|---|---|---|',
        f'| `{CRASH_CODE}` | 自检自身抛出异常（IR 形状太坏 / 自检有 bug） '
        f'| 1 | {detail} |', '',
        '## 怎么查', '',
        f'- 先看异常：`{detail}`；',
        '- 再拿 `story_ir.json` 过一遍 JSON 校验（对象/数组/键名是否如'
        '`build_neutral_ir` 的文档所述）；',
        '- 自检的判据在 `storygraph/pipeline/ir_validate.py`，'
        '用例在 `tests/test_ir_validate.py`。', ''])


def report_markdown(ir, engine: str = '') -> str:
    """自检报告（落盘成 `IR_CHECK.md`，给接引擎的人先看这一页）。"""
    summary = summarize(ir, engine)
    lines = ir.get('lines') if isinstance(ir, dict) else []
    if not isinstance(lines, list):
        lines = []                    # 坏数据也要能出报告（别在报告里再崩）
    assets = ir.get('assets') if isinstance(ir, dict) else {}
    if not isinstance(assets, dict):
        assets = {}
    asset_count = sum(len(v) for v in assets.values()
                      if isinstance(v, list))
    title = f'# 中立 IR 自检 · {engine}' if engine else '# 中立 IR 自检'
    out = [title, '',
           f'- 行：{len(lines or [])}，资源：{asset_count}',
           f'- 错误 {summary["error_count"]} · 警告 {summary["warning_count"]}',
           '',
           '> 这份报告说的是**数据自己对不对**（重复主键、统计不符、'
           '快照指向不存在的行）。目标引擎少拿了哪些字段是另一份：'
           '`IR_LOSS-<engine>.md`。', '']
    for level, label in (('error', '错误'), ('warning', '警告')):
        rows = [i for i in summary['issues'] if i['level'] == level]
        out += [f'## {label}', '']
        if not rows:
            out += ['（无）', '']
            continue
        out += ['| 代码 | 说明 | 数量 | 例子 |', '|---|---|---|---|']
        for row in rows:
            examples = '、'.join(str(e) for e in row['examples'][:4])
            out.append(f'| `{row["code"]}` | {row["message"]} | '
                       f'{row["count"]} | {examples} |')
        out.append('')
    out += ['## 自查了什么', '',
            '- `line_id` 非空、唯一（存档/埋点/协作锁的主键）；',
            '- `stats` 每一项与真实内容一致；',
            '- `stage[]` 的每一行都指向存在的 `line_id`，且不重复；',
            '- `timeline[].order` 是 0..n-1、事件里只有标量；',
            "- `structure.known` 与 `path` 不互相打脸；",
            '- `actors` 已逐条拆开、能对上资源清单里的 tag；',
            '- 资源 id 唯一；带了维度结构就必须带来源/置信度；',
            '- Unity 产物里**不许有嵌套字典**（JsonUtility 的硬约束）。',
            '']
    return '\n'.join(out)


def write_report(ir, out_dir, engine: str = '') -> str:
    """把自检报告写进导出目录，返回路径。"""
    path = Path(out_dir) / CHECK_REPORT_NAME
    write_text_atomic(path, report_markdown(ir, engine))
    return str(path)
