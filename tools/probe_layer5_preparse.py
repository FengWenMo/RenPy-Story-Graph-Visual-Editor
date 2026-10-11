"""第 1 项：**能不能让自定义写法在解析期就被引擎认识**？（真机判定：**不能**）

## 结论（2026-10-11 全部实测）

**引擎里不存在"注册代码先于解析执行"的位置。** 三条路都试过：

| 试法 | 结果 |
|---|---|
| 项目自己的 `init python: renpy.register_statement(...)` | ❌ 永远太晚（见下面时序） |
| 往副本里放 `game/*.py`（以为引擎会先跑它） | ❌ **引擎根本不执行项目里的 `.py`**（实测 `[hook]` 一行都没打印） |
| 外部 runner 挂 `renpy.main.main` / `early_init_emulator` 钩子 | ❌ Ren'Py 会 **pickle 模块属性做备份**（`renpy/__init__.py:341`）→ 新补的函数过不了 pickle（"Cannot pickle …"） |

## 时序为什么是死的（读书 + 实测）

| 环节 | 在哪 | 关键 |
|---|---|---|
| 解析所有 `.rpy` | `script.load_script()` | **只解析**：init 节点收进 `initcode` 后 `return`（`script.py:483`） |
| 执行 init（**注册代码在这里跑**） | `main.py` 的 init 循环 | 在所有文件**解析完之后** |
| `register_statement` 改什么 | `statements.py:344/350` | `renpy.parser.statements.add(...)` + `parsers.add(...)` —— **改的是解析器的语句字典** |
| 文件加载顺序 | `script.py:389-435` | `renpy/common` 组在前；`game` 分 libs/mods/大写/小写四档 —— **但哪一档都不是"先执行再解析"** |

⇒ **项目自己永远赶不上解析期**（与优先级、文件名、同文件/跨文件都无关，
这也解释了为什么三种布局实测全失败）。
⇒ 工具**只能用"注入副本"的办法**：把注册代码放进副本里、
**在引擎启动之前**由工具自己执行掉（那是工具的能力，不是项目的能力）。

## 本脚本验证的部分（可复用）

  ① `extract_init_python()` / `collect_registrations()`：从项目 `.rpy` 里
     **抽出注册代码**（纯函数，能抽出来 —— 这是"注入副本"那一招的前提）；
  ② 基线：不预注册时引擎确实编译不过（对照组，证明实验有效）；
  ③ 预注册：走 `game/*.py` 那条 —— **失败**，并把"没跑到"如实报出来。

    python tools/probe_layer5_preparse.py
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.pipeline.engine_collect import _engine_python, find_sdk   # noqa: E402
from storygraph.pipeline.statement_probe import _copy_for_ask              # noqa: E402

sdk = find_sdk(None)
py = _engine_python(Path(sdk))

#: 合成工程：注册一个 `weather` 并在**同文件**使用它（最简对抗）
PROJ = {
    'game/cds.rpy': '''init python:
    renpy.register_statement("weather", parse=lambda l: l.eol())
''',
    'game/use.rpy': 'label start:\n    weather rain\n    return\n',
}


def extract_init_python(text: str) -> str:
    """
    ① **提取**：把 `.rpy` 里 `init python:` 块的内容抽出来（纯函数、只认最朴素的形态）。

    做法：找出 `init python:` / `init -N python:` 行，取它后面**更深的缩进块**，
    去掉公共缩进。抽不出就返回空串（**不猜**）。
    """
    lines = str(text or '').splitlines()
    out, i = [], 0
    while i < len(lines):
        s = lines[i]
        stripped = s.strip()
        if stripped.startswith('init') and 'python' in stripped \
                and stripped.endswith(':'):
            base = len(s) - len(s.lstrip())
            block = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    block.append('')
                    j += 1
                    continue
                ind = len(nxt) - len(nxt.lstrip())
                if ind <= base:
                    break
                block.append(nxt)
                j += 1
            # 去掉公共缩进
            real = [b for b in block if b.strip()]
            if real:
                cut = min(len(b) - len(b.lstrip()) for b in real)
                out.extend(b[cut:] for b in block)
                out.append('')
            i = j
            continue
        i += 1
    return '\n'.join(out).strip('\n')


def collect_registrations(root: Path) -> str:
    """项目里所有 `.rpy` 的 `init python:` 代码，拼成一段（**顺序按文件名**）。"""
    chunks = []
    for fp in sorted((root / 'game').rglob('*.rpy')):
        try:
            text = fp.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        code = extract_init_python(text)
        if 'register_statement' in code or 'register_say' in code:
            chunks.append(f'# —— {fp.name} ——\n{code}')
    return '\n\n'.join(chunks)


def make_project(where: Path) -> Path:
    for rel, body in PROJ.items():
        fp = where / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding='utf-8')
    (where / 'game' / 'images').mkdir(parents=True, exist_ok=True)
    return where


# ── ① 提取 ────────────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix='l5_pre_'))
proj = make_project(tmp / 'proj')
print('=== ① 提取注册代码')
code = collect_registrations(proj)
print('    抽到：')
for ln in code.splitlines():
    print('      |', ln)
assert 'register_statement' in code, '提取失败 —— 后面都不用试了'

# ── 基线：不预注册，直接 lint（应当编译不过）────────────────────────
base = subprocess.run([str(py), str(Path(sdk) / 'renpy.py'), str(proj),
                       'lint'], capture_output=True, text=True, timeout=180,
                      encoding='utf-8', errors='replace')
base_out = (base.stdout or '') + (base.stderr or '')
print('\n=== ② 基线（不预注册）：exit =', base.returncode)
print('   ', [ln.strip()[:90] for ln in base_out.splitlines()
              if 'expected statement' in ln] or '（没看到 expected statement）')

# ── ③ 预注册：往**副本**里放一个 `game/*.py` ────────────────────────────
# 引擎在**解析 .rpy 之前**会先执行 `game/` 里的 `.py`（那是它自己的加载顺序），
# 而那时 `renpy.register_statement` 已经可用 ⇒ 注册能赶在解析期之前。
# 注册代码从**环境变量指向的文件**读进来 —— 这样不用把中文/长代码塞进源码字面量。
# ── ③ 试"往副本里放 game/*.py"（**已被真机否定**）────────────────────────
# 以为：引擎会先执行 `game/` 里的 `.py`，那时 `register_statement` 可用。
# 实测：**引擎根本不执行项目里的 `.py`**（钩子里那行打印一次都没出现）。
# 保留这一段是为了**留下反证**（以后有人再想到这招，直接看这里的结果）。
HOOK_PY = '''"""storygraph 预注册钩子（工具临时放进副本；用完即删）。"""
import os

print('[hook] game/*.py 被执行了')
_path = os.environ.get('STORYGRAPH_PREREGISTER')
import renpy
print('[hook] renpy.register_statement 在不在 =',
      hasattr(renpy, 'register_statement'))
if _path and os.path.isfile(_path):
    with open(_path, 'r', encoding='utf-8') as fh:
        _code = fh.read()
    exec(compile(_code, _path, 'exec'), globals())
    print('[hook] 注册代码执行完成')
'''

code_file = tmp / 'prereg_code.py'
code_file.write_text(code, encoding='utf-8')
(proj / 'game' / 'zz_storygraph_preregister.py').write_text(HOOK_PY,
                                                            encoding='utf-8')

env = dict(os.environ)
env['STORYGRAPH_PREREGISTER'] = str(code_file)
env['PYTHONIOENCODING'] = 'utf-8'
print('\n=== ③ 预注册（副本里放 game/zz_storygraph_preregister.py）')
pre = subprocess.run([str(py), str(Path(sdk) / 'renpy.py'), str(proj), 'lint'],
                     capture_output=True, text=True, timeout=300, env=env,
                     encoding='utf-8', errors='replace')
pre_out = (pre.stdout or '') + (pre.stderr or '')
print('    exit =', pre.returncode)
for ln in [x for x in pre_out.splitlines() if x.strip()][-12:]:
    print('   ', ln[:130])

print('\n=== 判定')
pre_ok = 'expected statement' not in pre_out and pre.returncode == 0
print('    基线（不预注册）编译不过 →',
      '✅' if 'expected statement' in base_out else '❌（基线就不对，结论无效）')
print('    钩子跑到了吗           →',
      '✅ 跑到了' if '[hook]' in pre_out else '❌ 没跑到')
print('    预注册后编译通过       →', '✅' if pre_ok else '❌')

shutil.rmtree(tmp, ignore_errors=True)
