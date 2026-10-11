"""
资源体检报告（把 `EditorSession.asset_health_report()` 的结果变成给人看的文本）。

用户 2026-10-05：

> 以及，你说有缺失资源时，可以这么做：在「变量分析」右边加一个检查资源
> 完整性的功能，后出示检查报告

为什么单独一个模块：报告是**纯函数**（dict → 字符串），可以脱开 Qt 测；
界面只负责把它塞进只读对话框（复用 `AnalysisDialog`，带过滤与复制）。
"""

from __future__ import annotations

from storygraph.core import engine_facts as EF

#: 每类问题最多列几条样例（报告是给人看的，不是日志倾倒场）
SAMPLE = 8


def _samples(items, kind: str = 'path') -> list:
    lines = []
    for it in list(items)[:SAMPLE]:
        if isinstance(it, dict):
            lines.append('    · ' + ' / '.join(
                f'{k}={v}' for k, v in it.items() if v not in (None, '')))
        else:
            lines.append(f'    · {it}')
    if len(items) > SAMPLE:
        lines.append(f'    …（还有 {len(items) - SAMPLE} 条，'
                     f'复制报告后用 Ctrl+F 找「{kind}」）')
    return lines


def _emit_custom_statement_section(stmts, cds, cds_error, out):
    """把「项目自定义语句」那一段写进报告（M4 从 `format_asset_health` 搬出，行为一字不改）。"""
    if stmts is not None or cds is not None or cds_error:
        out.append('')
        out.append('【⑧ 项目自定义语句（清单声明 + Ren\'Py CDS）】'
                   '—— 这是**能力状态**，不算上面那类问题')
        if cds_error:
            out.append(f'    ⚠ 没读成：{cds_error}')
        try:
            if stmts is not None:
                # 合并视图（M3）：优先级已经算好（清单覆盖宿主注册）。
                from storygraph.story.statement_registry import (
                    format_registry_report)
                text = format_registry_report(stmts)
            else:
                from storygraph.story.cds_registry import format_cds_report
                text = format_cds_report(cds or [])
            out.append('    ' + text.replace('\n', '\n    '))
        except Exception:                       # noqa: BLE001 - 报告不该因此失败
            out.append(f'    （读到 {len(stmts or cds or [])} 条声明）')

def _emit_engine_align_section(align: dict, out: list) -> None:
    """
    「资源表 ↔ 引擎事实」对账（E2 收尾）。**没有就整段不出现**（不宣称）。

    为什么这一段不改任何东西、也不建议删：
    两个权威回答的是**两个不同问题** —— 文件系统答"什么东西**存在**"，
    引擎声明答"什么东西**可选**"。表里有、引擎不认的（`mask` / `_ss_cut` 这类
    内部件）**文件真实存在、仍然可选、仍然能导入**；
    拿引擎的枚举去删候选，就会连"合法的、只是引擎还没认的"一起删掉。
    """
    if not align:
        return
    table_only = align.get('table_only') or {}
    engine_only = align.get('engine_only') or {}
    spelling = align.get('spelling_only') or {}
    if not table_only and not engine_only and not spelling:
        return
    out.append('')
    out.append('【⑨ 资源表 ↔ 引擎 对账】—— 这是**状态**，不是问题；'
               '候选与导入仍以实际存在的资源为准')
    n_s = sum(len(v) for v in spelling.values())
    if n_s:
        # ⚠ 真机逼出来的：不加这一桶，这个项目会报 175 项，其中 76 项只是
        # `bg1.1` / `bg1_1` 这种写法差异 —— 那样的清单没人看，等于没有报告。
        out.append(f'  · 只是写法不同（`bg1.1` vs `bg1_1` 这种，**不算差异**）：'
                   f'{n_s} 项')
    n_t = sum(len(v) for v in table_only.values())
    n_e = sum(len(v) for v in engine_only.values())
    out.append(f'  · 表里有、引擎不认：{n_t} 项'
               '（多半是掩码/切图这类**内部件**；选了它引擎会报错，'
               '但**不影响导入**）')
    for tag, words in sorted(table_only.items())[:8]:
        out.append(f'      {tag}: {"、".join(words[:10])}'
                   + (' …' if len(words) > 10 else ''))
    out.append(f'  · 引擎有、表里没登记：{n_e} 项'
               '（引擎的名字是**脚本里的真名**，`show` 要用的就是它；'
               '建议在资源表里补上）')
    for tag, words in sorted(engine_only.items())[:8]:
        out.append(f'      {tag}: {"、".join(words[:10])}'
                   + (' …' if len(words) > 10 else ''))


