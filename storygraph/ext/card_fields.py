"""
storygraph.ext.card_fields
==========================
**卡片右栏那几格的字段 schema**（内置一份 + 扩展点可覆盖）。

为什么要有它（用户 2026-11-07 的原话）
-------------------------------------
> "卡片编辑器右边的 UI 和自定义模块定义出的模块一定要对应！"
> "自定义模块归自定义模块，别挪到高级那里去……这么整理太乱了！！！"
> "太散了！"

在这之前，"卡片上一格长什么样"在 `card_editor` 里是**三条 if/elif**：

    for name in ADVANCED_FIELDS:              # 名字从 app.editor 来
        label = '台词后缀' if name == 'SayTail' else name     ← 标签
        if name == 'CodeBlock': w = CodeEditor()              ← 控件
        elif name in ('Stage', 'Notes'): w = QPlainTextEdit()  ← 控件
        elif name == 'SayTail': w = WheelSafeComboBox()        ← 控件（已是死代码）
        else: w = QLineEdit()
        adv.addWidget(card_rows.row(label, w))                ← 没有提示文字

于是同一个"字段"在四个地方各说一句：`app.editor.ADVANCED_FIELDS`（有哪些）、
`card_editor` 的 if 链（长什么样）、`story.beat_fields`（能不能存）、
扩展窗口（用户能不能加）。**前三处只要有一处没跟上，症状都是"格子在、
但改了不生效"或"加了字段却看不见"，而且不报错。**

现在这张表是**唯一一处**说"这一格叫什么、什么类型、提示什么"的地方；
控件由 `ext.schema.KIND_TO_WIDGET` 推出来（`ui_qt.field_widgets` 是唯一的
画法），能不能存仍归 `story.beat_fields`（那是另一份契约，不在这条链上）。

`field` 类扩展点在这里被消费
---------------------------
`ExtensionPoint(kind='field')` 以前**没有任何消费方** —— 窗口里建得出来、
存得下、树上看得见，卡片上什么都不会发生（和上一轮 `statement` 的断链是
同一类问题，见 `ext.bridge` 的 docstring）。

现在它的字段表就是"这一格的新样子"：

    {"id": "舞台动作", "kind": "field", "enabled": true,
     "fields": [{"key": "Stage", "label": "舞台动作", "kind": "code",
                 "help": "一行一条 stage 指令"}]}

保存 → 打开卡片 → `Stage` 那一行变成代码框、标签变成「舞台动作」，
**值仍然写回 `Beat.stage`**（schema 只改外观，不改数据的去处）。

⚠ 三条边界，都写在这里而不是靠人记：

  · **只能改已有的格**。卡片上"多一格"意味着多一列要存、要导出、要能撤回
    —— 那属于 `story.beat_fields.COLUMN_SPECS`（改代码），不是外观层能许的。
    给不出位置的 key 会进 `problems`，界面上照旧是原来那一格（**不静默**）；
  · **专用控件的格不在表里**（说话人下拉 / 立绘网格 / 背景色块 / 台词转场）：
    它们的"控件"是业务逻辑，不是一个 kind 能描述的。覆盖它们同样进
    `problems`；
  · **值格只装值**：开关 / 多选 / 固定字（`keyword` / `optional`）一律拒。
    卡片上那几格的值要折回 `Beat` 的字段、要折进语句头；`True`、列表、
    或者"写死的那个字"都不是这一拍的内容（固定字那一版实测会把
    `Stage='show'` 写进每一拍，2026-11-08）；
  · 关掉（`enabled: false`）就等于没有这条覆盖 —— 与其它三类扩展点同一条口径。
"""
from __future__ import annotations

from storygraph.ext.schema import (FIELD_KIND_LABELS, ExtensionPoint, FieldSpec)

