# -*- coding: utf-8 -*-
"""第三轮审计探针：两个未审面。

A. `ui_qt/window_nodes.py:_write_new_chapter` 的**编码腐蚀**：
   读用 `source_style.read_text`（`errors='replace'`）→ 写用 `write_text_atomic`
   （**默认 backup=False**）。文件里只要有一个非法 UTF-8 字节，"加第二个 label"
   就会把坏字节静默换成 U+FFFD 且不留备份。
   对照：`choice_point` 的追加路径为同一 hazard 专门改成严格解码 + 明确拒绝
   （源码注释原话："坏字节被替换成 \\ufffd 就是**改坏用户文件**"）——
   这里漏了。

B. 纯文字导入（`authoring/txt_pipeline.run`）整链往返：
   ① 有未映射写法 → 不决定就一个字都不写；
   ② 决定后落盘 → 检查产物字节（标记 / 换行 / 内容）；
   ③ 重复导入同一份 txt → 幂等（逐字节相同）；
   ④ 导入后**手改**产物再重导 → 手改被覆盖（文档化行为 + 有无 .bak）；
   ⑤ GBK 输入 → 明确跳过，什么都不写；
   ⑥ 与项目里已有 label 重名 → 门禁拦下。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FAIL = []


def check(cond, msg):
    print(f'  [{"PASS" if cond else "FAIL"}] {msg}')
    if not cond:
        FAIL.append(msg)


class _FakeView:
    """`_write_new_chapter` 只用到 view._status。"""

    def __init__(self):
        self.messages = []

    def _status(self, text=''):
        self.messages.append(str(text))


def probe_a_write_new_chapter():
    print('== A. _write_new_chapter 对非 UTF-8 文件的行为 ==')
    from storygraph.ui_qt.window_nodes import _write_new_chapter

    tmp = Path(tempfile.mkdtemp(prefix='sg_r3a_'))
    game = tmp / 'game'
    game.mkdir(parents=True)
    fp = game / 'ch01.rpy'
    # 注释里一个 Latin-1 的 0xE9（"café" 的 é）—— 非法 UTF-8 字节；
    # 中文行照常 UTF-8（铁律 10：坏样本用字节拼，不写字面量）
    raw = ('label ch01:\n'
           '    # note caf').encode('utf-8') + b'\xe9\n' \
        + ('    "第一句"\n'
           '    return\n').encode('utf-8')
    fp.write_bytes(raw)
    before = fp.read_bytes()
    print(f'  原文件 {len(before)} 字节，含非法字节 0xE9：'
          f'{bytes([0xE9]) in before}')

    view = _FakeView()
    wrote = _write_new_chapter(view, str(fp), 'ch02')
    print(f'  返回：{wrote!r}')
    print(f'  状态栏消息：{view.messages}')
    after = fp.read_bytes() if fp.exists() else b''
    print(f'  写后 {len(after)} 字节')
    print(f'  0xE9 还在吗：{bytes([0xE9]) in after}')
    print(f'  出现 U+FFFD 了吗：{b"\xef\xbf\xbd" in after}')
    baks = list(game.glob('*.bak'))
    print(f'  .bak 备份：{[b.name for b in baks]}')
    # 新 label 确实加上了吗（功能本身要对）
    text = after.decode('utf-8', 'replace')
    print(f'  含 label ch02：{"label ch02:" in text}')
    print(f'  原有行还在：{all(x in text for x in ("label ch01:", "return"))}')

    # —— 判据（2026-10-25 修 P1-A 之后重写）——
    #
    # 这条探针**当初**是用来举证腐蚀的（"坏字节被换成 U+FFFD"才算命中）。
    # P1-A 修好之后正确的行为变成了**明确拒绝**，所以判据要跟着改成"目标行为"，
    # 否则探针会永远红下去（"探针红 = 有缺陷"这个约定不能被历史判据反过来用）。
    #
    # 允许两种正确形态：
    #   ① 拒绝（`返回 ''`）且**字节一个不动**、状态栏说清"不是 UTF-8"；
    #   ② 真写成功了，但坏字节**还在**（没有 U+FFFD）且行数只多了新 label。
    corrupt = b'\xef\xbf\xbd' in after
    refused = (wrote == '') and after == before
    wrote_cleanly = (wrote != '') and not corrupt
    check(not corrupt and (refused or wrote_cleanly)
          and ('UTF-8' in ' '.join(view.messages) if refused else True),
          'A-1 非 UTF-8 不被腐蚀：要么拒绝并说清（字节不动），要么写成功但坏字节还在')
    check(corrupt or not baks, 'A-2 没有"腐蚀照旧 + 无备份"的组合')

    # 对照：choice_point 的追加路径对同一形状的文件是严格拒绝
    print('  —— 对照 choice_point 追加路径 ——')
    from storygraph.story.choice_point import (apply_choice_point,
                                               plan_choice_point)
    fp2 = game / 'ch01.rpy'                    # 章节本体（合法 UTF-8）
    fp2.write_bytes('label ch01:\n    "第一句"\n    return\n'
                    .encode('utf-8'))
    choices = game / 'ch01_choices.rpy'        # 已存在的选择点文件（带坏字节）
    choices.write_bytes(raw)
    before2 = choices.read_bytes()
    plan = plan_choice_point(tmp, fp2, 'ch01')
    print(f'  plan.ok={plan.ok} target={Path(plan.path).name} '
          f'creates_file={plan.creates_file}')
    res = apply_choice_point(plan, tmp)
    print(f'  apply 返回：ok={res.get("ok")} '
          f'error={(res.get("error") or "")[:50]}')
    print(f'  文件字节未动：{choices.read_bytes() == before2}')
    check(choices.read_bytes() == before2,
          'A-3 对照：choice_point 严格解码拒绝、字节未动（同 hazard 两种待遇）')
    shutil.rmtree(tmp, ignore_errors=True)


def probe_b_txt_import():
    print('== B. 纯文字导入往返 ==')
    from storygraph.assets.asset_registry import ensure_asset_tables
    from storygraph.authoring import txt_pipeline as TP

    tmp = Path(tempfile.mkdtemp(prefix='sg_r3b_'))
    root = tmp / 'proj'
    (root / 'game').mkdir(parents=True)
    made = ensure_asset_tables(root)
    check(made.get('ok'), f'B-0 资源表就绪（{made.get("error")!r}）')

    # 非空角色表：`_looks_like_speaker` 的缩写路径要求 chars.by_key 非空
    # （"角色表非空才说明这是本项目"）—— 空表时 MOM 会被静默判成旁白（见 C 组）
    ROWS = {'lj': {'ID': 'lj', 'Name': '林静'}}

    txt = root.parent / '1-2 初见.txt'
    txt.write_text('1.2 chapter1-2 初见\n'
                   'MOM"你回来了。"\n'
                   '我点点头。\n'
                   'MOM"吃点东西。"\n', encoding='utf-8')

    # ① 未映射写法：不决定 → 什么都不写
    #    ⚠ 空角色表时 `_looks_like_speaker` 的缩写路径被 `chars.by_key` 非空
    #    这道闸挡住（"角色表非空才说明这是本项目"）→ `MOM"…"` 判成旁白、
    #    **不提案**。这是探针要量的事实，见 C 组。
    res = TP.run(root, [txt], rows=ROWS, project_root=root, ask=lambda p: None)
    print(f'  ① 空表 + 不决定：needs_decision={res.needs_decision} '
          f'written={[p.name for p in res.written]}')
    target = root / 'game' / 'chapter1_2.rpy'
    print(f'     目标存在吗：{target.exists()}')
    check(res.needs_decision and not res.written and not target.exists(),
          'B-1 未映射写法未确认 → 一个字都不写')

    # ② 决定后落盘
    res = TP.run(root, [txt], rows=ROWS, project_root=root,
                 ask=lambda proposals: proposals)
    print(f'  ② 决定后：ok={res.ok} written={[str(p) for p in res.written]}')
    print(f'     messages={res.messages[:3]}')
    check(res.ok and target.is_file(), 'B-2 决定后落盘')
    if not target.is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        return
    first = target.read_bytes()
    text = first.decode('utf-8')
    print(f'     产物行数={len(text.splitlines())} 字节={len(first)}')
    print(f'     含生成标记：{text.splitlines()[0].startswith("#")}')
    for ln in text.splitlines()[:6]:
        print(f'       | {ln}')
    check('label chapter1_2:' in text, 'B-2b label 来自章节头')
    check('你回来了' in text, 'B-2c 台词在产物里')
    check(b'\r\n' not in first, 'B-2d 新文件用 LF（无 CRLF 混入）')

    # ③ 重复导入同一份 → 幂等
    res = TP.run(root, [txt], rows=ROWS, project_root=root,
                 ask=lambda proposals: proposals)
    second = target.read_bytes()
    print(f'  ③ 重导：written={[str(p) for p in res.written]} '
          f'字节相同={second == first}')
    check(second == first, 'B-3 重复导入逐字节相同（幂等）')

    # ④ 手改产物后再重导 → 覆盖（文档化行为）
    hand = first + '    "手加的一句"\n'.encode('utf-8')
    # 插在 return 前更真实，但简单追加也能验证"覆盖"语义
    target.write_bytes(hand)
    res = TP.run(root, [txt], rows=ROWS, project_root=root,
                 ask=lambda proposals: proposals)
    third = target.read_bytes()
    mark = '手加的一句'.encode('utf-8')
    print(f'  ④ 手改后重导：手加句还在={mark in third} '
          f'字节与首次相同={third == first}')
    baks = list((root / 'game').glob('*.bak'))
    print(f'     .bak：{[b.name for b in baks]}')
    check(third == first, 'B-4 重导覆盖手改（标记文件=工具地盘，文档化行为）')
    check(bool(baks), 'B-4b 覆盖前有 .bak（手改可恢复）')

    # ⑤ GBK 输入：`read_text_file` 的链是 BOM→utf-8-sig→utf-8→gbk，
    #    GBK 是**受支持**的兜底（中文 Windows 用户的记事本默认存 GBK）——
    #    判据换成"解对内容、正常落盘、产物是 UTF-8"。
    gbk = root.parent / 'gbk.txt'
    gbk.write_bytes('1.9 chapter1-9 乱码章\nMOM"在吗"\n'.encode('gbk'))
    res = TP.run(root, [gbk], rows=ROWS, project_root=root,
                 ask=lambda proposals: proposals)
    gbk_target = root / 'game' / 'chapter1_9.rpy'
    print(f'  ⑤ GBK：written={[p.name for p in res.written]} '
          f'skipped={res.skipped}')
    ok_gbk = gbk_target.is_file()
    if ok_gbk:
        got = gbk_target.read_bytes().decode('utf-8')
        print(f'     产物含「在吗」：{"在吗" in got}（GBK 解对了吗）')
        check('在吗' in got and 'MOM' in got,
              'B-5 GBK 输入按设计兜底解码、内容正确')
    else:
        check(False, 'B-5 GBK 输入应该能导入（链里刻意留了 gbk 兜底）')

    # ⑥ label 重名（**另一个文件**里已有同 label）→ 门禁拦下
    other = root / 'game' / 'other.rpy'
    other.write_bytes('label chapter1_2:\n    "别人的一章"\n    return\n'
                      .encode('utf-8'))
    other_before = other.read_bytes()
    dup = root.parent / 'dup.txt'
    dup.write_text('1.2 chapter1-2 又来一章\nMOM"重复了"\n', encoding='utf-8')
    res = TP.run(root, [dup], rows=ROWS, project_root=root,
                 ask=lambda proposals: proposals)
    print(f'  ⑥ 重名：ok={res.ok} '
          f'blocked={[(Path(p).name, w[:36]) for p, w in res.blocked]}')
    check(not res.ok and res.blocked, 'B-6 label 重名被门禁拦下')
    check(other.read_bytes() == other_before, 'B-6b 被拦下后别的文件没被动')
    check(not (root / 'game' / 'chapter1_2.rpy').exists()
          or target.read_bytes() == third, 'B-6c 没有新写出重名文件')

    shutil.rmtree(tmp, ignore_errors=True)


def probe_c_quote_form_gap():
    """
    C. 引号形式说话人的"静默旁白化"缺口。

    `_detect_speaker` 认得两种形状：`名字：台词`（冒号）与 `NAME"台词"`（引号）。
    `_looks_like_speaker` 的两条判据：表命中 / **全大写缩写**（且要求角色表非空）。
    不认 → 整行判旁白。冒号形状有第二张网（`_suspect_speakers`，2026-10-11
    "修静默缺陷"补的）会**报警**；引号形状**没有**这张网
    （`_SUSPECT_SPEAKER_RE` 只匹配冒号形式）。

    于是：角色表为空 / 名字不是全大写缩写（中文名、混合大小写）时，
    `MOM"台词"` / `小雨"台词"` 会被静默写成旁白 —— 产物里说话人没了，
    没有任何提案、没有任何警告。
    """
    print('== C. 引号形式说话人的静默旁白化 ==')
    from storygraph.authoring.txt_import import CharacterMap, convert_text

    ROWS = {'lj': {'ID': 'lj', 'Name': '林静'}}
    cases = [
        ('空表 + 全大写缩写', {}, 'MOM"你回来了。"'),
        ('空表 + 中文名', {}, '小雨"你回来了。"'),
        ('非空表 + 混合大小写', ROWS, 'Mom"你回来了。"'),
        ('非空表 + 中文名', ROWS, '小雨"你回来了。"'),
        ('非空表 + 全大写缩写（对照）', ROWS, 'MOM"你回来了。"'),
        ('冒号形式（对照：有第二张网）', ROWS, '小雨：你回来了。'),
    ]
    for name, rows, line in cases:
        res = convert_text(line + '\n', characters=CharacterMap(rows),
                           filename='t.txt', label='x')
        first_body = [ln for ln in res.rpy_text.splitlines()
                      if ln.strip() and not ln.startswith('#')
                      and not ln.startswith('label')]
        prod = first_body[0] if first_body else ''
        is_say = res.counts.get('say', 0) > 0
        warn = bool(res.warnings)
        unmapped = bool(res.unmapped)
        print(f'  {name:26s} → 产物 {prod!r}')
        print(f'  {"":26s}   say={is_say} 未映射提案={unmapped} '
              f'警告={warn}{" ← 静默旁白化" if not is_say and not unmapped and not warn else ""}')
        if '对照' not in name:
            check(not (not is_say and not unmapped and not warn),
                  f'C {name}：要么认成台词、要么提案/警告，不许静默旁白化')

    shutil_note = '（探针不写盘，只调纯函数 convert_text）'
    print(f'  {shutil_note}')


def main():
    probe_a_write_new_chapter()
    print()
    probe_b_txt_import()
    print()
    probe_c_quote_form_gap()
    print()
    if FAIL:
        print(f'命中 {len(FAIL)} 条：')
        for f in FAIL:
            print('  -', f)
        return 1
    print('全部符合预期。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
