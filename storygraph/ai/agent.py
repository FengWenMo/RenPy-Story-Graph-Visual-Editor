"""
Agent 循环：让模型自己调本工具（**写盘由代码强制，不靠提示词**）
=========================================================================

> 用户 2026-10-11："然后再让 ai 自行开始工作"。

## 这个循环干的事

    用户说一句话 → 模型决定调哪个工具 → **本地执行**（走 mcp_surface）
                 → 把回执喂回去 → 模型继续或收尾

工具清单**直接来自 `mcp.server.tools_list()`**（它又来自 `mcp_surface.capabilities()`）
—— 一处定义，三处使用（MCP / agent / 面板），**不会漂移**。

## ⭐ 三条护栏（**在代码里强制**，不写在提示词里）

1. **写盘必须有人点头**：模型调 `declare` 时，**代码**先把 `dry_run` 设成 True
   跑一遍（拿到 diff），再问 `approve(...)`；**没被批准就绝不真写**。
   ⇒ 就算模型被诱导着"直接写"，也写不下去 —— 提示词可以骗，代码不能。
2. **回执不许被截断成谎话**：工具输出喂回模型前会截断（省 token），
   但截断时会**明说"已截断"**（否则模型会以为那就看全了）。
3. **key 不进任何事件**：事件流会被面板显示、被日志收、被贴进 issue。

## 事件流（面板直接照它渲染）

    [{'kind': 'user'|'assistant'|'tool_call'|'tool_result'|'gate'|'error'|
              'done', ...}]
"""

from __future__ import annotations

import json

from storygraph.core.safeguard import swallow

#: 一次对话最多走几步（防止模型绕圈烧 token）
MAX_STEPS = 8

#: 工具回执喂回模型前的**截断长度**（超了就截，并明说截了）
RESULT_LIMIT = 6000

#: **写盘类**工具（必须过闸门）
WRITE_TOOLS = frozenset({'declare', 'table_export'})

#: 一句话系统提示：**只说事实与边界**，不试图用提示词当护栏
SYSTEM_PROMPT = (
    '你在一个 Ren\'Py 剧情编辑工具里干活。你有工具可以读项目（扫描、问引擎、'
    '体检）和改项目（写声明）。规矩：\n'
    '1. 先用只读工具搞清楚现状，再提议改动；不确定就问，别猜。\n'
    '2. **任何写盘都必须先试算**（工具会强制 dry_run）并把 diff 给人看；'
    '人没点头就不要写。\n'
    '3. 工具的返回里 `ok=false` 或 `verified=null` 表示"没做成/没证明"，'
    '**不是**"没问题" —— 如实转述，不要粉饰。\n'
    '4. 用中文、说人话；不要向用户解释 JSON 字段名。'
)


def tool_specs() -> list:
    """agent 用的工具描述（**与 MCP 同源**，OpenAI 的 function 形态）。"""
    from storygraph.mcp import server as S
    out = []
    for t in S.tools_list():
        out.append({'type': 'function', 'function': {
            'name': t['name'], 'description': t['description'],
            'parameters': t['inputSchema']}})
    return out


def _truncate(text: str) -> str:
    """截断要**明说**（否则模型以为看全了）。"""
    text = str(text or '')
    if len(text) <= RESULT_LIMIT:
        return text
    return (text[:RESULT_LIMIT]
            + f'\n…（**已截断**：原文 {len(text)} 字，这里只给了前 '
              f'{RESULT_LIMIT} 字）')


