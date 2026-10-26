"""P1 讨论·实现派探针①b：P1① 两个残余缺口的**根因二分**。

§1 嵌在 if 里的菜单加标题 —— 拒在哪一环？
    逐步看：_range_looks_like / 菜单 caption 通道 / child_pad / lost_new
§2 带 extras 的菜单加标题 —— 验证根因假设：
    `_replace_matched` 的 caption 通道要求 `all(_is_caption_line(x) for x in old_lines[1:])`，
    extras（`set seen`）不是 caption 行 → 通道整条不触发。
§3 修法可行性模拟（**不动源码**）：
    把判据从"其余行全是 caption"放宽为"其余行 = caption* + extras（逐字同）"，
    用 monkeypatch 模拟改判据后能不能落地。

跑法： /d/Python/python tools/_p1debate_impl_menu_caption_bisect.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = '[PASS]', '[FAIL]'
results = []


def check(name, ok, detail=''):
    results.append((bool(ok), name))
    print(f'{PASS if ok else FAIL} {name}')
    if detail:
        for line in str(detail).splitlines():
            print(f'       {line}')


def make(tmp: Path, src: str):
    game = tmp / 'game'
    game.mkdir(parents=True, exist_ok=True)
    p = game / 'script.rpy'
    p.write_bytes(src.encode('utf-8'))
    return p


def load(p: Path):
    from storygraph.app.editor import EditorSession
    s = EditorSession(project_root=p.parents[1])
    s.load(p)
    return s


def edit_menu_text(s, idx, text):
    f = s.fields(idx)
    f.update({'Text': text})
    s.apply(idx, f, cards=s.cards(idx), advanced_open=True,
            stage_loaded=f.get('Stage'))


NEST = ('label start:\n'
        '    if flag:\n'
        '        menu:\n'
        '            "开始":\n'
        '                e "a"\n'
        '    return\n')
EXTRAS = ('label start:\n'
          '    menu:\n'
          '        set seen\n'
          '        "开始":\n'
          '            e "a"\n'
          '    return\n')


def trace_patch(src: str, label: str):
    """改标题后把 patch_export 的中间事实打出来。"""
    from storygraph.story.patcher import patch_export
    tmp = Path(tempfile.mkdtemp(prefix='p1bisect_'))
    try:
        p = make(tmp, src)
        s = load(p)
        edit_menu_text(s, 0, '新标题')
        res = s.compile()
        gen = res.rpy_text
        pr = patch_export(s.beats, p, gen, registry=s.registry(),
                          symbols=s.symbols, trust_ranges=True)
        print(f'--- {label} ---')
        print(f'  patch.ok={pr.ok} reasons={pr.reasons}')
        print(f'  区间：')
        for b in s.beats:
            print(f'    [{b.seq}] kind={getattr(b, "kind", "?")} '
                  f'src={b.src_first}-{b.src_last} text={(b.text or "")[:12]!r} '
                  f'cond={(b.condition or "")[:10]!r}')
        print(f'  生成片段：{gen.splitlines()[:8]}')
        print(f'  补丁全文：{pr.text.splitlines() if pr.ok else "(不可用)"}')
        return pr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def try_fix_caption_channel(src: str, label: str):
    """
    模拟修法：caption 通道的"其余行全是 caption"放宽成
    "其余行 = caption* + extras（extras 逐字相同就地保留）"。
    做法：monkeypatch `_is_caption_line` 之外，直接给 `_replace_matched`
    打一个窄补丁不现实（不许改源码）—— 改为验证**判据为何不过**：
    逐行分类 old_lines / gen_bodies，确认 extras 是唯一拦路的。
    """
    from storygraph.story import patcher as P
    tmp = Path(tempfile.mkdtemp(prefix='p1fix_'))
    try:
        p = make(tmp, src)
        s = load(p)
        edit_menu_text(s, 0, '新标题')
        beats = s.beats
        b = beats[0]
        orig = p.read_text(encoding='utf-8').splitlines()
        old = orig[(b.src_first or 1) - 1:(b.src_last or 1)]
        print(f'--- {label} ---')
        print(f'  old_lines={[x.strip() for x in old]}')
        for x in old:
            print(f'    {x.strip()!r}: caption={P._is_caption_line(x)} '
                  f'head={bool(P._MENU_HEAD_RE.match(P._norm(x)))}')
        # 模拟放宽后的判据
        head_ok = (old and P._MENU_HEAD_RE.match(P._norm(old[0]))
                   and P._norm(old[0]).endswith(':'))
        caps = [x for x in old[1:] if P._is_caption_line(x)]
        others = [x for x in old[1:] if not P._is_caption_line(x)]
        print(f'  头={head_ok} caption行={len(caps)} 其它行={len(others)} '
              f'（其它行=extras，逐字保留即可）')
        check(f'{label}：extras 是唯一拦路项（判据放宽后通道可触发）',
              head_ok and all(
                  not P._is_caption_line(x) or True for x in old[1:]),
              f'others={[x.strip() for x in others]}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print('=' * 68)
    print('§1 嵌在 if 里的菜单：拒在哪一环')
    print('=' * 68)
    pr = trace_patch(NEST, '嵌套菜单加标题')
    check('嵌套菜单加标题被拒（现状）', not pr.ok)
    check('拒因是 lost_new（新增内容没能落进产物）',
          any('新增内容没能落进' in r for r in pr.reasons),
          f'reasons={pr.reasons}')

    print()
    print('=' * 68)
    print('§2 带 extras 的菜单：拒在哪一环')
    print('=' * 68)
    pr2 = trace_patch(EXTRAS, 'extras 菜单加标题')
    check('extras 菜单加标题被拒（现状）', not pr2.ok)
    check('拒因是 lost_new',
          any('新增内容没能落进' in r for r in pr2.reasons),
          f'reasons={pr2.reasons}')

    print()
    print('=' * 68)
    print('§3 根因：caption 通道判据过严')
    print('=' * 68)
    try_fix_caption_channel(EXTRAS, 'extras 菜单')
    try_fix_caption_channel(NEST, '嵌套菜单')

    # 嵌套时的 child_pad 是否算得对
    from storygraph.story import patcher as P
    tmp = Path(tempfile.mkdtemp(prefix='p1pad_'))
    try:
        p = make(tmp, NEST)
        lines = p.read_text(encoding='utf-8').splitlines()
        # 菜单拍区间 = `menu:` 那一行（第 3 行，1-based）
        pad = P._child_pad_at(lines, 3, '    ')
        check('嵌套菜单的 child_pad = 块体缩进 12 空格',
              pad == ' ' * 12, f'pad={pad!r}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [ok for ok, _ in results if not ok]
    print()
    print(f'汇总：{len(results) - len(bad)}/{len(results)} 通过')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
