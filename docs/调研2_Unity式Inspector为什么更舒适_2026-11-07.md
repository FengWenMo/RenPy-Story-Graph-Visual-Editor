# 调研 2：Unity 的 Inspector 为什么"看着舒服"，我们为什么"折磨"

> 用户 2026-11-07：
>
> > 为什么在 Unity 中，tools 的挂载的那种模块，包括脚本显示出来的那些变量模块，
> > 看得会令人更加舒适……？它的 UI 是如何排布的……？最好详细到代码层级的实现……
> > 而我们这的却这么令人折磨……？你觉得它们这几个模式哪个自定义模式最符合
> > 编剧+文案的操作理念……？而不是程序员的操作理念……？

**这一轮同样没有改产品代码**（上一轮撤掉代码格右键是唯一改动）。下面全是机制拆解与判断。

---

## 0. 一句话答案

Unity 的 Inspector 之所以"舒服"，不是配色，是**它把"排布"变成了一条不可违反的规则**：

> **一列，一行一条控件；标签右对齐、`fieldWidth` 全局统一；层级只用缩进表示；
> 组标题 / 留白 / 说明都是声明出来的装饰；改了就写回，没有"保存"。**

我们的卡片恰好在这五条上**每一条都不一样**：横竖混排、标签宽度不齐、用折叠代替缩进、
留白靠手写 spacing、并且有「保存这张卡片」。

---

## 1. Unity 的排布机制（代码级）

### 1.1 一行一条 —— 这是**硬约束**，不是风格

IMGUI 时代 Inspector 里根本没有"流式布局"给你用。Unity 论坛里一位的回答说得最直白：

> "the inspector isn't meant to be used with the layout system in the first place…
> **By default there's only one control per line.**"

所以 Inspector 的"每行一个字段"不是设计选择，是**架构事实**。每个字段拿到的是一块
`Rect`（`PropertyDrawer.OnGUI(Rect position, SerializedProperty property, GUIContent label)`），
你在那块矩形里画什么都可以，但**不能影响下一行的位置**。

### 1.2 层级 = 缩进

```csharp
EditorGUI.indentLevel++;
    EditorGUILayout.LabelField("Position:", ...);
    EditorGUILayout.LabelField("Rotation:", ...);
    EditorGUI.indentLevel++;          // 更深一层
        EditorGUILayout.LabelField("X:", ...);
    EditorGUI.indentLevel--;
EditorGUI.indentLevel--;
```

官方说明：`indentLevel` 是"字段标签的缩进级数"，**增大就往右移**。而且 `PropertyDrawer`
里 Unity **会帮你在画子字段前 +1、画完 -1**；你自己做多列时要显式归零再恢复：

```csharp
var indent = EditorGUI.indentLevel;
EditorGUI.indentLevel = 0;        // 横排时不受父级缩进影响
...
EditorGUI.indentLevel = indent;   // 一定要还回去
```

**关键点**：层级是**画出来的深度**，不是"折叠起来的东西"。你永远看得见自己在第几层。

### 1.3 标签 / 值的宽度是全局常量

`EditorGUIUtility.labelWidth`（以及每行里的 `fieldWidth`）决定"左边标签占多宽"。同一份
Inspector 里所有行共用它 —— 于是**每一行长得一模一样**：标签右对齐到同一条竖线，
控件从同一条竖线开始。论坛原话：

> "there's also the fieldWidth setting which controls **the fix spacing between the label
> on the left and the value on the right**"

这一条是"整齐感"的物理来源。我们在卡片里做的是"每个格子自己 `setMinimumWidth(72)`"，
有的设了有的没设 —— **竖线不存在**。

### 1.4 组标题 / 留白 / 说明也是**声明**，而且是"装饰器"

`[Header]`、`[Space]` 都不是属性绘制器，是 **DecoratorDrawer**：

