import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
OGG = b'OggS' + b'\x00' * 40

DEMO = ROOT / 'demo'
game = DEMO / 'game'

# --- 素材 ---
assets = {
    'images/bg/bg_classroom.png': PNG,
    'images/bg/bg_rooftop.png': PNG,
    'images/eileen/happy.png': PNG,
    'images/eileen/sad.png': PNG,
    'images/eileen/happy_blush.png': PNG,
    'images/mary/smile.png': PNG,
    'music/night.ogg': OGG,          # 无 bgm_ 前缀，旧实现会错判成音效
    'music/rain.ogg': OGG,
    'sounds/click.ogg': OGG,
    'sounds/door.ogg': OGG,
    'voice/v_eileen_01.ogg': OGG,
    'voice/v_eileen_02.ogg': OGG,
}
for rel, data in assets.items():
    p = game / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)

# --- 章节脚本（含自定义 transform + 注释说明 + 多角色 + 音频/语音）---
SCRIPT = '''define e = Character("爱莲", color="#c8ffc8", image="eileen",
                      voice_tag="eileen", what_prefix="（", what_suffix="）")
define m = Character("玛丽", color="#ffc8c8", image="mary")

# 靠左站，双人对话时主角在左
transform leftish:
    xpos 0.2
    xanchor 0.0

# 挨打/受惊时用的剧烈抖动
transform shake_hard(t=0.05, n=6):
    "剧烈抖动，挨打/惊吓演出用"
    block:
        linear t xoffset 8
        linear t xoffset -8
    repeat n

# 柔和淡入，用于回忆切入
transform fade_in_soft:
    alpha 0.0
    linear 0.6 alpha 1.0

label ch01:
    scene bg classroom
    play music night fadein 1.0
    show eileen happy at leftish
    # 表情A6，距离近
    e "早上好，昨晚睡得好吗？"
    voice v_eileen_01
    play sound door
    m "还行，就是雨下了一整晚。"
    hide eileen
    show eileen sad at leftish
    with dissolve
    play music rain fadeout 2.0
    show mary smile at right
    m "别想了，今天我们还有正事。"
    stop music
    return
'''
script_dir = game / 'script'
script_dir.mkdir(parents=True, exist_ok=True)
(script_dir / 'ch01.rpy').write_text(SCRIPT, encoding='utf-8')

# --- 扫描生成资源表 ---
from storygraph.assets.asset_registry import scan_assets, write_asset_excel
from storygraph.assets.asset_engine import AssetEngine
from storygraph.assets.asset_events import AssetEventBus, ConfirmationGate

asset_dir = DEMO / 'asset_tables'
asset_dir.mkdir(parents=True, exist_ok=True)
write_asset_excel(scan_assets(DEMO, include_ui=False), asset_dir)

# --- 通过引擎加载，让脚本里的 transform/角色 合并进注册表 ---
eng = AssetEngine(
    asset_dir=asset_dir, project_root=DEMO,
    script_files=[script_dir / 'ch01.rpy'],
    event_bus=AssetEventBus(),
    confirmation_gate=ConfirmationGate(ask_callback=lambda r: True),
    watch_source_dirs=False,
)
eng._initial_load()
reg = eng.get_registry()

# --- 把脚本解析出的角色/变换落成资源表（"从脚本导入角色表"的等价动作）---
from storygraph.assets.asset_registry import write_dict_to_excel

CHAR_FIELDS = ['ID', 'Name', 'Color', 'WhatColor', 'ImageTag', 'Kind',
               'VoiceTag', 'SideImage', 'WhatPrefix', 'WhatSuffix',
               'WhoPrefix', 'WhoSuffix', 'DefaultTransform', 'Notes']
write_dict_to_excel(
    asset_dir / 'characters.xlsx',
    {cid: {k: row.get(k, '') for k in CHAR_FIELDS}
     for cid, row in reg.characters.items()},
    CHAR_FIELDS)

TF_FIELDS = ['ID', 'Path', 'ATL', 'Params', 'Doc', 'Category', 'Notes']
write_dict_to_excel(
    asset_dir / 'transforms.xlsx',
    {tid: {k: row.get(k, '') for k in TF_FIELDS}
     for tid, row in reg.transforms.items()},
    TF_FIELDS)

# --- 生成剧本表（候选带说明列）---
from storygraph.story.script_table import open_rpy_as_table, import_beats_from_excel
table = open_rpy_as_table(script_dir / 'ch01.rpy',
                         DEMO / '剧本表_ch01.xlsx', registry=reg)

print('资源表目录 :', asset_dir)
print('  图片', len(reg.images), '音乐', len(reg.music),
      '音效', len(reg.sounds), '语音', len(reg.voices),
      '变换', len(reg.transforms), '角色', len(reg.characters))
print('自定义变换 :', sorted(reg.transforms))
print('剧本表     :', table)
print('演出单元数 :', len(import_beats_from_excel(table)))
unused = eng.find_unused_assets()
print('未使用资产 :', len(unused))
for u in unused:
    print('   -', u['category'], u['asset_id'])

# --- 本批新能力的演示产物 ---
print()
print('=== 多引擎导出 ===')
from storygraph.pipeline.build import build_project, format_build_result

for engine in ('unity', 'godot', 'json'):
    r = build_project(DEMO, out_dir=DEMO / 'build' / engine, engine=engine,
                      strict=False)
    print(f'  {engine}: {r.ok and "OK" or "有错误"} ->', r.out_dir)
    for a in r.artifacts:
        print('        ', Path(a).name)

print()
print('=== 三级校验 ===')
r = build_project(DEMO, out_dir=DEMO / 'build' / 'report', engine='json',
                  strict=False)
rep = r.report
print('  ', f"错误 {rep['error_count']}，警告 {rep['warning_count']}"
      f"（L1 {rep['by_level']['L1']} / L2 {rep['by_level']['L2']}"
      f" / L3 {rep['by_level']['L3']}）")

print()
print('=== 团队协作（Excel 前端 + SQLite 后端）===')
import storygraph.collab.collab_store as cs

db = DEMO / 'collab.db'
if db.exists():
    db.unlink()
cs.init_db(db, users=['alice', 'bob', 'carol'])
cs.push(db, table, actor='alice')
cs.upsert_assets(db, reg, actor='alice')
front = DEMO / '协作' / 'ch01_frontend_alice.xlsx'
front.parent.mkdir(parents=True, exist_ok=True)
cs.pull(db, front, actor='alice', registry=reg)
s = cs.status_summary(db)
print('  库文件  :', db)
print('  前端表  :', front, '（含「%s」工作表）' % cs.COLLAB_SHEET)
print('  总行数  :', s['lines'], '状态', s['by_status'])

print()
print('=== 协作命令示例 ===')
for line in [
    'python cli.py collab pull --db demo/collab.db --project demo '
    '--sheet demo/协作/ch01_alice.xlsx --user alice',
    'python cli.py collab push --db demo/collab.db --sheet '
    'demo/协作/ch01_alice.xlsx --user alice',
    'python cli.py collab lock --db demo/collab.db --label ch01 --user alice',
    'python cli.py validate demo --json',
    'python cli.py build demo --engine unity --out demo/build/unity',
]:
    print('  ', line)
