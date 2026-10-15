"""金标基线：把"指纹到底钉在**谁**身上"显式记下来。

为什么要有它（2026-10-15，用户审核提的 P1）
------------------------------------------
8 条金标全部钉在**活的、随时被编辑的真机项目**上，于是每次红了都要人肉二分：

    "工具变了" 与 "项目变了" 在指纹里**长得一模一样**。

更糟的是它**削弱所有金标的说服力** —— 真机上有一个"既有问题"（如
`mini_game_flipcard.rpy` 不幂等）时，你说不清那是这次改动引入的、还是本来就在。

这一份把**项目侧**的摘要单独记下来。红了先跑它：

    python tools/golden_baseline.py --check    # 项目侧变了吗？
    python tools/golden_baseline.py --save     # 确认新输出无误后**重生基线**

`--check` 的结论只有三种，都是**可执行**的：

  · 项目侧没变 → 指纹却变了 ⇒ **工具侧变化，是真回归**，去查代码；
  · 项目侧变了 → 指纹也变了 ⇒ 项目内容在长，**先怀疑数据**，
    逐条确认新输出无误后 `--save` 重生；
  · 项目侧变了 → 指纹没变 ⇒ 这次改动没碰到金标覆盖面（正常）。

⚠ **为什么不用"冻结的项目切片"当基线**（用户给的另一个选项，我判它不可行）：
`scan` / `engine_facts` 那两条金标依赖**真实的资源文件树**
（`game/images/**` 等，真机是 8GB 量级 —— 见 `golden_scan_fingerprint.txt` 本体
就有 1MB 的路径清单）。切片冻不住它，只能"记下项目侧摘要 + 显式重生"。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'tests' / 'golden_baseline.json'
REAL = Path(r'D:\千连万理\RenpyTest\sr_general')

#: 项目侧摘要要覆盖的**输入面**（金标的输入就是这些）：
#:   · `.rpy` 正文 —— parse / render / hydrate / patch / describe 的输入；
#:   · 资源文件清单（**只记路径**，不读内容：图/音是二进制，读它没意义）；
#:   · 资源表（`*asset_tables*/**.xlsx`）。
_RPY_GLOBS = ('game/**/*.rpy',)
_ASSET_DIRS = ('game/images', 'game/bgm', 'game/sound', 'game/audio',
               'game/voice', 'game/video')
_TABLE_GLOBS = ('game/asset_tables/*.xlsx', 'asset_tables/*.xlsx',
                'game/**/*asset_tables*/**/*.xlsx')


def _sha1_lines(lines) -> str:
    h = hashlib.sha1()
    for ln in sorted(str(x) for x in lines):
        h.update(ln.encode('utf-8', 'replace'))
        h.update(b'\n')
    return h.hexdigest()


def _git(root: Path, *args) -> str:
    try:
        p = subprocess.run(['git', *args], cwd=str(root), capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
        return (p.stdout or '').strip()
    except OSError:
        return ''


def snapshot(project: Path) -> dict:
    """项目侧的**内容摘要**（与工具代码无关，只描述"输入长什么样"）。"""
    project = Path(project)
    rpy = []
    asset_paths = []
    tables = []
    for pat in _RPY_GLOBS:
        rpy += [p for p in project.glob(pat) if p.is_file()]
    for pat in _TABLE_GLOBS:
        tables += [p for p in project.glob(pat) if p.is_file()]
    for rel in _ASSET_DIRS:
        d = project / rel
        if d.is_dir():
            asset_paths += [p for p in d.rglob('*') if p.is_file()]

    rpy_digest = hashlib.sha1()
    for p in sorted(rpy):
        rpy_digest.update(p.relative_to(project).as_posix().encode())
        rpy_digest.update(b'\0')
        rpy_digest.update(p.read_bytes())
        rpy_digest.update(b'\0')

    return {
        'project': str(project),
        'git_rev': _git(project, 'rev-parse', 'HEAD'),
        'git_dirty': _git(project, 'status', '--porcelain'),
        'rpy_files': len(rpy),
        'rpy_sha1': rpy_digest.hexdigest(),
        'assets': len(asset_paths),
        'assets_sha1': _sha1_lines(p.relative_to(project).as_posix()
                                   for p in asset_paths),
        'tables': len(tables),
        'tables_sha1': _sha1_lines(p.relative_to(project).as_posix()
                                   for p in tables),
    }


#: 比对时**只比这些键**（`project` 路径与 `git_rev` 只作展示；
#: 内容摘要才是判据 —— 用户 amend/rebuild 提交不该让内容摘要"变"）。
_COMPARE = ('rpy_files', 'rpy_sha1', 'assets', 'assets_sha1',
            'tables', 'tables_sha1')


def compare(old: dict, new: dict) -> list:
    """→ `[(键, 旧值, 新值), …]`：哪些**内容面**变了（空 = 项目侧没变）。"""
    return [(k, old.get(k), new.get(k)) for k in _COMPARE
            if old.get(k) != new.get(k)]


def main() -> int:
    ap = argparse.ArgumentParser(description='金标基线：项目侧内容摘要')
    ap.add_argument('project', nargs='?', default=str(REAL))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--save', action='store_true', help='记录当前项目侧摘要')
    g.add_argument('--check', action='store_true', help='与记录比：项目侧变了吗')
    a = ap.parse_args()

    project = Path(a.project)
    if not project.is_dir():
        print(f'项目不在本机：{project}（换机 → 这不算失败）')
        return 0
    cur = snapshot(project)

    if a.save:
        cur['saved_at'] = __import__('datetime').datetime.now().isoformat(
            timespec='seconds')
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(cur, ensure_ascii=False, indent=1,
                                       sort_keys=True), encoding='utf-8')
        print('已记录基线 -> %s' % BASELINE)
        for k in ('git_rev', 'rpy_files', 'rpy_sha1', 'assets', 'assets_sha1',
                  'tables'):
            print('  %-12s %s' % (k, cur[k]))
        print('  git_dirty   %s 行未提交'
              % len([ln for ln in cur['git_dirty'].splitlines() if ln.strip()]))
        return 0

    if not BASELINE.is_file():
        print('还没有基线：先跑 --save')
        return 2
    old = json.loads(BASELINE.read_text(encoding='utf-8'))
    diff = compare(old, cur)
    print('项目根：%s' % project)
    print('记录于：%s   git_rev %s' % (old.get('saved_at'), old.get('git_rev')))
    print('当前：       git_rev %s' % cur.get('git_rev'))
    if not diff:
        print()
        print('✅ **项目侧没变**（.rpy / 资源清单 / 资源表 摘要全同）')
        print('   → 金标若变红，那是**工具侧**变化 = 真回归，去查代码。')
        return 0
    print()
    print('⚠ **项目侧变了**：')
    for k, o, n in diff:
        print('   %-12s %s → %s' % (k, o, n))
    print()
    print('   → 金标若同时变红，**先怀疑数据**（项目内容在长）；')
    print('     逐条确认新输出是对的之后，跑 `--save` 重生基线、再刷新金标。')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
