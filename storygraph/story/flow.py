"""
storygraph.story.flow
=====================
`call` / `jump` 控制流行的**解析、改写与展示信息**（2026-10-22，P0-4 第一阶段）。

为什么单独一层
--------------
解析器 `renpy_parser` 早就能识别 `call` / `jump`，图构建器也会为它们画边；
但 Beat 表里它们只是一张 `kind=narration`、`raw='call side'` 的旁白拍：
卡片上显示成"这一拍还会原样执行：call side"，目标只能进高级区手打。

这一层不改变 Beat 的存储形态（第二阶段才升级成 `kind='flow'`），只提供：

  · `parse_flow_line()` / `parse_flow()`：从 raw 里认出 `call` / `jump`；
  · `flow_info()`：给卡片用的展示信息（目标、是否返回、返回后第几拍）；
  · `update_beat_flow()`：只改目标 / 类型，**保留参数与 from 子句**。

所以第一阶段"看得见、改得动、导出正确"三件事都落在这里，图边同源
（第二阶段）仍然可以在此基础上做，不用再猜一遍字符串。
"""
from __future__ import annotations

import re

#: 与解析器同一套 label 名写法（点号分段是真实项目常见写法）。
_LABEL = r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*'
_LABEL_RE = re.compile(rf'^{_LABEL}$')
_FLOW_RE = re.compile(rf'^(call|jump)\s+({_LABEL})(?=\s|\(|$)(.*)$')
_FROM_RE = re.compile(rf'^from\s+({_LABEL})$')

#: 控制流拍允许出现的类型（目前只有这两个；`return` 不落卡，见 beat_build）。
FLOW_TYPES = ('call', 'jump')

#: 卡片 / 列表用的中文动词。
FLOW_VERBS = {'call': '调用', 'jump': '跳转'}

#: 卡片上属于「控制流卡」的表单键（不是 Excel 列，只走卡片 → `Beat.raw`）。
#:
#: 为什么要有这个元组：`fields_to_beat` / `EditorSession.apply` 两处都要判断
#: "这次提交里有没有控制流字段"，从前各写一遍 `'FlowType' in fields or ...`。
#: 第二阶段把参数与 `from` 也做成可编辑之后，两处都改不对就会**漏掉新字段**
#: （表现：改了参数，保存后原样没变）。放这里一处定义、两处引用。
FLOW_FIELD_KEYS = ('FlowType', 'FlowTarget', 'FlowArgs', 'FlowFrom')


def is_valid_label(name) -> bool:
    """`main.route` / `side.choice` 合法；空串、点号首尾、连续点号不合法。"""
    return bool(_LABEL_RE.match(str(name or '').strip()))


def _scan_balanced(text: str, start: int) -> int:
    """从 `text[start] == '('` 起找配平的右括号下标；不配平返回 -1。"""
    depth = 0
    quote = ''
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == quote:
                quote = ''
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def parse_flow_line(line) -> dict | None:
    """
    一行 `call X` / `jump X` → `{type,target,args,from_clause,line}`。

    不认识的尾巴（`jump expression …` / `call screen …` / 手打的怪写法）
    一律返回 None —— 宁可让那张拍继续走"原样执行"通道，也不猜。
    """
    text = str(line or '').strip()
    m = _FLOW_RE.match(text)
    if not m:
        return None
    flow_type, target, rest = m.group(1), m.group(2), m.group(3).strip()
    args = ''
    if rest.startswith('('):
        if flow_type != 'call':
            return None
        close = _scan_balanced(rest, 0)
        if close < 0:
            return None
        args = rest[:close + 1]
        rest = rest[close + 1:].strip()
    from_clause = ''
    if rest:
        if flow_type != 'call':
            return None
        fm = _FROM_RE.match(rest)
        if not fm:
            return None
        from_clause = fm.group(1)
    return {
        'type': flow_type,
        'target': target,
        'args': args,
        'from_clause': from_clause,
        'line': text,
    }


