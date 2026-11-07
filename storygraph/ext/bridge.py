"""
storygraph.ext.bridge
=====================
**扩展点 → 运行时声明**：在扩展窗口里新建的写法，工具真的会用。

为什么要有这一层（2026-11-07 探针实测的断链）
--------------------------------------------
上一轮做完的是"看得见、改得动、存得下"：扩展窗口读四个来源，写
`storygraph.ext.json`，「是否使用」关掉时 `statement_registry` 会把它摘掉。
但**新建**一条呢？探针给出的是：

    ext.json 里声明 mycue（fields = 文字 / 片段 / with / 转场）
      → StatementRegistry.for_project(root).match('mycue')  = None
      → spec_columns(reg, 'mycue')                          = []

也就是说：窗口里点「＋ 新建」、加几格、保存 —— 界面回你"已保存 N 条"，
而**解析器根本不知道有这回事**。卡片不出格子、表格不出列、批量改不了。
这正是用户 2026-11-07 反复说的"UI 根本没有给我这个功能"的最深一层：
不是界面没画出来，是画出来的东西没有消费方。

这个模块就是那条消费链的中间一段：

    扩展点（字段表） --slot_of_field--> 声明（`pattern` / `columns`）
                     --statement_registry--> 解析器认得的写法
                     --app.batch_edit--> 批处理规则

为什么不直接复用 `registry` 里那份"旧清单投影"
--------------------------------------------
投影是**给界面看的**：它把每一格抹平成 `FieldSpec`（控件类型、标签、候选）。
运行时需要的是**给解析器看的** `pattern`（列名、固定字、可选段、多值）。
两者是同一个东西的两面，所以由 `slot_of_field()`（唯一一处逆映射）换回去，
而不是再写一遍"字段怎么变成格子"。

纯函数 + 薄包装
---------------
`statement_declarations()` / `call_declarations()` 只吃 `[ExtensionPoint]`，
不碰磁盘 —— 判据可以在内存里造一批点直接验，不用建临时项目。
`for_project()` 才读项目（**会执行 `extensions/*.py` 插件**，所以它只在
`StatementRegistry.for_project` 那一层调用，而那一层按项目缓存）。
"""
from __future__ import annotations

from storygraph.ext.schema import ExtensionPoint


def _is_project_source(point: ExtensionPoint) -> bool:
    """
    这条扩展点是不是**项目自己的声明**（而不是工具自带 / 旧清单的投影）。

    `builtin`（`renpy.transition` 这类引擎 API）与 `rules.json`（旧清单投影）
    都不该在这里再产生一份声明：前者由 `story.spec_args` 负责，后者本来就
    已经是 `statement_registry` 的输入 —— 再喂一遍就是**两份真源**。

    `override`（只带开关的记录）同样跳过：它的形状归别处的真源，
    在扩展窗口里改的是"用不用"，不是"长什么样"。
    """
    if getattr(point, 'override', False):
        return False
    source = str(point.source or '')
    return source == 'project' or source.startswith('plugin:')


def _origin_of(point: ExtensionPoint) -> str:
    """
    这条扩展点**住在哪个文件**（写进运行时声明的 `origin`）。

    为什么不能直接用 `point.source`（2026-11-07 第四轮）：项目自己的那几条
    `source` 是 `'project'` —— 那是个**装载来源的分类**，不是文件名。
    界面拿它说话就成了"来处：project"（人看不懂），而 `save_calls` 要按
    文件名判断"这条归哪个文件管"（写错文件 = 写的这份永远读不到）。
    插件照旧是 `plugin:<文件名>`，它本来就是文件。
    """
    source = str(point.source or '')
    if not source or source == 'project':
        return 'storygraph.ext.json'
    return source


