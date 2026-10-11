"""**检验办法**（用户提的）：完全通过 MCP 面去操作真实项目。

场景（用户 2026-10-11 原话）：
> "你操控 MCP 去做我的真实项目试试……比如说那个自定义模块导入那里，
>  你就可以试着去导入真实项目还未实装的 ABC 三套服装的定义命令"

于是本脚本**只用 `storygraph.mcp_surface` 的公开函数**（模拟模型看到的工具），
按一个真实工作流走一遍：

  ① `capabilities()`        —— 我有哪些工具、边界在哪
  ② `project_context(真项目)` —— 一次拿全现状
  ③ 把三套服装的定义命令**写进副本**（模拟"我想加这三条命令"）
     再看：`scan_unknown` / `discover` 有没有发现它们
  ④ `ask_engine(root, 三个名字)` —— 引擎认不认（**三态**）
  ⑤ `host_validate(...)`    —— 程序（storygraph.host.py）有没有话要说
  ⑥ `registry_check(...)`   —— 注册表里有没有（**没采到就是 unasked，不是 unknown**）
  ⑦ `health(root)`          —— 结构化体检

    python tools/probe_mcp_real_project.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph import mcp_surface as M                                # noqa: E402
from storygraph.pipeline.statement_probe import _copy_for_ask          # noqa: E402

REAL = Path(r'D:\千连万理\RenpyTest\sr_general')
if not REAL.is_dir():
    print('真实项目不在本机，跳过')
    raise SystemExit(0)

#: 三套服装的"定义命令"——按用户项目里已有的写法风格（`swim_suit` 那种）
SUITS = {
    'suit_a': 'suitA lyj 泳装A 强度=1',
    'suit_b': 'suitB lyj 泳装B 强度=2',
    'suit_c': 'suitC lyj 泳装C 强度=3',
}


def show(title, got, keys=('ok', 'source', 'reason')):
    print(f'\n--- {title}')
    for k in keys:
        if k in got:
            v = got[k]
            print(f'    {k} = {str(v)[:150]}')
    return got


print('=' * 74)
print('① capabilities() —— 模型先看自己有什么工具')
caps = M.capabilities()
done = [t for t in caps['tools'] if t.get('status', 'done') == 'done']
planned = [t for t in caps['tools'] if t.get('status') == 'planned']
print(f'    可用 {len(done)} 个：' + '、'.join(t['name'] for t in done))
print(f'    规划中 {len(planned)} 个：' + '、'.join(t['name'] for t in planned))
print(f'    硬边界 {len(caps["hard_limits"])} 条，第一条：{caps["hard_limits"][0][:76]}')

print('\n' + '=' * 74)
print('② project_context(真项目) —— 一次拿全现状')
ctx = M.project_context(REAL)
print(f'    ok={ctx["ok"]} ｜ 引擎可用={ctx["engine"]["collectable"]}'
      f' ｜ 扫描到未知={len(ctx["scan"]["unknown"])} 个'
      f' ｜ 体检问题={ctx["health"].get("problems")}')

print('\n' + '=' * 74)
print('③ 把三套服装的命令写进**副本**（模拟"我想加这三条命令"）')
tmp = Path(tempfile.mkdtemp(prefix='mcp_real_'))
work = _copy_for_ask(REAL, tmp)
(work / 'game' / 'zz_new_suits.rpy').write_text(
    'label zz_new_suits:\n'
    + ''.join(f'    {line}\n' for line in SUITS.values())
    + '    return\n', encoding='utf-8')
print(f'    副本：{work}')
sc = show('scan_unknown（工具认识的写法）', M.scan_unknown(work),
          ('ok', 'source', 'reason'))
print(f'    heads = {[(h["head"], h["count"]) for h in sc["heads"]][:8]}')
print(f'    ⚠ {sc.get("disclaimer", "")}')
di = show('discover（界面左边那一栏）', M.discover(work),
          ('ok', 'source', 'reason'))
print(f'    heads = {[h["head"] for h in di["heads"]][:8]}')

print('\n' + '=' * 74)
print('④ ask_engine(三个名字) —— 引擎认不认（三态）')
ask = show('ask_engine', M.ask_engine(work, list(SUITS), timeout=900),
           ('ok', 'source', 'reason', 'blocked_by'))
for h in ask.get('heads') or []:
    print(f'    {h["head"]:8s} recognized={h["recognized"]}'
          f'  {str(h.get("summary"))[:60]}')

print('\n' + '=' * 74)
print('⑤ host_validate(..) —— 程序有没有话要说')
for name in list(SUITS)[:1]:
    hv = show(f'host_validate({name})',
              M.host_validate(work, name, SUITS[name]),
              ('ok', 'source', 'reason', 'available', 'verified', 'why'))
    print(f'    ⇒ 结论：{hv.get("reason") or hv.get("why") or "（程序给了判断）"}')

print('\n' + '=' * 74)
print('⑥ registry_check(三个名字) —— 三态（没采到 = unasked，不是 unknown）')
rc = show('registry_check', M.registry_check(work, list(SUITS)),
          ('ok', 'source', 'reason'))
for k in ('known', 'unknown', 'unasked'):
    print(f'    {k} = {rc.get(k)}')

print('\n' + '=' * 74)
print('⑦ health(真项目) —— 结构化体检')
hd = show('health', M.health(REAL), ('ok', 'source', 'reason', 'problems'))
for f in (hd.get('findings') or [])[:5]:
    print(f'    [{f["severity"]:7s}] {f["title"]} × {f["count"]}')
cap = hd.get('capability') or {}
for key in ('undetermined', 'behavior'):
    if key in cap:
        print(f'    capability.{key}: severity={cap[key].get("severity")}'
              f' ｜ {str({k: v for k, v in cap[key].items() if k not in ("source",)})[:90]}')

shutil.rmtree(tmp, ignore_errors=True)
print('\n' + '=' * 74)
print('（副本已删；真实项目一个字都没动）')