> "DecoratorDrawer 类似于 PropertyDrawer，但它**不绘制属性**，而是根据其对应的
> PropertyAttribute 绘制**装饰元素**。Unity 用内置的 DecoratorDrawer 处理
> `SpaceAttribute` 和 `HeaderAttribute`。"

```csharp
[Header("Health Settings")]   // 一个标题（装饰器画的）
public int health = 0;
public int maxHealth = 100;

[Space(10)]                   // 10 像素留白（也是装饰器）
public int shield = 0;
```

`PropertyAttribute.order` 还能控制多个装饰器的先后。**"呼吸感"是声明出来的 10 像素**，
不是谁去调 CSS。

### 1.5 绑定：一行代码 = 一个字段的整套 UI

UI Toolkit 的做法最狠：

```csharp
public override VisualElement CreateInspectorGUI()
{
    return new PropertyField(serializedObject.FindProperty("coreTemperature"));
}
```

**一行**，你得到：标签、合适的控件、类型转换、多选编辑、prefab override 标记、Undo 支持。
写回靠 `SerializedObject.ApplyModifiedProperties()`（UI Toolkit 自动绑定替你调）。

而我们每加一个字段，要写：控件 + 布局 + `_widgets[name] = w` + `_set_value` 分支 +
`collect_fields` 兜底 + 脏标记 + 撤销快照。**同一件事的 7 个落点。**

### 1.6 没有"保存"

Inspector 改值 = 立刻写进场景/资产（Undo 栈兜底）。**没有提交步骤，也就没有"我改了没存"的焦虑。**

---

## 2. 为什么这五条让人"舒服"（感知层）

| 机制 | 眼睛/脑子少做了什么 |
|---|---|
| 一列、一行一条 | **只有垂直扫描**，不用在分区之间来回跳 |
| 标签宽度统一 | 每一行**同构**，读起来像名单，不是像拼图 |
| 缩进代替折叠 | 层级**可见**；折叠=看不见=不记得它存在 |
| 留白/标题是声明 | 节奏稳定；不会出现"这块挤、那块空" |
| 即时写回 | 不用记住"我改过什么还没提交" |
| 一行一个 `PropertyField` | 心里模型简单：**一个字段 = 一行**，没有例外 |

一句话：**它把"读一个对象"变成"读一列"**。列是线性的，人是线性读者。

---

## 3. 我们为什么"折磨"（逐条对到代码）

| Unity | 我们（具体位置） |
|---|---|
| 一列 | **横竖混排**：语句参数 `_spec_form`(QVBox) / 台词区 `QHBoxLayout`（说话人+台词）/ 立绘 `_grid`(QGridLayout) / 备注与条件 `QHBoxLayout` / 高级区 label+控件横排 |
| `labelWidth` 全局 | **每格自己定**：`_spec_cell_label` 里 `setMinimumWidth(72)`，备注区的 `cap` 没设，高级区 `lab.setMinimumWidth(72)` —— 三处口径不同 |
| 缩进表层级 | **折叠**：`_adv_host.setVisible(False)`（高级区默认收起）。用户原话"这么整理太乱了"里有这一份 |
| 装饰器统一留白 | `T.SPACE['xs'] / ['sm'] / ['md']` **三种混用**，同一屏里间距不一致 |
| 一个滚动区 | 多个（记忆里"屏幕上一堆滚动条是本仓历史病"） |
| 无保存按钮 | **「保存这张卡片」** → 提交式交互，还带"未保存"红点 |
| 一行 = 一个字段 | 一拍是**复合**的：主句 + 顺带 show + 音乐 + 备注 + 条件 + 未知行。字段之间没有统一的行模型 |

**最要命的是最后一条**：Unity 的对象是"一组平级属性"，我们的拍是"一句主句 + 一堆附带"。
照抄 Unity 会把"主次"抹平；不照抄又回到混排。**这正是我们反复改不好的根因。**

---

## 4. 哪个模式最符合**编剧 + 文案**？

按"编剧是在读剧本、不是配参数"这个前提排序：

