# -*- coding: utf-8 -*-
"""
storygraph.core.user_prefs
==========================
**用户级偏好文件**（`settings.json`）：**写盘路上就要用到**的那些开关。

和三处别混（各自管一件事，混在一起就会互相踩）：

  · `ui_qt/prefs.py`（`QSettings`）= **视图状态**：折叠了哪些、资源底色。
    只有界面读，而且它在注册表里 —— core 层既不该也不该为了读一个开关去碰 Qt。
  · **这里** = **策略**：脚本的旧版本留多久。它必须在**没有 Qt** 的时候也读得到，
    因为写 `.rpy` 的不只是窗口：CLI、批处理、引擎试玩都会走
    `story.paths.write_text_atomic` → `story.version_store.snapshot`。
  · 项目里的 `.storygraph/` = **跟着这个项目走**的东西（历史版本本身）。

位置与 `session.json` / `ai.json` 同一个用户级目录（`core/user_path.py`），
`STORYGRAPH_SETTINGS` 可以覆盖（测试、便携版）。

## 一个出口

    read()                  → dict           读整份；缺 / 坏 / 不是对象 → {}
    read_state()            → (dict, 状态)   还要分清"没设置过"和"坏了"时用它
    update(name, patch)     → dict           锁里读 → 只改这一节 → 原子写

**写只有 `update`**：它只覆盖你给的键，文件里别的键、别的节原样留着 ——
"两个窗口同时存"才不会互相抹掉（与 `statement_registry._edit_manifest`
同一条规矩）。`patch` 里**值为 `None` = 删掉那个键**（"取消自定义"就靠它）。

## 读不懂那份文件时

**不覆盖**（`CorruptSettings`）：这是一份人也会手改的文件，覆盖等于删掉
别人的内容 —— 与 `core/session_store.remember_cards` 拒写的那条理由一样。
读路径上则一律当"没有设置"（用默认值）：**偏好坏了最坏是"这次用默认"，
绝不能因为它把写盘拦下来**。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from storygraph.core import atomicio
from storygraph.core.filelock import DEFAULT_TIMEOUT, file_lock
from storygraph.core.user_path import app_data_dir

#: 文件名（与 `session.json` / `ai.json` 同住 `%LOCALAPPDATA%/StoryGraph/`）
FILE_NAME = 'settings.json'
#: 位置覆盖（测试 / 便携版）；**只影响位置，不影响内容格式**
ENV_CONFIG = 'STORYGRAPH_SETTINGS'

#: 文件里的键按这个顺序写（人要看，别按字母乱序）
_TOP_KEYS = ('backups',)


class CorruptSettings(ValueError):
    """这份设置文件读不懂（JSON 坏了 / 不是对象）—— **不许**拿它当白纸覆盖。"""

    def __init__(self, path):
        self.path = str(path)
        super().__init__(f'设置文件读不懂（或读不出来）：{self.path}')


def path() -> Path:
    """设置文件在哪儿（**不建目录**：读路径不该有副作用）。"""
    override = str(os.environ.get(ENV_CONFIG) or '').strip()
    if override:
        return Path(override)
    return app_data_dir(FILE_NAME)


def _load(target: Path):
    """
    整个文件 → `(数据, 状态)`，状态 ∈ `{'ok', 'missing', 'corrupt'}`。

    单独一个函数是因为"文件不存在"和"文件坏了"必须分开：前者是正常的第一次
    使用（可以写），后者是**别人写的东西**（写就是覆盖）。
    """
    try:
        raw = Path(target).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}, 'missing'
    except OSError:
        return {}, 'corrupt'
    except UnicodeDecodeError:
        return {}, 'corrupt'
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}, 'corrupt'
    if not isinstance(data, dict):
        return {}, 'corrupt'
    return data, 'ok'


def read() -> dict:
    """读整份 → `dict`。缺 / 坏 / 读不出来 / 不是 JSON 对象 → `{}`（**不抛**）。"""
    return _load(path())[0]


def read_state():
    """
    `(整份数据, 状态)`，状态 ∈ `{'ok', 'missing', 'corrupt'}`。

    调用方要区分"没设置过"和"设置坏了"时用它（`read()` 把两者都当 `{}`）；
    一次读拿到两样东西，别为了状态再读一遍文件。
    """
    return _load(path())


def update(name, patch, *, lock_timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    锁里读 → 只改 `name` 这一节 → 原子写；返回这一节的新内容。

    `patch` 里**值为 `None` = 删掉那个键**（"不再自定义、跟档位走"）。
    别的节、别人新加的顶层键**原样写回**。文件坏了 → `CorruptSettings`
    （一个字都不写）。
    """
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(target, timeout=lock_timeout):
        data, state = _load(target)          # 锁里重读：拿最新的那份
        if state == 'corrupt':
            raise CorruptSettings(target)
        merged = _merge(data.get(name), patch)
        data[name] = merged
        atomicio.write_text_atomic(target, _dumps(data))
    return merged


def _merge(current, patch) -> dict:
    """`current` 是这一节现在的值（可能是垃圾）→ 合上 `patch` 之后的新值。"""
    out = dict(current) if isinstance(current, dict) else {}
    for key, value in dict(patch or {}).items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def _dumps(data: dict) -> str:
    """按 `_TOP_KEYS` 的顺序写（其余键按字母），缩进 1，带末尾换行。"""
    ordered = {k: data[k] for k in _TOP_KEYS if k in data}
    for key in sorted(k for k in data if k not in ordered):
        ordered[key] = data[key]
    return json.dumps(ordered, ensure_ascii=False, indent=1) + '\n'


__all__ = ['CorruptSettings', 'ENV_CONFIG', 'FILE_NAME', 'path', 'read',
           'read_state', 'update']
