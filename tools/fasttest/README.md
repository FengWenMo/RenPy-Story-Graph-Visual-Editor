# fasttest —— 本项目的全量测试加速器

## 30 秒上手

```powershell
python tools/fasttest/run.py
```

就这一条。它跑的是**完整** `tests/`（当前 316 个文件 / 6662 条用例），
不跳过、不筛选、不改任何判据；只是把"怎么跑"换成并行 + 重文件优先。

- 通过：退出码 `0`，最后一行 `[fasttest] RESULT: PASS`
- 有失败：退出码 `1`，会直接列出失败 nodeid（可复制去单跑）
- 环境不对：退出码 `2`（缺 pytest / pytest-xdist）
- 看环境：`python tools/fasttest/run.py --check`

> **为什么不用裸 `python -m pytest` 跑全量？**
> 串行约 20 分钟；而且 `pytest.ini` 把 basetemp 固定在
> `.pip-tmp/pytest`，两个 pytest 同时跑会互删同一个目录，炸出成百上千个
> 假 ERROR（记忆里的 D6）。fasttest 每次用**全新的、带进程号的 basetemp**，
> 并在入口加锁。

---

## 它解决的三个具体问题

### 1. 串行全量被"真机金标"拖住

`tests/test_{scan,validate,patch,describe}_golden.py` 这几组会把 3.6 GB 的
`D:\千连万理\RenpyTest\sr_general` 反复解析/扫描，而且每组都有
`main + deterministic` 两条，deterministic 内部还要再算两遍 —— 同一份 3.6 GB
数据被算很多次。串行时它们就是墙钟时间的大头。

fasttest 让 xdist 把这些**文件**分到不同进程：
`-n 12 --dist loadfile`（一个测试文件只在一个 worker 上跑，保留文件内顺序；
金标文件本身只有 1~2 条，不会因此变成瓶颈）。

### 2. 裸 pytest 的 basetemp 会互相踩

`pytest.ini` 的 `addopts = --basetemp=.pip-tmp/pytest` 是给单人顺序跑准备的。
两个 pytest（比如两个 agent）同时跑时，后启动的会在 session 开始时
`rmtree` 掉前一个正在用的目录 → 大量 `FileNotFoundError`。

fasttest 每轮创建
`.pip-tmp/fasttest/runs/<时间戳>-<pid>/tmp`，xdist 再给每个 worker 分
`popen-gwN` 子目录；**并发安全**。入口的 `run.lock` 还会让第二个 fasttest
排队（裸 pytest 不受这个锁保护）。

### 3. 并行也会留长尾

金标文件本身很小（2~4 KB），按默认收集顺序排在最后。12 个 worker 先跑完
几千条快用例，最后一起等金标 —— 并行收益被尾部吃掉。

fasttest 按**历史每文件耗时**排序，把重文件提到队首（`--dist loadfile` 会
按文件把它分给空闲 worker）。权重来自：

1. 提交的 `seed.json`（冷启动用，见文末"维护"）；
2. `.pip-tmp/fasttest/durations.json`（每轮全量后自动学习，EWMA 平滑）。

文件内顺序不变；要原始收集顺序：`--no-order`。

---

## 用法

```powershell
python tools/fasttest/run.py                    # 全量（推荐）
python tools/fasttest/run.py --check            # 只看环境，不跑
python tools/fasttest/run.py --workers 8        # 机器忙 / 核少时降并发
python tools/fasttest/run.py --serial           # 对照：单进程（不要日常用）
python tools/fasttest/run.py --no-order         # 不按耗时重排
python tools/fasttest/run.py --keep             # 成功后也保留 basetemp（排查现场）
python tools/fasttest/run.py --clean-temp       # 先清 .pip-tmp 下 >24h 的旧临时目录
python tools/fasttest/run.py --no-prune-temp    # 完全不碰 .pip-tmp 旧目录
python tools/fasttest/run.py --no-wait          # 已有另一轮在跑时立刻退出
```

其余参数**原样透传给 pytest**：

```powershell
python tools/fasttest/run.py -x --tb=short
python tools/fasttest/run.py -k "golden or scan" --workers 4
python tools/fasttest/run.py tests/test_scan_golden.py -v
```

> 带文件 / nodeid 的调用会被当作"定向运行"：默认只用最多 4 个 worker，
> 且**不更新**耗时权重（避免把局部耗时学成全量）。真正验收请跑无参数的全量。

并发默认 `min(12, CPU 核数 - 1)`；可用 `--workers N` 或环境变量
`FASTTEST_WORKERS` 覆盖。不要用 `-n 0`。

### 输出与现场

```
[fasttest] RESULT: PASS
  用例   6662   通过 6659   失败 0   错误 0   跳过 2   预期失败 1
  用时   1m58s（12 进程）
  参考   历史串行约 21m00s，本轮约 10.7x
  日志   .pip-tmp/fasttest/runs/20261025-173000-1234/output.log
  明细   .pip-tmp/fasttest/runs/20261025-173000-1234/junit.xml
```

