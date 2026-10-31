# -*- coding: utf-8 -*-
"""从真机项目**重建**两个预设（`storygraph/presets/`）：

  · `storygraph.mapping.json` —— 立绘代号表：服装字母（A/B/C…）+ 表情编号；
  · `storygraph.rules.json`   —— `show` 三列候选（角色 / 服装 / 表情）。

用法：

    python tools/build_presets_real.py                    # 默认真机项目 → presets/
    python tools/build_presets_real.py --project <项目根>
    python tools/build_presets_real.py --dry-run          # 只打印，不写盘
    python tools/build_presets_real.py --out <目录>

## 四条真源（一条都不猜）

1. **表情编号** ← `game/images/characters/<tag>/对应.txt`（也认 `中英对应.txt`）；
   两份都在且编号打架时，由剧本注释投票挑一份（`sprite_codes.scan_project`）。
2. **服装字母** ← 剧本注释 `# 表情B7` 后面那条 `show` 的服装属性
   （同一处；证据太弱的留空，不拿顺序硬凑）。
3. **服装/角色的中文名** ← 项目自己的 `charactercenter.rpy`
   （`CC_OUTFIT_LABEL` / `CHARACTER_NAMES`）与 `definitions.rpy`
   （`default <别名>_name = "…"` + `define <别名> = DynamicCharacter(..., image='<tag>')`）。
4. **`show` 的角色候选** ← 剧本里真的写过的第一词（同一份扫描）。

## 为什么要有它

预设是"给真项目用的一份真的"，不是空骨架。手抄一次就会漂：
剧本改编号、美术加服装，预设不知道。所以把重建过程写成可重跑的脚本，
每次改完剧本重跑一次，diff 就是这周项目真实发生的变化。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_mapping import (  # noqa: E402
    MAPPING_SCHEMA, load_mapping, validate_mapping)
from storygraph.assets.sprite_codes import (  # noqa: E402
    SECTION_EXPRESSIONS, SECTION_OUTFITS, _script_texts, parse_layeredimage,
    scan_project, template_rows)

ROOT = Path(__file__).resolve().parents[1]
PRESET_DIR = ROOT / 'storygraph' / 'presets'
DEFAULT_PROJECT = Path(r'D:\千连万理\sr_general')

#: 文本里 `default <别名>_name = "…"` 与 `define <别名> = … image='<tag>'`。
_DEFAULT_NAME_RE = re.compile(r'^\s*default\s+(\w+)_name\s*=\s*"([^"]*)"', re.M)
_CHARACTER_IMAGE_RE = re.compile(
    r"^\s*define\s+(\w+)\s*=\s*\w*Character\([^)]*?image\s*=\s*['\"](\w+)['\"]",
    re.M | re.S)


# ---------------------------------------------------------------------------
#  读项目的"人话名字"
# ---------------------------------------------------------------------------

def _literal_dict(text: str, name: str) -> dict:
    """把 `NAME = {…}` 这种**纯字面量**字典抠出来（`ast.literal_eval`，不执行）。"""
    m = re.search(rf'^\s*{name}\s*=\s*(\{{.*?^\s*\}})', text, re.M | re.S)
    if not m:
        return {}
    try:
        got = ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError):
        return {}
    return got if isinstance(got, dict) else {}


def read_names(root: Path) -> tuple:
    """
    项目里的**人话名字** → `({tag: 中文}, {服装属性: 中文})`。

    两处来源都读：角色中心（`CHARACTER_NAMES` / `CC_OUTFIT_LABEL`）与
    `definitions.rpy`（`default x_name` + `define x = DynamicCharacter(image='tag')`）。
    """
    center = root / 'game' / 'charactercenter' / 'charactercenter.rpy'
    text = center.read_text(encoding='utf-8', errors='replace') if center.is_file() else ''
    chars = dict(_literal_dict(text, 'CHARACTER_NAMES'))
    outfits = dict(_literal_dict(text, 'CC_OUTFIT_LABEL'))

    defs = root / 'game' / 'definitions.rpy'
    if defs.is_file():
        body = defs.read_text(encoding='utf-8', errors='replace')
        names = {m.group(1): m.group(2) for m in _DEFAULT_NAME_RE.finditer(body)}
        for m in _CHARACTER_IMAGE_RE.finditer(body):
            alias, tag = m.group(1), m.group(2)
            if tag in chars:
                continue
            got = names.get(alias, '').strip('「」『』')
            if got:
                chars[tag] = got
    return chars, outfits


# ---------------------------------------------------------------------------
#  建两份数据
# ---------------------------------------------------------------------------

def build_mapping(project: Path) -> tuple:
    """真项目 → `(映射 dict, 说明行 list)`。"""
    scan = scan_project(project)
    rows = template_rows(scan)
    chars, outfit_labels = read_names(project)
    avatar_dir = project / 'game' / 'images' / 'characters' / 'avatar'

    # 项目自己声明"这个角色有几套衣服"（`CC_OUTFIT_CYCLE`）：扫出来缺的那几套
    # 补一行**空代号**，等人填 —— 与 `scan_project` 对待"剧本里没出现过"的
    # 服装完全一致（真实项目：xing 的 A 号只对上一次，票数不够，代号留空）。
    known = {(r['char'], r['attr']) for r in rows[SECTION_OUTFITS]}
    cycle = _literal_dict(
        (project / 'game' / 'charactercenter' / 'charactercenter.rpy')
        .read_text(encoding='utf-8', errors='replace')
        if (project / 'game' / 'charactercenter' / 'charactercenter.rpy').is_file()
        else '', 'CC_OUTFIT_CYCLE')
    for tag, attrs in cycle.items():
        for attr in attrs if isinstance(attrs, (list, tuple)) else ():
            attr = str(attr)
            if (tag, attr) in known:
                continue
            known.add((tag, attr))
            rows[SECTION_OUTFITS].append({
                'char': tag, 'code': '', 'attr': attr,
                'label': '项目里声明了这套，剧本没写过它的代号'})

    outfit_rows = []
    for row in rows[SECTION_OUTFITS]:
        attr = str(row.get('attr') or '')
        label = outfit_labels.get(attr, '')
        evidence = str(row.get('label') or '')
        code = str(row.get('code') or '')
        if code and label:
            text = f'{label}（{evidence}）'
        elif code:
            text = evidence
        else:
            text = f'{label}（{evidence}）' if label else evidence
        outfit_rows.append({'char': row['char'], 'code': code,
                            'attr': attr, 'label': text})
    outfit_rows.sort(key=lambda r: (r['char'], r['code'] or 'zz', r['attr']))

    expr_rows = [{'char': r['char'], 'no': str(r['no']), 'attr': r['attr'],
                  'label': r['label']} for r in rows[SECTION_EXPRESSIONS]]

    tags = []
    for row in outfit_rows + expr_rows:
        if row['char'] not in tags:
            tags.append(row['char'])
    if avatar_dir.is_dir():
        for png in sorted(avatar_dir.glob('*.png')):
            if png.stem not in tags:
                tags.append(png.stem)

    characters = {}
    for tag in tags:
        item = {}
        if chars.get(tag):
            item['display'] = chars[tag]
        avatar = avatar_dir / f'{tag}.png'
        if avatar.is_file():
            item['avatar'] = avatar.relative_to(project).as_posix()
        if item:
            characters[tag] = item

    data = {
        'schema': MAPPING_SCHEMA,
        'name': f'{project.name} · 立绘代号（服装字母 + 表情编号）',
        'characters': characters,
        SECTION_OUTFITS: outfit_rows,
        SECTION_EXPRESSIONS: expr_rows,
    }
    notes = [f'角色 {len(characters)} / 服装 {len(outfit_rows)} 行 / 表情 {len(expr_rows)} 行']
    notes += [f'扫描说明：{n}' for n in scan.notes]
    return data, notes


def build_rules(project: Path, mapping: dict, rules_path: Path) -> tuple:
    """在**现有** rules.json 的骨架上换候选值（形状/顺序/注释性字段都不动）。"""
    data = json.loads(rules_path.read_text(encoding='utf-8'))
    statement = next((s for s in data.get('statements') or ()
                      if s.get('name') == 'show'), None)
    if statement is None:
        raise SystemExit(f'{rules_path} 里没有 show 语句，拒绝改。')

    shown = _shown_tags(project, _script_texts(project))
    real_outfits = []
    for row in mapping[SECTION_OUTFITS]:
        attr = str(row.get('attr') or '')
        if attr and attr not in real_outfits:
            real_outfits.append(attr)
    real_exprs = []
    for row in mapping[SECTION_EXPRESSIONS]:
        attr = str(row.get('attr') or '')
        if attr and attr not in real_exprs:
            real_exprs.append(attr)

    changes = []

    def _merge(old, new, *, keep_tail=True):
        out = [v for v in new if v not in old]
        tail = [v for v in old if v not in out] if keep_tail else []
        return out + tail, [v for v in new if v not in old], \
            [v for v in old if v not in new]

    old_roles = list((_column(statement, '角色') or {}).get('choices') or ())
    roles, added_roles, missing_in_rules = _merge(old_roles, shown)
    changes.append(('角色', roles, added_roles, missing_in_rules))

    old_outfits = list((_column(statement, '服装') or {}).get('choices') or ())
    outfits, added_outfits, gone_outfits = _merge(old_outfits, real_outfits)
    changes.append(('服装', outfits, added_outfits, gone_outfits))

    old_exprs = list((_column(statement, '表情') or {}).get('choices') or ())
    exprs, added_exprs, gone_exprs = _merge(old_exprs, real_exprs)
    changes.append(('表情', exprs, added_exprs, gone_exprs))

    for column in statement.get('columns') or ():
        for name, values, *_ in changes:
            if column.get('name') == name:
                column['choices'] = values
    for slot in statement.get('pattern') or ():
        for name, values, *_ in changes:
            if slot.get('column') == name:
                slot['options'] = values
                if name == '角色':
                    # `show` 的第一个词是**图片 tag**，不封闭：真项目里还有
                    # `show bgoceanblur` / `show icon` 这种"一张图一个字"。
                    slot['limit'] = False
    return data, changes


def _column(statement, name):
    return next((c for c in statement.get('columns') or ()
                 if c.get('name') == name), None)


def _character_tags(project: Path, texts: dict) -> list:
    """项目里**真的是一个角色**的 tag：有 `images/characters/<tag>/` 或声明过 layeredimage。"""
    tags = []
    base = project / 'game' / 'images' / 'characters'
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if child.is_dir():
                tags.append(child.name)
    for text in texts.values():
        for tag in parse_layeredimage(text):
            if tag not in tags:
                tags.append(tag)
    return tags


def _shown_tags(project: Path, texts: dict) -> list:
    """
    剧本里 `show` 过的**角色** tag（保序去重）。

    ⚠ 不能把"`show` 后面第一个词"全收：真项目里还有
    `show bgoceanblur`（一张背景图一个字）、`show icon`、`show aa ye hi`（手误）
    —— 它们进候选列只会让下拉更长，不会帮人选对。
    """
    allowed = set(_character_tags(project, texts))
    # `show cg cg01` 是同一套 show 语法（cg 是图 tag，不是角色），
    # 它在真剧本里出现 11 次，候选里给它留一格。
    allowed.add('cg')
    skip = {'screen', 'image', 'expression', 'layer'}
    tags = []
    for text in texts.values():
        for m in re.finditer(r'^\s*show\s+(\w+)', text, re.M):
            tag = m.group(1)
            if tag in skip or tag not in allowed or tag in tags:
                continue
            tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
#  落盘 / 入口
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    """仓库里的预设：临时文件 + `os.replace`（不留 `.bak` —— 它进 git，diff 就是备份）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8', newline='\n')
    tmp.replace(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='从真机项目重建 presets/ 两份配置')
    ap.add_argument('--project', default=str(DEFAULT_PROJECT))
    ap.add_argument('--out', default=str(PRESET_DIR))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    project = Path(args.project)
    if not (project / 'game').is_dir():
        raise SystemExit(f'不是一个 Ren\u2019Py 项目（缺 game/）：{project}')
    out = Path(args.out)
    mapping_path = out / 'storygraph.mapping.json'
    rules_path = out / 'storygraph.rules.json'

    mapping, notes = build_mapping(project)
    print('=' * 72)
    print(f'项目：{project}')
    for line in notes:
        print('  · ' + line)
    warnings = validate_mapping(mapping)
    print(f'映射自查：{len(warnings)} 条警告' + ('' if not warnings else ''))
    for w in warnings:
        print('  ! ' + w)

    rules, changes = build_rules(project, mapping, rules_path)
    print('-- rules.json 候选变化 --')
    for name, values, added, stale in changes:
        print(f'  {name}: {len(values)} 个（新增 {added or "无"}；'
              f'旧候选仍保留 {len(stale)} 个：{stale or "无"}）')

    if args.dry_run:
        print('（--dry-run：没有写盘）')
        return 0
    _write_json(mapping_path, mapping)
    _write_json(rules_path, rules)
    print(f'已写：{mapping_path}')
    print(f'已写：{rules_path}')
    # 回读一遍（走**工具自己的读取器**，别拿 json.loads 骗自己）
    data, warnings = load_mapping(str(mapping_path))
    print(f'回读：schema={data.get("schema")} / 警告 {len(warnings)} 条')
    return 1 if warnings else 0


if __name__ == '__main__':
    raise SystemExit(main())
