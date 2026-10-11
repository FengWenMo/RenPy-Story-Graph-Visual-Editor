"""
**程序侧的钩子**：让程序用"一行/一个函数"把工具接上
=========================================================================

> 用户 2026-10-11 定的原则：
> "工具的价值不在于'不依赖程序'，而在于**'依赖程序时成本最低'**。
>  程序写一行 CDS 注册，工具自动派生声明；**程序写一个验证函数，工具自动调用**。
>  这才是团队协作该有的效率。"

`cds_registry` 已经做到"扫 CDS 代码 → 自动派生声明"。
这一份负责**第二条**：程序写一个**验证函数**，工具**自动找到并调用**它。

## 约定（程序侧要做的全部事情）

在项目根（或 `game/`）放一个 `storygraph.host.py`，里面**按需**定义两个函数：

```python
def declarations():
    '''返回额外的声明（形状同 storygraph.rules.json 的 statements 段）。'''
    return [{'name': 'weather', 'label': '天气', 'columns': [...]}]

def validate_statement(head, text):
    '''这个写法这一行长什么样合不合法？

    返回 (True, '')            → 我认它，没问题
    返回 (False, '为什么')      → 我认它，但这一行不对
    返回 None                  → 我不管这个（工具自己判断）
    '''
    if head == 'weather':
        return ('强度' in text), '天气必须写强度'
    return None
```

**工具侧**：`load_host_hooks()` 找文件 → 安全 exec → 暴露 `available` /
`declarations()` / `validate(head, text)`。**程序不给，就什么都不发生**（零成本）。

## 三条自我约束

  ① **程序不给就不发生**：没有 `storygraph.host.py` → `available=False`，
     工具行为与以前**一模一样**（不许多一分依赖）；
  ② **它写的代码不许弄坏工具**：exec 里抛异常一律吞掉并记下来，
     `declarations()`/`validate()` 抛异常也只记不炸（"依赖程序"不能变成"被程序拖死"）；
  ③ **它说什么要能看见**：`notes` 记下"钩子说了什么/哪一步失败了"，
     报告里要如实转述（否则人不知道结论是谁给的）。
"""

from __future__ import annotations

from pathlib import Path

from storygraph.core.safeguard import swallow

#: 程序侧钩子的文件名（项目根或 `game/` 下，二者取先找到的）
HOST_HOOK_NAMES = ('storygraph.host.py',)

#: 工具会调用的三个入口（其余不管）
HOST_API = ('declarations', 'validate_statement')


class HostHooks:
    """程序提供的钩子（没有就是 `available=False` 的空壳）。"""

    def __init__(self, available=False, path='', notes=None, decls=None,
                 validate=None):
        self.available = available
        self.path = path
        self.notes = list(notes or [])
        self._decls = decls
        self._validate = validate

    # ---- 工具侧的调用口 ----
    def declarations(self) -> list:
        """程序额外声明的语句（读不出来 → 空表 + 记一笔）。"""
        if self._decls is None:
            return []
        try:
            got = self._decls()
        except Exception as e:                      # noqa: BLE001 - 程序代码不许炸工具
            swallow('story.host_hooks.declarations', e)
            self.notes.append(f'程序的 declarations() 抛异常：'
                              f'{type(e).__name__}: {e}')
            return []
        if not isinstance(got, (list, tuple)):
            self.notes.append('程序的 declarations() 没返回列表 —— 已忽略')
            return []
        rows = [r for r in got if isinstance(r, dict) and r.get('name')]
        if len(rows) != len(got):
            self.notes.append(f'程序的 declarations() 里有 {len(got) - len(rows)} '
                              f'条格式不对，已跳过')
        return rows

    def validate(self, head: str, text: str):
        """
        调程序的验证函数。返回 `(True|False|None, 原话)`。

        `None` = **程序不管这个**（工具自己判断）—— 与"程序说没问题"是两件事，
        所以必须区分（否则会假装所有写法都被程序验过了）。
        """
        if self._validate is None:
            return None, ''
        try:
            got = self._validate(str(head or ''), str(text or ''))
        except Exception as e:                      # noqa: BLE001
            swallow('story.host_hooks.validate', e)
            self.notes.append(f'程序的 validate_statement("{head}") 抛异常：'
                              f'{type(e).__name__}: {e}')
            return None, f'（程序的验证函数抛异常：{type(e).__name__}）'
        if got is None:
            return None, ''
        if isinstance(got, tuple) and len(got) >= 1:
            ok = got[0]
            why = str(got[1]) if len(got) > 1 else ''
            return (None if ok is None else bool(ok)), why
        return bool(got), ''


def find_host_hook(project_root) -> Path:
    """项目根或 `game/` 下的 `storygraph.host.py`（没有 → `None`）。"""
    root = Path(project_root) if project_root else None
    if root is None:
        return None
    for base in (root, root / 'game'):
        for name in HOST_HOOK_NAMES:
            fp = base / name
            try:
                if fp.is_file():
                    return fp
            except OSError as e:                     # noqa: BLE001
                swallow('story.host_hooks.find', e)
    return None


def load_host_hooks(project_root) -> HostHooks:
    """
    读程序的钩子（**没有就返回空壳**；程序代码报错也只记不炸）。

    ⚠ 这是本工具**唯一**会执行"项目里的代码"的地方 —— 所以：
      · 只认**固定文件名** `storygraph.host.py`（不是"扫到 .py 就跑"）；
      · `exec` 的命名空间**不含工具内部对象**（给不了它什么，也就弄不坏）；
      · 任何异常都进 `notes`，报告里看得见。
    """
    fp = find_host_hook(project_root)
    if fp is None:
        return HostHooks()
    try:
        src = fp.read_text(encoding='utf-8', errors='replace')
    except OSError as e:                            # noqa: BLE001
        swallow('story.host_hooks.read', e)
        return HostHooks(notes=[f'读不动程序的钩子 {fp.name}：{e}'])
    ns: dict = {'__name__': 'storygraph_host', '__file__': str(fp)}
    notes = []
    try:
        exec(compile(src, str(fp), 'exec'), ns)     # noqa: S102 - 这是约定好的入口
    except Exception as e:                          # noqa: BLE001
        swallow('story.host_hooks.exec', e)
        return HostHooks(notes=[f'程序的钩子 {fp.name} 没跑起来：'
                                f'{type(e).__name__}: {e}'])
    missing = [n for n in HOST_API if not callable(ns.get(n))]
    if missing:
        notes.append(f'程序的钩子有 {missing} 没定义（那部分自动跳过）')
    return HostHooks(available=True, path=str(fp), notes=notes,
                     decls=ns.get('declarations'),
                     validate=ns.get('validate_statement'))


__all__ = ['HOST_API', 'HOST_HOOK_NAMES', 'HostHooks', 'find_host_hook',
           'load_host_hooks']