- `summary.json`：同一目录下的机器可读结论（耗时、计数、失败 nodeid）。
- `.pip-tmp/fasttest/last.json`：最近一轮（含定向运行），方便别的工具/agent 读取。
- `.pip-tmp/fasttest/last_full.json`：上一轮**纯全量**，用来算"新增失败 /
  上轮失败已消失"；定向运行不会覆盖它。
- 成功时默认删掉 basetemp（省磁盘）；失败时保留到**下一轮开跑前**，
  方便看 `tmp/` 现场。下一轮开始时会清掉上一轮的 `tmp/`（只留
  junit/log/summary），否则 `test_disk_hygiene` 要递归扫几千个旧目录。
- 只保留最近 3 轮 run 目录；近期目录不会动。超过 7 天没动的 `.pip-tmp`
  旧临时目录会在开跑前清掉（`--clean-temp` 把阈值降到 24h），这是
  `test_disk_hygiene` 从 391s 降到秒级的关键。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 全绿 |
| 1 | 有失败 / 错误 |
| 2 | 用法错、缺依赖、启动失败 |
| 3 | 另一轮在跑且 `--no-wait` / 排队超时 |
| 5 | 没收集到用例 |
| 130 | 被 Ctrl+C 中断 |

---

## 常见问题

**Q：必须装 pytest-xdist 吗？**
是（并行路径）。`python -m pip install -r requirements-dev.txt` 会装。
只跑 `--serial` 或 `--check` 时不需要。

**Q：为什么 UI 测试被跳过？**
没装 PySide6。fasttest 会在启动时打印 `PySide6: 未安装（UI 测试会被跳过）`；
那不是完整全量。CI 的无界面 job 可以接受，验收不行。

**Q：金标全 skip 了？**
`D:\千连万理\RenpyTest\sr_general` 不在本机（或用
`STORYGRAPH_REAL_PROJECT` 指到别处）。`--check` 会显示 `[缺失]`。

**Q：我能不能同时开两个 fasttest？**
不建议：两个都会排队（第二个等到第一个结束）。真要并发，`--no-wait` 只是
不排队，**不**代表安全 —— 测试里有少量写 `.pip-tmp` 固定路径的用例。

**Q：跑得比 `--serial` 还慢？**
看 `--workers`。机器被别的程序占满时（Qt/杀毒/另一个 AI），12 个进程会互相
抢；降到 6~8 再试。判断依据是 `RESULT` 上方的总耗时，不是 pytest 的百分比
（百分比按用例数，不按时间）。

**Q：失败数比上一轮多了，是回归吗？**
汇总会打印"新增失败 / 上轮失败已消失"。先确认没有第二个 pytest 在跑，
再按失败 nodeid 单跑；金标类失败先看是不是真机内容漂移（`MEMORY.md` 有记）。

---

## 维护

### seed.json 是什么

`tools/fasttest/seed.json` 只存"每个测试文件大概几秒"，用来冷启动排序。
它**不是判据**，删了也能跑（只是第一轮排序按文件体积估）。

测试文件大改后，建议重录一次：

```powershell
python tools/fasttest/run.py
python tools/fasttest/make_seed.py (Get-ChildItem .pip-tmp/fasttest/runs -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName\junit.xml
```

（脚本默认保留 seed 里已有的 `reference_seconds` —— 它只是汇总里的"历史
串行参考"，不影响通过/失败；要换就显式传
`--reference-seconds 1260`。）

本机学习到的权重在 `.pip-tmp/fasttest/durations.json`（已 gitignore）；
想回到干净状态直接删 `.pip-tmp/fasttest/`。

### 本工具**不**做什么

- 不修改 `tests/`、不 skip、不 `xfail`、不改断言；
- 不在测试进程内做缓存（不会"上一轮绿了这轮就不跑"）；
- 不碰 `.rpy` / Excel / 用户数据；只写 `.pip-tmp/fasttest/`。

### 为什么不用 `pytest -n auto` 就完事

`-n auto` 解决不了：固定 basetemp 的并发互删、重文件排在队尾的长尾、
两个 agent 同时跑、失败后的单行结论。fasttest 是这四件事的薄封装，
真正跑用例的仍是 pytest 本身。

---

## 实测（2026-10-25，本机 14 核 / 32G）

| 跑法 | 墙钟 |
|---|---|
| 串行 `python -m pytest`（README 历史记录） | 约 21 分钟 |
| 12 进程、普通收集顺序（改造前） | 7m24s |
| **fasttest（重文件优先 + 清理旧 `.pip-tmp`）** | **1m58s ~ 3m20s**（多次实测） |
| 最长单项 | `test_scan_assets_result_matches_the_golden` 19~39s |

折算 **6.3x ~ 10.7x**（机器上同时开着别的程序，负载不同）。改造前那轮里
`test_disk_hygiene` 单项 **391s** —— 它递归数 `.pip-tmp` 下 20 万个历史临时
文件，成了整轮的关键路径。清掉旧目录 + 把它固定到第一个跑之后，它回到
**8~11s**；再往后由本工具自己的保留策略维持，不会再涨回去。

