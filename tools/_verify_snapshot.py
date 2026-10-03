#!/usr/bin/env python
"""
验证 `stage_snapshots` 重构**逐字段等价**。

做法：从 git 取 HEAD 版本的 `stage_snapshot.py`，与工作区版本对
**同一批真实 Beat** 各跑一遍，把结果做深度比较。

为什么用真 Beat 而不是手搓：这个函数的复杂度全在"真实写法"上
（`show bg X` 换背景、`hide cg03`、同 tag 换表情、幂等重复、
`scene` 清空立绘…）。手搓的样例覆盖不到那些分支。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def norm(obj):
    """把结果弄成可比较的规范形（set → 排序 list）。"""
    if isinstance(obj, dict):
        return {k: norm(v) for k, v in sorted(obj.items(), key=str)}
    if isinstance(obj, (set, frozenset)):
        return sorted(str(norm(x)) for x in obj)
    if isinstance(obj, (list, tuple)):
        return [norm(x) for x in obj]
    return obj


def main() -> int:
    old_src = subprocess.run(
        ['git', 'show', 'HEAD:storygraph/story/stage_snapshot.py'],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    tmp = ROOT / 'tools' / '_snapshot_head.py'
    tmp.write_text(old_src, encoding='utf-8')
    try:
        OLD = load(tmp, '_snap_head')
        NEW = load(ROOT / 'storygraph' / 'story' / 'stage_snapshot.py',
                   '_snap_new')

        # —— 采集真 Beats ——
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        # `_source_paths` 在 tests/ 下（它按模块名问真实文件，不硬编码路径）
        sys.path.insert(0, str(ROOT / 'tests'))
        from _source_paths import REAL_PROJECT

        game = REAL_PROJECT / 'game'
        files = sorted(game.rglob('*.rpy'))
        if not files:
            print(f'!! 找不到真实项目脚本：{game}')
            return 1
        print(f'真实项目脚本 {len(files)} 个')

        az = IncrementalRenPyAnalyzer()
        pr, _ = az.analyze(files=files, graph=None)
        beats = beats_from_parse_result(pr)
        print(f'解析出 {len(beats)} 拍')

        # 全量一批 + 逐文件小批（小批能把"状态跨拍累积"的差异也照出来）
        batches = [('全量', beats)]
        by_file: dict = {}
        for b in beats:
            by_file.setdefault(getattr(b, 'label', '') or '', []).append(b)
        for i, (label, bs) in enumerate(sorted(by_file.items())):
            if i >= 40:
                break
            batches.append((f'label={label}', bs))
        # 每 25 拍一段（切断累积状态，制造更多边界）
        for i in range(0, min(len(beats), 400), 25):
            batches.append((f'切片 {i}', beats[i:i + 25]))

        bad = 0
        for name, bs in batches:
            a = norm(OLD.stage_snapshots(bs))
            b = norm(NEW.stage_snapshots(bs))
            if a == b:
                continue
            bad += 1
            if bad <= 3:
                print(f'** {name} 不一致（{len(bs)} 拍）')
                sa = json.dumps(a, ensure_ascii=False, sort_keys=True)
                sb = json.dumps(b, ensure_ascii=False, sort_keys=True)
                for k in range(min(len(sa), len(sb))):
                    if sa[k] != sb[k]:
                        print('   旧:', sa[max(0, k - 90):k + 90])
                        print('   新:', sb[max(0, k - 90):k + 90])
                        break
        # 空输入
        for edge in ([], None):
            if norm(OLD.stage_snapshots(edge)) != norm(
                    NEW.stage_snapshots(edge)):
                print(f'** 边界输入 {edge!r} 不一致')
                bad += 1
        print(f'\n比了 {len(batches)} 批（+2 个边界），不一致 {bad} 批')
        return 1 if bad else 0
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    sys.exit(main())
