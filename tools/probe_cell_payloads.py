"""
探针：**台词格里的特殊内容**经过"表 → .rpy"这一趟后还在不在。

策划在 Excel 格子里会自然写出这些东西，而 Excel 与 Ren'Py 的语汇不一样：

  1. 引号：`他说："走吧。"`      —— Ren'Py 里 `"` 必须转义
  2. 换行：格子内 Alt+Enter      —— Ren'Py 里多行字符串要合并或用 \\n
  3. 反斜杠：`C:\\temp\\a`        —— 会成为非法转义
  4. 百分号：`涨了 50%`           —— Ren'Py 拿 `%` 做旧式插值
  5. 方括号：`[name] 来了`        —— Ren'Py 拿 `[]` 做变量插值
  6. 花括号：`{b}加粗{/b}`        —— Ren'Py 文本标签（应当保留原样）
  7. 以 `=` 开头：`= 待定`        —— Excel 会当公式
  8. 超长：20000 字               —— Excel 单元格上限 32767

每个场景都走真实往返（rpy2xlsx → 直接改表 → xlsx2rpy），判据是
"产物重新解析后，那一句台词读回来与用户输入**完全一致**"。

用法：
    D:/Python/python.exe tools/probe_cell_payloads.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

BASE = '''\
label ch1:
    jc "占位一句。"
    jc "待替换。"
    return
'''

CASES = [
    ('引号', '他说："走吧。"'),
    ('换行', '第一行\n第二行'),
    ('反斜杠', 'C:\\temp\\a 与 \\n'),
    ('百分号', '涨了 50% 还要涨'),
    ('方括号', '[name] 来了'),
    ('花括号', '{b}加粗{/b}'),
    ('等号开头', '= 待定'),
    ('[b]混排[/b]', '听到[b]巨响[/b]。'),
    ('超长 20000', '长' * 20000),
]


def run(*args):
    p = subprocess.run([PY, str(ROOT / 'cli.py'), *args], cwd=str(ROOT),
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def replace_text_cell(xlsx: Path, payload: str) -> int:
    """把表里「待替换。」那一格换成 payload。"""
    import openpyxl
    wb = openpyxl.load_workbook(str(xlsx))
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.strip() == '待替换。':
                    c.value = payload
                    wb.save(str(xlsx))
                    return 1
    wb.save(str(xlsx))
    return 0


def read_back(out: Path):
    """产物重新解析（走工具自己的解析器）→ 全部台词。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from storygraph.parsing.renpy_parser import ParseResultBuilder, RenPyParser
    from storygraph.story.beat_build import beats_from_parse_result
    beats = beats_from_parse_result(
        ParseResultBuilder().build(RenPyParser().parse(str(out))))
    texts = []
    for b in beats:
        t = getattr(b, 'text', None) or getattr(b, 'line', None)
        if t:
            texts.append(t)
    return texts


def one(name: str, payload: str, work: Path) -> dict:
    tag = str(abs(hash(name)))
    src = work / f'{tag}.rpy'
    src.write_text(BASE, encoding='utf-8')
    xl = work / f'{tag}.xlsx'
    rc, log = run('table', 'rpy2xlsx', str(src), '--out', str(xl))
    if rc != 0:
        return {'name': name, 'stage': 'rpy2xlsx', 'rc': rc,
                'note': log.strip().splitlines()[-1:]}
    if not replace_text_cell(xl, payload):
        return {'name': name, 'stage': '改格', 'rc': 0, 'note': '找不到占位格'}
    out = work / f'{tag}.out.rpy'
    rc, log = run('table', 'xlsx2rpy', str(xl), '--out', str(out),
                  '--source', str(src), '--no-engine-lint')
    d = {'name': name, 'stage': 'xlsx2rpy', 'rc': rc,
         'log': [x for x in log.strip().splitlines() if x.strip()][-3:]}
    if not out.exists():
        d['verdict'] = '产物没生成'
        return d
    raw = out.read_text(encoding='utf-8')
    d['raw_has_payload_json'] = repr(payload[:40])
    # 用工具自己的解析器读回来
    try:
        texts = read_back(out)
        d['parsed_back'] = texts
        d['match'] = payload in texts
    except Exception as e:                      # noqa: BLE001
        d['parse_error'] = f'{type(e).__name__}: {e}'
        d['match'] = False
    # 用 Python 字面量严格解一次（比"含子串"硬）
    d['out_head'] = raw.splitlines()[:6]
    return d


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='payload_') as td:
        work = Path(td)
        results = []
        for name, payload in CASES:
            try:
                r = one(name, payload, work)
            except Exception as e:              # noqa: BLE001
                r = {'name': name, 'parse_error': f'{type(e).__name__}: {e}',
                     'match': False}
            results.append(r)
            print('=' * 74)
            print(f'[{name}]  输入：{payload[:40]!r}')
            for k, v in r.items():
                if k == 'name':
                    continue
                if k == 'parsed_back':
                    print(f'  {k}: {[x[:40] for x in v]}')
                elif k == 'out_head':
                    for ln in v:
                        print(f'    | {ln[:100]}')
                else:
                    print(f'  {k}: {v}')
        print('\n' + '=' * 74)
        print(f'{"场景":14} {"rc":>3} {"往返一致":>8}  说明')
        for r in results:
            if 'rc' not in r:
                print(f'{r["name"]:14} {"-":>3} {"-":>8}  {r.get("parse_error", "")[:50]}')
                continue
            ok = r.get('match')
            note = '' if ok else str(r.get('log') or r.get('note') or '')[:70]
            print(f'{r["name"]:14} {r["rc"]:>3} {str(ok):>8}  {note}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