| 排名 | 模式 | 为什么 |
|---|---|---|
| **1** | **Unity Inspector** | 一列、一行一条、组标题、内联说明、即时写回 —— **就是"读一列"**，和读剧本同一种眼动 |
| 2 | Godot `@export_group` | 机制几乎一样（注解→控件、组→标题），但 Inspector 也允许折叠，且属性面板偏"工程参数" |
| 3 | RJSF 表单 | "一问一答"，像填问卷；适合设置，不适合"连着读十句台词" |
| 4 | VS Code 设置页 | **搜索优先**（顶部一个大搜索框）。它是"找某个配置"，不是"顺着读" |

### 为什么是 Unity 而不是 Godot

差别只有一处，但对编剧关键：**Unity 的层级用缩进、默认全部展开**；
Godot / 我们习惯用**折叠**。编剧的心智是"我刚才写的那句台词旁边还有什么"，
折叠一收，他就得靠记忆。

### 但不能照抄的三点（照抄会坏）

1. **一拍是复合的**：主句 + 附带。所以保留"**这一拍是什么**"的标题（我们已经做了），
   下面按 Unity 的一列往下排，**附带的东西缩进一级**（而不是折叠）。
2. **立绘 chip 网格是例外**：视觉素材必须"看得见"，横排是对的 —— 但**一行只放一维**
   （这一点已经做对，`立绘卡一维一格`）。
3. **我们需要批量保存**（Excel / 表）。但**卡片上不该有提交按钮** ——
   应该"改了就暂存 + 标题上一个小状态点"，离开时统一提交。这是行为改动，要你拍板。

---

## 5. 照 Unity 改的清单（还没做，等你点头）

1. **卡片右栏统一成一个竖列**：台词区、备注、条件改成"标签在上 / 控件在下"的竖排（现在横排的三处）。
2. **一个全局 `LABEL_WIDTH`**（对齐 `EditorGUIUtility.labelWidth`），所有标签右对齐到同一条竖线。
3. **折叠 → 缩进**：高级区默认展开、缩进一级 + 一行小标题（像 `[Header]`）；真要收也留"缩进 + 灰字"。
4. **留白收敛成一张表**（`SPACE_ROW / SPACE_GROUP / SPACE_SECTION`），不再在调用处随便挑。
5. **状态点代替保存按钮**：标题旁一个 `●`（未保存）/"已自动暂存"；保存走已有的批量入口。
6. **一行一个字段**作为硬规则写进守卫（像 `test_every_panel_method_...`）：
   新增字段必须走 `_row(label, widget)` 这个唯一构造口，不许自己拼 `QHBoxLayout`。

> 第 6 条是关键：**没有守卫的"约定"三天就会破**。本仓已有"一个落盘点一个登记"的先例。

---

## 6. 参考

| 内容 | 链接 |
|---|---|
| `EditorGUI.indentLevel`（缩进级数、官方示例） | <https://docs.unity3d.com/kr/2018.2/ScriptReference/EditorGUI-indentLevel.html> |
| "Inspector 不用布局系统 / 一行一个控件 / fieldWidth"（论坛实证） | <https://forums.unity3d.com/t/why-is-there-so-much-empty-space-with-beginhorizontal/223401/1> |
| `DecoratorDrawer`（Header / Space 是装饰器） | <https://docs.unity3d.org.cn/ScriptReference/DecoratorDrawer.html> |
| `HeaderAttribute` / `SpaceAttribute` 官方说明 | <https://docs.unity3d.com/2020.2/Documentation/ScriptReference/HeaderAttribute.html> |
| `PropertyField` + `ApplyModifiedProperties`（一行 = 一个字段） | <https://docs.unity3d.com/6000.0/Documentation/Manual/UIE-bind-to-custom-data-type.html> |

Unity / Godot 文档里的 Inspector 截图是 JS 渲染的，三次抓取都拿不到原图；
本报告的对照图是按它们**代码里的布局规则**重画的，不是截图。
