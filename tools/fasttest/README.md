# fasttest —— 本项目的全量测试加速器

## 30 秒上手

```powershell
python tools/fasttest/run.py
```

就这一条。它跑的是**完整** `tests/`（当前 320+ 个文件 / 6700+ 条用例，
以本轮 `summary.json` 的 `total` 为准），不跳过、不筛选、不改任何判据；
只是把"怎么跑"换成并行 + 重文件优先。

- 通过：退出码 `0`，最后一行 `[fasttest] RESULT: PASS`
- 有失败：退出码 `1`，会直接列出失败 nodeid（可复制去单跑）
- 环境不对：退出码 `2`（缺 pytest / pytest-xdist）
- 卡死：退出码 `124`，会点名卡住时正在跑的 nodeid（默认 300s 无输出触发）
- 看环境：`python tools/fasttest/run.py --check`

跑的时候每 15 秒有一行心跳（已跑多久 / 到百分之几 / 哪几条在跑），
agent 侧不会再把"正常但安静的长用例"误判成"读取任务输出卡死"。

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

旧 `tmp/` 不在启动时同步删：先**同盘 rename** 到仓库根的
`.fasttest-trash/`（毫秒级），再交给脱离的 `cmd /c rmdir` 后台删。
删 1.5 万个临时文件实测要 40 秒，积压多时能 5 分钟；以前这段时间正好卡在
"读取任务输出"上。`.fasttest-trash/` 已在 `.gitignore` 里，且不在
`.pip-tmp` 下，所以 `test_disk_hygiene` 不会去数它。

### 3. 并行也会留长尾

金标文件本身很小（2~4 KB），按默认收集顺序排在最后。12 个 worker 先跑完
几千条快用例，最后一起等金标 —— 并行收益被尾部吃掉。

fasttest 按**历史每文件耗时**排序，把重文件提到队首（`--dist loadfile` 会
按文件把它分给空闲 worker）。权重来自：

1. 提交的 `seed.json`（冷启动用，见文末"维护"）；
2. `.pip-tmp/fasttest/durations.json`（每轮全量后自动学习，EWMA 平滑）。

> ⚠ **pytest-xdist 的默认行为和上面的排序相反**：它默认打开
> `--loadscope-reorder`，会按"每个文件有多少条用例"重排 work unit。
> 于是 3 条用例的 `test_disk_hygiene`、只有 2 条的真机金标全被踢到 99%，
> 重文件优先等于白做（2026-10-26 实测：全量 6m14s，最后 2 分钟全在等金标）。
> fasttest 显式传 `--no-loadscope-reorder`；手写 `pytest -n` 时别忘了这个开关。

文件内顺序不变；要原始收集顺序：`--no-order`。

---

## 用法

```powershell
python tools/fasttest/run.py                    # 全量（推荐）
python tools/fasttest/run.py --check            # 只看环境，不跑
python tools/fasttest/run.py --workers 8        # 机器忙 / 核少时降并发
python tools/fasttest/run.py --serial           # 对照：单进程（不要日常用）
python tools/fasttest/run.py --retry-failed     # 只复跑上一轮纯全量失败的 nodeid
python tools/fasttest/run.py --no-order         # 不按耗时重排
python tools/fasttest/run.py --keep             # 成功后也保留 basetemp（排查现场）
python tools/fasttest/run.py --clean-temp       # 先清 .pip-tmp 下 >24h 的旧临时目录
python tools/fasttest/run.py --no-prune-temp    # 完全不碰 .pip-tmp 旧目录
python tools/fasttest/run.py --no-wait          # 已有另一轮在跑时立刻退出
python tools/fasttest/run.py --heartbeat 30     # 心跳间隔（默认 15s；0=关）
python tools/fasttest/run.py --idle-timeout 600 # 600s 无输出才判卡死（默认 300s）
python tools/fasttest/run.py --no-timeout       # 关掉卡死看门狗（串行全量可这样跑）
```

其余参数**原样透传给 pytest**：

```powershell
python tools/fasttest/run.py -x --tb=short
python tools/fasttest/run.py -k "golden or scan" --workers 4
python tools/fasttest/run.py tests/test_scan_golden.py -v
```

> 带文件 / nodeid 的调用会被当作"定向运行"：默认只用最多 4 个 worker，
> 且**不更新**耗时权重（避免把局部耗时学成全量）。真正验收请跑无参数的全量。
>
> `--retry-failed` 读取 `.pip-tmp/fasttest/last_full.json` 里的失败 nodeid，
> 只复跑这些用例（同样按"定向运行"处理）：没有纯全量记录时退出 `2`，
> 上一轮没有失败时退出 `0`，**不会偷偷改成跑全量**。修完后仍要再用无参数
> 全量验收一次。别用 pytest 的 `--lf`：每轮 basetemp 都是全新的，
> lastfailed 缓存不跨轮，`--lf` 实际会重跑全量。