def execute(name: str, args: dict, *, project_root, approve=None,
            events: list = None, emit=None) -> dict:
    """
    执行**一次**工具调用（含写盘闸门）。返回回执 dict（形状同 `mcp_surface`）。

    ⚠ 闸门在**这里**，不在提示词里（见模块文档第 1 条）。

    `emit`：事件出口（面板实时渲染用）。从前这里只 `events.append`，
    而 `run_agent` 转发"最近 3 条里的 gate"那段**是空循环**（`pass`），
    于是面板永远收不到 gate 事件，批准对话框也就无从弹起。
    现在闸门事件走 `emit`，一次也不漏。
    """
    from storygraph import mcp_surface as M
    events = events if events is not None else []

    def _emit(ev):
        # 有面板出口就交给它（它会同时收进 events 列表）；没有就直接塞
        if emit is not None:
            emit(ev)
        else:
            events.append(ev)

    short = str(name or '').replace('storygraph_', '')
    fn = getattr(M, short, None)
    if not callable(fn):
        return {'ok': False, 'source': 'none',
                'reason': f'没有这个工具：{name}'}
    args = dict(args or {})
    args.setdefault('project_root', str(project_root or ''))
    if short == 'declare':
        approved = False
        args['dry_run'] = True                     # ← 先强制试算
        preview = fn(**args)
        _emit({'kind': 'gate', 'tool': short, 'args': args,
               'preview': preview})
        if approve is not None:
            try:
                approved = bool(approve(short, args, preview))
            except Exception as e:                 # noqa: BLE001 - 人给的钩子出错
                swallow('ai.agent.execute.approve', e)
                _emit({'kind': 'error',
                       'reason': f'批准钩子出错：{type(e).__name__}: {e}'})
                approved = False
        if not approved:
            preview['reason'] = (str(preview.get('reason') or '')
                                 + '｜**未获批准，没有写盘**（这是试算结果）')
            preview['written'] = []
            preview['dry_run'] = True
            return preview
        args['dry_run'] = False                    # 人点了头 → 才真写
    try:
        return fn(**args)
    except Exception as e:                          # noqa: BLE001 - 工具出错不许炸
        swallow('ai.agent.execute', e)
        return {'ok': False, 'source': 'none',
                'reason': f'工具出错：{type(e).__name__}: {e}'}


def run_agent(project_root, messages: list, cfg=None, *, transport=None,
              approve=None, max_steps: int = MAX_STEPS,
              on_event=None) -> dict:
    """
    跑一轮对话（**有界**：最多 `max_steps` 步）。

    返回 `{'messages': […], 'events': […], 'final': 文本, 'ok': bool,
           'reason': str}`；`messages` 可以直接喂回下一次（多轮对话）。
    """
    from storygraph.ai import client as C
    events = []

    def emit(ev):
        events.append(ev)
        if on_event:
            try:
                on_event(ev)
            except Exception as e:                  # noqa: BLE001 - 面板出错别拖累
                swallow('ai.agent.run_agent.on_event', e)

    convo = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    convo.extend(messages or [])
    specs = tool_specs()
    for step in range(int(max_steps)):
        try:
            reply = C.chat(convo, specs, cfg, transport=transport)
        except C.LlmError as e:
            emit({'kind': 'error', 'reason': str(e)})
            return {'ok': False, 'messages': convo, 'events': events,
                    'final': '', 'reason': str(e)}
        if reply.get('text'):
            emit({'kind': 'assistant', 'text': reply['text']})
        calls = reply.get('tool_calls') or []
        if not calls:
            # 没有工具调用 = 模型说完了
            convo.append({'role': 'assistant', 'content': reply.get('text') or ''})
            emit({'kind': 'done', 'steps': step + 1})
            return {'ok': True, 'messages': convo, 'events': events,
                    'final': reply.get('text') or '', 'reason': ''}
        convo.append({'role': 'assistant', 'content': reply.get('text') or '',
                      'tool_calls': [{'id': c['id'], 'type': 'function',
                                      'function': {'name': c['name'],
                                                   'arguments': json.dumps(
                                                       c['arguments'],
                                                       ensure_ascii=False)}}
                                     for c in calls]})
        for call in calls:
            emit({'kind': 'tool_call', 'name': call['name'],
                  'arguments': call['arguments']})
            got = execute(call['name'], call['arguments'],
                          project_root=project_root, approve=approve,
                          events=events, emit=emit)
            emit({'kind': 'tool_result', 'name': call['name'], 'result': got})
            convo.append({'role': 'tool', 'tool_call_id': call['id'],
                          'content': _truncate(json.dumps(got, ensure_ascii=False,
                                                          default=str))})
    emit({'kind': 'error', 'reason': f'走了 {max_steps} 步还没说完 —— 先停在这里'})
    return {'ok': False, 'messages': convo, 'events': events, 'final': '',
            'reason': f'超过 {max_steps} 步上限'}


__all__ = ['MAX_STEPS', 'RESULT_LIMIT', 'SYSTEM_PROMPT', 'WRITE_TOOLS', 'execute',
           'run_agent', 'tool_specs']
