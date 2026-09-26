# 项目长期记忆：RenPy 剧情树可视化编辑器

## 交付目标（项目所有者设定）

小团队（约 50 人：文案 / 策划 / 美术 / 音乐 / 程序）用的 Ren'Py 演出编排工具：

- 用户**不写代码**，对着 Excel 就能精确到句子级配置演出（图片、音效、角色、位置变换）
- 表格之间相互引用联动
- 程序那边敲代码要能自动反映到表格 —— **Excel ↔ .rpy 双向联通**
- 需要支持自定义 transform 变换
- 要考虑多语言复用（未来可能迁 Unity），保持工业化能力

## 架构约定

- 源码是**扁平导入**，不是包结构；`core/` 是唯一子包且不反向依赖项目模块
- 运行方式：项目根目录执行 `python -m pytest`（500 用例 / ~8 秒）
- `pytest.ini` 把 SyntaxWarning 升级为失败

## 关键不变量（改动前先想清楚）

- `core.statement._compute_content_hash` 的输出必须与
  `renpy_parser.TranslationUnitGenerator` 逐字节一致（menu_option 用
  前缀 `'menu'` 是历史兼容，不是笔误）
- `core/uid.py` 是 uid 拼接的唯一真源，禁止各处自己拼 f-string
- `GraphBuilder.build(clear_graph=True)` 会删除不在本次 AST 里的 label 节点；
  部分文件重解必须传 `clear_graph=False`

## 已修坑（别再踩回去）

- `analysis.analyze_reachability` 必须有 widening，否则带循环的图不终止
- 可达性不能从区间是否为空反推（无变量图会全误判），要用独立 reached 集合
- `_handle_table_changed` 必须**先 diff 再同步脚本角色定义**，否则 Excel 改名被掩盖
- 音频 ID 前缀不能重复拼接
- Excel 路径类 key 统一用 posix 分隔符（团队跨平台）

## 待办优先级建议

1. image tag ↔ 资产 ID 映射（当前「未使用资产」100% 误报）
2. 支持现代 Ren'Py tl 翻译格式（当前多语言链路不可用）
3. 收敛重复的补全器 / 面板模型实现