def _emit_engine_unknown_section(unknown: dict, out: list) -> None:
    """
    「⑩ 剧本里引擎不认的立绘属性」汇总（路线图 §8.9 的"报出来"）。**没有就整段不出现**。

    与卡片那条逐拍提示（`card_note` → `beat_unknown_attrs`）**同源**，
    这里只是汇总一次：一个项目到底有多少处会 lint 报错、集中在哪几个角色身上。
    ⚠ **只是汇总**：这些属性仍然可选、仍然能导入（用户 2026-10-11 的口径）。
    """
    if not unknown:
        return
    n = sum(len(v) for v in unknown.values())
    out.append('')
    out.append(f'【⑩ 剧本里引擎不认的立绘属性】{n} 处、'
               f'涉及 {len(unknown)} 个角色 —— 这是**提示**，不是"要删掉"')
    out.append('    选了它们，`renpy lint` 会报 `\'lan xxx\' is not an image`；'
               '但它们**文件真实存在、照样能选、照样能导入**。')
    for tag, words in sorted(unknown.items(),
                             key=lambda kv: (-len(kv[1]), kv[0]))[:10]:
        out.append(f'    {tag}: {"、".join(words[:12])}'
                   + (' …' if len(words) > 12 else ''))
    if len(unknown) > 10:
        out.append(f'    …（还有 {len(unknown) - 10} 个角色，复制报告后搜「⑩」）')


def _emit_undetermined_section(summary: dict, out: list) -> None:
    """
    「⑪ 扫描 vs 引擎：能不能问出结论」（第四层）。**没有就整段不出现**。

    为什么必须有这一段（用户 2026-10-11 四层方案的第四层："诚实标记测不出来，
    不当假绿"）：扫描发现的"工具不认识的写法"，**测不出来是常态** ——
    未知写法本身就是 parse error，而 `init python`（注册 CDS 的地方）属于
    后面的解析组（`renpy/script.py:447-452`），引擎连注册代码都没机会跑。
    只显示"✅ 采集完成"就是假绿。
    """
    text = EF.format_undetermined(summary or {})
    if not text:
        return
    out.append('')
    out.extend(text.splitlines())


