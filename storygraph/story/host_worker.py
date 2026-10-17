"""
故事图工具的**宿主钩子子进程**
================================

由 `host_hooks.load_host_hooks()` 用 `python -I` 启动，**唯一**会执行项目
`storygraph.host.py` 的地方。协议极简：stdin / stdout 每行一个 JSON。

请求：

    {"id": 1, "op": "declarations"}
    {"id": 2, "op": "validate", "head": "weather", "text": "weather rain"}

响应：

    {"id": 1, "ok": true, "value": [...]}
    {"id": 2, "ok": false, "error": "ValueError: ..."}

启动握手（无 id）：

    {"op": "ready", "ok": true}
    {"op": "ready", "ok": false, "error": "...", "trace": "..."}

三条纪律
--------
  ① **项目代码的 print 不许污染协议**：加载前把 `sys.stdout` 换成 `stderr`，
     协议只走启动时抓住的 `stdout.buffer`；
  ② **项目代码抛什么都不能带崩工具**：每个请求单独 try，异常原样回给父进程；
  ③ **stdin 关了就走**（父进程退出/被杀时管道关闭）—— 不留孤儿进程。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback


def _send(out, obj) -> None:
    """写一行 JSON（UTF-8，不转义中文）。"""
    line = json.dumps(obj, ensure_ascii=False, default=str) + '\n'
    out.write(line.encode('utf-8'))
    out.flush()


def _err_text(exc: BaseException) -> str:
    return f'{type(exc).__name__}: {exc}'


def _load_module(path: str):
    """按文件路径加载项目钩子（模块名固定，不碰 sys.path）。"""
    spec = importlib.util.spec_from_file_location('storygraph_host', path)
    if spec is None or spec.loader is None:
        raise ImportError(f'加载不了 {path}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['storygraph_host'] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv) -> int:
    if len(argv) < 2:
        _send(sys.stdout.buffer, {'op': 'ready', 'ok': False,
                                  'error': '用法：host_worker.py <hook.py>'})
        return 2

    protocol_out = sys.stdout.buffer
    # 项目代码的 print / sys.stdout.write 全部改道到 stderr（父进程只留尾部）。
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError) as exc:
        sys.stderr.write(f'host_worker: stderr 设不成 UTF-8：{exc}\n')
    sys.stdout = sys.stderr

    hook_path = argv[1]
    try:
        module = _load_module(hook_path)
    except BaseException as exc:                        # noqa: BLE001 - 项目代码的错
        _send(protocol_out, {
            'op': 'ready', 'ok': False, 'error': _err_text(exc),
            'trace': traceback.format_exc(limit=4)[-2000:],
        })
        return 1

    _send(protocol_out, {'op': 'ready', 'ok': True})

    for raw in sys.stdin.buffer:
        try:
            req = json.loads(raw.decode('utf-8', 'replace'))
        except Exception as exc:                        # noqa: BLE001 - 坏行就跳过
            sys.stderr.write(f'host_worker: 忽略坏请求行：{exc}\n')
            continue
        if not isinstance(req, dict):
            continue
        rid = req.get('id')
        op = req.get('op')
        try:
            if op == 'declarations':
                fn = getattr(module, 'declarations', None)
                value = fn() if callable(fn) else None
            elif op == 'validate':
                fn = getattr(module, 'validate_statement', None)
                value = (fn(str(req.get('head') or ''), str(req.get('text') or ''))
                         if callable(fn) else None)
            elif op == 'ping':
                value = 'pong'
            else:
                raise ValueError(f'未知操作：{op!r}')
            _send(protocol_out, {'id': rid, 'ok': True, 'value': value})
        except BaseException as exc:                    # noqa: BLE001 - 不许带崩工具
            _send(protocol_out, {'id': rid, 'ok': False,
                                 'error': _err_text(exc)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