#: 卡片上**由字段表画出来**的格（顺序 = 卡片上的顺序）。
#:
#: 前两格（备注 / 条件）在主区，其余在高级区；`SayTail` / `Speaker` / `Text` /
#: `Scene` / `BGM` / `Voice` **不在**这里 —— 它们是专用控件（见
#: `BESPOKE_CARD_FIELDS`）。
BUILTIN_CARD_FIELDS = (
    FieldSpec(key='Comment', label='备注', kind='variable',
              placeholder='写进脚本的注释（不用自己加 #）：例 bg1.2 bgm13 / '
                          '场景备忘 / 给同事的说明',
              help='写进 .rpy 的 `# 注释`。要"不进脚本"的备注请写在别处。'),
    FieldSpec(key='condition', label='＋ 条件', kind='expr',
              placeholder='留空 = 无条件（例：love >= 5）',
              help='填了就在这一拍外面包一层 `if`（例：love >= 5）。'
                   '留空 = 无条件。'),
    FieldSpec(key='SFX', kind='variable',
              help='这一拍放的声音（引擎里的声音名；多个用空格分开）。'),
    FieldSpec(key='CodeBlock', kind='code',
              help='这一拍的额外代码，原样写进 .rpy。左边是源文件里的真实行号。'),
    FieldSpec(key='Label', kind='variable',
              help='这一拍所在的 label / 跳转目标名。'),
    FieldSpec(key='Kind', kind='variable',
              help='这一拍的类型（say / show / scene / menu …）。'),
    FieldSpec(key='Stage', kind='multiline',
              help='舞台指令原文（`show eileen at left` 这类），一行一条。'),
    FieldSpec(key='MenuName', kind='variable',
              help='菜单头 `menu 名(args):` 的名字那一段。'),
    FieldSpec(key='MenuArgs', kind='variable',
              help='菜单头 `menu 名(args):` 括号里那段（screen 参数）。'),
)

#: 卡片上**专用控件**的格：它们的"长什么样"是业务逻辑（下拉候选来自项目、
#: 网格要缩略图），一个 kind 描述不了。字段表**不碰**它们的控件。
BESPOKE_CARD_FIELDS = ('Speaker', 'Text', 'SayTail', 'Scene', 'BGM', 'Voice')


def builtin_card_fields() -> dict:
    """内置 schema → `{字段名: FieldSpec}`（新建一份 dict，调用方随便改）。"""
    return {spec.key: spec for spec in BUILTIN_CARD_FIELDS}


def overridable_keys() -> tuple:
    """字段表能改的那些格的名字（报错信息 / 提示里要用同一份）。"""
    return tuple(spec.key for spec in BUILTIN_CARD_FIELDS)


def _point_name(point: ExtensionPoint) -> str:
    return str(getattr(point, 'label', '') or getattr(point, 'id', '') or '?')


def field_overrides(points, base=None) -> tuple:
    """
    `field` 类扩展点 → `({字段名: 新外观}, [problems])`（纯函数）。

    只看**启用的、项目自己的**扩展点（`builtin` / `rules.json` 的开关投影
    不产生覆盖：它们的外观归原处，见 `ext.bridge._is_project_source`）。
    """
    base = dict(base if base is not None else builtin_card_fields())
    out: dict = {}
    problems: list = []
    for point in points or ():
        if str(getattr(point, 'kind', '')) != 'field':
            continue
        if not getattr(point, 'enabled', True):
            continue
        if getattr(point, 'override', False):
            continue
        if str(getattr(point, 'source', '') or '') in ('builtin', 'rules.json'):
            continue
        specs = list(point.field_specs() or ())
        if not specs:
            problems.append(f'拍字段扩展点「{_point_name(point)}」没写要改哪一格'
                            f'（在「字段」表里加一行，键名写 '
                            f'{" / ".join(overridable_keys()[:3])} …）')
            continue
        for spec in specs:
            key = str(spec.key or '').strip()
            if key in base:
                if spec.fixed:
                    # 固定字（`keyword` / `optional`）不是"这一拍的值"。那一格
                    # 只装值：画成只读框之后，`collect_fields()` 会把框里那几个
                    # 字当成内容交出去（`Stage='show'`），于是"把一格改成固定字"
                    # 等于"给每一拍塞一句脚本"（2026-11-08 用真控件量到）。
                    # 与开关 / 多选同一条口径：值格放不下的类型，当场拒。
                    problems.append(
                        f'拍字段扩展点「{_point_name(point)}」把「{key}」'
                        f'改成了固定字（{FIELD_KIND_LABELS.get(spec.kind, spec.kind)}）：'
                        f'卡片上的格只装这一拍的值 —— 那几个字会被当成内容'
                        f'写进脚本')
                    continue
                if spec.widget in ('bool', 'multi_choice'):
                    # 卡片上那几格的值**都是字符串**（要写回 `Beat` 的字段、
                    # 要折进语句头）。开关/多选读出来是 True / 列表 —— 到
                    # 写回时就变成 `Stage=True` 这种脚本里没有的东西。
                    problems.append(
                        f'拍字段扩展点「{_point_name(point)}」把「{key}」'
                        f'改成了 {FIELD_KIND_LABELS.get(spec.kind, spec.kind)}：'
                        f'卡片上的格只装字符串，开关/多选写不回去')
                    continue
                out[key] = _with_origin(spec, base[key], _point_name(point))
            elif key in BESPOKE_CARD_FIELDS:
                problems.append(
                    f'拍字段扩展点「{_point_name(point)}」想改「{key}」——'
                    f'那是专用控件（{_why_bespoke(key)}），字段表管不到它的控件')
            else:
                problems.append(
                    f'拍字段扩展点「{_point_name(point)}」想改「{key}」——'
                    f'卡片上没有这一格（能改：{" / ".join(overridable_keys())}）')
    return out, problems


