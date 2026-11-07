"""
引擎命令的**兼容阶梯**：从"最全"退到"最简"
==========================================

> 2026-11-05（L1 加固）。用户原话：「如果引擎读不到或者版本不对应的话，尝试一下
> 兼容地读取，如果还是失败……不要弹窗，直接在控制台界面说失败，回退回静态解析就好。」

## 为什么需要阶梯

`--json-dump` / `--error-code` 这两面旗**不是所有版本都认**：

| 旗 | 存在条件 | 认不出的表现 |
|---|---|---|
| `--json-dump` | Ren'Py 7.4+ / 8.x | `unrecognized arguments: --json-dump`（立刻退出） |
| `--error-code` | 8.0+ | 同上 |

而工具**没有**"按版本精确选旗"的把握（版本本身经常探不到：`vc_version.py`
写的可能是 `8.5.3.26051504`，也可能被 fork 改掉）。所以做法是**试**：

    ① lint --error-code --json-dump <path>   （全：诊断 + 清单 + 退出码）
    ② lint                                    （简：只要诊断）

一档跑不动就退下一档；两档都跑不动 = 这次真的采不到（交给退化链）。

## ⚠ 为什么第二档是 `lint` 而不是 `--lint`

Ren'Py 的 lint 是**子命令**（`renpy.py <项目> lint`），不是旗。写成
`--lint` 会得到 `unrecognized arguments`，白白浪费一档。

## 这一层只出**命令**，不跑

保持纯函数（`engine/` 只许 stdlib，也不该碰 `subprocess`）：
真正执行在 `pipeline/engine_collect.py`，那里才知道 SDK 与超时。
"""

from __future__ import annotations

#: 输出里出现这些词 = **引擎不认这面旗**（不是"项目有错"）
#: ⚠ 判据是"不认旗"，不是"退出码非 0"：项目有语法错时 exit 1 是**正常**的，
#: 那种情况绝对不能触发重试（重试也白跑，还多花一轮时间）。
_REJECT_HINTS = (
    'unrecognized arguments',
    'no such option',
    'invalid option',
    'unknown option',
    'error: argument',
)


def reject_reason(text) -> str:
    """这段输出像不像"引擎不认这面旗"？是 → 命中的那个词；否 → 空串。"""
    low = str(text or '').lower()
    for hint in _REJECT_HINTS:
        if hint in low:
            return hint
    return ''


def lint_ladder(version=None, dump_path: str = '') -> list:
    """
    按"从全到简"排好的命令阶梯（每一项都是 `run_engine` 的 `args`）。

    · 给了 `dump_path` 且**版本看起来够新**（或版本未知）→ 第一档带
      `--json-dump`；
    · 第二档永远是最保守的 `['lint']`；
    · `version` 明显太老（`< 7.4`）→ **跳过**带 dump 的第一档（省一轮）。

    `version` 收的是 `engine.detect.EngineVersion`（也接受 `None`）。
    """
    full = ['lint', '--error-code']
    if dump_path:
        full += ['--json-dump', str(dump_path)]
    bare = ['lint']
    if version is None:
        return [full, bare]              # 版本未知 → 试一次全的，不行退简的
    try:
        too_old = not version.at_least(7, 4)
    except AttributeError:               # 传了个字符串之类的 → 当未知处理
        too_old = False
    return [bare] if too_old else [full, bare]


def describe_ladder(ladder) -> str:
    """一行说明（写日志用）：`lint --error-code --json-dump … → lint`。"""
    rows = [' '.join(str(a) for a in (argv or ())) for argv in (ladder or ())]
    return ' → '.join(rows) if rows else '（空）'


__all__ = ['describe_ladder', 'lint_ladder', 'reject_reason']
