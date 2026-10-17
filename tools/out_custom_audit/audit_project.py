"""审计用合成项目：服装 / 动作 / 表情 三组，含“引擎事实有/没有”两档。

只写 `tools/out_custom_audit/` 下的探针产物，不碰 storygraph/ tests/ docs/。
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from _ui_project import build_project, PNG_1PX                      # noqa: E402

RULES = '''{
  "attr_groups": [
    {"name": "outfit", "label": "服装", "options": ["swim_suit", "uniform"]},
    {"name": "pose", "label": "动作", "options": ["stand", "wave"]},
    {"name": "expressions", "label": "神态", "options": ["happy", "sad"]}
  ]
}
'''

SCRIPT = '''label start:
    show lan_tag swim_suit stand
    lan "一：正确的 show tag + 引擎给了两个组"
    show lan swim_suit stand
    lan "二：作者写的是变量名 lan（不是 show tag）"
    show mix swim_suit stand
    lan "三：引擎只有一个 outfit 组，服装和动作混在一起"
    show solo uniform
    lan "四：引擎只给了一个组"
    return
'''

DEFS = '''define lan = Character("岚", image="lan_tag")

layeredimage lan_tag:
    image_format 'images/lan_tag/{image}.png'
    group outfit:
        attribute swim_suit default:
            'swim_suit'
        attribute uniform:
            'uniform'
    group pose:
        attribute stand default:
            'stand'
        attribute wave:
            'wave'

layeredimage mix:
    image_format 'images/mix/{image}.png'
    group outfit:
        attribute swim_suit default:
            'swim_suit'
        attribute uniform:
            'uniform'
        attribute stand:
            'stand'
        attribute wave:
            'wave'
    group expressions:
        attribute happy default:
            'happy'
        attribute sad:
            'sad'

layeredimage solo:
    image_format 'images/solo/{image}.png'
    group outfit:
        attribute uniform default:
            'uniform'
'''

ATTRS = {
    'lan_tag': ['swim_suit', 'uniform', 'stand', 'wave'],
    'mix': ['swim_suit', 'uniform', 'stand', 'wave', 'happy', 'sad'],
    'solo': ['uniform'],
}


def build_audit_project(root: Path) -> Path:
    """生成项目（覆盖同名目录）。返回项目根。"""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    root = build_project(root)
    game = root / 'game'
    (game / 'defs.rpy').write_text(DEFS, encoding='utf-8')
    (game / 'script' / 'ch01.rpy').write_text(SCRIPT, encoding='utf-8')

    rows = []
    for tag, attrs in ATTRS.items():
        for a in attrs:
            rel = f'images/{tag}/{a}.png'
            p = game / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(PNG_1PX)
            rows.append({'ID': f'{tag}_{a}', 'Path': f'game/{rel}',
                         'Tag': tag, 'Attributes': a,
                         'Category': 'character', 'Notes': ''})
    from storygraph.assets.asset_registry import (
        _TABLE_FIELDS, append_rows_to_table, write_dict_to_excel)
    append_rows_to_table(root / 'asset_tables', 'images', rows)
    write_dict_to_excel(
        root / 'asset_tables' / 'characters.xlsx',
        {'lan': {'ID': 'lan', 'Name': '岚', 'Color': '',
                 'ImageTag': 'lan_tag', 'Aliases': '', 'Notes': ''}},
        _TABLE_FIELDS['character'])
    (root / 'storygraph.rules.json').write_text(RULES, encoding='utf-8')
    return root


def write_facts(root: Path) -> Path:
    """写假引擎事实（两个 tag 有分组；solo 只有一个组）。"""
    from storygraph.core import engine_facts as EF
    attrs = {
        'lan_tag': [
            {'group': 'outfit', 'attribute': 'swim_suit'},
            {'group': 'outfit', 'attribute': 'uniform'},
            {'group': 'pose', 'attribute': 'stand'},
            {'group': 'pose', 'attribute': 'wave'},
        ],
        'mix': [
            {'group': 'outfit', 'attribute': 'swim_suit'},
            {'group': 'outfit', 'attribute': 'uniform'},
            {'group': 'outfit', 'attribute': 'stand'},
            {'group': 'outfit', 'attribute': 'wave'},
            {'group': 'expressions', 'attribute': 'happy'},
            {'group': 'expressions', 'attribute': 'sad'},
        ],
        'solo': [{'group': 'outfit', 'attribute': 'uniform'}],
    }
    cands = {tag: [a for a in ATTRS[tag]] for tag in ATTRS}
    facts = EF.build_facts({
        'attributes': [(EF.SRC_PROBE, attrs)],
        'candidates': [(EF.SRC_PROBE, cands)],
    }, collection={'parse_clean': True, 'probe_complete': True})
    path = EF.facts_path(root)
    path.write_text(EF.dumps(facts), encoding='utf-8')
    return path


class EngineStub:
    def __init__(self, root):
        self.project_root = str(root)
        self._registry = None
        try:
            from storygraph.pipeline.build import load_asset_registry
            self._registry = load_asset_registry(self.project_root)
        except Exception:
            pass
        if self._registry is None:
            return
        try:
            from storygraph.parsing.discover import discover_scripts
            from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer
            from storygraph.story.beat_build import apply_script_definitions
            pr, _g = IncrementalRenPyAnalyzer().analyze(
                files=discover_scripts(self.project_root), graph=None)
            apply_script_definitions(self._registry, pr)
        except Exception:
            pass

    def get_registry(self):
        return self._registry