def statement_declarations(points) -> list:
    """写法类扩展点 → `storygraph.rules.json` 的 `statements` 条目（纯函数）。

    `id` 可以写成空格分隔的多个词（`"mycue cue"`）：与 `rules.json` 的
    `name` 同一条口径，第一个词是语句头，后面的是别名。

    没有字段的写法**也**收（它等价于 `{"name": "mycue"}`：认这个头、
    逐字透传），因为"先声明再慢慢配字段"是真实的使用顺序。
    """
    out = []
    for point in points or ():
        if point.kind != 'statement' or not point.enabled \
                or not _is_project_source(point):
            continue
        entry = {'name': point.id, 'label': point.label, 'docs': point.docs,
                 # 来处（`plugin:闪电动画.json`）：界面上要看得清"这条是谁加的"，
                 # 出问题时也得知道去改哪个文件。
                 'origin': _origin_of(point)}
        pattern = point.pattern()
        if pattern:
            entry['pattern'] = pattern
        out.append(entry)
    return out


def call_declarations(points) -> list:
    """调用类扩展点 → `rules.json` 的 `calls` 条目（纯函数）。

    每一格参数怎么落到 `columns[]` **只有一处**：`schema.column_of_field`。
    字段自己带 `position`（第几个参数）/ `kw`（关键字名）就按它，
    都没写才按**行序**数位置参数（老 `storygraph.ext.json` 就是这个意思，
    补默认值会改掉老文件的语义）。

    ⚠ 位置**只数位置参数**（2026-11-07 第四轮改）：从前用的是
      `enumerate(字段)` 的下标，于是把关键字那行写在前面（`layer=` 第一行、
      `方向` 第二行）会给 `方向` 一个 `position: 1` —— 工具就去读第 1 个参数，
      而人明明是"第一个位置参数"。这条不会报错，只会**读错值**，所以现在
      关键字行不占位置（判据 `test_keyword_rows_do_not_shift_positions`）。

    ⚠ **2026-11-07 第五轮**：`layer=` 那个键名记号只是"老文件的说法"，
      仍然收（`column_of_field` 里翻），但新写入走 `FieldSpec.kw` ——
      扩展窗口的字段表已经有「位置 / 关键字名」这一列了。

    `effect` / `seconds_arg` 是**调用自己的**演出元数据（不是参数），
    原样带给 `story.statement_registry` 的 `calls`（舞台预览按它演）。
    """
    from storygraph.ext.schema import column_of_field

    out = []
    for point in points or ():
        if point.kind != 'call' or not point.enabled \
                or not _is_project_source(point):
            continue
        columns = []
        position = 0
        for spec in point.field_specs():
            if not str(spec.key or '').strip():
                continue
            column = column_of_field(spec, position=position)
            if not column.get('kw'):
                position = max(position, int(column['position'])) + 1
            columns.append(column)
        if not columns:
            continue
        entry = {'head': point.id, 'label': point.label,
                 'columns': columns, 'origin': _origin_of(point)}
        effect = str(getattr(point, 'effect', '') or '').strip()
        if effect:
            entry['effect'] = effect
        if getattr(point, 'seconds_arg', None) is not None:
            entry['seconds_arg'] = int(point.seconds_arg)
        out.append(entry)
    return out


def declarations(registry) -> dict:
    """一个已装好的注册表 → 两类运行时声明（纯函数，便于判据）。"""
    points = registry.all()
    return {'statements': statement_declarations(points),
            'calls': call_declarations(points)}


def for_project(project_root) -> dict:
    """读一个项目 → `{'statements': [...], 'calls': [...]}`。

    没有项目根 → 两个空列表。**装载扩展点不该让解析打不开**：单个坏扩展点
    在 `ExtensionRegistry` 那边已经变成 `problems`，走不到这里。
    """
    if not project_root:
        return {'statements': [], 'calls': []}
    from storygraph.ext.registry import ExtensionRegistry
    return declarations(ExtensionRegistry.for_project(project_root))


__all__ = ['call_declarations', 'declarations', 'for_project',
           'statement_declarations']
