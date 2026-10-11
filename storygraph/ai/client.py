"""
LLM 客户端（**零新依赖**：只用标准库 `urllib`）
=========================================================================

> 用户 2026-10-11："然后再让 ai 自行开始工作"；"让别人跟其余用户自己填 api key"。

## 三个设计决定（自己定的，写在这里备查）

1. **只用标准库** —— 本仓的依赖白名单只有 openpyxl/PySide6/Pillow，
   所以 HTTP 走 `urllib.request`，**不引入** openai/anthropic 那些 SDK；
2. **传输层可注入**（`transport=`）—— 判据**不联网**也能验：
   给一个假 transport，就能断言"请求里有什么、回复怎么解析"；
   ⚠ 也因此**判据不会误打真接口**（省 token、也不会泄漏 key）；
3. **请求构造与回复解析都是纯函数**（`build_request` / `parse_reply`）——
   它们是最容易出错、也最值得单测的两段。

## 支持的提供方

| provider | 接口 | 工具调用形态 |
|---|---|---|
| `openai` / `deepseek` / `openai-compat` | `POST {base}/chat/completions` | `tools` + `tool_calls`（arguments 是 **JSON 字符串**） |
| `anthropic` | `POST {base}/v1/messages` | `tools` + `content` 里的 `tool_use` 块 |

⚠ **key 只进请求头**（`Authorization` / `x-api-key`），**绝不进请求体** ——
请求体常被打印、被日志收、被贴进 issue。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from storygraph.core import ai_settings as AI
from storygraph.core.safeguard import swallow

#: 一次请求的默认超时（秒）—— 聊天要等人，别太短
DEFAULT_TIMEOUT = 120


class LlmError(Exception):
    """请求/解析出错（**带上能给人看的原因**，不吞）。"""

    def __init__(self, message: str, *, status: int = 0, body: str = ''):
        super().__init__(message)
        self.status = int(status or 0)
        self.body = str(body or '')[:500]          # 截断：别把整页 HTML 灌进日志


# ==================== 纯函数：请求 ====================

def build_request(cfg: dict, messages: list, tools=()) -> dict:
    """
    组一个请求（**纯函数**）：`{'url', 'headers', 'body'}`。

    ⚠ key 只在 `headers` 里 —— `body` 里永远不出现（判据钉住这一条）。
    """
    conf = AI.resolved(cfg)
    provider = conf['provider']
    key = conf['api_key']
    base = (conf['base_url'] or '').rstrip('/')
    if not base:
        raise LlmError('这个服务方没填接口地址（自建/代理时必须填）')
    if not key:
        raise LlmError('还没填 API key —— 点「🔑 AI 助手 · 填 API key」填一次')
    if provider == 'anthropic':
        body = {'model': conf['model'], 'messages': _anthropic_messages(messages),
                'max_tokens': 4096}
        if tools:
            body['tools'] = [_anthropic_tool(t) for t in tools]
        return {'url': f'{base}/v1/messages',
                'headers': {'content-type': 'application/json',
                            'x-api-key': key,
                            'anthropic-version': '2023-06-01'},
                'body': body}
    # 默认：OpenAI 兼容
    body = {'model': conf['model'], 'messages': messages}
    if tools:
        body['tools'] = tools
        body['tool_choice'] = 'auto'
    return {'url': f'{base}/chat/completions',
            'headers': {'content-type': 'application/json',
                        'authorization': f'Bearer {key}'},
            'body': body}


def _anthropic_messages(messages: list) -> list:
    """OpenAI 风格的 messages → Anthropic 的（**只做够用的转换**）。"""
    out = []
    for m in messages or ():
        role = str(m.get('role') or 'user')
        if role == 'tool':
            out.append({'role': 'user', 'content': [
                {'type': 'tool_result',
                 'tool_use_id': str(m.get('tool_call_id') or ''),
                 'content': str(m.get('content') or '')}]})
            continue
        if role == 'assistant' and m.get('tool_calls'):
            blocks = []
            if m.get('content'):
                blocks.append({'type': 'text', 'text': str(m['content'])})
            for tc in m['tool_calls']:
                fn = tc.get('function') or {}
                try:
                    args = json.loads(fn.get('arguments') or '{}')
                except ValueError:
                    args = {}
                blocks.append({'type': 'tool_use', 'id': str(tc.get('id') or ''),
                               'name': str(fn.get('name') or ''),
                               'input': args})
            out.append({'role': 'assistant', 'content': blocks})
            continue
        out.append({'role': role if role in ('user', 'assistant') else 'user',
                    'content': str(m.get('content') or '')})
    return out


def _anthropic_tool(tool: dict) -> dict:
    fn = tool.get('function') or {}
    return {'name': fn.get('name'), 'description': fn.get('description') or '',
            'input_schema': fn.get('parameters') or {'type': 'object',
                                                     'properties': {}}}


# ==================== 纯函数：回复 ====================

def parse_reply(provider: str, data: dict) -> dict:
    """
    把接口回复**规范化**成 `{'text', 'tool_calls': [{'id','name','arguments'}]}`。

    `arguments` 解析成 **dict**（模型偶尔给空串或坏 JSON —— 那不算崩，
    给 `{}` 并记一条 `note`，让上层能如实说"这次调用没有参数"）。
    """
    out = {'text': '', 'tool_calls': [], 'notes': [], 'usage': {}}
    data = data if isinstance(data, dict) else {}
    out['usage'] = data.get('usage') or {}
    if str(provider) == 'anthropic':
        for block in data.get('content') or ():
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text':
                out['text'] += str(block.get('text') or '')
            elif block.get('type') == 'tool_use':
                out['tool_calls'].append({'id': str(block.get('id') or ''),
                                          'name': str(block.get('name') or ''),
                                          'arguments': block.get('input') or {}})
        return out
    choices = data.get('choices') or []
    msg = (choices[0].get('message') if choices
           and isinstance(choices[0], dict) else {}) or {}
    out['text'] = str(msg.get('content') or '')
    for tc in msg.get('tool_calls') or ():
        fn = (tc or {}).get('function') or {}
        raw = fn.get('arguments')
        args = {}
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                args = json.loads(raw)
            except ValueError as e:
                swallow('ai.client.parse_reply.arguments', e)
                out['notes'].append(f'模型给的参数不是合法 JSON：{raw[:60]}')
        if not isinstance(args, dict):
            args = {}
        out['tool_calls'].append({'id': str((tc or {}).get('id') or ''),
                                  'name': str(fn.get('name') or ''),
                                  'arguments': args})
    return out


# ==================== 传输层（可注入）====================

def http_transport(request: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """真发一次 HTTP（**只有这里碰网络**）。"""
    body = json.dumps(request['body'], ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(request['url'], data=body,
                                 headers=request['headers'], method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')
        except Exception as e2:                    # noqa: BLE001
            swallow('ai.client.http_transport.read_error', e2)
        raise LlmError(f'接口返回 {e.code}：{detail[:200] or e.reason}',
                       status=e.code, body=detail) from e
    except urllib.error.URLError as e:
        raise LlmError(f'连不上接口：{e.reason}') from e
    try:
        return json.loads(raw)
    except ValueError as e:
        raise LlmError(f'接口返回的不是 JSON：{raw[:120]}', body=raw) from e


def chat(messages: list, tools=(), cfg=None, *, transport=None,
         timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    问一次模型 → 规范化回复（**不替调用方重试**；重试是 agent 的策略）。

    `transport(request, timeout) -> dict` 可注入（判据用假的，绝不联网）。
    """
    conf = AI.resolved(cfg or {})
    request = build_request(conf, messages, tools)
    send = transport or http_transport
    try:
        data = send(request, timeout)
    except LlmError:
        raise
    except urllib.error.HTTPError as e:             # 自定义传输层直接抛也算
        raise LlmError(f'接口返回 {e.code}：{e.reason}', status=e.code) from e
    except urllib.error.URLError as e:
        raise LlmError(f'连不上接口：{e.reason}') from e
    except ValueError as e:                         # 传输层解 JSON 失败
        raise LlmError(f'接口返回的不是 JSON：{e}') from e
    out = parse_reply(conf['provider'], data)
    out['provider'] = conf['provider']
    out['model'] = conf['model']
    return out


__all__ = ['DEFAULT_TIMEOUT', 'LlmError', 'build_request', 'chat',
           'http_transport', 'parse_reply']
