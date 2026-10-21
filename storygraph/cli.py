#!/usr/bin/env python
"""
cli.py
======
命令行入口：让这套工具能被 CI、脚本、以及不爱点鼠标的程序使用。

    python cli.py validate <项目根>            # 只体检
    python cli.py build <项目根> --engine renpy # 体检 + 出产物
    python cli.py table rpy2xlsx <a.rpy>       # 单文件摊表
    python cli.py table xlsx2rpy <a.xlsx>      # 表写回脚本
    python cli.py collab init|pull|push|status|lock|unlock|approve

退出码：0 通过；1 有错误；2 用法/环境问题。CI 直接看退出码。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_diff3(args) -> int:
    """
    **表 vs 源码**的拍级对账（只读）：`story/beat_merge.three_way`（设计文档 §5 第 6 步）。

    为什么要有这条命令：导出/覆盖之前，"表里到底改了什么"现在只能靠肉眼
    或全量重生成 —— 有了它就能先看一眼（**冲突时它是说出口的唯一地方**）。
    口径：`base` = 源码、`right` = 源码（同一份）→ 表侧的改动都会落进
    `left_changed` / `conflicts`；源码侧没动，所以不会出现假冲突。

    `--json` 出机器读的那份（`Delta.to_dict()`），方便接进 CI 或界面提示。
    """
    import json as _json

    from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
    from storygraph.story.beat_build import beats_from_parse_result
    from storygraph.story.beat_merge import format_report, three_way
    from storygraph.story.excel_import import import_beats_from_excel

    src = Path(args.source)
    if not src.is_file():
        print(f'找不到源码文件：{src}', file=sys.stderr)
        return 2
    xlsx = Path(args.excel) if args.excel else src.with_suffix('.xlsx')
    if not xlsx.is_file():
        print(f'找不到表文件：{xlsx}（先用 `table rpy2xlsx` 生成）',
              file=sys.stderr)
        return 2
    pr, _extra = IncrementalRenPyAnalyzer().analyze(files=[src], graph=None)
    from_source = beats_from_parse_result(pr)
    from_table = import_beats_from_excel(xlsx)
    delta = three_way(from_source, from_table, from_source)
    if args.json:
        print(_json.dumps(delta.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f'源码：{src}\n表：  {xlsx}')
        print(format_report(delta))
    return 0 if delta.clean else 1


def _cmd_learn(args) -> int:
    """
    **把一条结构"教给"工具**：写进 `storygraph.rules.json` 的 `statements`
    （设计文档 `docs/archive/设计_自定义结构发现与交互_2026-10-07.md` §5 第 3 步）。

    为什么要有个命令（而不是只写文档）：设计的核心一句是
    **"得知必须落成一份可评审的文件"** —— 手改 JSON 当然也行，但那是
    "知道格式的人"才敢做的事；这个入口让"教一次"变成一条谁都能跑的指令，
    而且它**拒绝覆盖坏清单**（见 `declare_statement`），
    所以不会把用户手写的东西弄丢。

    参数里的参数形状写成 `名字[:标签[:候选,逗号隔开]]`，可以给多个
    （`--param intensity:强度:1,2,3 --param target:目标`）。
    """
    import json as _json

    from storygraph.story.statement_registry import declare_statement

    columns = []
    for spec in (args.param or ()):
        parts = str(spec).split(':')
        col = {'name': parts[0].strip()}
        if len(parts) > 1 and parts[1].strip():
            col['label'] = parts[1].strip()
        if len(parts) > 2 and parts[2].strip():
            col['choices'] = [c.strip() for c in parts[2].split(',')
                              if c.strip()]
        if col['name']:
            columns.append(col)
    block = None
    if args.block:
        block = True
    elif args.no_block:
        block = False

    res = declare_statement(args.project, args.name, label=args.label,
                            columns=columns, block=block, docs=args.docs)
    if args.json:
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    elif res['ok']:
        verb = '更新' if res['action'] == 'updated' else '新增'
        print(f'{verb}声明：{args.name}'
              + (f'（{len(columns)} 个参数）' if columns else '')
              + f'\n  写进：{res["path"]}'
              + f'\n  现在 `describe` 会把它算作 declared。')
    else:
        print(f'没有写：{res["reason"]}', file=sys.stderr)
    return 0 if res['ok'] else 1


def _cmd_describe(args) -> int:
    """
    **把"工具以为的自定义结构"摊开**（设计文档 §2.1 的第一档接口，只读）。

    为什么要有这个命令（用户 2026-10-07 问："工具如何得知自定义结构、
    怎么和程序员直接交互调节"）：

      · 没有它，人只能猜"工具到底认不认我的 `mycue`、认成什么档、凭什么"；
      · 有了它，**认知可 diff**：把输出存进版本库，
        `git diff --exit-code 现状.json` 就能抓住
        "代码改了（CDS）、工具的认知没跟上"这种漂移 —— 这正是设计文档里
        "C 反射负责防漂移"那一条的落地形态。

    它**只读**：不写任何文件（要"调节"就改 `storygraph.rules.json` 的
    `statements` 或代码里的 `renpy.register_statement`，然后重跑它）。
    """
    import json as _json

    from storygraph.story.statement_registry import describe_statements

    data = describe_statements(project_root=args.project)
    if args.json:
        print(_json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    s = data['summary']
    print(f"项目：{data['project']}")
    print(f"见过的语句头 {s['heads']} 个（已声明 {s['declared_heads']} / "
          f"只靠猜 {s['guessed_heads']}）")
    print(f"需要动作的 {s['needs_action']} 个")
    print()
    print(f"{'语句':<18}{'来源':<14}{'保真':<6}{'次数':>6}  参数 / 说明")
    for r in data['statements'][:args.limit]:
        cols = '，'.join(c['name'] for c in r['columns']) or '—'
        src = {'declared': '声明', 'host-registry': 'CDS',
               'shape-guess': '猜的'}.get(r['source'], r['source'])
        print(f"{r['name']:<18}{src:<14}{r['fidelity']:<6}"
              f"{r['occurrences']:>6}  {cols}")
        for w in r['warnings']:
            print(f"{'':<18}⚠ {w}")
    hidden = len(data['statements']) - args.limit
    if hidden > 0:
        print(f'…（还有 {hidden} 个，用 --limit 调）')
    return 0


def _cmd_validate(args) -> int:
    """
    只体检：**不写盘**。

    2026-09-28 修：以前这里走 `build_project(engine='json')` —— 而 build
    一定会写产物，于是"只体检"的命令会顺手把 `story_ir.json` 写进
    `out_dir`（默认 `<项目根>/build_out/`），**悄悄改了用户的工作区**。

    现在走 `collect_report()`：它与编辑器里的「项目体检」面板是同一份逻辑，
    且明确承诺"不产出任何文件"。数字口径由 `collect_stats()` 统一，
    面板看到的与 CI 看到的必然一致。
    """
    from storygraph.pipeline.build import collect_report, collect_stats

    collected = collect_report(
        args.project, asset_dir=args.asset_dir, char_limit=args.char_limit)
    report = collected['report']

    if args.json:
        print(report.to_json())
    else:
        stats = collect_stats(collected)
        print(f"项目：{args.project}")
        print(f"章节脚本 {len(stats.get('scripts', []))} 个，"
              f"演出 {stats.get('lines', 0)} 行 / "
              f"{stats.get('labels', 0)} 章 / "
              f"文本 {stats.get('chars', 0)} 字")
        if stats.get('registry'):
            print('资源表：' + '，'.join(
                f'{k} {v}' for k, v in stats['registry'].items()))
        print()
        print(report.format())
    return 0 if report.ok else 1


def _cmd_build(args) -> int:
    from storygraph.pipeline.build import build_project, format_build_result

    result = build_project(
        args.project, asset_dir=args.asset_dir, out_dir=args.out,
        engine=args.engine, strict=args.strict, char_limit=args.char_limit,
        engine_lint=not bool(getattr(args, 'no_engine_lint', False)))
    print(format_build_result(result))
    if result.report_path:
        print(f'\n报告：{result.report_path}')
    return 0 if result.ok else 1


def _report_guard(result, out, stream=None, source=None) -> bool:
    """
    打印生成门禁结论；被拦下返回 False（调用方据此给退出码 1）。

    为什么单独抽出来：`table xlsx2rpy` 与 `compile` 两条命令的落盘策略相同，
    门禁结论必须**一致地**报给人（stderr）与 CI（退出码）——
    否则 CI 看到 0 却拿不到产物，问题被吞掉。
    """
    stream = stream or sys.stderr
    if not result.written and not result.guard_issues and not result.review_path:
        # 空表那条路（`skip_empty`）：没有产物也没有门禁结论，
        # 调用方按 `result.empty` 给退出码 2，这里不冒充"被拦下"。
        return False
    if result.written:
        print(f'已生成脚本：{out}')
        return True
    print(f'生成门禁拦下，未写入目标路径：{out}', file=stream)
    for issue in result.guard_issues:
        print(f'  {issue.format()}', file=stream)
    if result.review_path:
        print(f'  已留副本供对照：{result.review_path}', file=stream)
    if any(getattr(i, 'code', '') == 'ENGINE_PARSE_ERROR'
           for i in result.guard_issues):
        # **表改了、文件没改** —— 这个状态必须说清楚（2026-10-19，P1-3）。
        # 用户此刻手里是"表里删了一拍、文件里还在"的两份东西，而报告只写
        # "门禁拦下"：他既不知道该信哪份，也不知道怎么退回去。
        print('  源文件没有被改动：它仍是导出前的样子（表里的这次改动没有生效）。',
              file=stream)
        print('  修好表里出问题的那几行后重新导出即可；想回到原样，'
              '把这次改动撤销（改回原值）再导一次。', file=stream)
    codes = {getattr(i, 'code', '') for i in result.guard_issues}
    if 'HANDWRITTEN_TARGET' in codes:
        # 这一条**有**逃生舱，而且和上面那条"坏脚本"的性质不同：
        # 不是产物有问题，是"目标是人手写的"。所以提示要分开给，
        # 否则用户会以为自己的产物坏了。
        if source and not result.used_patch and result.patch_attempted:
            # **给了 --source 却没配上**（2026-10-21，P0）：`--source` 指的
            # 就是目标本身，而这一刀是全量。此前这里走 `else`，说的是
            # "目标是你手写的 .rpy" —— 用户明明给了底稿，却被告知"再给一次
            # 底稿"，方向被彻底带偏。真话是：表和源码对不上。
            print('  这次**没能**增量合并（表与源码对不上，原因见上），'
                  '而 `--source` 指的正是目标文件本身 —— 全量产物会丢掉源里'
                  '不在表内的语句（程序员新加的 `pause` / 台词 / `$` 代码）。',
                  file=stream)
            print('  先把这一章重新载入（让表和源码对上）再导出；'
                  '确认产物完整、就是要全量覆盖时加 `--force`。', file=stream)
        elif result.used_patch:
            # ⚠ 补丁成功时**必须说真话**（2026-10-05）：这一次是在原文上
            # **合并**出来的，`default` / `init python` / `transform` 逐字保留，
            # 一刀下去什么都不会丢。笼统地说"会丢掉这些"是假话，而它恰好
            # 出现在最常见的那条路上（改自己章节里的一句台词、原地导出）。
            print('  这次是**增量**：只替换改过的那几行，原文其余部分逐字保留。',
                  file=stream)
            print('  写入只会在首行加一条生成标记（工具据此认出自家产物），'
                  '确认后加 --force 即可。', file=stream)
        else:
            print('  目标是你手写的 .rpy，工具不敢覆盖它（会丢掉 default / '
                  'init python / transform）。', file=stream)
            if not source:
                # 2026-10-05 拓展边界②：以前这里**只**能建议 `--force`（= 明知会丢
                # 东西还要覆盖）。现在还有一条**无损**的路：给出源 .rpy 让它增量合并
                # —— 改一句台词就只改那一行，第三类内容原样留着。
                # 这条路必须被说出来，否则用户只会看到"要么别改、要么全毁"。
                print('  想保住 default / init python / transform：加 '
                      '--source <这一章原来的 .rpy>，只增量改你改过的行。',
                      file=stream)
            print('  确认要覆盖（或换个 --out 路径）时加 --force。', file=stream)
    else:
        print('  （坏脚本落进 game/ 会让游戏起不来，所以门禁拒绝写入）',
              file=stream)
    return False


def _report_incremental(result, stream=None) -> None:
    """
    把"这次走的是增量还是全量"报出来。

    为什么必须报（拓展边界②的纪律）：退回全量意味着**第三类内容不在产物里**。
    无头 CLI 上没人能看见这件事 —— 不说，就等于静默降级。

    但**只在"本来该走增量"时才说**（`patch_attempted`）：没给 `--source`
    时全量是正常默认，每次都喊一句只会让人开始忽略这类提示 ——
    而那正是 `OPAQUE_LOST` 最需要被看见的时候。
    """
    stream = stream or sys.stdout
    if result.used_patch:
        if getattr(result, 'untouched', False):
            # 零改动往返：以前这里报"改动 0 行"却把文件重写了一遍（还多插
            # 2 行生成标记）。现在是真的没碰 —— 说清楚，别让人以为写过了。
            print('增量补丁：0 行改动，原文逐字节相同（文件没有被改动）',
                  file=stream)
        else:
            print(f'增量补丁：改动 {result.changed_lines} 行，'
                  f'原文其余部分（含 default / init python / transform）逐字节保留',
                  file=stream)
    elif result.patch_attempted and result.patch_note:
        print(f'增量未采用（{result.patch_note}）→ 已按全量生成',
              file=stream)
        # **全量只认得表里的拍**（2026-10-21，P0 的另一半）：目标不是源文件
        # 本身时（`--out 新路径`），门禁那条"原地覆盖要拦"的闸不适用，
        # 产物照写 —— 于是用户拿到一份少了行数、退出码却是 0 的产物。
        # 这里必须把"少了什么"说出来，否则"退回全量"三个字看着像正常降级。
        if result.written:
            print('  ⚠ 这份产物里**没有**源文件中不在表内的语句'
                  '（导表之后程序员新加的 `pause` / 台词 / `$` 代码等）。'
                  '要保住它们：先让表和源码对上（重新导入这一章）再导出。',
                  file=stream)


def _cmd_table(args) -> int:
    from storygraph.story.script_table import open_rpy_as_table

    registry = _load_registry(args)

    if args.action == 'rpy2xlsx':
        out = open_rpy_as_table(args.path, args.out, registry=registry)
        print(f'已生成剧本表：{out}')
        return 0
    if args.action == 'xlsx2rpy':
        # 落盘逻辑全在 `pipeline.compile.compile_table`（2026-10-05 收口）。
        # 这里以前自己又写了一遍 import + compile_beats + write_generation ——
        # 与 `compile_table` 是同一件事的两份实现，于是新增的 `--source`
        # （增量合并且保住第三类内容）得改两处，漏一处就是**静默退回全量**。
        from storygraph.pipeline.compile import (compile_table,
                                                 format_compile_result)

        # 默认输出：给了 `--source` 就**写回那一章**（2026-10-18，审计 P1-7
        # "写回不闭环"）—— 从前默认是 xlsx 旁边的同名 .rpy，产物落不到
        # 原章节上，用户还得自己搬。没给 `--source` 就交给 `compile_table`：
        # 表里记着来源（隐藏表「表信息」）时它同样写回那一章，否则落表旁边。
        if args.out:
            out = Path(args.out)
        elif getattr(args, 'source', None):
            out = Path(args.source)
        else:
            out = None
        _path, _sheet, result = compile_table(
            args.path, rpy_path=out, registry=registry, write_sheet=False,
            force=bool(getattr(args, 'force', False)),
            source_path=getattr(args, 'source', None), skip_empty=True,
            engine_lint=not bool(getattr(args, 'no_engine_lint', False)))
        out = Path(_path)
        if result.empty:
            print('剧本表为空', file=sys.stderr)
            return 2
        if not _report_guard(result, out,
                             source=getattr(args, 'source', None)):
            return 1
        _report_incremental(result)
        print()
        print(format_compile_result(result))
        return 0
    print(f'未知动作：{args.action}', file=sys.stderr)
    return 2


# ==================================================================
#  collab：Excel 前端 + 数据库后端
# ==================================================================

def _collab_db(args) -> Path:
    if args.db:
        return Path(args.db)
    return Path(args.project) / 'collab.db'


def _cmd_collab(args) -> int:
    import storygraph.collab.collab_store as cs

    db = _collab_db(args)
    action = args.action

    if action == 'init':
        users = [u for u in (args.users or '').split(',') if u.strip()]
        cs.init_db(db, users=users, owner=args.owner)
        print(f'已建库：{db}')
        if users:
            print('成员：' + '，'.join(users))
            print('角色：' + '，'.join(
                f"{m['id']}[{m['role_label']}]" for m in cs.members(db)))
        print('下一步：collab pull 把库导出成 Excel 交给文案；')
        print('        collab push 把改好的 Excel 写回库。')
        return 0

    if not db.exists():
        print(f'找不到数据库 {db}，先跑 collab init', file=sys.stderr)
        return 2

    if action == 'pull':
        if not args.sheet:
            print('pull 需要 --sheet 指定要写出的 Excel', file=sys.stderr)
            return 2
        registry = _load_registry(args)
        info = cs.pull(db, args.sheet, actor=args.user, registry=registry)
        print(f"已导出 {info['rows']} 行到 {info['sheet']}")
        print(f'（含「{cs.COLLAB_SHEET}」工作表：Revision / LockedBy / Status，'
              f'push 时用它判断冲突）')
        return 0

    if action == 'push':
        if not args.sheet:
            print('push 需要 --sheet 指定要写回的 Excel', file=sys.stderr)
            return 2
        res = cs.push(db, args.sheet, actor=args.user, force=args.force)
        print(f"新增 {len(res['added'])}，更新 {len(res['updated'])}，"
              f"未变 {len(res['unchanged'])}，"
              f"复活 {len(res.get('revived') or ())}，"
              f"自动合并 {len(res.get('merged') or ())}，"
              f"冲突 {len(res['conflicts'])}，被拒 {len(res['rejected'])}")
        # **工具替你做了什么，必须说**（"自动合并要报告"）
        print('  ' + cs.auto_merge_summary(res))
        for c in res['conflicts']:
            kind = str(c.get('kind') or '')
            if kind == 'fields':
                fields = '、'.join(cs.field_label(f.get('field'))
                                  for f in (c.get('fields') or ()))
                print(f"  你和 {c.get('updated_by') or 'TA'} 改了同一句 "
                      f"{c['line_id']}（{fields}）—— 在编辑器里选一个留下的版本")
                continue
            print(f"  冲突 {c.get('line_id') or c.get('label') or ''}"
                  f"（库里 rev {c.get('db_revision', 0)} > "
                  f"你基于的 rev {c.get('base_revision', 0)}，"
                  f"最后改于 {c.get('updated_by') or ''}）")
        for r in res['rejected']:
            print(f"  拒绝 {r['line_id']}：{r['reason']}")
        for k in (res.get('kept') or ())[:5]:
            print(f"  {k.get('hint')}")
        if res['conflicts'] or res['rejected']:
            print('先 pull 同步、或与对方沟通后用 --force 覆盖')
            return 1
        return 0

    if action == 'status':
        s = cs.status_summary(db)
        print(f"总行数 {s['lines']}，已锁 {s['locked_total']}")
        print('状态：' + '，'.join(f'{k} {v}' for k, v in s['by_status'].items()))
        if s['locked_by']:
            print('持锁人：' + '，'.join(f'{k} {v} 行'
                                      for k, v in s['locked_by'].items()))
        if s.get('expired_locks'):
            print(f"其中 {s['expired_locks']} 行锁已过期（别人可以接手）")
        if s.get('users'):
            print('成员：' + '，'.join(
                f"{u['id']}[{u.get('role_label') or u.get('role') or '编辑'}]"
                for u in s['users']))
        # 所有者是**事实**，不是暗示：没有 owner 的库谁都能认领一次。
        st = cs.owner_state(db)
        print('所有者：' + (st['owner'] if st['has_owner']
                        else '**还没有**（任何成员可认领一次）'))
        if s['labels']:
            print('按章：')
            for l in s['labels']:
                print(f"  {l['label']}: {l['total']} 行"
                      f"（approved {l['approved'] or 0}"
                      f"，in_review {l['in_review'] or 0}"
                      f"，locked {l['locked'] or 0}）")
        if s['recent']:
            print('最近变更：')
            for r in s['recent'][:8]:
                print(f"  {r['ts']} {r['actor']} {r['action']} {r['line_id']}")
        return 0

    if action == 'renew':
        res = cs.renew_lock(db, args.line_id, args.user, ttl=args.ttl)
        print(f"已续租到 {res.get('expires_at')}" if res['ok']
              else f"失败：{res['reason']}")
        return 0 if res['ok'] else 1

    if action == 'role':
        if not args.member:
            print('role 需要 --member 指定成员', file=sys.stderr)
            return 2
        res = cs.set_role(db, args.member, args.role, actor=args.user)
        if not res['ok']:
            print(f"失败：{res['reason']}")
            return 1
        print(f"已把 {res['member']} 设为 {res['role']}")
        if res.get('claimed'):
            # **说出来**：工具没有认证，"谁先认领谁就是所有者"是事实，
            # 但别让人以为这库一直是有主的（审计里已记 claimed=true）。
            print('⚠ 这是本库**第一次**认领所有者（此前没有 owner）——'
                  '已写入审计。'
                  f'（之后想换人，得由 owner 用 role --member … --role owner）')
        return 0

    if action == 'owner':
        st = cs.owner_state(db)
        if st['has_owner']:
            print(f"所有者：{st['owner']}（成员 {st['members']} 人）")
        else:
            print(f"本库**尚无所有者**（成员 {st['members']} 人）："
                  '谁先用 role --member <自己> --role owner 认领，谁就是所有者。')
        return 0

    if action == 'members':
        for m in cs.members(db):
            print(f"{m['id']}\t{m['role_label']}（{m['role']}）"
                  f"\t{m.get('display_name') or ''}")
        return 0

    if action == 'lock':
        if args.label:
            res = cs.lock_range(db, args.label, args.user, ttl=args.ttl)
            if res.get('ok') is False:
                print(f"失败：{res.get('reason') or '权限不足'}")
                return 1
            print(f"已锁 {len(res['locked'])} 行"
                  + (f"，跳过 {len(res['skipped'])} 行（别人持有）"
                     if res['skipped'] else ''))
            return 0
        res = cs.lock(db, args.line_id, args.user, note=args.note or '',
                      ttl=args.ttl)
        if res['ok']:
            print(f"已上锁（租约到 {res.get('expires_at')}）")
        else:
            print(f"失败：{res['reason']}")
        return 0 if res['ok'] else 1

    if action == 'unlock':
        res = cs.unlock(db, args.line_id, args.user, force=args.force)
        print('已解锁' if res['ok'] else f"失败：{res['reason']}")
        return 0 if res['ok'] else 1

    if action == 'approve':
        res = cs.set_status(db, args.line_id, args.status, args.user)
        print(f"已置为 {res.get('status')}" if res['ok']
              else f"失败：{res['reason']}")
        return 0 if res['ok'] else 1

    if action == 'import-ir':
        # 从 .rpy 灌进数据库：新项目第一次入库走这个
        from storygraph.pipeline.build import load_asset_registry
        from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
        from storygraph.story.beat_build import beats_from_parse_result
        from storygraph.pipeline.build import discover_scripts

        scripts = discover_scripts(args.project)
        analyzer = IncrementalRenPyAnalyzer()
        pr, _ = analyzer.analyze(files=scripts, graph=None)
        beats = beats_from_parse_result(pr)
        cs.init_db(db, users=[args.user])
        tmp = Path(args.sheet) if args.sheet else Path(args.project) / \
            '_import_tmp.xlsx'
        from storygraph.story.excel_export import export_beats_to_excel
        export_beats_to_excel(beats, tmp)
        res = cs.push(db, tmp, actor=args.user, force=True)
        cs.upsert_assets(db, load_asset_registry(args.project), actor=args.user)
        print(f"入库 {len(res['added'])} 行（来自 {len(scripts)} 个脚本）")
        return 0

    print(f'未知动作：{action}', file=sys.stderr)
    return 2


def _cmd_selfcheck(args) -> int:
    """
    **打包后的自检**：跑一遍主链路（尤其是导出），把结论说出来。

    为什么要做成命令（而不是只留 `StoryGraph.exe --selfcheck`）：
    交付出去的是 exe，用户/CI 需要一个**不需要装 Python** 就能跑的
    验证入口 —— 出问题时第一步就是"让用户跑一下自检，把输出发过来"。
    无头、不碰用户文件（产物写在临时目录里）。
    """
    from storygraph import selfcheck
    return selfcheck.main()


def _load_registry(args):
    from storygraph.pipeline.build import load_asset_registry
    if not getattr(args, 'project', None):
        return None
    return load_asset_registry(args.project, getattr(args, 'asset_dir', None))


def _drift_text(drift: dict) -> str:
    """漂移报告 → 人话（E3）。空字符串 = 没有漂移。"""
    if not drift or drift.get('same'):
        return ''
    out = ['检出**引擎行为变化**（这就是"漂移"）—— 与基线比：']
    if drift.get('engine', {}).get('version'):
        old, new = drift['engine']['version']
        out.append(f'  · 引擎版本：{old} → {new}')
    for key, items in sorted((drift.get('added') or {}).items()):
        out.append(f'  · 新增 {key}：{items[:8]}'
                   + (f' …共 {len(items)}' if len(items) > 8 else ''))
    for key, items in sorted((drift.get('removed') or {}).items()):
        out.append(f'  · 消失 {key}：{items[:8]}'
                   + (f' …共 {len(items)}' if len(items) > 8 else ''))
    for key in sorted((drift.get('changed') or {})):
        out.append(f'  · {key}：结构变了')
    out.append('  ⚠ 这不是"出错"——是**引擎/项目变了**。确认是有意改的就更新基线：')
    out.append('    把新的 engine_facts.json 提交进版本库即可。')
    return '\n'.join(out)


def _undetermined_exit(facts, *, strict: bool) -> tuple:
    """
    **第四层的门禁**：把"测不出来的写法"折算成退出码（纯函数，可单测）。

    返回 `(退出码, 提示文本)`：

      · 有 `undetermined` 且 `--strict` → **1**（可修：要么把写法补进声明，
        要么接受它测不出来）；提示文本说明为什么测不出来。
      · 有 `undetermined` 但**没加 `--strict`** → **0**（不偷偷把 CI 变红），
        但提示文本照样返回（让人看得见）。
      · 没有 → `(0, '')`。

    ⚠ 为什么不默认退 1：`undetermined` 的成因是**引擎的解析顺序**
    （未知写法让后面的解析组都不跑，见 `renpy/script.py:447-452`），
    不是这个项目"做错了什么"。默认变红会让门禁天天误报 ——
    那正是"人会把它关掉"的来源。所以**只在 `--strict` 下当失败**。
    """
    from storygraph.core import engine_facts as EF
    summary = EF.undetermined_statements(facts)
    text = EF.format_undetermined(summary)
    if not summary.get('undetermined'):
        return 0, ''
    n = len(summary['undetermined'])
    note = (f'⚠ 有 {n} 个写法**测不出来**（既没被引擎确认，也没被否认）'
            f'—— 它们不能当成"没问题"。')
    return (1 if strict else 0), (note + '\n' + text)


def _cmd_engine_facts(args) -> int:
    """
    **采集引擎事实**（E1）+ **漂移检测**（E3）。

    退出码约定（给 CI 用，**这是本节的全部意义**）：

        0  一切正常（或：只有"不阻塞"的提示）
        1  **可修**：检出漂移，或（加了 `--strict`）有语义/资源类问题
        2  **阻塞**：parse error，或采集失败（**采不到 ≠ 没问题**，坑 4）

    `--check-only` 不跑引擎，只比对两份现成的事实文件 —— **CI 里不需要 SDK**。
    """
    from storygraph.core import engine_facts as EF
    from storygraph.pipeline import engine_collect as EC

    project = Path(args.project)

    if args.check_only:
        # 只比对（无 SDK 也能跑；CI 用这条）
        cur_path = Path(args.out) if args.out else EF.facts_path(project)
        if not args.baseline:
            print('--check-only 需要同时给 --baseline <基线 facts.json>',
                  file=sys.stderr)
            return 2
        try:
            cur = EF.loads(Path(cur_path).read_text(encoding='utf-8'))
            base = EF.loads(Path(args.baseline).read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            print(f'读事实文件失败：{e}', file=sys.stderr)
            return 2
        drift = EF.diff_facts(base, cur)
        text = _drift_text(drift)
        print(text or '✅ 与基线一致：引擎行为没有变化。')
        # 第四层：`--check-only` 也能报"测不出来的写法"（不需要 SDK）
        code_u, note_u = _undetermined_exit(cur, strict=args.strict)
        if note_u:
            print()
            print(note_u)
        print()
        print(EF.format_behavior_line(cur))
        return 1 if (text or code_u) else 0

    result = EC.collect(project, sdk=args.sdk, mode=args.mode,
                        timeout=args.timeout)
    facts = result['facts']
    out_path = Path(args.out) if args.out else EF.facts_path(project)
    EC.write_facts(out_path, facts)

    coll = facts.get('collection') or {}
    print(f'引擎事实：{out_path}')
    print(f'  parse_clean    = {coll.get("parse_clean")}')
    print(f'  probe_complete = {coll.get("probe_complete")}')
    print(f'  lint 退出码     = {coll.get("lint_exit")}')
    for key in EF.FACT_KEYS:
        value = EF.value_of(facts, key)
        n = len(value) if isinstance(value, (list, dict)) else 0
        src = EF.source_of(facts, key) or '—'
        print(f'  {key:<11}{n:>6} 条   来源 {src}')

    lint = facts.get('lint') or {}
    for bucket in ('parse_errors', 'semantic_errors', 'program_errors'):
        rows = lint.get(bucket) or []
        if rows:
            print(f'  {bucket}：{len(rows)} 条（示例：{rows[0][:70]}）')
    over = (facts.get('derived') or {}).get('overridden_builtins') or []
    if over:
        print(f'  ⚠ 覆盖了内置语句：{[o.get("name") for o in over]}'
              '（覆盖=完全替换，形状不保证 —— N5）')
    for c in facts.get('conflicts') or []:
        print(f'  ⚠ 来源冲突：{c.get("field")} ← {c.get("sources")}')
    if result['manifest_ok'] is False:
        print('  ⚠ 采集前后 game/ 文本清单**不一致**（除 .rpyc 外本应一致）')
    if result['removed']:
        print(f'  已清理探针：{result["removed"]}')

    # ---- 漂移（E3）：与基线比 ----
    drift_text = ''
    if args.baseline:
        try:
            base = EF.loads(Path(args.baseline).read_text(encoding='utf-8'))
            drift_text = _drift_text(EF.diff_facts(base, facts))
        except (OSError, ValueError) as e:
            print(f'  ⚠ 基线读不动，跳过漂移检测：{e}')
        if drift_text:
            print()
            print(drift_text)

    # ---- 第四层：测不出来的写法，如实说（不当假绿）----
    und_text = ''
    code_u, und_text = _undetermined_exit(facts, strict=args.strict)
    if und_text:
        print()
        print(und_text)
    # ⭐ 行为级那一条：验过几个 / 没证明成几个（**没验过就明说没覆盖**）
    print()
    print(EF.format_behavior_line(facts))

    # ---- 退出码（CI 门禁）----
    if not result['ok']:
        print(f'✗ 采集不可用（阻塞）：{result["reason"]}')
        return 2
    n_block = len(lint.get('parse_errors') or [])
    n_fixable = len(lint.get('semantic_errors') or []) \
        + len(lint.get('program_errors') or [])
    if drift_text:
        print('✓ 采集完成；但**检出了漂移**（需人工确认）')
        return 1
    if code_u:
        print('✓ 采集完成；但**有测不出来的写法**（`--strict` 下算可修）')
        return 1
    if args.strict and n_fixable:
        print(f'✓ 采集完成；`--strict` 下有 {n_fixable} 条需修的问题')
        return 1
    print(f'✓ 采集完成（parse 错误 {n_block} / 需修 {n_fixable}'
          + ('；未比基线' if not args.baseline else '；无漂移') + '）')
    return 0


def _cmd_compile(args) -> int:
    """
    演出表 → .rpy（**不做全局校验**）。

    这是"策划写人话、工具翻代码"的主路径：缺资源只在脚本里留
    `# TODO: 缺少资源 …`，退出码仍然是 0——构建不该因为一句台词
    引用了一个还没登记的图片就整体失败。

    **落盘逻辑全在 `pipeline.compile.compile_table`**（2026-10-05 收口）：
    这里以前自己又写了一遍 `import_beats_from_excel` + `compile_beats` +
    `write_generation`，与 `compile_table` 是同一件事的两份实现 ——
    门禁结论的字段名、`force` 的传递都得改两处，迟早漂移
    （`excel/cells.py` 的 `set_text` 就是三份实现漂移后收敛的）。
    """
    from storygraph.pipeline.compile import (compile_table,
                                            format_compile_result,
                                            merge_export_sheet)

    table_path = Path(args.table)
    if not table_path.exists():
        print(f'找不到演出表：{table_path}', file=sys.stderr)
        return 2

    registry = _load_registry(args)
    # 与 `table xlsx2rpy` 同一条口径（2026-10-18，审计 P1-7）：给了 --source
    # 就默认**写回那一章**；都没给就让 compile_table 用"表里记的来源"，
    # 没有来源才落到表旁边。
    if args.out:
        out = Path(args.out)
    elif getattr(args, 'source', None):
        out = Path(args.source)
    else:
        out = None
    # `--force` 是逃生舱：允许覆盖已存在的手写 .rpy
    # （见 `docs/archive/P0_静默全量覆盖_2026-10-03.md`）。
    # `--source` 是**无损**那条路：给出这一章原来的 .rpy，走增量合并，
    # 只改你改过的行，default / init python / transform 原样保留。
    _path, sheet, result = compile_table(
        table_path,
        rpy_path=out,
        registry=registry,
        write_sheet=bool(args.sheet) and not args.in_place_sheet,
        force=bool(getattr(args, 'force', False)),
        source_path=getattr(args, 'source', None))
    out = Path(_path)

    if not _report_guard(result, out, source=getattr(args, 'source', None)):
        return 1
    _report_incremental(result)
    if args.in_place_sheet:
        merge_export_sheet(table_path, result)
        print(f'编译结果已作为「Export_RenPy」表并入：{table_path}')
    elif args.sheet and sheet:
        print(f'编译结果表：{sheet}')
    print()
    print(format_compile_result(result))
    return 0


# ==================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='cli.py',
        description='RenPy 剧情编辑器命令行：体检 / 构建 / 表格 / 协作')
    sub = p.add_subparsers(dest='cmd', required=True)

    def add_build_args(sp):
        sp.add_argument('project', help='项目根目录（含 game/）')
        sp.add_argument('--asset-dir', default=None, help='资源表目录')
        sp.add_argument('--out', default=None, help='产物目录')
        sp.add_argument('--char-limit', type=int, default=120,
                        help='单句台词字数上限，仅作提醒（W101）；设为 0 关闭该检查')
        sp.add_argument('--no-strict', dest='strict', action='store_false',
                        help='有错误也继续出产物')

    sp = sub.add_parser('describe',
                        help='摊开"工具以为的自定义结构"（只读，可 diff）')
    sp.add_argument('project', nargs='?', default='.',
                    help='项目根目录（含 game/）')
    sp.add_argument('--json', action='store_true',
                    help='输出 JSON（存进版本库就能 diff 出认知漂移）')
    sp.add_argument('--limit', type=int, default=40,
                    help='非 JSON 模式下最多列几条（默认 40）')
    sp.set_defaults(func=_cmd_describe)

    sp = sub.add_parser('diff3',
                        help='表 vs 源码的拍级对账（只读，冲突会说出来）')
    sp.add_argument('source', help='源码 .rpy 路径')
    sp.add_argument('--excel', default='', help='表 xlsx（默认同名 .xlsx）')
    sp.add_argument('--json', action='store_true', help='输出 JSON')
    sp.set_defaults(func=_cmd_diff3)

    sp = sub.add_parser('learn',
                        help='把一条自定义结构"教给"工具（写进 rules.json）')
    sp.add_argument('name', help='语句名（如 mycue）')
    sp.add_argument('project', nargs='?', default='.',
                    help='项目根目录（含 game/）')
    sp.add_argument('--label', default='', help='给策划看的标签（可中文）')
    sp.add_argument('--param', action='append', default=[],
                    help='参数形状：名字[:标签[:候选1,候选2]]，可重复')
    sp.add_argument('--block', action='store_true', help='这条语句有块体')
    sp.add_argument('--no-block', action='store_true', help='这条语句没有块体')
    sp.add_argument('--docs', default='', help='一句话说明')
    sp.add_argument('--json', action='store_true', help='输出 JSON')
    sp.set_defaults(func=_cmd_learn)

    sp = sub.add_parser('validate', help='只做三级校验')
    add_build_args(sp)
    sp.add_argument('--json', action='store_true', help='输出 JSON（CI 用）')
    sp.set_defaults(func=_cmd_validate)

    sp = sub.add_parser('build', help='校验 + 导出产物')
    add_build_args(sp)
    sp.add_argument('--engine', default='renpy',
                    choices=['renpy', 'unity', 'godot', 'json', 'csv'],
                    help='目标引擎（默认 renpy）')
    sp.add_argument('--no-engine-lint', dest='no_engine_lint',
                    action='store_true',
                    help='不跑 SDK lint（默认**跑**：产物能不能被引擎解析，'
                         '现在是一条构建门禁。没有 SDK 时自动跳过并如实说明）')
    sp.set_defaults(func=_cmd_build)

    sp = sub.add_parser('table', help='单文件 .rpy ↔ .xlsx')
    sp.add_argument('action', choices=['rpy2xlsx', 'xlsx2rpy'])
    sp.add_argument('path')
    sp.add_argument('--out', default=None)
    sp.add_argument('--asset-dir', default=None, help='资源表目录')
    sp.add_argument('--project', default=None, help='项目根目录（用于找资源表）')
    sp.add_argument('--source', default=None,
                    help='这一章原来的 .rpy；给了就走**增量合并**'
                         '（只改你改过的行，default / init python / '
                         'transform 原样保留）。改已有章节请用它 —— '
                         '没有它就只能全量重生成，那会丢掉不进表的东西')
    sp.add_argument('--force', action='store_true',
                    help='目标已存在也允许覆盖（会丢掉 default/init python '
                         '等不进表的东西，明确知道自己在做什么时才用）')
    sp.add_argument('--no-engine-lint', dest='no_engine_lint',
                    action='store_true',
                    help='不跑 SDK lint（默认跑：这是唯一能挡住'
                         '"产物过不了引擎"的闸门）')
    sp.set_defaults(func=_cmd_table)

    sp = sub.add_parser('compile',
                        help='演出表 → .rpy（缺资源写 TODO，不报错不中断）')
    sp.add_argument('table', help='演出表 .xlsx')
    sp.add_argument('--out', default=None, help='输出的 .rpy 路径')
    sp.add_argument('--asset-dir', default=None, help='资源表目录')
    sp.add_argument('--project', default=None, help='项目根目录（用于找资源表）')
    sp.add_argument('--source', default=None,
                    help='这一章原来的 .rpy；给了就走**增量合并**'
                         '（保住 default / init python / transform）')
    sp.add_argument('--force', action='store_true',
                    help='目标已存在也允许覆盖（逃生舱，见上）')
    sp.add_argument('--sheet', action='store_true',
                    help='另外写一份编译结果表（Export_RenPy）')
    sp.add_argument('--in-place-sheet', dest='in_place_sheet',
                    action='store_true',
                    help='把编译结果作为新表并进原工作簿（不碰 Beats）')
    sp.set_defaults(func=_cmd_compile)

    sp = sub.add_parser('selfcheck',
                        help='跑一遍主链路自检（打包后排查用，不碰用户文件）')
    sp.set_defaults(func=_cmd_selfcheck)

    sp = sub.add_parser('collab', help='团队协作（Excel 前端 + SQLite 后端）')
    sp.add_argument('action',
                    choices=['init', 'pull', 'push', 'status', 'lock',
                             'unlock', 'renew', 'role', 'owner', 'members',
                             'approve', 'import-ir'])
    sp.add_argument('--db', default=None, help='数据库路径（默认 <项目>/collab.db）')
    sp.add_argument('--project', default='.', help='项目根目录')
    sp.add_argument('--asset-dir', default=None)
    sp.add_argument('--sheet', default=None, help='Excel 路径')
    sp.add_argument('--user', default='anonymous', help='你的成员 ID')
    sp.add_argument('--users', default='', help='init 时登记的成员，逗号分隔')
    sp.add_argument('--owner', default='',
                    help='init 时的所有者（默认第一个成员）')
    sp.add_argument('--line-id', dest='line_id', default='')
    sp.add_argument('--label', default='', help='lock 时按整章上锁')
    sp.add_argument('--status', default='in_review',
                    choices=['draft', 'in_review', 'approved'])
    sp.add_argument('--note', default='')
    sp.add_argument('--member', default='', help='role 动作要改的成员 ID')
    sp.add_argument('--role', default='editor',
                    choices=['viewer', 'editor', 'owner'],
                    help='role 动作的目标角色（默认 editor）')
    sp.add_argument('--ttl', type=int, default=0,
                    help='锁租约秒数（默认 900；低于 60 按 60）')
    sp.add_argument('--force', action='store_true', help='忽略锁/冲突强制写入')
    sp.set_defaults(func=_cmd_collab)

    sp = sub.add_parser('engine-facts',
                        help='采集引擎事实（E1：lint + json-dump + 注入探针，一次运行）')
    sp.add_argument('--project', default='.', help='项目根目录（含 game/）')
    sp.add_argument('--sdk', default=None,
                    help='Ren\'Py SDK 目录（默认按环境变量 '
                         'STORYGRAPH_RENPY_SDK / 常见路径找）')
    sp.add_argument('--out', default=None,
                    help='输出 JSON（默认 <项目>/storygraph.engine_facts.json）')
    sp.add_argument('--mode', default='in-place', choices=['in-place', 'copy'],
                    help='in-place=只增删一个探针文件（默认，大项目用这个）；'
                         'copy=整份拷到临时目录再采（小项目/测试用）')
    sp.add_argument('--timeout', type=int, default=600,
                    help='单次引擎命令超时（秒）')
    sp.add_argument('--baseline', default=None,
                    help='基线 facts.json —— 给了就做**漂移检测**（E3）：'
                         '与它比，检出差异时退 1')
    sp.add_argument('--check-only', dest='check_only', action='store_true',
                    help='**不跑引擎**，只比对现成的事实文件（CI 里不需要 SDK）')
    sp.add_argument('--strict', action='store_true',
                    help='语义/资源类问题（`is not an image` 等）也算失败（退 1）')
    sp.set_defaults(func=_cmd_engine_facts)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f'路径不存在：{e}', file=sys.stderr)
        return 2
    except Exception as e:                      # noqa: BLE001
        print(f'执行失败：{e}', file=sys.stderr)
        if '--debug' in (argv or sys.argv):
            raise
        return 2


if __name__ == '__main__':
    sys.exit(main())
