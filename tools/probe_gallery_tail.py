"""
最小复现：`character_gallery.rpy` 改一句台词，**另一句的文案跟着没了**。

现象（`tools/hell_cross_file.py --only 真实`）：
    改第 234 行（`"系统" "你已解锁角色【万理】…"`）之后，
    第 240 行（`"系统" "你已解锁隐藏角色的人物介绍！"`）变成只有 `"系统"` ——
    台词正文丢了，而那一拍**我根本没碰**。

这个探针只做一件事：把这条链路摊开 —— beats 长什么样、目标拍是谁、
补丁产物的逐行差异。产物写临时目录，真实项目只读。
"""
from __future__ import annotations

import difflib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.app.editor import EditorSession

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')
WORK = Path(r'C:\Users\28749\AppData\Local\Temp\gallery_probe')


def main() -> int:
    src = REAL / 'game' / 'character_gallery.rpy'
    if not src.exists():
        print(f'没有真实项目：{src}')
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    dst = WORK / src.name
    shutil.copyfile(src, dst)

    s = EditorSession(project_root=REAL)
    s.load(dst)

    print(f'共 {len(s.beats)} 拍。**全部**列出来：')
    for i, b in enumerate(s.beats):
        print(f'  [{i:3d}] kind={b.kind!r:12s} speaker={b.speaker!r:10s} '
              f'text={str(b.text or "")!r}')
        print(f'         src={getattr(b, "src_first", None)}..'
              f'{getattr(b, "src_last", None)} '
              f'code={str(getattr(b, "code", "") or "")!r}')

    # 关键一问：改**别的地方**（`$` 那一拍），那两句"双字符串 say"会不会照样被截断？
    # 若会 → 缺陷不是"改这一句连累那一句"，而是"模型里就没有后半段台词，
    # 于是**只要这一章动过任何一处**，它们就会被改写成残缺版本"。
    target = s.beats[0]
    print(f'\n目标拍 = [{0}] kind={target.kind!r} code={target.code!r}')
    target.code = 'persistent.wanli_unlocked = True  # 改过了'
    res = s.export(dst)
    print(f'\nexport ok={res.get("ok")} note={res.get("note")!r}')

    before = src.read_text(encoding='utf-8').splitlines()
    after = dst.read_text(encoding='utf-8').splitlines()
    print('\n--- 逐行差异（原文 vs 产物）---')
    for ln in difflib.unified_diff(before, after, lineterm='', n=2):
        print(ln)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