def format_asset_health(report: dict) -> str:
    """
    `asset_health_report()` 的结果 → 体检报告文本。

    `problems == 0` 就是"表与资源一一对应"，报告第一行先把结论说清楚。
    """
    report = report or {}
    if not report.get('ok'):
        return (f'资源体检没做成：{report.get("error") or "未知原因"}\n'
                f'（先打开一个项目；读不到资源表时看控制台里那行提示）')
    problems = int(report.get('problems') or 0)
    counts = report.get('counts') or {}
    out = []
    if problems == 0:
        out.append('✅ 结论：资源表与项目资源**一一对应**（没有发现需要处理的问题）')
    else:
        out.append(f'⚠ 结论：发现 {problems} 处需要处理的地方 —— 见下')
    out.append('')
    out.append('【表里的条数】')
    out.append(f'   图片 {counts.get("images", 0)} · 音乐 {counts.get("music", 0)}'
               f' · 音效 {counts.get("sounds", 0)}'
               f' · 语音 {counts.get("voices", 0)}'
               f' · 变换 {counts.get("transforms", 0)}'
               f' · 角色 {counts.get("characters", 0)}')

    missing = report.get('missing') or {}
    n_missing = sum(len(v) for v in missing.values())
    out.append('')
    out.append(f'【① 表里有、磁盘上没有】{n_missing} 条'
               + ('（打开项目时**已经自动删掉**了；删不掉才会留在这儿）'
                  if n_missing == 0 else ''))
    for kind, rows in missing.items():
        out.append(f'  · {kind} {len(rows)} 条')
        out.extend(_samples(rows))

    unreg = report.get('unregistered') or {}
    n_unreg = sum(len(v) for v in unreg.values())
    outside = report.get('outside_game') or {}
    out.append('')
    out.append(f'【② 磁盘上有、表里没登记】{n_unreg} 条'
               + ('（打开项目时**已经自动补登**了）' if n_unreg == 0 else ''))
    for kind, rows in unreg.items():
        out.append(f'  · {kind} {len(rows)} 条')
        out.extend(_samples(rows))
    if n_unreg and int(outside.get('count') or 0):
        # 实测：项目里存在"另一份副本"时，副本里的文件会一直被算成"未登记"
        # —— 不是漏登记，而是**同一份资源有两份**，而表里那行只指了其中一份。
        out.append('    ⚠ 你有 {0} 行指向 `game/` 之外（多半是**另一份副本**）。'
                   '这些"未登记"里有相当一部分是**副本里的同名文件**：'
                   '表里一行只能指一个位置，所以另一份永远是"没登记"。\n'
                   '       想彻底对齐，得先决定留哪一份（见下）。'
                   .format(int(outside.get('count') or 0)))

    outside = report.get('outside_game') or {}
    n_out = int(outside.get('count') or 0)
    out.append('')
    out.append(f'【③ 表里的行指向本项目 `game/` 之外】{n_out} 条')
    if n_out:
        for top, n in sorted((outside.get('by_top') or {}).items(),
                             key=lambda kv: -kv[1])[:6]:
            out.append(f'    · {top}/ —— {n} 行')
        out.append('    （多半是**另一份副本**的旧行，或者 `tmp/` 这类'
                   '现在已经不扫的目录；这些行**不会自动删**，'
                   '因为删掉可能影响剧情引用）')

    dup_ids = report.get('duplicate_ids') or []
    out.append('')
    out.append(f'【④ 同一个 ID 出现多次】{len(dup_ids)} 个')
    if dup_ids:
        out.extend(_samples(dup_ids, 'ID'))

    dup_paths = report.get('duplicate_paths') or []
    out.append('')
    out.append(f'【⑤ 同一个文件登记了多行（一对一被破坏）】{len(dup_paths)} 条')
    if dup_paths:
        out.extend(_samples(dup_paths, 'Path'))

    no_path = report.get('no_path') or []
    out.append('')
    out.append(f'【⑥ 行里没写 Path】{len(no_path)} 条')
    if no_path:
        out.extend(_samples(no_path, '没有'))

    ph = report.get('placeholders') or []
    out.append('')
    out.append(f'【⑦ 人工占位（没有文件但明确标了「占位」）】{len(ph)} 条'
               f' —— 这是**合法**的，不会被动')
    if ph:
        out.extend(_samples(ph))

    # 【⑧ 自定义语句】——**不是缺陷，是能力状态**（M2，2026-10-07）。
    #
    # 为什么放这里：用户说过「若是程序自定义了一个结构，而编剧用这个编辑器
    # 编辑时发现：你没有适应到它的自定义结构怎么办？他会立即退出，因为工具
    # 没有用！」。这一段的唯一职责就是**把这个对话显性化** ——
    # 项目声明了什么、工具接住了没有、接不住时该怎么办。
    stmts = report.get('statements')
    cds = report.get('cds')
    cds_error = report.get('cds_error') or ''
    _emit_custom_statement_section(stmts, cds, cds_error, out)
    _emit_engine_align_section(report.get('engine_align') or {}, out)
    _emit_undetermined_section(report.get('engine_undetermined') or {}, out)
    _emit_engine_unknown_section(report.get('engine_unknown') or {}, out)

    out.append('')
    out.append('—— 说明 ——')
    out.append('· 打开项目时会自动对齐：磁盘上有的补进来、文件已经不在的删掉'
               '（写前都备份 `*.bak.xlsx`）。')
    out.append('· 想保留一行"还没有出图"的资源：在那一行的 `Notes` 里写「占位」，'
               '它就不会被自动删，而且仍然可选。')
    return '\n'.join(out)


__all__ = ['format_asset_health', 'SAMPLE']
