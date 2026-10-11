"""
**MCP server**（stdio / JSON-RPC 2.0）—— 让别人家的 AI 客户端连上本工具
=========================================================================

> 用户 2026-10-11："先 2 吧【写真正的 MCP server】……由于工具是本地的，
>  所以最好的做法是让别人跟其余用户自己填 api key"

## 两个身份，别混

| 谁 | 谁管 key | 说明 |
|---|---|---|
| **MCP 客户端**（Claude Desktop / 各家 IDE / 你自己的 agent） | **它自己** | 它连上本 server，用**它的**模型与 key 干活 |
| **本工具里的「AI 助手」**（下一步做） | **本工具**（用户填，存在用户级目录） | 用户在界面里填 key，我们自己跑 agent |

⇒ 所以这个 server **完全不需要 key**，也不需要联网 —— 它只是把
`mcp_surface` 那些函数按 MCP 协议暴露出去。**这是最省事、也最尊重用户的做法**：
想用谁家的模型，用户自己在那里填。

## 协议范围（只做必要的，做扎实）

  · `initialize` → 回协议版本 + server 信息 + `tools` 能力；
  · `tools/list` → 工具清单（**名称/描述/入参 schema 从 `mcp_surface.capabilities()`
    与一张 schema 表来**，不另写一份，避免漂移）；
  · `tools/call` → 调 `mcp_surface` 里的对应函数，返回 MCP 的
    `{content:[{type:'text', text:JSON}]}`；
  · `notifications/*` → 一律忽略（协议要求不回）。
  · 任何异常都变成 **JSON-RPC error**，**绝不让 server 崩**（崩了客户端只会看到断线）。

## 怎么接（给用户的一句话）

在客户端的 MCP 配置里加：

```json
{"mcpServers": {"storygraph": {
    "command": "python", "args": ["-m", "storygraph.mcp.server"],
    "env": {"STORYGRAPH_PROJECT": "D:/你的项目"}}}}
```

⚠ `STORYGRAPH_PROJECT` 给了就默认操作那个项目；不给就要求每次调用带
`project_root`（**不猜用户的目录**）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 本 server 的说法（协议版本按 MCP 的日期式版本号）
PROTOCOL_VERSION = '2024-11-05'
SERVER_INFO = {'name': 'storygraph', 'version': '1.0.0'}

#: 工具入参 schema（**唯一真源**：工具的"存在性/描述/边界"来自
#: `mcp_surface.capabilities()`，这里只补 JSON Schema —— 不重复写描述）。
_SCHEMAS = {
    'scan_unknown': {'type': 'object', 'properties': {}, 'required': []},
    'discover': {'type': 'object', 'properties': {}, 'required': []},
    'engine_facts': {'type': 'object', 'properties': {}, 'required': []},
    'registry_check': {'type': 'object', 'properties': {
        'heads': {'type': 'array', 'items': {'type': 'string'}}},
        'required': []},
    'ask_engine': {'type': 'object', 'properties': {
        'heads': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['heads']},
    'verify_behavior': {'type': 'object', 'properties': {
        'head': {'type': 'string'}}, 'required': ['head']},
    'host_validate': {'type': 'object', 'properties': {
        'head': {'type': 'string'}, 'text': {'type': 'string'}},
        'required': ['head', 'text']},
    'health': {'type': 'object', 'properties': {}, 'required': []},
    'project_context': {'type': 'object', 'properties': {}, 'required': []},
    'declare': {'type': 'object', 'properties': {
        'name': {'type': 'string'}, 'label': {'type': 'string'},
        'docs': {'type': 'string'},
        'dry_run': {'type': 'boolean',
                    'description': 'true = 只试算给 diff、不写盘（建议先这样）'}},
        'required': ['name']},
    'table_export': {'type': 'object', 'properties': {}, 'required': []},
}

#: 不需要项目根的工具（其余的一律要）
_NO_PROJECT = frozenset({'capabilities'})


def _project_root(args: dict) -> str:
    """
    这次调用操作哪个项目：**调用参数优先，其次环境变量，最后空**。

    ⚠ **不猜**（不默认当前目录）—— 猜错项目去写盘是灾难。
    """
    return str((args or {}).get('project_root')
               or os.environ.get('STORYGRAPH_PROJECT') or '')


def tools_list() -> list:
    """MCP 的 `tools/list` 结果（**描述与边界直接取 `capabilities()`**）。"""
    from storygraph import mcp_surface as M
    caps = {t['name'].replace('storygraph_', ''): t
            for t in M.capabilities()['tools']}
    out = []
    for name, schema in _SCHEMAS.items():
        meta = caps.get(name) or {}
        if not callable(getattr(M, name, None)):
            continue                                 # 没实现的**不列**（免得客户端调空）
        props = dict(schema.get('properties') or {})
        if name not in _NO_PROJECT:
            props['project_root'] = {
                'type': 'string',
                'description': ('项目根目录（含 game/ 的那个）。'
                                '留空则用环境变量 STORYGRAPH_PROJECT')}
        desc = str(meta.get('summary') or name)
        limits = meta.get('known_limits') or []
        if limits:
            desc += '\n⚠ 已知边界：' + '；'.join(limits)
        if meta.get('status') == 'planned':
            desc += '\n⚠ **这个工具还没实现**（调它会返回 ok=false）'
        out.append({'name': f'storygraph_{name}',
                    'description': desc,
                    'inputSchema': {'type': 'object', 'properties': props,
                                    'required': list(schema.get('required') or [])}})
    return out


def call_tool(name: str, args: dict) -> dict:
    """
    调一个工具 → MCP 的 `tools/call` 结果。

    ⚠ 工具自己的 `{ok:false, reason}` **不算协议错误**（照常返回文本），
    只有"工具名不认识 / 参数结构不对"才是 JSON-RPC error ——
    这样模型能读到我们如实写的 `reason`（那正是最有用的一句话）。
    """
    from storygraph import mcp_surface as M
    short = str(name or '').replace('storygraph_', '')
    fn = getattr(M, short, None)
    if short not in _SCHEMAS or not callable(fn):
        raise ValueError(f'没有这个工具：{name}')
    args = dict(args or {})
    root = _project_root(args)
    args.pop('project_root', None)
    if short not in _NO_PROJECT and not root:
        got = {'ok': False, 'source': 'none',
               'reason': '没给项目根：请在参数里给 project_root，'
                         '或设环境变量 STORYGRAPH_PROJECT'}
    else:
        got = fn(root, **args) if short not in _NO_PROJECT else fn(**args)
    text = json.dumps(got, ensure_ascii=False, indent=2, default=str)
    return {'content': [{'type': 'text', 'text': text}],
            'isError': not bool(got.get('ok', True))}


def handle(msg: dict) -> dict:
    """
    处理一条 JSON-RPC 消息 → 回一条（`notifications/*` 返回 `{}` = 不回）。

    纯函数（不碰stdin/stdout）—— 所以判据可以把它当普通函数测。
    """
    mid = (msg or {}).get('id')
    method = str((msg or {}).get('method') or '')

    def ok(result):
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def err(code, message):
        return {'jsonrpc': '2.0', 'id': mid,
                'error': {'code': code, 'message': str(message)}}

    if method.startswith('notifications/'):
        return {}
    try:
        if method == 'initialize':
            return ok({'protocolVersion': PROTOCOL_VERSION,
                       'capabilities': {'tools': {'listChanged': False}},
                       'serverInfo': SERVER_INFO})
        if method in ('tools/list', 'list_tools'):
            return ok({'tools': tools_list()})
        if method in ('tools/call', 'call_tool'):
            params = (msg or {}).get('params') or {}
            return ok(call_tool(params.get('name'), params.get('arguments') or {}))
        if method in ('ping',):
            return ok({})
        return err(-32601, f'不支持的方法：{method}')
    except TypeError as e:                           # 参数不对
        return err(-32602, f'参数不对：{e}')
    except ValueError as e:                          # 工具名不对
        return err(-32602, str(e))
    except Exception as e:                           # noqa: BLE001 - server 绝不许崩
        swallow('mcp.server.handle', e)
        return err(-32603, f'内部错误：{type(e).__name__}: {e}')


def serve(stdin=None, stdout=None) -> int:
    """
    stdio 主循环：**一行一条 JSON**（MCP 的 stdio 传输）。

    退出条件：stdin 结束。任何消息出错都回 error，且**继续读**（崩了客户端只会断线）。
    """
    fin = stdin or sys.stdin
    fout = stdout or sys.stdout
    for line in fin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {'jsonrpc': '2.0', 'id': None,
                     'error': {'code': -32700, 'message': 'JSON 解析失败'}}
        else:
            reply = handle(msg)
        if not reply:
            continue
        try:
            fout.write(json.dumps(reply, ensure_ascii=False) + '\n')
            fout.flush()
        except OSError as e:                         # 客户端断了
            swallow('mcp.server.serve.write', e)
            return 0
    return 0


def main(argv=None) -> int:
    """`python -m storygraph.mcp.server`（stdio）。"""
    if os.environ.get('STORYGRAPH_MCP_DEBUG'):
        sys.stderr.write(f'storygraph MCP server 已启动（stdio）\n')
    return serve()


if __name__ == '__main__':
    raise SystemExit(main())