def _why_bespoke(key: str) -> str:
    """一句话说清那一格为什么不在字段表里（报错信息要能直接看懂）。"""
    return {
        'Speaker': '说话人下拉，候选来自项目角色表',
        'Text': '台词框，带文字标签预览',
        'SayTail': '台词转场，候选来自项目 transform',
        'Scene': '背景，带色块与资源选择器',
        'BGM': '音乐，带资源选择器',
        'Voice': '语音，带资源选择器',
    }.get(key, '业务控件')


def _with_origin(spec: FieldSpec, old: FieldSpec, point_name: str) -> FieldSpec:
    """
    把覆盖后的字段**补上缺省**（没写 label 就沿用原来的标签）并记上来处。

    为什么补缺省：扩展窗口里"加一格只改类型"是最常见的用法，那时 label 是
    空的 —— 直接用它会让卡片上那一行**变成没有标签的空白格**（不是"改类型"，
    是"把标签弄丢了"）。
    """
    return FieldSpec(
        key=old.key,
        label=str(spec.label or old.label),
        kind=str(spec.kind or old.kind),
        help=(str(spec.help or old.help)
              + f'\n（这一格的外观来自扩展「{point_name}」）'),
        default=spec.default if spec.default != '' else old.default,
        choices=tuple(spec.choices or old.choices),
        required=bool(spec.required),
        group=str(spec.group or old.group),
        literal=str(spec.literal or old.literal),
        multi=bool(spec.multi),
        limit=bool(spec.limit),
        placeholder=str(spec.placeholder or old.placeholder),
    )


def signature(specs) -> tuple:
    """
    schema 的**形状指纹** —— 只有它变了才值得重建界面。

    为什么必须比一遍（2026-11-07 实测）：`set_card_field_specs` 会在"打开项目"
    和"扩展窗口保存完"两处被调，而两次的 schema **通常一模一样**。无条件重建
    的代价不是性能，是**交互状态**：正在编辑的那一格会被 `setParent(None)`
    换掉（光标、选区、输入法全丢），窗口的初始焦点也会跑到另一格上
    （`tests/test_ui_qt.py::test_page_switch_keeps_the_focus_inside_the_visible_page`
    当场抓到）。
    """
    out = []
    for key in sorted(specs or {}):
        spec = specs[key]
        out.append((key, spec.label, spec.kind, spec.help, spec.placeholder,
                    tuple(spec.choices), spec.shown_literal))
    return tuple(out)


def card_field_specs(points=None, base=None) -> tuple:
    """`({字段名: FieldSpec}, [problems])` —— 内置 + 扩展覆盖后的最终样子。"""
    merged = dict(base if base is not None else builtin_card_fields())
    overrides, problems = field_overrides(points, merged)
    merged.update(overrides)
    return merged, problems


def for_project(project_root) -> tuple:
    """
    读一个项目 → `({字段名: FieldSpec}, [problems])`。

    没有项目根 / 读不出来 → 只回内置那份。**卡片不该因为扩展点坏掉而画不出来**
    （单个坏扩展点在 `ExtensionRegistry` 那边已经变成 `problems`）。
    """
    if not project_root:
        return builtin_card_fields(), []
    from storygraph.ext.registry import ExtensionRegistry
    reg = ExtensionRegistry.for_project(project_root)
    specs, problems = card_field_specs(reg.all())
    return specs, [*reg.problems, *problems]


__all__ = ['BESPOKE_CARD_FIELDS', 'BUILTIN_CARD_FIELDS', 'builtin_card_fields',
           'card_field_specs', 'field_overrides', 'for_project',
           'overridable_keys', 'signature']
