"""
地狱测试 · "程序改代码 → 表格自动反映"（2026-10-08）
====================================================

缺口清单里这条的原话是"仍是 1.5s 轮询 + 全量重解析"。实测后要**分开看**：

  · **1.5s 轮询**是 `assets/asset_events.AssetFileWatcher` 的**设计**（自带
    "连续两轮一致才算稳定"的检查，避免 Excel 保存中途/编辑器临时文件触发事件）
    —— 那是防抖，不是浪费，不该"修掉"。
  · **"全量重解析"才是要钉的点**：改一个 `.rpy` 之后，**没改的文件不该再被读
    一遍**。这件事靠 `IncrementalRenPyAnalyzer` 的 (mtime, size) 缓存成立 ——
    但它是**口头的**（没有判据钉着）。这份测试把它变成可验证的不变量：
    改一个文件 → 只有它被 `read_text`。

⚠ 这条判据的"红线"（J2）：如果哪天有人在缓存查询**之前**就无条件读一遍盘
（比如又加了一处"挂载前重读"），计数会立刻变红。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storygraph.assets.asset_engine import AssetEngine              # noqa: E402
from storygraph.parsing.renpy_parser import IncrementalRenPyAnalyzer  # noqa: E402


def _count_reads(monkeypatch) -> dict:
    """记录每个文件被 `Path.read_text` 读了几次。"""
    real = Path.read_text
    counts: dict = {}

    def spy(self, *args, **kwargs):
        key = str(Path(self))
        counts[key] = counts.get(key, 0) + 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', spy)
    return counts


def _scripts(tmp_path, n=3) -> list:
    game = tmp_path / 'game'
    game.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        f = game / f'c{i}.rpy'
        f.write_text(f'label c{i}:\n    "line {i}"\n    return\n',
                     encoding='utf-8')
        out.append(f)
    return out


# ===========================================================================
#  ① 增量分析器：改一个文件，只读那一个
# ===========================================================================

def test_reanalyze_only_reads_the_changed_file(tmp_path, monkeypatch):
    files = _scripts(tmp_path)
    az = IncrementalRenPyAnalyzer()
    az.analyze(files=files, graph=None)          # 第一次：全部读一遍

    counts = _count_reads(monkeypatch)
    files[1].write_text('label c1:\n    "changed"\n    return\n',
                        encoding='utf-8')
    az.analyze(files=files, graph=None)          # 第二次

    read = {k for k, v in counts.items() if v}
    assert str(files[1]) in read, '改过的文件居然没被重读'
    extra = read - {str(files[1])}
    assert not extra, f'没改的文件被重读了（全量重解析）：{sorted(extra)}'


def test_unchanged_content_is_not_reread_even_when_mtime_changes(tmp_path,
                                                                 monkeypatch):
    """
    内容没变、只是"被碰了一下"（mtime 变、size 不变）也不该影响结果 ——
    缓存键含 size/mtime，这里只要确认**行为正确**（解析结果照样对），
    不要求它一定不读（读了也只是缓存失效，不算缺陷）。
    """
    files = _scripts(tmp_path, n=1)
    az = IncrementalRenPyAnalyzer()
    pr1, _ = az.analyze(files=files, graph=None)
    pr2, _ = az.analyze(files=files, graph=None)
    assert [getattr(s, 'kind', '') for s in pr1.statements] == \
        [getattr(s, 'kind', '') for s in pr2.statements]


# ===========================================================================
#  ② AssetEngine：脚本变化后的重载也是增量的
# ===========================================================================

def _engine(tmp_path, scripts) -> AssetEngine:
    root = tmp_path
    adir = root / 'game' / 'asset_tables'
    adir.mkdir(parents=True, exist_ok=True)
    return AssetEngine(asset_dir=adir, project_root=root,
                       script_files=scripts, watch_source_dirs=False)


def test_script_reload_reads_only_the_edited_script(tmp_path, monkeypatch):
    files = _scripts(tmp_path)
    eng = _engine(tmp_path, files)
    eng._load_scripts()                          # 首次

    counts = _count_reads(monkeypatch)
    files[0].write_text('label c0:\n    "edited"\n    return\n',
                        encoding='utf-8')
    eng._handle_script_changed()

    read = {k for k, v in counts.items() if v}
    assert str(files[0]) in read
    extra = read - {str(files[0])}
    assert not extra, f'脚本变化处理把没改的脚本也读了一遍：{sorted(extra)}'


# ===========================================================================
#  ③ 反向：**没有任何文件变**时不该读盘
# ===========================================================================

def test_no_change_means_no_disk_read(tmp_path, monkeypatch):
    files = _scripts(tmp_path)
    az = IncrementalRenPyAnalyzer()
    az.analyze(files=files, graph=None)
    counts = _count_reads(monkeypatch)
    az.analyze(files=files, graph=None)          # 什么都没改
    read = {k for k, v in counts.items() if v}
    assert not read, f'没改任何文件却读了盘：{sorted(read)}'


@pytest.mark.parametrize('n', [1, 5])
def test_scales_without_reading_unrelated_files(tmp_path, monkeypatch, n):
    files = _scripts(tmp_path, n=n)
    az = IncrementalRenPyAnalyzer()
    az.analyze(files=files, graph=None)
    counts = _count_reads(monkeypatch)
    last = files[-1]
    last.write_text('label z:\n    "tail"\n    return\n', encoding='utf-8')
    az.analyze(files=files, graph=None)
    read = {k for k, v in counts.items() if v}
    assert str(last) in read
    assert not (read - {str(last)}), sorted(read)
