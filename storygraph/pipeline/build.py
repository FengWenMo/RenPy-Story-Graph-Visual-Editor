"""
build.py
========
构建流水线：一条命令把「.rpy / 剧本表」变成「可发布产物 + 体检报告」。

对标调研里被反复提到的两类工具（renkit 的构建自动化、renpy-lint-action
的 CI 校验）：团队到了 50 人这个规模，"谁在本地漏跑了一次校验"就是线上
事故。所以 build 的定位是**可被 CI 调用的单一入口**：

    python cli.py build <项目根> --engine renpy --strict

它做四件事，顺序固定：
  1. 发现章节：项目里所有 game/*.rpy（跳过 tl/ 与 .generated.rpy）
  2. 解析并折叠成 Beat（与编辑器、表使用同一份 IR）
  3. 三级校验（validate.py）—— strict 模式下错误直接让构建失败
  4. 导出目标引擎（engine_exporters）+ 写 build_report.json

没有"魔法"：所有输入输出路径都显式打印，报告落在产物目录里，
CI 读 exit code 就够。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import storygraph.story.stage_snapshot as stage_snapshot  # noqa: F401  (保持导入面稳定，供下游扩展)
from storygraph.pipeline.engine_exporters import export_ir
from storygraph.parsing.discover import discover_scripts  # noqa: F401 (re-export)
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
from storygraph.story.beat_build import beats_from_parse_result
from storygraph.story.beat_render import render_beats_to_rpy
from storygraph.story.generation_guard import write_generation
from storygraph.pipeline.validate import DEFAULT_CHAR_LIMIT, validate_all


@dataclass
class BuildResult:
    ok: bool
    engine: str
    out_dir: str
    artifacts: list = field(default_factory=list)
    report_path: str = ''
    report: dict = field(default_factory=dict)

    @property
    def errors(self) -> list:
        return [i for i in self.report.get('issues', [])
                if i.get('severity') == 'error']

    @property
    def guard_blocked(self) -> bool:
        """
        产物是否被**生成门禁**拦下（没写进目标路径）。

        与 `errors`（三级校验）是两回事：校验说"这份演出数据有病"，
        门禁说"这份产物根本不能落进项目"（中文图像名 / label 重名）。
        """
        guard = (self.report.get('build', {}) or {}).get('guard') or {}
        return not guard.get('written', True)

    def summary(self) -> str:
        r = self.report
        stats = (r.get('build', {}) or {}).get('stats', {}) or {}
        head = (f"构建{'成功' if self.ok else '失败'} · 引擎 {self.engine} · "
                f"{stats.get('lines', 0)} 行 / {stats.get('labels', 0)} 章")
        tail = (f"校验：错误 {r.get('error_count', 0)}，"
                f"警告 {r.get('warning_count', 0)}"
                f"（L1 {r.get('by_level', {}).get('L1', 0)} / "
                f"L2 {r.get('by_level', {}).get('L2', 0)} / "
                f"L3 {r.get('by_level', {}).get('L3', 0)}）")
        todos = stats.get('todos', 0)
        if todos:
            tail += (f"\n待补资源：{todos} 处"
                     f"（已写成 # TODO 注释，构建不受影响）")
        guard = (r.get('build', {}) or {}).get('guard') or {}
        if guard.get('blocking'):
            tail += (f"\n生成门禁：{guard['blocking']} 条致命问题 —— "
                     f"产物**未写入目标路径**"
                     f"（已留 .blocked.rpy 供对照，见产物列表）")
        # 非致命结论**逐条印出来**（M1）：一句"有 N 条警告"等于没说 ——
        # 用户需要知道的是"哪几行工具不管辖"，因为那正是他要自己盯的地方。
        for w in (guard.get('warnings') or ()):
            tail += f'\n门禁提示：{w}'
        # 项目声明的自定义语句（M3）：用户说过「你没有适应到它的自定义结构，
        # 他会立即退出，因为工具没有用」—— 所以"工具知道哪些"必须说出来。
        if guard.get('statements'):
            tail += f'\n{guard["statements"]}'
        return f'{head}\n{tail}'


# `discover_scripts` 已下移到 `storygraph.parsing.discover`（依赖方向修正：
# 生成门禁也要用它，留在 pipeline 层会让 story ↔ pipeline 成环）。
# 这里继续 re-export，既有调用方（cli / story / 测试）无需改动。


def load_asset_registry(project_root, asset_dir=None, walk=None):
    """读资源表（不存在就返回 None，校验会相应降级为 L1/L2 为主）。"""
    project_root = Path(project_root)
    candidates = []
    if asset_dir:
        candidates.append(Path(asset_dir))
    candidates += [project_root / 'asset_tables',
                    project_root / 'game' / 'asset_tables']
    # ⚠ 只认**真表**：`*.bak.xlsx`（我们自己的备份）、`~$*.xlsx`（Excel 锁文件）
    # 和 `*.broken-*.xlsx`（读不出来时挪到一边的坏文件）都不算"有表" ——
    # 否则一个只放着备份的目录会让这里返回一张**空表**，于是永远不去建表、
    # 打开项目看到 0 个资源（2026-10-05 实测）。
    try:
        from storygraph.assets.asset_registry import is_real_table_file
    except Exception as e:                      # noqa: BLE001 - 极端情况退回宽判
        swallow('pipeline.build.load_asset_registry.import', e)

        def is_real_table_file(p):
            return str(p).lower().endswith('.xlsx')
    for c in candidates:
        try:
            if c.exists() and any(is_real_table_file(p)
                                  for p in c.glob('*.xlsx')):
                from storygraph.assets.asset_registry import (load_hooks,
                                                              parse_asset_tables)
                try:
                    return parse_asset_tables(
                        c, hooks=load_hooks(project_root=project_root),
                        project_root=project_root, walk=walk)
                except Exception as e:
                    swallow('pipeline.build.load_asset_registry', e)
                    return None
        except OSError as e:                    # noqa: BLE001
            swallow('pipeline.build.load_asset_registry', e)
            continue
    return None


def collect_report(project_root, asset_dir=None, char_limit=DEFAULT_CHAR_LIMIT,
                   registry=None):
    """
    只做"解析 + 校验"，不产出任何文件。

    拆出来是给编辑器用的：健康面板要能随时体检（不写盘、不改工作区），
    构建才是"体检 + 出产物"。两条路共用同一份分析，避免面板看到的
    结论和 CI 看到的结论不一样。
    """
    project_root = Path(project_root)
    scripts = discover_scripts(project_root)
    if registry is None:
        registry = load_asset_registry(project_root, asset_dir)

    analyzer = IncrementalRenPyAnalyzer()
    parse_result, graph = analyzer.analyze(files=scripts, graph=None)
    beats = beats_from_parse_result(parse_result)

    # 没有资源表也不能让导出退化成"只有角色变量名"：Unity/Godot 侧要的是
    # 显示名。脚本里 define / image / transform 声明本身就够用。
    from storygraph.story.beat_build import apply_script_definitions
    from storygraph.story.script_table import registry_from_parse_result
    if registry is None:
        registry = registry_from_parse_result(parse_result)
    else:
        # 把脚本里的角色/变换/image 声明合并进资源表——不合并的话，
        # `scene black`（image 声明出来的）会被报"未登记"，
        # 说话人也会被报"不在角色表里"。
        apply_script_definitions(registry, parse_result)
        # 引用分析：registry.references 不填，L3 的"未使用资产"会 100% 误报。
        # 这里走与编辑器相同的那一条抽取链（Statement 优先 + 正则兜底）。
        try:
            from storygraph.assets.asset_registry import analyze_references, \
                analyze_statement_references
            if parse_result.statements:
                analyze_statement_references(parse_result.statements, registry)
            analyze_references(scripts, registry)
        except Exception as e:
            swallow('build.collect_report', e)

    # 舞台/音频是 DSL 人话，必须先用符号表解释成结构化字段，
    # 否则舞台快照、校验、多引擎导出都会看到"空舞台"。
    try:
        from storygraph.story.beat_build import hydrate_from_dsl
        from storygraph.story.beat_render import make_symbols
        hydrate_from_dsl(beats, make_symbols(registry))
    except Exception as e:
        swallow('build.collect_report', e)

    report = validate_all(
        beats, registry=registry, graph=graph,
        script_warnings=parse_result.warnings,
        project_root=project_root, char_limit=char_limit,
        menu_labels=getattr(parse_result, 'menu_labels', None),
        project_speakers={b.speaker for b in beats if b.speaker})

    return {
        'report': report,
        'beats': beats,
        'registry': registry,
        'scripts': scripts,
        'parse_result': parse_result,
        'graph': graph,
    }


def build_project(project_root, asset_dir=None, out_dir=None,
                  engine: str = 'renpy', strict: bool = False,
                  char_limit: int = DEFAULT_CHAR_LIMIT,
                  registry=None, write_report: bool = True) -> BuildResult:
    """
    跑完一整条流水线。返回 BuildResult（含可 json 化的 report）。

    strict 默认 **False**：构建不因"某句台词引用了还没登记的资产"而失败。
    缺资源只在生成的脚本里留 `# TODO: 缺少资源 …`——构建照常出产物。
    要拿校验当 CI 门禁用 `cli.py validate`（或显式 strict=True）。
    """
    project_root = Path(project_root)
    out_dir = Path(out_dir) if out_dir else project_root / 'build_out'
    out_dir.mkdir(parents=True, exist_ok=True)

    collected = collect_report(project_root, asset_dir=asset_dir,
                               char_limit=char_limit, registry=registry)
    report = collected['report']
    beats = collected['beats']
    registry = collected['registry']

    artifacts = []
    todo_count = 0
    guard_info = {}

    # 1) 引擎产物
    if engine == 'renpy':
        # Ren'Py 目标：按 label 分段生成脚本，并带上自定义 transform 定义。
        #
        # 落盘统一走**生成门禁**（`write_generation`）：命中 block 级问题
        # （中文图像名 / label 与项目其他 .rpy 重名）就**不写目标文件**，
        # 改写 `<stem>.blocked.rpy` 供人对照，结论带进构建报告。
        #
        # 2026-09-28 修：这里以前直接 `write_text`，**绕过了门禁**——
        # 而 build 恰恰是 CI 的主路径，等于把"绝不写出跑不起来的脚本"
        # 漏在了门外。
        target = out_dir / 'story.generated.rpy'
        text = render_beats_to_rpy(beats, label_name=None, registry=registry)
        artifact = write_generation(text, target, project_root=project_root,
                                    beats=beats)
        # 项目声明的自定义语句（M3）＋ **能力覆盖率**（元模型 A）：
        # 「工具愿不愿意被教」「工具有多少是替得了你的」都要看得见。
        # 这一步只读，失败也只少一行报告，不影响构建。
        statements_text = ''
        try:
            from storygraph.story.statement_registry import (
                StatementRegistry, format_capability_summary,
                format_registry_report)
            _reg = StatementRegistry.for_project(project_root)
            statements_text = format_registry_report(_reg)
            # 覆盖率用**这一轮真正编译过的拍**算 —— 它就是产物里的东西。
            cap_text = format_capability_summary(beats=beats, registry=_reg)
            if cap_text:
                statements_text = f'{statements_text}\n{cap_text}'
            # **原文真迹覆盖率**（CST，D3）：`cst_coverage` / `format_cst_report`
            # 从前**只有单测在调**（悬空能力）—— 于是"真迹退化"（比如某类语句
            # 拿不到逐字原文、或者更糟的贴错行）在生产里没人看得见。
            # 放进构建报告，CI（`tools/journey_check.py` 走 CLI build）每次都会打。
            _st = getattr(collected.get('parse_result'), 'statements', None) or []
            if _st:
                from storygraph.core.cst import (cst_coverage, format_cst_report,
                                                 overlaps)
                cst_line = format_cst_report(cst_coverage(_st), "语句")
                # `cst_attached` 从前**只写不读**（悬空）：它记的是"这一轮给多少条
                # 语句挂上了真迹"。读出来才对得上 —— 挂载数 < 语句数就说明有一类
                # 语句拿不到原文（真迹退化），得看得见。
                _pr = collected.get('parse_result')
                attached = int(getattr(_pr, 'cst_attached', 0) or 0)
                if attached:
                    cst_line += (f'（本次挂载 {attached} 条'
                                 f'{"，有 " + str(len(_st) - attached) + " 条没挂上" if attached < len(_st) else ""}）')
                # **重叠 span = 写回时必然打架**（文件级内部缺陷）—— 从前 `overlaps()`
                # 只在单测里跑；接进构建报告，CI 每次都会打。
                bad = overlaps(_st)
                if bad:
                    cst_line += (f'\n🚫 CST 原文区间重叠 {len(bad)} 处 —— '
                                 f'写回时这些行会互相覆盖（这是工具内部缺陷，'
                                 f'请附上复现报告）')
                statements_text = f'{statements_text}\n{cst_line}'
                # **"工具不认识的东西"点名**（CST 第二步，2026-10-07）：
                # 缺声明就自动降级（没有参数列、只有形状保真），但**必须说出口** ——
                # 不然"表里少了几列"永远没人发现，直到有人手工对账。
                # 名单与判据都在 `describe_statements`（`cli describe` 同一个真源），
                # 这里只负责把它塞进构建报告。
                from storygraph.story.statement_registry import (
                    describe_statements)
                # ⚠ 传的是 `project_root`，**不是** `collected['registry']` ——
                # 那个是**资产**注册表（`AssetRegistry`），不是语句注册表；
                # 传错了 `describe_statements` 会抛、被上面的 swallow 吞掉，
                # 这一行就永远不出现（第一版就是这么"静默缺席"的）。
                _d = describe_statements(project_root=project_root,
                                         statements=_st)
                _guess = [r for r in _d['statements']
                          if r['source'] == 'shape-guess']
                if _guess:
                    names = '、'.join(f"{r['name']}×{r['occurrences']}"
                                      for r in _guess[:8])
                    more = '' if len(_guess) <= 8 else f'…（共 {len(_guess)} 个）'
                    statements_text = (
                        f'{statements_text}\n'
                        f'⚠ 未声明（只靠形状猜）：{names}{more}'
                        f' —— 工具不知道它们的参数/约束，表里也不会有'
                        f'"每参数一列"。写进 storygraph.rules.json 的 statements，'
                        f'或让代码里有 renpy.register_statement'
                        f'（见 docs/设计_自定义结构发现与交互_2026-10-07.md）')
                # **项目级"工具不认识的结构"**（`project_conventions` 的第三层）：
                # 全仓扫描（含没进这一轮编译的文件），回答编剧真正关心的问题
                # ——"我剧本里哪一种结构工具管不了"。这套三层机制（认形状 /
                # 让它声明 / 不认识就明说）先前**零生产调用**；接进构建报告后
                # CI 每次都会打，它设计要回答的那个问题才真的有人看见。
                from storygraph.story.project_conventions import (
                    unknown_statements)
                _unk = unknown_statements(project_root)
                if _unk:
                    _uh = '、'.join(f'{k}×{v["count"]}'
                                    for k, v in list(_unk.items())[:8])
                    _um = '' if len(_unk) <= 8 else f'…（共 {len(_unk)} 种）'
                    statements_text = (
                        f'{statements_text}\n'
                        f'⚠ 工具不认识的语句头（全仓扫描）：{_uh}{_um}')
        except Exception as e:                  # noqa: BLE001
            swallow('pipeline.build.statements_report', e)
        guard_info = {
            'written': artifact.written,
            'blocking': len(artifact.blocking),
            'issues': [i.format() for i in artifact.issues],
            'statements': statements_text,
            # **非致命的门禁结论也要留下**（M1，2026-10-07）：
            # `UNMANAGED_BLOCK` / `KNOWN_UNMODELED_BLOCK` 是 warn ——
            # 它们不拦导出，但"这几行不归工具管"必须**说出口**
            # （用户 D1 的选择：透传 + 报警 + 报告必须出现）。
            # 只放进 `issues` 是不够的：报告只打印计数，人就看不到内容。
            'warnings': [i.format() for i in artifact.warnings],
        }
        artifacts.append(str(artifact.review_path or artifact.out_path))
        todo_count = sum(1 for ln in text.splitlines()
                         if ln.strip().startswith('# TODO'))
        _publish_build_degradation(artifact, todo_count, target)
        # 同时导出 IR，方便 Unity 迁移或外部工具消费
        artifacts += export_ir('json', beats, out_dir, registry=registry,
                               project=str(project_root.name))
    else:
        artifacts += export_ir(engine, beats, out_dir, registry=registry,
                               project=str(project_root.name))

    # 构建成功 = 校验（strict 时）过了 **且** 产物真的落地了。
    # 门禁拦下时目标文件没写出去，报"成功"会骗 CI。
    result_ok = ((report.ok if strict else True)
                 and guard_info.get('written', True))

    stats = collect_stats(collected)
    stats['todos'] = todo_count

    report_dict = report.to_dict()
    report_dict['build'] = {
        'ok': result_ok,
        'engine': engine,
        'strict': strict,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'stats': stats,
        'artifacts': artifacts,
        'guard': guard_info,
    }

    report_path = ''
    if write_report:
        report_path = str(out_dir / 'build_report.json')
        Path(report_path).write_text(
            json.dumps(report_dict, ensure_ascii=False, indent=2),
            encoding='utf-8')

    return BuildResult(ok=result_ok, engine=engine, out_dir=str(out_dir),
                       artifacts=artifacts, report_path=report_path,
                       report=report_dict)


def _publish_build_degradation(artifact, todo_count: int, target) -> None:
    """
    **把构建的降级结论说给用户听**（P2-9）。

    构建报告 / CI 退出码里一直有这些数字，但界面上不发一条通知 ——
    用户点完"构建"看到成功，产物里却有几处 `# TODO: 缺少资源 …`
    （那些台词／立绘在游戏里**不会出现**）。

    两条：门禁拦下（error）与缺资源兜底（warn）。都没问题就**什么也不发**
    —— 通知一多就没人看了。

    无头环境默认没有订阅者，发通知零副作用（见 `core.notify`）。
    """
    from storygraph.core import notify
    try:
        if not artifact.written:
            # 门禁那条已经在 `generation_guard._publish_blocked` 里发过
            # （带具体 issue），这里不重复刷屏。
            return
        if todo_count:
            notify.warn(
                f'构建完成，但有 {todo_count} 处引用了表里没有的资源，'
                f'产物里已写成 `# TODO`（这些内容在游戏里不会出现）。'
                f'产物：{Path(target).name}',
                title='资源缺失')
    except Exception as e:                  # noqa: BLE001 - 通知失败不拖垮构建
        swallow('pipeline.build._publish_build_degradation', e)



def _registry_stats(registry) -> dict:
    if registry is None:
        return {}
    return {
        attr: len(getattr(registry, attr, {}) or {})
        for attr in ('images', 'music', 'sounds', 'voices', 'transforms',
                     'characters')
    }


def collect_stats(collected) -> dict:
    """
    从 `collect_report()` 的结果算出统计数字（单一口径）。

    抽出来的理由：`cli.py validate` 要"只体检、不写盘"，而**数字口径不该
    因为没出产物就跟 build 不一致**——体检面板、CLI、CI 报告必须说同一件事
    （这就是"同类信息只许有一处实现"）。

    `todos` 只有真正渲染过产物才知道（贴位置的 `# TODO`），留 0 给调用方补。
    """
    beats = collected.get('beats') or []
    return {
        'scripts': [str(s) for s in (collected.get('scripts') or [])],
        'lines': len(beats),
        'labels': len({b.label for b in beats if b.label}),
        'chars': sum(len(b.text or '') for b in beats),
        'todos': 0,
        'registry': _registry_stats(collected.get('registry')),
    }


def format_build_result(result: BuildResult) -> str:
    """给人看的构建输出（CLI 用）。"""
    from storygraph.pipeline.validate import Issue, ValidationReport

    lines = [result.summary(), '']
    b = result.report.get('build', {})
    stats = b.get('stats', {})
    if stats.get('scripts'):
        lines.append('章节脚本：')
        for s in stats['scripts']:
            lines.append(f'  · {s}')
        lines.append('')
    if b.get('stats', {}).get('registry'):
        pairs = '，'.join(f'{k} {v}' for k, v in stats['registry'].items())
        lines.append(f'资源表：{pairs}')
        lines.append('')
    if result.artifacts:
        lines.append('产物：')
        for a in result.artifacts:
            lines.append(f'  · {a}')
        lines.append('')
    r = ValidationReport()
    r.issues = [Issue(**i) for i in result.report.get('issues', [])]
    lines.append(r.format(max_per_level=15))
    return '\n'.join(lines).rstrip()
