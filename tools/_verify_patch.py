#!/usr/bin/env python
"""
验证 `patch_export` 重构**逐字段等价**。

做法：从 git 取 HEAD 版本的 `patcher.py`，与工作区版本对
**同一批真实章节 + 同一批改造过的拍**各跑一遍，比较 `PatchResult`
的每个字段（`text` / `matched` / `changed` / `reasons` / 各种计数器）。

为什么要比 `text` 全文而不只比 `ok`：增量补丁的价值就在"写出来的
那几行"，`ok` 相同而正文不同等于悄悄改了产物。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FIELDS = ('ok', 'matched', 'changed', 'stale_beats', 'unlanded_lines',
          'dropped_beats', 'inserted_lines', 'inserted_beats',
          'removed_lines', 'grown_lines', 'shrunk_lines', 'reasons')


def snap(res):
    out = {f: getattr(res, f, None) for f in FIELDS}
    out['reasons'] = list(out['reasons'] or [])
    out['text'] = res.text or ''
    return out


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/story/patcher.py'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    tmp = ROOT / 'tools' / '_patcher_head.py'
    tmp.write_text(old_src, encoding='utf-8')
    try:
        OLD = load(tmp, '_patch_head')
        NEW = load(ROOT / 'storygraph' / 'story' / 'patcher.py',
                   '_patch_new')

        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.story.beat_render import render_beats_to_rpy
        from _source_paths import REAL_PROJECT

        game = REAL_PROJECT / 'game'
        files = sorted(game.rglob('*.rpy'))
        print(f'真实项目脚本 {len(files)} 个')

        cases = 0
        bad = 0

        def compare(label, beats, src_path, full_text):
            nonlocal cases, bad
            cases += 1
            a = snap(OLD.patch_export(list(beats), src_path, full_text))
            b = snap(NEW.patch_export(list(beats), src_path, full_text))
            if a == b:
                return
            bad += 1
            if bad <= 4:
                print(f'** {label} 不一致')
                for k in ('ok', 'matched', 'changed', 'inserted_lines',
                          'removed_lines', 'reasons'):
                    if a[k] != b[k]:
                        print(f'   {k}: 旧={a[k]!r}  新={b[k]!r}')
                if a['text'] != b['text']:
                    print(f'   text 不同（旧 {len(a["text"])} 字 / '
                          f'新 {len(b["text"])} 字）')

        # —— 每个脚本：原样跑一次（应当"不改动"），再跑"改台词/插拍/删拍" ——
        for fp in files[:14]:
            az = IncrementalRenPyAnalyzer()
            pr, _ = az.analyze(files=[fp], graph=None)
            beats = beats_from_parse_result(pr)
            if not beats:
                continue
            full = render_beats_to_rpy(beats)

            # ① 原样（无改动）
            compare(f'{fp.name} 原样', beats, fp, full)

            # ② 改第一句台词
            import copy
            b2 = copy.deepcopy(beats)
            for x in b2:
                if getattr(x, 'text', ''):
                    x.text = (x.text or '') + '（改）'
                    break
            compare(f'{fp.name} 改台词', b2, fp,
                    render_beats_to_rpy(b2))

            # ③ 删一张卡（第 2 拍）
            if len(beats) > 2:
                b3 = copy.deepcopy(beats)
                del b3[1]
                compare(f'{fp.name} 删拍', b3, fp,
                        render_beats_to_rpy(b3))

            # ④ 插一张卡（末尾）
            from storygraph.story.beat_model import Beat
            b4 = copy.deepcopy(beats)
            b4.append(Beat(seq=999, label=beats[0].label, kind='say',
                           speaker='e', text='插进来的新台词'))
            compare(f'{fp.name} 插拍', b4, fp,
                    render_beats_to_rpy(b4))

        # —— 边界 ——
        from storygraph.story.beat_model import Beat
        one = [Beat(seq=1, label='a', kind='say', speaker='e', text='x')]
        compare('不存在的源文件', one, ROOT / 'no_such_file.rpy', 'x\n')
        compare('空 beats', [], files[0], 'x\n')

        print(f'\n比了 {cases} 例，不一致 {bad} 例')
        return 1 if bad else 0
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
