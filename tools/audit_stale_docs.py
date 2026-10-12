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

#: 文档里表示"还没做"的词
PENDING_MARKS = ('待做', '未做', '还没做', 'TODO', '⏳', '待补', '未完成')

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


def audit(docs_dir: Path, root: Path = None) -> dict:
    """
    跑一遍审计 → `{'rows': [...], 'counts': {...}}`。

    每行一项：`{'doc', 'line', 'text', 'names', 'found'}`；`found` 非空 = 可疑。
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
            if not any(mark in line for mark in PENDING_MARKS):
                continue
            names = names_in_line(line)
            found = {n: index[n][:2] for n in names if n in index}
            rows.append({'doc': fp.name, 'line': no,
                         'text': line.strip()[:120], 'names': names,
                         'found': found})
    counts = {'rows': len(rows),
              'suspicious': sum(1 for r in rows if r['found']),
              'unjudgeable': sum(1 for r in rows if not r['names'])}
    return {'rows': rows, 'counts': counts}


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    docs = Path(argv[0]) if argv else ROOT / 'docs'
    got = audit(docs)
    c = got['counts']
    print(f'扫了 {c["rows"]} 行"待做"标记；其中 **可疑（代码里已有同名）** '
          f'{c["suspicious"]} 行；无从判定 {c["unjudgeable"]} 行')
    print('=' * 72)
    for row in got['rows']:
        if not row['found']:
            continue
        print(f'\n⚠ {row["doc"]}:{row["line"]}')
        print(f'   {row["text"]}')
        for name, where in row['found'].items():
            print(f'     代码里已有「{name}」→ {where}')
    print('\n' + '=' * 72)
    print('说明：命中只说明**代码里有这个名字**，不等于那件事做完了 —— '
          '去核一眼；核完**回头把文档改掉**（这才是这条命令的意义）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
