"""重生 6 个金标指纹（2026-10-31，ATL 那一刀的签字）。

**只在这几种变化都已被逐条确认时才跑**：
  · parse：12 个文件 = 12 个「含 show」的文件；payload 多 `atl`；
           chapter3_1 的 33 条 + screens 的 2 条从 `raw` 变 `show`；
  · render：screens.rpy 那两条带块 show 从「文件级块」进 beat，
           产物文字逐字一致（已人工比对）；
  · patch / hydrate / describe / validate：随 parse 变（下游）。

格式（两种，按各测试的读取方式反向构造）：
  · 空格交替型 —— 第一行总指纹，之后 `名字 哈希` 交替（parse / hydrate / render）
  · 首行 + JSON 型 —— 第一行总指纹，第二行起整段 JSON（describe / patch / validate）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(ROOT / 'tests')]

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
G = ROOT / 'tests'


def write_pairs(name: str, fp: str, per: dict) -> None:
    body = [fp]
    for k in sorted(per):
        body += [k, str(per[k])]
    (G / name).write_text('\n'.join(body) + '\n', encoding='utf-8')
    print(f'  {name}: {len(per)} 条')


def write_head_json(name: str, fp: str, payload) -> None:
    text = fp + '\n' + json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  indent=1) + '\n'
    (G / name).write_text(text, encoding='utf-8')
    print(f'  {name}: 已写（json {len(json.dumps(payload))} 字节）')


def main() -> int:
    if not REAL.is_dir():
        print('真机项目不在本机 —— 不重生（换机不该改金标）')
        return 2

    skipped = []

    def step(name, fn):
        print(name)
        try:
            fn()
        except ImportError as e:
            # 本机没装 openpyxl（pip 无网）→ 这几条**必须**在他那边重生，
            # 不能拿假桩糊过去：patch/hydrate/validate 真要读写 xlsx。
            skipped.append((name, str(e)))
            print(f'  跳过（{e}）')

    def p_parse():
        import probe_parse_fingerprint as m
        fp, per = m.fingerprint(REAL)
        write_pairs('golden_parse_fingerprint.txt', fp, per)

    def p_render():
        import probe_render_fingerprint as m
        fp, per, _n = m.fingerprint(REAL)
        write_pairs('golden_render_fingerprint.txt', fp, per)

    def p_describe():
        import probe_describe_fingerprint as m
        fp, _t, cases = m.fingerprint(REAL)
        write_head_json('golden_describe_fingerprint.txt', fp, cases)

    def p_hydrate():
        import probe_hydrate_fingerprint as m
        fp, _t, _pf, per_hash = m.fingerprint(REAL)
        write_pairs('golden_hydrate_fingerprint.txt', fp, per_hash)

    def p_patch():
        import probe_patch_fingerprint as m
        fp, _t, per = m.fingerprint(REAL)
        write_head_json('golden_patch_fingerprint.txt', fp, per)

    def p_validate():
        import probe_validate_fingerprint as m
        fp, text, _c, _n = m.fingerprint(REAL)
        write_head_json('golden_validate_fingerprint.txt', fp,
                        json.loads(text) if isinstance(text, str) else text)

    for name, fn in (('parse', p_parse), ('render', p_render),
                     ('describe', p_describe), ('hydrate', p_hydrate),
                     ('patch', p_patch), ('validate', p_validate)):
        step(name, fn)

    if skipped:
        print('\n⚠ 这些没重生（本机缺依赖）：' + '、'.join(n for n, _ in skipped))
        print('  在有 openpyxl 的环境里重跑本脚本即可补上。')
    return 0 if not skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
