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
        engine=args.engine, strict=args.strict, char_limit=args.char_limit)
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
    codes = {getattr(i, 'code', '') for i in result.guard_issues}
    if 'HANDWRITTEN_TARGET' in codes:
        # 这一条**有**逃生舱，而且和上面那条"坏脚本"的性质不同：
        # 不是产物有问题，是"目标是人手写的"。所以提示要分开给，
        # 否则用户会以为自己的产物坏了。
        if result.used_patch:
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
        print(f'增量补丁：改动 {result.changed_lines} 行，'
              f'原文其余部分（含 default / init python / transform）逐字节保留',
              file=stream)
    elif result.patch_attempted and result.patch_note:
        print(f'增量未采用（{result.patch_note}）→ 已按全量生成',
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

        out = Path(args.out) if args.out else \
            Path(args.path).with_suffix('.rpy')
        _path, _sheet, result = compile_table(
            args.path, rpy_path=out, registry=registry, write_sheet=False,
            force=bool(getattr(args, 'force', False)),
            source_path=getattr(args, 'source', None), skip_empty=True)
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
        cs.init_db(db, users=users)
        print(f'已建库：{db}')
        if users:
            print('成员：' + '，'.join(users))
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
              f"冲突 {len(res['conflicts'])}，被拒 {len(res['rejected'])}")
        for c in res['conflicts']:
            print(f"  冲突 {c['line_id']}（库里 rev {c['db_revision']} > "
                  f"你基于的 rev {c['base_revision']}，"
                  f"最后改于 {c['updated_by']}）")
        for r in res['rejected']:
            print(f"  拒绝 {r['line_id']}：{r['reason']}")
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

    if action == 'lock':
        if args.label:
            res = cs.lock_range(db, args.label, args.user)
            print(f"已锁 {len(res['locked'])} 行"
                  + (f"，跳过 {len(res['skipped'])} 行（别人持有）"
                     if res['skipped'] else ''))
            return 0
        res = cs.lock(db, args.line_id, args.user, note=args.note or '')
        print('已上锁' if res['ok'] else f"失败：{res['reason']}")
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


def _load_registry(args):
    from storygraph.pipeline.build import load_asset_registry
    if not getattr(args, 'project', None):
        return None
    return load_asset_registry(args.project, getattr(args, 'asset_dir', None))


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
    out = Path(args.out) if args.out else table_path.with_suffix('.rpy')
    # `--force` 是逃生舱：允许覆盖已存在的手写 .rpy
    # （见 `docs/P0_静默全量覆盖_2026-10-03.md`）。
    # `--source` 是**无损**那条路：给出这一章原来的 .rpy，走增量合并，
    # 只改你改过的行，default / init python / transform 原样保留。
    _path, sheet, result = compile_table(
        table_path,
        rpy_path=out,
        registry=registry,
        write_sheet=bool(args.sheet) and not args.in_place_sheet,
        force=bool(getattr(args, 'force', False)),
        source_path=getattr(args, 'source', None))

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

    sp = sub.add_parser('validate', help='只做三级校验')
    add_build_args(sp)
    sp.add_argument('--json', action='store_true', help='输出 JSON（CI 用）')
    sp.set_defaults(func=_cmd_validate)

    sp = sub.add_parser('build', help='校验 + 导出产物')
    add_build_args(sp)
    sp.add_argument('--engine', default='renpy',
                    choices=['renpy', 'unity', 'godot', 'json', 'csv'],
                    help='目标引擎（默认 renpy）')
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

    sp = sub.add_parser('collab', help='团队协作（Excel 前端 + SQLite 后端）')
    sp.add_argument('action',
                    choices=['init', 'pull', 'push', 'status', 'lock',
                             'unlock', 'approve', 'import-ir'])
    sp.add_argument('--db', default=None, help='数据库路径（默认 <项目>/collab.db）')
    sp.add_argument('--project', default='.', help='项目根目录')
    sp.add_argument('--asset-dir', default=None)
    sp.add_argument('--sheet', default=None, help='Excel 路径')
    sp.add_argument('--user', default='anonymous', help='你的成员 ID')
    sp.add_argument('--users', default='', help='init 时登记的成员，逗号分隔')
    sp.add_argument('--line-id', dest='line_id', default='')
    sp.add_argument('--label', default='', help='lock 时按整章上锁')
    sp.add_argument('--status', default='in_review',
                    choices=['draft', 'in_review', 'approved'])
    sp.add_argument('--note', default='')
    sp.add_argument('--force', action='store_true', help='忽略锁/冲突强制写入')
    sp.set_defaults(func=_cmd_collab)

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
