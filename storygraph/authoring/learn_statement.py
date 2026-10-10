"""
自定义语句：表单 → 声明（**纯函数**，UI/CLI/测试共用）
=======================================================

用户在 2026-10-10 报的那个缺口：

> 你说支持自适应的解析……但是我并没有找到一个入口可以教这个工具学会
> 我自定义的项目模块……至于什么 `swim_suit` 一类的貌似无法做到呢……
> 至少我想要个能够自定义的模块入口放在最上方

后端其实一直是齐的（`declare_statement()` 是"教一次"的唯一写入口，
CLI 的 `learn` 在用它），缺的是**发现 + 入口**：

    `discover_undeclared()`（扫项目找出还不认识的写法）
        → 弹窗（勾选 / 填参数）→ `build_declaration()`（本模块，纯）
        → `declare_statement()`（写 `storygraph.rules.json`）
        → `clear_cache()` → 表里立刻多出参数列与候选值

## 本模块只做"表单 → 后端参数"这一步

为什么值得单独一个纯函数：**表单数据最脏**（用户手输的参数名可能是
`intensity=3`、`强度 intensity`、带空格、带中文……），而 `declare_statement`
的契约是"坏条目跳过"—— 若在这里不校验，用户会看到"成功了"但表里没有那一列。
所以这里把**人怎么写都行**的输入规整成后端要的形状，并**报出**丢弃了什么。
"""

from __future__ import annotations

import re

#: 参数名必须是合法标识符（Ren'Py 关键字参数的名字就是标识符）
_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
#: 语句名：允许**空格分隔的多个词**（与 CDS 的 `name="a b"` 同形，后端会拆成别名）
_STMT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\s+[A-Za-z_][A-Za-z0-9_]*)*$')
#: 候选值分隔符：`,，、;；|` 与空白
_CHOICE_SPLIT_RE = re.compile(r'[,，、;；|\s]+')


def parse_choices(text) -> list:
    """
    候选值那一格 → 列表（`1,2,3` / `1，2 / 低 中 高` 都认）。

    用户口径（`spec_arg_choices` 那条契约）：**没声明就返回空** ——
    所以这里也不猜：认不出分隔符就当**一个**候选值，绝不擅自切。
    """
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        parts = [str(x) for x in text]
    else:
        parts = _CHOICE_SPLIT_RE.split(str(text))
    out = []
    for raw in parts:
        item = str(raw).strip()
        if item and item not in out:
            out.append(item)
    return out


def build_declaration(name: str, label: str = '', docs: str = '',
                      block=None, params=None) -> dict:
    """
    表单 → `declare_statement` 的参数（**纯函数**）。

    `params` 是 `[{'name','label','choices'}]`（UI 表格的行），
    `choices` 可以是字符串（`'1,2,3'`）或列表。

    返回 `{'ok', 'name', 'label', 'docs', 'block', 'columns', 'warnings'}`：

      · `ok=False` 只在**语句名**不合法时（那是硬错误，写下去会污染清单）；
      · 参数行里的坏名字（`intensity=3`、中文名…）**丢掉并在 `warnings` 里报**
        —— 后端"坏条目跳过"是静默的，这里必须出声，否则用户以为加上了；
      · `name` 允许 `a b` 这种多词写法（后端会拆成别名）。
    """
    name = str(name or '').strip()
    warnings = []
    if not name:
        return {'ok': False, 'name': '', 'label': '', 'docs': '',
                'block': block, 'columns': [],
                'warnings': ['语句名不能为空']}
    if not _STMT_RE.match(name):
        return {'ok': False, 'name': name, 'label': '', 'docs': '',
                'block': block, 'columns': [],
                'warnings': [f'"{name}" 不是合法的语句名（只能是字母/数字/下划线，'
                             f'多个词用空格分隔）']}
    columns = []
    seen = set()
    for row in (params or ()):
        if not isinstance(row, dict):
            continue
        cname = str(row.get('name') or '').strip()
        # 容忍"顺手把等号也写进去"：`intensity=3` / `intensity = 3`
        if '=' in cname:
            cname = cname.split('=', 1)[0].strip()
            warnings.append(f'参数名里带了 `=`：已按 "{cname}" 处理')
        if not cname:
            continue
        if not _NAME_RE.match(cname):
            warnings.append(f'参数名 "{cname}" 不是合法标识符 —— 这一行**没写进**'
                            f'声明（表里不会出现这一列）')
            continue
        if cname in seen:
            warnings.append(f'参数 "{cname}" 写了两次 —— 只保留第一次')
            continue
        seen.add(cname)
        col = {'name': cname}
        clabel = str(row.get('label') or '').strip()
        if clabel:
            col['label'] = clabel
        choices = parse_choices(row.get('choices'))
        if choices:
            col['choices'] = choices
        columns.append(col)
    if block is None:
        block = None                    # 后端默认 `possible`：不确定就别写死
    return {'ok': True, 'name': name, 'label': str(label or '').strip(),
            'docs': str(docs or '').strip(), 'block': block,
            'columns': columns, 'warnings': warnings}


def declaration_preview(built: dict, path: str = '') -> str:
    """给人看的"这会写什么"（写盘前/后的确认文本）。"""
    if not built.get('ok'):
        return '（没写：' + '；'.join(built.get('warnings') or ['?']) + '）'
    lines = [f'语句：{built["name"]}']
    if built.get('label'):
        lines.append(f'  中文名：{built["label"]}')
    if built.get('docs'):
        lines.append(f'  说明：{built["docs"]}')
    if built.get('block'):
        lines.append('  带块：是（块体逐字保真）')
    elif built.get('block') is False:
        lines.append('  带块：否')
    else:
        lines.append('  带块：未指定（后端按 possible 处理）')
    cols = built.get('columns') or []
    if cols:
        lines.append(f'  参数 {len(cols)} 个：')
        for c in cols:
            ch = '，'.join(c.get('choices') or [])
            lines.append(f'    · {c["name"]}'
                         + (f'（{c["label"]}）' if c.get('label') else '')
                         + (f' 候选：{ch}' if ch else ''))
    else:
        lines.append('  参数：无（只逐字透传）')
    if built.get('warnings'):
        lines.append('⚠ ' + '；'.join(built['warnings']))
    if path:
        lines.append(f'  写进：{path}')
    return '\n'.join(lines)


__all__ = ['build_declaration', 'declaration_preview', 'parse_choices']