def parse_flow(beat) -> dict | None:
    """
    从一张 Beat 认出控制流拍（只认"这一拍除了这一行什么都没有"的形态）。

    刻意不猜的情况：
      · `raw` 有多行（同一拍里还挂着别的原生语句）；
      · 这一拍还有 code / opaque / 舞台 / 台词 / 说话人（那不是纯控制流卡）；
      · 条件拍**允许**（`if score: call side` 仍然是调用，条件由渲染层包）。
    """
    if beat is None:
        return None
    # **两种 kind 都收**（2026-10-22 第二阶段）：
    #   · `flow`      —— 新产物（`beat_build._emit_jump_call` 写入）；
    #   · `narration` —— 老表 / 旧内存对象里的 call 拍，不能因为升级就读不出来。
    if str(getattr(beat, 'kind', '') or '') not in ('narration', 'flow'):
        return None
    raw = str(getattr(beat, 'raw', '') or '').strip()
    if not raw:
        return None
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) != 1:
        return None
    for attr in ('code', 'opaque', 'stage', 'scene', 'show', 'hide',
                 'bgm', 'sfx', 'voice', 'text', 'speaker'):
        if str(getattr(beat, attr, '') or '').strip():
            return None
    return parse_flow_line(lines[0])


def render_flow_line(flow_type: str, target: str, args: str = '',
                     from_clause: str = '') -> str:
    """结构化字段 → 一行源码。`jump` 不接受参数 / from（调用方自己清空）。"""
    flow_type = str(flow_type or '').strip()
    target = str(target or '').strip()
    args = str(args or '').strip() if flow_type == 'call' else ''
    from_clause = (str(from_clause or '').strip()
                   if flow_type == 'call' else '')
    line = f'{flow_type} {target}{args}'
    if from_clause:
        line += f' from {from_clause}'
    return line


def _normalize_args(args) -> str:
    """
    `call side(a, b)` 的参数段：空串，或**配平的一对括号**。

    为什么必须校验：这段会被原样拼进产物。`call side(a` 这种没配平的写法
    会让整份脚本解析不了，而工具这边看不出任何异常 —— 那种"导出成功、
    游戏起不来"的错最难查。
    """
    text = str(args or '').strip()
    if not text:
        return ''
    if not text.startswith('('):
        raise ValueError('参数要以 ( 开头')
    close = _scan_balanced(text, 0)
    if close < 0 or close != len(text) - 1:
        raise ValueError('参数括号不配平')
    return text


def update_beat_flow(beat, flow_type=None, target=None, args=None,
                     from_clause=None) -> dict:
    """
    就地改一张控制流拍的 raw（**只改这一行，不动其它字段**）。

    返回 `{ok, changed, error, info}`：
      · 目标非法 / 参数括号不配平 / `from` 不合法 / 这一拍不是控制流拍
        → `ok=False`，Beat 一个字节都不动；
      · 类型从 call 改成 jump → 参数与 `from` 子句**明确丢掉**（Ren'Py 的
        `jump` 不接受它们，留着会生成跑不起来的脚本）；
      · 没变化 → `changed=False`（不制造"未保存"）。

    `args` / `from_clause` 传 `None` = **保持原值**（第二阶段新加）；
    传空串 = 清空。所以调用方「只想改目标」时不用知道原来有没有参数。
    """
    old = parse_flow(beat)
    if old is None:
        return {'ok': False, 'changed': False,
                'error': '这一拍不是 call / jump 控制流', 'info': None}
    kind = str(flow_type or old['type']).strip()
    if kind not in FLOW_TYPES:
        return {'ok': False, 'changed': False,
                'error': f'不认识的控制流类型：{kind}', 'info': None}
    wanted = str(target if target is not None else old['target']).strip()
    if not is_valid_label(wanted):
        return {'ok': False, 'changed': False,
                'error': f'目标 label 不合法：{wanted or "（空）"}',
                'info': None}
    if kind != 'call':
        # jump 不接受参数 / from（Ren'Py 语法），明确清掉而不是留着
        args_text = from_text = ''
    else:
        try:
            args_text = _normalize_args(
                old['args'] if args is None else args)
        except ValueError as e:
            return {'ok': False, 'changed': False,
                    'error': f'参数不合法：{e}', 'info': None}
        from_text = str(old['from_clause'] if from_clause is None
                        else from_clause).strip()
        if from_text and not is_valid_label(from_text):
            return {'ok': False, 'changed': False,
                    'error': f'from 标识不合法：{from_text}', 'info': None}
    line = render_flow_line(kind, wanted, args_text, from_text)
    changed = line != str(getattr(beat, 'raw', '') or '').strip()
    if changed:
        beat.raw = line
    info = parse_flow(beat)
    return {'ok': True, 'changed': changed, 'error': '', 'info': info}


