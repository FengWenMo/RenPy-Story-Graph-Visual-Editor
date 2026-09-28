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

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

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
        return f'{head}\n{tail}'


# `discover_scripts` 已下移到 `storygraph.parsing.discover`（依赖方向修正：
# 生成门禁也要用它，留在 pipeline 层会让 story ↔ pipeline 成环）。
# 这里继续 re-export，既有调用方（cli / story / 测试）无需改动。


def load_asset_registry(project_root, asset_dir=None):
    """读资源表（不存在就返回 None，校验会相应降级为 L1/L2 为主）。"""
    project_root = Path(project_root)
    candidates = []
    if asset_dir:
        candidates.append(Path(asset_dir))
    candidates += [project_root / 'asset_tables',
                    project_root / 'game' / 'asset_tables']
    for c in candidates:
        if c.exists() and any(c.glob('*.xlsx')):
            from storygraph.assets.asset_registry import load_hooks, parse_asset_tables
            try:
                return parse_asset_tables(
                    c, hooks=load_hooks(project_root=project_root),
                    project_root=project_root)
            except Exception:
                return None
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
        except Exception:
            pass

    # 舞台/音频是 DSL 人话，必须先用符号表解释成结构化字段，
    # 否则舞台快照、校验、多引擎导出都会看到"空舞台"。
    try:
        from storygraph.story.beat_build import hydrate_from_dsl
        from storygraph.story.beat_render import make_symbols
        hydrate_from_dsl(beats, make_symbols(registry))
    except Exception:
        pass

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
    scripts = collected['scripts']

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
        artifact = write_generation(text, target, project_root=project_root)
        guard_info = {
            'written': artifact.written,
            'blocking': len(artifact.blocking),
            'issues': [i.format() for i in artifact.issues],
        }
        artifacts.append(str(artifact.review_path or artifact.out_path))
        todo_count = sum(1 for ln in text.splitlines()
                         if ln.strip().startswith('# TODO'))
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
