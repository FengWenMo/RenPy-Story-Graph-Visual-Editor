"""
拍表的**单一定义**（设计文档 §3 的 index 层第一件）
================================================================================

问题（设计文档 §3）：列定义散在好几处 ——
`beat_model.BEAT_FIELDS` / `BEAT_HEADERS` / `HIDDEN_BEAT_COLUMNS` /
`_HEADER_ALIASES`、`app.editor.PRIMARY_FIELDS` / `ADVANCED_FIELDS`、
导出侧的表头与列宽…… 改一处忘一处就是"契约漂移"。

这个模块**不重复定义**（那会变成第五处），而是把现有常量**汇总成一份
可 diff 的定义**，并给出"代码 vs 已发布定义"的对账：

    python -m storygraph.story.schema            # 打印（人看）
    python -m storygraph.story.schema --json      # 机器读
    python -m storygraph.story.schema --write docs/schema.json
    python -m storygraph.story.schema --check     # 与已发布定义比对，漂移则退出 1

⚠ 口径：**生成物不参与运行**（运行时仍然读 `beat_model` 那几个常量）。
这一层的价值是"**只有一个地方能看见全部契约 + 改了没同步会被判据抓住**"；
把它反过来做代码生成（由 schema 生成 `BEAT_FIELDS`）是**下一步**，
那一步的验收是"生成物与现有定义逐字节一致"（见设计文档 §5 第 5 步）。
"""

# ⚠ 放在**顶层**（不是 `story/`）：它是对 `story`（列定义）与 `app`（卡片分区）的**只读视图** —— 塞进 `story/` 就成了 story -> app 的反向依赖
# （`tests/test_package_layering.py::test_lazy_imports_are_declared` 当场红）。

from __future__ import annotations

import json
import sys
from pathlib import Path

#: 已发布定义的默认位置（进版本库；CI 与 `--check` 都看它）
DEFAULT_PATH = Path('docs') / 'schema.json'

#: 定义格式版本：**改结构时才动**（改列不改它）。
#: v2（2026-10-17）：列上多了 snapshot / fingerprint / collab / single_line
#: 四个旗标 —— 它们决定"这一列进不进撤销快照 / 内容指纹 / 协作 payload"，
#: 是调研里"字段契约散在五处"那一条的收敛结果（见 `story.beat_fields`）。
SCHEMA_VERSION = 2


def beat_schema() -> dict:
    """现有常量 → 一份可 diff 的拍表定义（纯汇总，不改任何行为）。"""
    from storygraph.app.editor import ADVANCED_FIELDS, PRIMARY_FIELDS
    from storygraph.story.beat_fields import COLUMN_SPECS
    from storygraph.story.beat_model import (
        BEAT_HEADERS, HIDDEN_BEAT_COLUMNS, _HEADER_ALIASES)

    hidden = set(HIDDEN_BEAT_COLUMNS)
    columns = []
    for spec in COLUMN_SPECS:
        columns.append({
            'name': spec.name,
            'header': BEAT_HEADERS.get(spec.name, spec.name),
            'hidden': spec.name in hidden,
            'snapshot': spec.snapshot,
            'fingerprint': spec.fingerprint,
            'collab': spec.collab,
            'single_line': spec.single_line,
        })
    return {
        'version': SCHEMA_VERSION,
        'columns': columns,
        'aliases': dict(sorted(_HEADER_ALIASES.items())),
        'groups': {
            'primary': list(PRIMARY_FIELDS),
            'advanced': list(ADVANCED_FIELDS),
        },
    }


def dumps(schema: dict = None) -> str:
    """稳定序列化（排序 + 固定缩进）—— `--check` 比的就是它。"""
    return json.dumps(schema or beat_schema(), ensure_ascii=False,
                      indent=2, sort_keys=True) + '\n'


