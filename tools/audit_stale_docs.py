"""**文档过期审计**：文档说"待做"，代码里其实早做了吗？
=========================================================================

## 为什么需要它（2026-10-11，一天之内踩了三次）

| 文档说 | 真相 | 代价 |
|---|---|---|
| 路线图 §七 "S3/S4 待做" | `import_plain_text`（S3）与 `docx`（S4）**早有实现与判据**，只是**界面没接** | 我**重写了一份**转换器（比正式入口少三样东西），已删 |
| 导出缺陷清单 44 号 "候选太多没法选" | `choice_picker` **早就有** `filter_choices` / `filter_groups` | 白查一遍 |
| （同类）"待做"标记 | 多数是**做完没回头改文档** | 每次都要人工核 |

⇒ 根因不是"谁偷懒"，而是**文档与代码没有对账的动作**。所以把它做成一条命令：

    python tools/audit_stale_docs.py [docs 目录]

## 它怎么判（**只做有把握的判定，不猜**）

扫 `docs/*.md` 里带 `待做 / 未做 / TODO / ⏳ / 还没做` 的行，
从行里抽出**反引号包起来的标识符**（`foo` / `foo.bar` / `foo.py` / `def foo`），
再去**代码索引**里查：

  · 找得到 → **⚠ 可疑：文档说待做，代码里已有**（最可能已实现 → 去核）
  · 找不到 → ✅ 真的还没做（这条待做是准的）
  · 抽不出标识符 → ⚠ 无从判定（建议改文档时把符号写进反引号）

⚠ 它**不判谁对谁错**，只把"可疑的行"列出来让人去核 ——
"代码里有这个名字"不等于"那件事做完了"（可能只是同名）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 文档里表示"还没做"的词（**只有这些词本身还不够**，见 `is_pending_line`）
PENDING_MARKS = ('待做', '未做', '还没做', 'TODO', '⏳', '待补', '未完成')

#: 已经了结的说法：这一行在说"修好了"，不是"待做"
RESOLVED_MARKS = ('已修', '已完成', '已解决', '已做', '已改', '已补')


def is_pending_line(line: str) -> bool:
    """
    这一行是在**声明一件事还没做**，还是只是**提到了**"TODO/未完成"这些字？

    ⚠ 2026-10-12 实测：本仓 8 行"可疑"里 **6 行是误报**，根因就是后者 ——
    本仓文档经常在**描述**里提到 `# TODO`（工具本来就会往生成代码里写 `# TODO`），
    还会把"未完成"当**名词**用（"而不是'完成/未完成'的布尔"）。
    噪声比不报更糟，所以按下面的判据收紧：

      · 行里有"已修/已完成/…" → 这是在说**了结**了，不算待做；
      · `TODO` → 必须**不在反引号里**、且**前面不是 `#`**（那是生成物的标记，不是待办）；
      · `未完成` → 必须出现在**表格行**里（`|`）或跟"待"同现，否则多半是名词用法；
      · `待做 / 未做 / 还没做 / ⏳ / 待补` → 直接算。
    """
    text = str(line or '')
    if any(mark in text for mark in RESOLVED_MARKS):
        return False
    if any(mark in text for mark in ('待做', '未做', '还没做', '⏳', '待补')):
        return True
    if '未完成' in text and ('|' in text or '待' in text):
        return True
    # `TODO` 要在反引号外、且不是 `# TODO` / `#TODO` 这种生成物标记
    for m in re.finditer(r'TODO', text):
        before = text[:m.start()]
        if before.count('`') % 2 == 1:          # 在代码段里
            continue
        if before.rstrip().endswith('#'):        # `# TODO`
            continue
        return True
    return False

#: 从反引号里抽标识符（`foo` / `foo.bar` / `path/foo.py` / `def foo`）
_TICKED = re.compile(r'`([^`]{2,60})`')

#: 反引号里不像标识符的（命令行、路径片段、中文…）就别当名字使
_NOT_A_NAME = re.compile(r'[\u4e00-\u9fff\s]|^--|^python |^pytest ')


def code_index(root: Path) -> dict:
    """
    建一个代号索引：`{名字: [定义它的文件…]}`（模块名、类名、函数名、方法名）。

    ⚠ 只收**定义**（`def` / `class` / 顶层赋值）—— 收引用的话，
    "文档提到过的名字"会在任意 import 处命中，那这条审计就没用了。
    """
    index: dict = {}

    def add(name: str, rel: str):
        if not name or len(name) < 3:
            return
        index.setdefault(name, []).append(rel)

    for fp in sorted(root.rglob('*.py')):
        # ⚠ 跳过规则要按**相对于扫描根**判断（我第一版按绝对路径判断，
        #   于是 pytest 的临时目录正好在 `.pip-tmp` 里 → 索引整个空了，
        #   判据当场抓到）。
        try:
            rel_parts = fp.relative_to(root).parts
        except ValueError:
            continue
        if any(part in ('.pip-tmp', 'legacy', '__pycache__')
               for part in rel_parts):
            continue
        rel = str(fp.relative_to(root))
        add(fp.stem, rel)                            # 模块名
        try:
            tree = ast.parse(fp.read_text(encoding='utf-8', errors='replace'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                add(node.name, rel)
    return index


def names_in_line(line: str) -> list:
    """这一行里值得去代码里查的名字（去掉不像名字的）。"""
    out = []
    for hit in _TICKED.findall(str(line or '')):
        text = hit.strip()
        if _NOT_A_NAME.search(text):
            continue
        for part in re.split(r'[.\s(（]+', text):
            part = part.strip()
            if not part or _NOT_A_NAME.search(part):
                continue
            if re.fullmatch(r'[\w./-]{3,60}', part):
                out.append(part)
    # 模块路径 `a/b.py` 也当模块名查一次
    return list(dict.fromkeys(out))


#: ⚠ **泛名表**：这些词在文档里到处都是，命中了也说明不了什么。
#:
#: 2026-10-12 实测：本仓 42 行"待做"里报出 11 行可疑，其中好几行只是因为
#: 文档里出现了 `registry` / `engine` / `character` 这种**到处都有的词** ——
#: 那是误报。而**噪声比不报更糟**（人会开始无视整份报告，本仓已有教训）。
#: 所以：命中**只算泛名**的行降级成"泛名命中"，不进"可疑"那一栏。
GENERIC_NAMES = frozenset({
    'registry', 'engine', 'character', 'characters', 'columns', 'column',
    'label', 'labels', 'field', 'fields', 'text', 'texts', 'value', 'values',
    'path', 'paths', 'file', 'files', 'line', 'lines', 'name', 'names',
    'type', 'types', 'data', 'index', 'node', 'nodes', 'audit', 'report',
    'session', 'config', 'setting', 'settings', 'table', 'tables', 'row',
    'rows', 'cell', 'cells', 'kind', 'kinds', 'spec', 'specs', 'group',
    'groups', 'item', 'items', 'state', 'stage', 'beat', 'beats',
})


def is_specific(name: str) -> bool:
    """
    这个命中的名字**够具体吗**（够具体才值得人工去核）。

    判据（够长、或有下划线/驼峰这种"起名痕迹"）：`stage_digest` ✅、
    `filter_choices` ✅、`AssetIndex` ✅、`registry` ❌（泛名）、`path` ❌。
    """
    name = str(name or '')
    if name.lower() in GENERIC_NAMES:
        return False
    if len(name) >= 6:
        return True
    return bool(re.search(r'[A-Z]|_', name))


def audit(docs_dir: Path, root: Path = None) -> dict:
    """
    跑一遍审计 → `{'rows': [...], 'counts': {...}}`。

    每行一项：`{'doc', 'line', 'text', 'names', 'found', 'specific'}`：
      · `found` 非空 = 有命中；
      · `specific` 非空 = **够具体的**命中 → 这一行才算"可疑"，值得人工去核。
    （只命中泛名的行降级成 `generic_only`，不占用人眼。）
    """
    root = Path(root) if root else ROOT
    index = code_index(root)
    rows = []
    for fp in sorted(Path(docs_dir).rglob('*.md')):
        try:
            lines = fp.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for no, line in enumerate(lines, 1):
            if not is_pending_line(line):
                continue
            names = names_in_line(line)
            found = {n: index[n][:2] for n in names if n in index}
            specific = {n: w for n, w in found.items() if is_specific(n)}
            rows.append({'doc': fp.name, 'line': no,
                         'text': line.strip()[:120], 'names': names,
                         'found': found, 'specific': specific})
    counts = {'rows': len(rows),
              'suspicious': sum(1 for r in rows if r['specific']),
              'generic_only': sum(1 for r in rows
                                  if r['found'] and not r['specific']),
              'unjudgeable': sum(1 for r in rows if not r['names'])}
    return {'rows': rows, 'counts': counts}


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    docs = Path(argv[0]) if argv else ROOT / 'docs'
    got = audit(docs)
    c = got['counts']
    print(f'扫了 {c["rows"]} 行"待做"标记；其中 **可疑（代码里有够具体的同名）** '
          f'{c["suspicious"]} 行；只命中泛名（不算）{c.get("generic_only", 0)} 行；'
          f'无从判定 {c["unjudgeable"]} 行')
    print('=' * 72)
    for row in got['rows']:
        if not row.get('specific'):
            continue
        print(f'\n⚠ {row["doc"]}:{row["line"]}')
        print(f'   {row["text"]}')
        for name, where in row['specific'].items():
            print(f'     代码里已有「{name}」→ {where}')
    print('\n' + '=' * 72)
    print('说明：命中只说明**代码里有这个名字**，不等于那件事做完了 —— '
          '去核一眼；核完**回头把文档改掉**（这才是这条命令的意义）。')
    print('（只命中泛名的行不算可疑：`registry`/`engine` 这种词到处都是，'
          '报出来只会让人无视整份报告。）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