并发默认 `min(12, CPU 核数 - 1)`；可用 `--workers N` 或环境变量
`FASTTEST_WORKERS` 覆盖。不要用 `-n 0`。

### 输出与现场

```
[fasttest] RESULT: PASS
  用例   6764   通过 6761   失败 0   错误 0   跳过 2   预期失败 1
  用时   6m00s（12 进程）
  参考   历史串行约 21m00s，本轮约 3.5x
  日志   .pip-tmp/fasttest/runs/20261025-173000-1234/output.log
  明细   .pip-tmp/fasttest/runs/20261025-173000-1234/junit.xml
```

- `summary.json`：同一目录下的机器可读结论（耗时、计数、失败 nodeid、
  `failed_details` 一句话原因、`failed_by_file` 分布）。
- 失败时 `RESULT` 会直接打印每个 nodeid 的一句话原因，不需要先翻几万行日志。
- `.pip-tmp/fasttest/last.json`：最近一轮（含定向运行），方便别的工具/agent 读取。
- `.pip-tmp/fasttest/last_full.json`：上一轮**纯全量**，用来算"新增失败 /
  上轮失败已消失"；定向运行不会覆盖它。
- 成功时默认把 basetemp rename 到 `.fasttest-trash/` 后**后台**删（省磁盘，
  不拖退出）；失败/超时保留 `tmp/` 现场，下一轮开跑前再 rename 后后台删。
  只留 junit/log/summary，否则 `test_disk_hygiene` 要递归扫几千个旧目录。
- 只保留最近 3 轮 run 目录。超过 7 天没动的 `.pip-tmp` 旧临时目录会在开跑前
  改名到 `.fasttest-trash/`（`--clean-temp` 把阈值降到 24h），这是
  `test_disk_hygiene` 从 391s 降到秒级的关键。
- `watch/*.json` 是各 worker 的"当前在跑"标记，卡死时 `RESULT` 会打印；
  正常结束后会清掉，worker 崩溃时残留文件就是线索。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 全绿 |
| 1 | 有失败 / 错误 |
| 2 | 用法错、缺依赖、启动失败 |
| 3 | 另一轮在跑且 `--no-wait` / 排队超时 |
| 5 | 没收集到用例 |
| 124 | 看门狗判定卡死（默认连续 300s 无输出），已结束整棵 pytest 树 |
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

**Q：上一轮有失败，只想复跑那几条？**
`python tools/fasttest/run.py --retry-failed`。它只读 `last_full.json`
（纯全量）的 `failed_ids`，不会拿 `-k` / 单文件的最后一轮来凑数；没有记录
退出 2、没有失败退出 0。复跑全绿也只说明"那几条现在过了"，验收仍要无参数
全量。

**Q：一直停在 98%，WorkBuddy 显示"读取任务输出"卡 20 分钟？**
2026-10-26 抓到的真实现场：`test_ui_qt.py::TestInsertDeleteCard::test_delete_removes_the_current_card`
的删卡三选一、以及块头级联确认，
弹的是**真实 `QMessageBox`**。测试只 patch 了旧的 `QMessageBox.question`，
而新代码 `_ask_delete_mode` 改用 `box.exec()`，于是那个 worker 永远等用户
点按钮；其余 11 个 worker 早已跑完，整棵进程树 0 CPU，看起来就是"卡死"。
现在测试同时 patch `_ask_delete_mode` 和 `QMessageBox.question`；fasttest
再加 300s 看门狗：真再遇到，5 分钟内退出 124，并打印卡住时正在跑的 nodeid。

看到 124 时：复制 `?` 后面那条 nodeid，先
`python tools/fasttest/run.py --serial <nodeid>` 复跑；能秒过就说明是
测试里的真实模态框/等待，去补 patch；单跑也卡就 `py-spy dump --pid <pid>`
看栈（本次就是这样定位到 `window_card.py` 的 `box.exec()`）。

**Q：启动时出现一串 `Windows fatal exception: code 0x8007000e`？**
这是 xdist worker 启动时 `platform.uname()` 走 WMI 的偶发异常（2026-10-26
21:46 那轮出现 4 次），不是测试失败；看心跳是否继续、最终 `RESULT` 是什么。
若长时间停在 `bringing up nodes...` 且心跳也不推进，按 124 的流程排查。

**Q：心跳里的"进度 ?"是什么？**
xdist 还在 `bringing up nodes...` / collection，没打出第一行百分比；
不是卡住。看同一行的"最近输出 Xs 前"和"在跑 ..."即可。

