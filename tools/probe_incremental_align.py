"""
O1 取证：真实项目里那 6 个"只改一句台词却退回全量"的文件，**卡在哪一条判据**上。

清单 `docs/导出缺陷清单_2026-10-05.md` 的 O1 只给了现象
（`note = 增量未采用：1 处新增内容没能落进产物`）。退回全量的直接原因是
`patcher.patch_export` 末尾那句：

    lost_new += 1        # 对位守卫不过，而生成结果里有"原文里根本没有"的行

所以真正要问的是：**那一拍为什么没过 `_range_looks_like`／
`_narration_slot_replaced`**。这个探针把两边的入参和返回值都打出来：

    python tools/probe_incremental_align.py            # 全部 40 个文件
    python tools/probe_incremental_align.py script.rpy  # 只跑一个
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL = Path(r'D:\千连万理\RenPyTest\sr_general')


def _files(only: list) -> list:
    game = REAL / 'game'
    files = sorted(p for p in game.rglob('*.rpy')
                   if not p.name.endswith('.generated.rpy'))
    if only:
        files = [p for p in files if p.name in only]
    return files


def _probe_one(src: Path, tmp: Path, verbose: bool) -> dict:
    from storygraph.app.editor import EditorSession
    from storygraph.story import patcher

    d = tmp / src.stem
    d.mkdir(parents=True, exist_ok=True)
    dst = d / src.name
    shutil.copyfile(src, dst)
    original = dst.read_text(encoding='utf-8')

    calls: list = []
    orig_guard = patcher._range_looks_like
    orig_slot = patcher._narration_slot_replaced

    def guard(old_lines, beat, gen_lines=None, exact_lines=None):
        got = orig_guard(old_lines, beat, gen_lines, exact_lines)
        calls.append(('guard', got, beat, list(old_lines or []),
                      list(gen_lines or [])))
        return got

    def slot(beat, first, last, old, gen, index, blocks, strict_of):
        got = orig_slot(beat, first, last, old, gen, index, blocks, strict_of)
        # 把「旁白整句换写」那条兜底的**内部判据**摊开：它依赖邻居，
        # 而邻居是不是 strict 只有这里能看见。
        nbs = []
        for j, f, l, _g, _b in blocks:
            if j == index or l < f:
                continue
            if l < first or f > last:
                nbs.append((j, f, l, bool(strict_of.get(j, False))))
        calls.append(('slot', got, beat, list(old or []), list(gen or []),
                      nbs, len([ln for ln in old if str(ln).strip()]),
                      len([ln for ln in gen if str(ln).strip()])))
        return got

    patcher._range_looks_like = guard
    patcher._narration_slot_replaced = slot
    try:
        s = EditorSession(project_root=src.parents[2])
        s.load(dst)
        target = next((b for b in s.beats
                       if (b.text or '').strip()
                       and not (b.text or '').startswith('\x00')), None)
        if target is None:
            return {'file': src.name, 'skip': '没有带台词的拍'}
        old_text = target.text
        target.text = 'X改过的一句话X'
        res = s.export(dst)
    finally:
        patcher._range_looks_like = orig_guard
        patcher._narration_slot_replaced = orig_slot

    now = dst.read_text(encoding='utf-8')
    touched = sum(1 for a, b in zip(original.splitlines(), now.splitlines())
                  if a != b)
    out = {
        'file': src.name,
        'ok': res.get('ok'),
        'note': str(res.get('note') or ''),
        'blocking': list(res.get('blocking') or []),
        'lines': len(original.splitlines()),
        'touched': touched,
        'target': (target.kind, target.speaker, old_text),
        'src': (target.src_first, target.src_last),
        'stage': (target.show, target.hide, target.scene, target.stage),
        'calls': calls,
    }
    if verbose:
        _dump(out)
    return out


def _dump(info: dict) -> None:
    print('=' * 74)
    print(f"[{info['file']}] ok={info['ok']} note={info['note']!r}")
    print(f"  目标拍：kind={info['target'][0]!r} speaker={info['target'][1]!r} "
          f"text={info['target'][2]!r}")
    print(f"  src={info['src']} 舞台字段={info['stage']}")
    for call in info.get('calls', []):
        kind, got, beat, old, gen = call[:5]
        if beat is not info.get('_target_obj') and not _near(beat, info):
            continue
        print(f"  ── {kind}({getattr(beat, 'text', '')!r}) → {got}")
        print(f"       原文段 : {old}")
        print(f"       生成段 : {gen}")
        if kind == 'slot':
            print(f"       邻居（j, first, last, strict）：{call[5]}")
        if not got:
            _why(beat, old, gen)


def _near(beat, info: dict) -> bool:
    want = info['target'][2]
    if (getattr(beat, 'text', '') or '') in (want, 'X改过的一句话X'):
        return True
    first, last = info['src']
    sf = getattr(beat, 'src_first', 0) or 0
    return bool(first and sf == first)


def _why(beat, old: list, gen: list) -> None:
    """逐条判据说一遍"为什么不过"（与 `_range_looks_like` 一一对应）。"""
    import re
    blob = '\n'.join(old or [])
    text = (getattr(beat, 'text', '') or '').strip()
    speaker = (getattr(beat, 'speaker', '') or '').strip()
    checks = [
        ('正文在原文段里', bool(text) and text in blob),
        ('说话人在原文段里', bool(speaker)
         and bool(re.search(rf'(?<![\w]){re.escape(speaker)}(?![\w])', blob))),
        ('raw/code 行在原文段里',
         any(ln.strip() and ln.strip() in blob
             for attr in ('raw', 'code')
             for ln in (getattr(beat, attr, '') or '').splitlines())),
        ('舞台指纹在原文段里', any(t.split(':', 1)[-1] in blob
                                   for t in _stage_tokens(beat))),
        ('没台词也没舞台 → 放行', not text and not _stage_tokens(beat)),
    ]
    for name, ok in checks:
        print(f"       [{'过' if ok else '×'}] {name}")
    if text and not speaker and not _stage_tokens(beat):
        old_n = [ln.strip() for ln in (old or []) if str(ln).strip()]
        gen_n = [ln.strip() for ln in (gen or []) if str(ln).strip()]
        print(f"       旁白形状兜底：旧 {len(old_n)} 行 / 新 {len(gen_n)} 行 → "
              f"{'行数不等，判据失效' if len(old_n) != len(gen_n) else '行数相等（再看相似度 ≥0.6）'}")


def _stage_tokens(beat) -> list:
    from storygraph.story.patcher import _stage_tokens as f
    return list(f(beat))


def main(argv) -> int:
    only = argv[1:]
    tmp = Path(tempfile.mkdtemp(prefix='align_'))
    rows = []
    for src in _files(only):
        try:
            rows.append(_probe_one(src, tmp, verbose=bool(only)))
        except Exception as exc:                            # noqa: BLE001
            rows.append({'file': src.name, 'error': repr(exc)})
    print(f'\n{"文件":<30}{"行数":>6}{"改动":>6}  退回理由')
    degraded = []
    for r in rows:
        if 'error' in r:
            print(f'{r["file"]:<30}  ERROR {r["error"]}')
            continue
        if 'skip' in r:
            continue
        why = '；'.join(r.get('blocking') or []) or r['note']
        print(f'{r["file"]:<30}{r["lines"]:>6}{r["touched"]:>6}  {why[:78]}')
        if r.get('blocking'):
            degraded.append(r)
    print(f'\n退回全量的文件：{len(degraded)} 个')
    for r in degraded:
        # 只打"没过守卫"的那几拍
        bad = [c for c in r['calls'] if not c[1]]
        print(f'--- {r["file"]}（{len(bad)} 拍没过守卫）')
        for call in bad[:4]:
            kind, _got, beat, old, gen = call[:5]
            print(f'    {kind}: kind={beat.kind!r} text={beat.text!r} '
                  f'speaker={beat.speaker!r} src={beat.src_first}-{beat.src_last}')
            print(f'        原文段 {old}')
            print(f'        生成段 {gen}')
            _why(beat, old, gen)
            if kind == 'slot':
                nbs, n_old, n_gen = call[5], call[6], call[7]
                print(f'        旁白槽兜底：非空行 旧{n_old}/新{n_gen} → '
                      f'{"行数不等" if n_old != n_gen else "行数相等"}')
                print(f'        邻居（j, first, last, strict）：{nbs}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
