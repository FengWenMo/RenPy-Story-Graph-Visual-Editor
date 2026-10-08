"""
地狱测试 · 改名补丁的**确认闸门**（D3'，2026-10-08）
=====================================================

用户 2026-10-08 定的契约（先做闸门、不接 Qt）：

> 默认**不碰用户的原始文件**，先展示 diff 或预览，确认后才写入，
> 且写入必须可回滚。

三条判据（用户点名）：

  ① 未确认时**不产生任何写操作**；
  ② 确认后写入的字节**与预览一致**；
  ③ 回滚后字节**逐位还原**。

⚠ 变异验证（J2）：把 `apply_rename_patch` 的 `confirm` 判断去掉（恢复"算完就写"），
本文件必须变红。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_engine import AssetEngine, _Change            # noqa: E402
from storygraph.assets.asset_extensions import (                          # noqa: E402
    apply_rename_patch, generate_rename_patch, plan_rename_patch,
    rollback_rename_patch,
)

SRC = ('define e = Character("Eileen")\n'
       'label start:\n'
       '    play music bgm_hero\n'
       '    show hero\n'
       '    show hero_happy\n'
       '    e "hi"\n'
       '    jump ending\n'
       'label ending:\n'
       '    return\n')


def _proj(tmp_path) -> tuple:
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    script = game / 'script.rpy'
    script.write_text(SRC, encoding='utf-8')
    return tmp_path, script


def _digest(path) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


# ===========================================================================
#  ① 预览是纯的：算 diff 不写盘
# ===========================================================================

def test_plan_is_pure_and_reports_exactly_what_would_change(tmp_path):
    _root, script = _proj(tmp_path)
    before = script.read_bytes()
    plan = plan_rename_patch('hero', 'protagonist', [script])
    assert script.read_bytes() == before, '算 diff 竟然写了盘'
    assert plan['matched_files'] == [str(script)]
    # `hero_happy`（词后接 `_`）与 `bgm_hero`（词前接 `_`）都**不该**被部分匹配
    # → 只有第 4 行 `show hero` 会变。
    assert plan['total_lines'] == 1, plan['files'][0]['changed']
    changed_lines = [c[0] for c in plan['files'][0]['changed']]
    assert changed_lines == [4], changed_lines


def test_plan_reports_nothing_when_no_reference(tmp_path):
    _root, script = _proj(tmp_path)
    plan = plan_rename_patch('nobody', 'x', [script])
    assert plan['files'] == [] and plan['total_lines'] == 0


# ===========================================================================
#  ② 未确认 → 零写操作
# ===========================================================================

def test_unconfirmed_apply_produces_no_write(tmp_path):
    _root, script = _proj(tmp_path)
    before = script.read_bytes()
    plan = plan_rename_patch('hero', 'protagonist', [script])
    res = apply_rename_patch(plan, confirm=False, backup=True)
    assert res['ok'] is False and res['reason'] == 'needs_confirmation'
    assert res['modified'] == [] and res['backup_files'] == []
    assert script.read_bytes() == before, '未确认却改了文件'
    assert not script.with_suffix('.rpy.bak').exists(), '未确认却留了备份'
    assert res['plan']['total_lines'] == 1, '拒绝的同时要把预览带回来'


def test_facade_dry_run_false_still_needs_confirm(tmp_path):
    """`generate_rename_patch(dry_run=False)` 也**不再等于会写**。"""
    _root, script = _proj(tmp_path)
    before = script.read_bytes()
    res = generate_rename_patch('hero', 'protagonist', [script],
                               dry_run=False, backup=True)
    assert res['ok'] is False and res['reason'] == 'needs_confirmation'
    assert script.read_bytes() == before


# ===========================================================================
#  ③ 确认后：写入字节 == 预览；回滚逐位还原
# ===========================================================================

def test_confirmed_write_matches_the_preview_byte_for_byte(tmp_path):
    _root, script = _proj(tmp_path)
    plan = plan_rename_patch('hero', 'protagonist', [script])
    preview = plan['files'][0]['new_text']
    res = apply_rename_patch(plan, confirm=True, backup=True)
    assert res['ok'] is True and res['modified'] == [str(script)]
    assert script.read_text(encoding='utf-8') == preview, '写进去的与预览不一致'
    # 词边界仍然成立
    text = script.read_text(encoding='utf-8')
    assert 'protagonist' in text and 'hero_happy' in text


def test_rollback_restores_the_bytes_exactly(tmp_path):
    _root, script = _proj(tmp_path)
    before = _digest(script)
    plan = plan_rename_patch('hero', 'protagonist', [script])
    res = apply_rename_patch(plan, confirm=True, backup=True)
    assert _digest(script) != before, '没写成功，回滚判据无意义'
    rb = rollback_rename_patch(res)
    assert rb['restored'] == [str(script)] and rb['failed'] == []
    assert _digest(script) == before, '回滚后字节没有逐位还原'


def test_rollback_multiple_files(tmp_path):
    game = tmp_path / 'game'
    game.mkdir(parents=True)
    a = game / 'a.rpy'
    b = game / 'b.rpy'
    a.write_text('show hero\n', encoding='utf-8')
    b.write_text('label x:\n    show hero\n    return\n', encoding='utf-8')
    da, db = _digest(a), _digest(b)
    plan = plan_rename_patch('hero', 'protagonist', [a, b])
    res = apply_rename_patch(plan, confirm=True, backup=True)
    assert len(res['modified']) == 2
    rollback_rename_patch(res)
    assert _digest(a) == da and _digest(b) == db


# ===========================================================================
#  ④ 引擎那侧：闸门真的被征询（approve/refuse 两条路）
# ===========================================================================

def _engine(tmp_path, script) -> AssetEngine:
    return AssetEngine(asset_dir=tmp_path / 'game' / 'asset_tables',
                       project_root=tmp_path, script_files=[script],
                       watch_source_dirs=False)


def _rename_change(new_id='protagonist') -> _Change:
    return _Change(kind='renamed', category='characters', asset_id='hero',
                   before={'Name': 'Hero'}, after={'Name': 'Protagonist'},
                   new_id=new_id)


def test_engine_refusal_leaves_the_file_untouched(tmp_path):
    _root, script = _proj(tmp_path)
    eng = _engine(tmp_path, script)
    before = script.read_bytes()
    eng._handle_rename(_rename_change(), refs=[(str(script), 4)], approved=False)
    assert script.read_bytes() == before, '用户拒绝后文件还是被改了'


def test_engine_approval_writes_and_is_rollbackable(tmp_path):
    _root, script = _proj(tmp_path)
    eng = _engine(tmp_path, script)
    before = _digest(script)
    eng._handle_rename(_rename_change(), refs=[(str(script), 4)], approved=True)
    assert _digest(script) != before, '确认了却没写'
    assert 'protagonist' in script.read_text(encoding='utf-8')
    # 备份在 → 可逐位还原（回滚能力是契约的一部分）
    bak = script.with_suffix('.rpy.bak')
    assert bak.is_file()
    assert _digest(bak) == before, '备份不等于写前那一份 → 回滚不可靠'


@pytest.mark.parametrize('op', ['plan', 'apply_unconfirmed'])
def test_no_write_in_any_preview_path(tmp_path, op):
    _root, script = _proj(tmp_path)
    before = script.read_bytes()
    if op == 'plan':
        plan_rename_patch('hero', 'x', [script])
    else:
        apply_rename_patch(plan_rename_patch('hero', 'x', [script]),
                           confirm=False)
    assert script.read_bytes() == before