def check(published_path=DEFAULT_PATH) -> tuple:
    """
    代码里的定义 vs 已发布定义 → `(ok, 人话)`。

    漂移的两种可能都要说清：**列变了没更新定义** / **定义被人手改了**。
    """
    path = Path(published_path)
    if not path.is_file():
        return False, (f'没有已发布定义 {path}（跑 '
                       f'`python -m storygraph.story.schema --write {path}` 生成）')
    want = dumps()
    try:
        got = path.read_text(encoding='utf-8')
    except OSError as e:                        # noqa: BLE001
        return False, f'读不到 {path}：{e}'
    if want == got:
        return True, f'{path} 与代码一致（{len(beat_schema()["columns"])} 列）'
    wl, gl = want.splitlines(), got.splitlines()
    diff = next((f'第 {i + 1} 行：代码 {a!r} ≠ 定义 {b!r}'
                 for i, (a, b) in enumerate(zip(wl, gl)) if a != b), '')
    if not diff:
        diff = f'行数不同：代码 {len(wl)} vs 定义 {len(gl)}'
    return False, (f'{path} 与代码**漂移**了（{diff}）—— '
                   f'要么是改了列没更新定义，要么是定义被手改了；'
                   f'确认无误后跑 `--write` 重新生成')


def codegen_beat_model() -> str:
    """
    由定义**生成** `beat_model` 里那三个常量（设计文档 §5 第 5 步的"反转"）。

    本轮先做到**可对账**：生成出来的是**源文本**，判据 `exec` 它之后与运行时
    真正的常量逐项比 `==` —— "由 schema 生成"就不再是空话，而"把运行时切过去"
    只剩一次纯机械替换。

    ⚠ 为什么不直接切：那要动 `beat_model` 的**导入期行为**（所有读写路径都依赖
    这三个常量），与"资源表换位"踩过的是同一类坑。先让生成物**逐字节可对**，
    再单独一轮切过去（那时判据已经在了）。
    """
    s = beat_schema()
    lines = ['# 由 storygraph/schema.py 生成 —— 别手改（改 docs/schema.json 后重跑）',
             '# 当前仅供对账；运行时仍以 beat_model 里的定义为准', '',
             'BEAT_FIELDS = [']
    for c in s['columns']:
        lines.append(f'    {c["name"]!r},')
    lines += [']', '', 'HIDDEN_BEAT_COLUMNS = (']
    for c in s['columns']:
        if c['hidden']:
            lines.append(f'    {c["name"]!r},')
    lines += [')', '', 'HEADER_OF = {']
    for c in s['columns']:
        lines.append(f'    {c["name"]!r}: {c["header"]!r},')
    lines += ['}', '',
              '# 运行时 `BEAT_HEADERS` = 这份 HEADER_OF 的**旧写法**（含历史别名），',
              '# 两者由 `tests/test_schema_single_source.py` 对账。', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if '--generate' in args:
        print(codegen_beat_model(), end='')
        return 0
    if '--check' in args:
        ok, msg = check(args[args.index('--check') + 1]
                        if len(args) > args.index('--check') + 1
                        and not args[args.index('--check') + 1].startswith('-')
                        else DEFAULT_PATH)
        print(msg)
        return 0 if ok else 1
    if '--write' in args:
        i = args.index('--write')
        path = Path(args[i + 1]) if len(args) > i + 1 else DEFAULT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps(), encoding='utf-8')
        print(f'已写出 {path}（{len(beat_schema()["columns"])} 列）')
        return 0
    if '--json' in args:
        print(dumps(), end='')
        return 0
    s = beat_schema()
    print(f'拍表定义 v{s["version"]}：{len(s["columns"])} 列'
          f'（隐藏 {sum(1 for c in s["columns"] if c["hidden"])}）')
    for c in s['columns']:
        flag = '（隐藏）' if c['hidden'] else ''
        print(f"  {c['name']:<18}{c['header']}{flag}")
    print(f'主区 {len(s["groups"]["primary"])} 个 / '
          f'高级区 {len(s["groups"]["advanced"])} 个 / '
          f'表头别名 {len(s["aliases"])} 条')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
