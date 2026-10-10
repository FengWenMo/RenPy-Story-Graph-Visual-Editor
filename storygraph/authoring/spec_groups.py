"""
**词组分栏**（声明驱动，纯函数）
=================================

用户 2026-10-10：

> 实际上需要分组显示……但因为我这个要求通用！所以一般人其实是没有服装这个
> 选项的！所以事实上应当通过 B 这个自定义模块，做到把服装跟表情这种东西分开！

所以这里**不写死"服装/表情"** —— 项目自己在 `storygraph.rules.json` 里声明
"有哪几组词"，工具只负责按声明分栏。没有声明时，一切与从前**完全一样**
（一个平铺列表），这条是本模块最重要的性质。

## 声明的形状

```json
{
  "attr_groups": [
    {"name": "outfit", "label": "服装",
     "options": ["swim_suit", "uniform", "casual"]},
    {"name": "expression", "label": "表情",
     "options": ["happy", "calm", "sad"]},
    {"name": "rest", "label": "其他"}          ← 没写 options = 兜底组
  ]
}
```

## 归属规则（能解释、可预期；**不猜**）

1. **显式命中**：候选词**逐字**在某一组的 `options` 里 → 进那一组；
2. **整词命中**：候选用 `_` 切开后，**整段**出现在某一组的 `options` 里
   （`swim_suit_glad` 里 `swim_suit` 是完整一段 → 进"服装"）；
   ⚠ 刻意不做子串包含：`unhappy` 里含 `happy`，那会把"不开心"归进"开心"，
   这正是 `Symbols.attr_for` 当初踩过的坑；
2b. **通配**：`options` 里以 `*` 结尾的写法按**前缀**匹配
   （`swim_*` 收 `swim_suit`、`swim_body`）—— 给"一类词"省得逐个列；
3. **兜底组**（`options` 为空的组，通常叫"其他"）：装 1/2 都没归到的词；
4. **没有兜底组**时，没归到的词**留在原地**（单独成组 `label=''`）——
   绝不因为"分了组"就把词弄丢或藏起来。

一个词只进**一组**（按声明顺序，先声明的组优先）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AttrGroup:
    """一组词（项目声明的）。`options` 为空 = **兜底组**。"""
    name: str = ''
    label: str = ''
    options: list = field(default_factory=list)

    @property
    def is_rest(self) -> bool:
        return not self.options

    def title(self) -> str:
        return self.label or self.name or '（未命名）'


def parse_attr_groups(raw) -> list:
    """
    `rules.json` 的 `attr_groups` 段 → `[AttrGroup]`。

    **坏条目跳过**（与 `_from_manifest` 的既有口径一致）：不是对象、没名字、
    options 里混了非字符串 —— 都跳过而不是炸；整段不是列表 → 返回空。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue                      # 只认对象（字符串不是"组声明"）
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        opts = []
        for o in (item.get('options') or ()):
            s = str(o or '').strip()
            if s and s not in opts:
                opts.append(s)
        out.append(AttrGroup(name=name, label=str(item.get('label') or ''),
                             options=opts))
    return out


def _tokens(text: str) -> list:
    return [t for t in str(text or '').split('_') if t]


def _option_matches(value: str, opt: str) -> bool:
    """
    一个属性名算不算落在某个 option 上（**只认"整段词序列"**）。

      · `opt='happy'`、`value='close_happy'` → 是（`happy` 是一整段）；
      · `opt='swim_suit'`、`value='swim_suit_glad'` → 是（连续两段完全相等）；
      · `opt='happy'`、`value='unhappy'` → **不是**（`unhappy` 切出来只有
        `unhappy` 一整段，没有 `happy` —— 这正是当初 `attr_for` 踩过的坑）。
    """
    if not opt or not value:
        return False
    if value == opt:                       # 逐字相同（含带下划线的整名）
        return True
    want, have = _tokens(opt), _tokens(value)
    if not want or len(want) > len(have):
        return False
    for i in range(len(have) - len(want) + 1):
        if have[i:i + len(want)] == want:
            return True
    return False


def group_of(value: str, groups) -> str:
    """
    一个候选词属于哪一组（返回组名；没有组 / 不属于任何组 → `''`）。
    规则见模块 docstring（显式命中 → 整词命中 → 通配 → 兜底组）。
    """
    v = str(value or '').strip()
    if not v or not groups:
        return ''
    rest = ''
    for g in groups:
        if not g.options:
            rest = rest or g.name          # 兜底组只记名，最后用
            continue
        for opt in g.options:
            if opt.endswith('*'):                      # 前缀通配：`swim_*`
                if len(opt) > 1 and v.startswith(opt[:-1]):
                    return g.name
            elif _option_matches(v, opt):
                return g.name
    return rest


def group_values(values, groups) -> list:
    """
    `(这个 tag 真实有的属性, 词组声明)` → `[{'name','label','values'}]`。

    与 `group_choices` 的区别：这里输出**组名 + 标签 + 词**（给 UI 做分栏），
    而且**空组直接不出现** —— 项目声明了"服装"但某个角色没有泳装图时，
    那个角色就不该看到一个空的"服装"栏。
    """
    out = []
    for entry in group_choices(values, groups):
        out.append({'name': entry['group'], 'label': entry['label'],
                    'values': entry['values']})
    return out


def group_choices(values, groups) -> list:
    """
    `(候选词清单, 词组声明)` → `[{'group','label','values'}]`。

    **没有声明时返回 `[]`** —— 调用方据此走原来那条"平铺候选"的路，
    于是不声明词组的项目行为**一个字节不变**。
    组内保持输入顺序（`attrs_of_tag` 已经是确定性的），空组不出现。
    """
    if not groups:
        return []
    buckets: dict = {}
    order: list = []
    for g in groups:
        if g.name not in buckets:
            buckets[g.name] = []
            order.append(g)
    ungrouped = []
    for v in (values or ()):
        s = str(v or '').strip()
        if not s:
            continue
        name = group_of(s, groups)
        if name and name in buckets:
            if s not in buckets[name]:
                buckets[name].append(s)
        elif s not in ungrouped:
            ungrouped.append(s)
    out = []
    for g in order:
        if buckets.get(g.name):
            out.append({'group': g.name, 'label': g.title(),
                        'values': buckets[g.name]})
    if ungrouped:
        # ⚠ 没归到组的词**留在原地**（单独一组、没有标题）——
        # "分了组"绝不等于"能藏词"
        out.append({'group': '', 'label': '', 'values': ungrouped})
    return out


def group_report(groups) -> str:
    """给人看的声明摘要（体检 / 弹窗里显示）。"""
    if not groups:
        return ('项目**没有声明**词组：立绘属性候选是一个平铺列表。'
                '想让"服装 / 表情"这类分开显示，在 `storygraph.rules.json` 的 '
                '`attr_groups` 里写一组即可（或者用「🧩 自定义模块」里的分组页）。')
    lines = [f'项目声明了 {len(groups)} 个词组：']
    for g in groups:
        if g.is_rest:
            lines.append(f'  · {g.title()}（兜底：没归到别的组的词都进这里）')
        else:
            lines.append(f'  · {g.title()}：{len(g.options)} 个词'
                         + (f'（{", ".join(g.options[:6])}'
                            + ('…' if len(g.options) > 6 else '') + '）'
                            if g.options else ''))
    return '\n'.join(lines)


__all__ = ['AttrGroup', 'group_choices', 'group_of', 'group_report',
           'group_values', 'parse_attr_groups']