def flow_info(beat, index: int = 0, beats=None) -> dict | None:
    """
    卡片顶部控制流条要的全部信息。

    `return_index` 是**调用返回后继续的那一拍**（0-based；只对 call 且
    下一拍仍在同一个 label 里时成立）。`None` 有两种含义，用 `return_note`
    说清楚：jump 不返回 / call 后面没有同 label 的拍了。
    """
    info = parse_flow(beat)
    if info is None:
        return None
    out = dict(info)
    out['index'] = int(index)
    out['return_index'] = None
    nxt = (list(beats)[index + 1]
           if beats is not None and index + 1 < len(beats) else None)
    same_label = (nxt is not None
                  and str(getattr(nxt, 'label', '') or '')
                  == str(getattr(beat, 'label', '') or ''))
    out['next_same_label'] = same_label
    if same_label:
        out['return_index'] = index + 1
        out['call_return_note'] = f'调用后回到这里，继续第 {index + 2} 拍'
    else:
        out['call_return_note'] = '调用后回到本章末尾（后面没有同 label 的拍）'
    out['jump_return_note'] = '跳转后不返回：这一章在这里结束'
    out['return_note'] = (out['jump_return_note'] if info['type'] == 'jump'
                          else out['call_return_note'])
    return out


def flow_edges(beats) -> list:
    """
    这批拍里的控制流拍 → **图边的唯一真源**（2026-10-22 第二阶段）。

    每条 `{from, to, type, condition, from_clause, line_id}`：

      · `from`  —— 这一拍所在的 label；
      · `to`    —— `call` / `jump` 的目标；
      · `line_id` —— 稳定身份（`ch01.0007` 那种），用来**区分同一 label 里
        多次 call 同一个目标** —— 图边按 `from+type+to` 找第一条的老做法
        在那种场景下会指错（第二阶段要收的第四件事）。

    `origin='source'` 的边应当**恒等于**这个清单；手工画的边
    （`origin='manual'`）不在里面，也不该被它覆盖。
    """
    out = []
    for beat in beats or ():
        info = parse_flow(beat)
        if not info:
            continue
        out.append({
            'from': str(getattr(beat, 'label', '') or ''),
            'to': info['target'],
            'type': info['type'],
            'condition': str(getattr(beat, 'condition', '') or ''),
            'from_clause': info['from_clause'],
            'line_id': str(getattr(beat, 'line_id', '') or ''),
        })
    return out


def flow_card_text(info: dict | None) -> str:
    """列表 / 删除确认里的一行短文案（没有控制流返回空串）。"""
    if not info:
        return ''
    return f"{info['type']} {info['target']}"


__all__ = ['FLOW_FIELD_KEYS', 'FLOW_TYPES', 'FLOW_VERBS',
           'flow_card_text', 'flow_edges', 'flow_info',
           'is_valid_label', 'parse_flow', 'parse_flow_line',
           'render_flow_line', 'update_beat_flow']