**Q：看门狗会不会把慢用例误杀？**
默认 300s 指"**完全没有输出**"，不是单条用例总时长；历史最慢文件约
170s，且跑的过程中会持续推进。确实有超过 300s 静默的合法场景时，用
`--idle-timeout 600` 或 `--no-timeout`。

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
- `--retry-failed` 只是复跑失败集合，不是全量验收；验收仍用无参数命令；
- 不在测试进程内做缓存（不会"上一轮绿了这轮就不跑"）；
- 不碰 `.rpy` / Excel / 用户数据；只写 `.pip-tmp/fasttest/`
  和待删的 `.fasttest-trash/`。

### 为什么不用 `pytest -n auto` 就完事

`-n auto` 解决不了：固定 basetemp 的并发互删、重文件排在队尾的长尾、
两个 agent 同时跑、失败后的单行结论。fasttest 是这四件事的薄封装，
真正跑用例的仍是 pytest 本身。

---

## 实测（2026-10-25 / 2026-10-26，本机 14 核 / 32G）

| 跑法 | 墙钟 |
|---|---|
| 串行 `python -m pytest`（README 历史记录） | 约 21 分钟 |
| 12 进程、普通收集顺序（改造前） | 7m24s |
| **fasttest（重文件优先 + 清理旧 `.pip-tmp`）** | **1m58s ~ 6m00s**（同一台 14 核机器，负载不同；2026-10-26 三轮 4m36s / 5m02s / 6m00s） |
| 最长单项 | `test_scan_assets_result_matches_the_golden` 19~39s |

折算 **6.3x ~ 10.7x**（机器上同时开着别的程序，负载不同）。改造前那轮里
`test_disk_hygiene` 单项 **391s** —— 它递归数 `.pip-tmp` 下 20 万个历史临时
文件，成了整轮的关键路径。清掉旧目录 + 把它固定到第一个跑之后，它回到
**8~11s**；再往后由本工具自己的保留策略维持，不会再涨回去。

### 2026-10-26：WorkBuddy 卡 20 分钟，查出两个独立原因

| 现象 | 根因 | 处理 |
|---|---|---|
| 停在 98% 二十多分钟，0 CPU | `test_ui_qt.py::TestInsertDeleteCard::test_delete_removes_the_current_card` 的删卡三选一用 `QMessageBox.exec()` 真弹模态；测试只 patch 了旧的 `question()`，worker 永远等人点按钮 | 测试同时 patch `_ask_delete_mode` + `QMessageBox.question`；fasttest 加 300s 看门狗，124 时点名 active nodeid |
| 开跑前先卡 5 分钟，没有任何输出 | 启动时同步 `rmdir` 上一轮 1.5 万个文件的 basetemp（积压多时更久） | 旧 tmp 同盘 rename 到 `.fasttest-trash/`，交给脱离的 `cmd rmdir` 后台删；启动实测 **~1s** |

同一天的实测补充（6759 条；机器上始终有 WorkBuddy / 其他 agent 在跑）：

- 故意挂起的自测用例 `time.sleep(30)`：`--idle-timeout 3` 在 **4.2s** 内退出
  `124`，并打印 `test_deliberate_hang`；
- 20:04 那轮（旧参数，xdist 默认重排）：**PASS / 7m14s**；金标全被排到
  99%，最后 202s 无输出但正常跑完（未误杀）；
- 20:11 那轮（加 `--no-loadscope-reorder`）：**PASS / 4m36s**；scan/patch/
  hydrate 三条真机金标**一起从第 1 分钟开始跑**，不再堆在 99% 等；
- 开跑前旧 tmp 改名到 `.fasttest-trash/`：本轮从命令启动到 run 目录建好
  实测 **~1s**（此前同步删 1.5 万文件要 40s~5min）；
- 21:46 定稿轮（新增心跳/看门狗/`--retry-failed`/失败一句话摘要后）：
  **PASS / 5m02s / 6764 条**（6761 passed, 2 skipped, 1 xfailed）。启动时
  4 个 xdist worker 的 `platform.uname()` 撞上 Windows WMI 的
  `0x8007000e`（机器忙时的偶发异常），日志里会看到几段
  `Windows fatal exception`，但心跳和用例继续推进，最终判据不受影响；
- 21:47 提交前复跑：**PASS / 6m00s / 6764 条**（无 WMI 噪声）。同一台机器
  不同负载下 4m36s / 5m02s / 6m00s 都出现过，判断回归看失败集合，不要只看
  单轮墙钟。

> 结论：**心跳 + 124 只解决"永远不出结果"，不改变判据**。这轮又快了
> 2m38s 的关键是 `--no-loadscope-reorder`（pytest-xdist 默认按"文件里
> 用例数"重排，会把只有 2 条的 scan/hydrate/patch 金标踢到最后）。
> 想再压时间，下一步是减少金标重复计算或按磁盘 I/O 串行化金标，而不是
> 放宽看门狗。

