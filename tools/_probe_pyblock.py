"""
探针：变量工作台往 `python:` 块那一拍插操作，产物到底长什么样？
==========================================================================

2026-10-22 复查时提的两个问题（当时**未验证**，这里是实测）：

  Q1. 工作台会把一条操作显示成"在 `for i in range(2):` 里"吗？
      （`_source_block_of` 的 heads 里有 `for`，但块选择器不列 `for`）
  Q2. 往 `python:` 块那一拍插一条新操作，产物会是 `$ x` 吗？
      （`$` 在 `python:` 块里是语法错误）

用法：
    D:/Python/python.exe tools/_probe_pyblock.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

#: `python:` 块里套 `for`（`for` 唯一合法的位置）
PY_FOR = '''default love = 0

label pylab:
    e "开场"
    python:
        for i in range(2):
            love += 1
    e "收场"
    return
'''

#: `python:` 块，里面是平铺语句（没有 for）
PY_FLAT = '''default love = 0

label pylab:
    e "开场"
    python:
        love += 1
        flag = True
    e "收场"
    return
'''

#: 对照：脚本层的 `if`（一切都正常的那条路）
PLAIN_IF = '''default love = 0

label pylab:
    e "开场"
    if love > 3:
        $ love += 1
    e "收场"
    return
'''


def _build(tmp, text, label='pylab'):
    from _ui_project import build_project
    from storygraph.app.editor import EditorSession
    from storygraph.graph.models import Graph

    root = build_project(Path(tmp) / 'proj')
    script = root / 'game' / 'script' / 'py.rpy'
    script.write_text(text, encoding='utf-8')
    session = EditorSession(project_root=root)
    res = session.import_chapters([script], Graph())
    node = next(n for n in res['graph'].nodes.values()
                if n.label_name == label)
    session.load(script, label=label)
    return session, res['graph'], node


def _insert(session, graph, node, ops, beat, where):
    """往 `beat` 那一拍插一条，返回产物文本。"""
    if where == 'head':
        anchors = {len(ops): (beat, 'head')}
    else:
        anchors = {len(ops): beat}
    session.apply_node_variable_edits(
        graph, node.id, list(ops) + ['probe_new = 1'],
        anchors=anchors, insert_beat=beat)
    return str(session.compile().rpy_text or '')


def _report(title, text):
    print('=' * 70)
    print(title)
    print('=' * 70)

    with tempfile.TemporaryDirectory() as tmp:
        session, graph, node = _build(tmp, text)
        data = session.variable_workbench(graph, node.id)

        print('--- 源码 ---')
        print(text.rstrip())

        print('\n[A] 下拉里能选的块清单（`_chapter_blocks`）')
        blocks = data.get('blocks') or []
        if not blocks:
            print('    （空）')
        for b in blocks:
            print(f"    {b.get('text')!r} kind={b.get('kind')!r} "
                  f"beat={b.get('beat')} anchor={b.get('anchor')}")

        print('\n[B] 每条操作被标成「在哪个块里」（`_source_block_of`）')
        seen = set()
        beat_of = None
        for row in (data.get('variables') or []):
            for op in (row.get('operations') or []):
                key = (op.get('text'), op.get('beat_index'))
                if key in seen:
                    continue
                seen.add(key)
                print(f"    第{op.get('beat_no')}拍 文本={op.get('text')!r}")
                print(f"           block={op.get('block')!r} "
                      f"indent={op.get('indent')} "
                      f"行号={op.get('line')} 拍下标={op.get('beat_index')}")
                if op.get('beat_index') is not None:
                    beat_of = op['beat_index']

        print(f'\n[C] 落点拍 = {beat_of}')

        for where in ('end', 'head'):
            with tempfile.TemporaryDirectory() as t2:
                s2, g2, n2 = _build(t2, text)
                d2 = s2.variable_workbench(g2, n2.id)
                body = _insert(s2, g2, n2, list(d2.get('operations') or []),
                               beat_of, where)
            print(f'\n[D-{where}] 插到那一拍的「{where}」→ 产物')
            for ln in body.splitlines():
                if ln.strip() and not ln.startswith('#'):
                    print(f'      {ln}')
            if 'probe_new' not in body:
                print('      ⚠ 新操作没进产物')
            else:
                line = next(l for l in body.splitlines()
                            if 'probe_new' in l)
                stripped = line.strip()
                if stripped.startswith('$ '):
                    print(f'      → 写成 {stripped!r}  （脚本层写法）')
                else:
                    print(f'      → 写成 {stripped!r}  （裸 python 写法）')
                print(f'      → 缩进 {len(line) - len(line.lstrip())} 空格')


def main():
    _report('一、python: 块里套 for', PY_FOR)
    print()
    _report('二、python: 块，平铺语句', PY_FLAT)
    print()
    _report('三、对照：脚本层的 if 块', PLAIN_IF)


if __name__ == '__main__':
    main()
