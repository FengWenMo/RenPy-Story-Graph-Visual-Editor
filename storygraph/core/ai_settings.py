"""
**用户自己的 AI 配置**（API key 存哪儿、怎么存）
=========================================================================

> 用户 2026-10-11："由于工具是本地的，所以最好的做法是让别人跟其余用户自己填
>  api key，最好打开之后能有一个地方点开，然后让用户输入 api key，
>  然后再让 ai 自行开始工作这样子"

## 三条硬规矩

1. **key 绝不进项目仓库** —— 存在**用户级**目录（与崩溃日志同一处：
   `%LOCALAPPDATA%/StoryGraph/`），可以用 `STORYGRAPH_AI_CONFIG` 覆盖
   （打包/多用户/测试都要用得上）；
2. **key 绝不进日志、不进回执、不进界面回显** —— 一律走 `masked()`；
   文件权限在 Windows 上尽力收紧（其它平台 `chmod 600`）；
3. **没有 key 就不工作**（不是"偷偷用别处的 key"）—— `is_ready()` 说清楚缺什么，
   界面照它提示，用户自己决定填不填。
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from storygraph.core.safeguard import swallow

#: 支持的提供方（**只列真的能用的**；`base_url` 可选，兼容自建/代理）
PROVIDERS = {
    'deepseek': {'label': 'DeepSeek', 'base_url': 'https://api.deepseek.com',
                 'default_model': 'deepseek-chat'},
    'openai': {'label': 'OpenAI', 'base_url': 'https://api.openai.com/v1',
               'default_model': 'gpt-4o-mini'},
    'anthropic': {'label': 'Anthropic', 'base_url': 'https://api.anthropic.com',
                  'default_model': 'claude-3-5-sonnet-latest'},
    'openai-compat': {'label': '兼容 OpenAI 的服务（自建 / 代理）',
                      'base_url': '', 'default_model': ''},
}

#: 配置文件里**允许出现**的键（别的一律不读，防止手写文件塞进奇怪东西）
_FIELDS = ('provider', 'model', 'api_key', 'base_url')


def config_path() -> Path:
    """
    用户级配置文件（`STORYGRAPH_AI_CONFIG` 可覆盖）。**不在项目里**。

    与崩溃日志同一处（`core/crashlog.py` 的约定）：Windows 用 `%LOCALAPPDATA%`，
    其它平台用 `~/.local/state`；都不行就退回用户主目录。
    """
    env = os.environ.get('STORYGRAPH_AI_CONFIG', '').strip()
    if env:
        return Path(env)
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or ''
        if base:
            return Path(base) / 'StoryGraph' / 'ai.json'
    base = os.environ.get('XDG_STATE_HOME') or ''
    root = Path(base) if base else Path.home() / '.local' / 'state'
    return root / 'storygraph' / 'ai.json'


def load(path=None) -> dict:
    """读配置（读不出来 → 空配置 + 不抛异常；**不校验 key 有没效**）。"""
    fp = Path(path) if path else config_path()
    out = {k: '' for k in _FIELDS}
    try:
        if not fp.is_file():
            return out
        data = json.loads(fp.read_text(encoding='utf-8'))
    except Exception as e:                          # noqa: BLE001 - 手写文件容错
        swallow('core.ai_settings.load', e)
        return out
    if isinstance(data, dict):
        for key in _FIELDS:
            out[key] = str(data.get(key) or '')
    return out


def save(provider: str, api_key: str, *, model: str = '', base_url: str = '',
         path=None) -> dict:
    """
    写配置（**只写用户级文件**，并尽力收紧权限）。返回 `{'ok', 'path', 'reason'}`。

    ⚠ 返回值里**绝不含 key 本身** —— 回执会被打印、被模型读到、被贴进 issue。
    """
    fp = Path(path) if path else config_path()
    if not str(provider or '').strip():
        return {'ok': False, 'path': str(fp), 'reason': '没选服务方'}
    if not str(api_key or '').strip():
        return {'ok': False, 'path': str(fp),
                'reason': 'API key 是空的 —— 没填就不保存（免得存了个空壳）'}
    payload = {'provider': str(provider).strip(), 'api_key': str(api_key).strip(),
               'model': str(model or '').strip(),
               'base_url': str(base_url or '').strip()}
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                      encoding='utf-8')
        _tighten(fp)
    except OSError as e:
        swallow('core.ai_settings.save', e)
        return {'ok': False, 'path': str(fp), 'reason': f'写不进去：{e}'}
    return {'ok': True, 'path': str(fp), 'reason': ''}


def _tighten(fp: Path) -> None:
    """尽量只让当前用户能读（Windows 上尽力而为，失败不算错）。"""
    try:
        os.chmod(fp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:                            # noqa: BLE001 - 平台差异
        swallow('core.ai_settings.tighten', e)


def masked(key: str) -> str:
    """
    给界面/日志看的**脱敏**形式：`sk-abc…wxyz`（短到没法还原）。

    ⚠ 任何要显示 key 的地方都必须过这一个（否则 key 会进截图、进日志、进 issue）。
    """
    text = str(key or '')
    if not text:
        return ''
    if len(text) <= 8:
        return '•' * len(text)
    return f'{text[:4]}…{text[-4:]}（共 {len(text)} 位）'


def is_ready(cfg: dict) -> tuple:
    """`(能不能用, 缺什么)` —— 界面照它提示用户，**不替用户猜**。"""
    cfg = cfg or {}
    if not str(cfg.get('provider') or '').strip():
        return False, '还没选服务方（DeepSeek / OpenAI / …）'
    if not str(cfg.get('api_key') or '').strip():
        return False, '还没填 API key —— 点「填 API key」填一次即可（存在本机）'
    return True, ''


def resolved(cfg: dict) -> dict:
    """把配置补全成"能直接拿去请求"的样子（model/base_url 缺就取默认）。"""
    cfg = cfg or {}
    name = str(cfg.get('provider') or '').strip()
    spec = PROVIDERS.get(name) or {}
    return {'provider': name,
            'api_key': str(cfg.get('api_key') or '').strip(),
            'model': str(cfg.get('model') or '').strip()
            or spec.get('default_model', ''),
            'base_url': str(cfg.get('base_url') or '').strip()
            or spec.get('base_url', '')}


__all__ = ['PROVIDERS', 'config_path', 'is_ready', 'load', 'masked',
           'resolved', 'save']
