"""
立绘卡的**提示语**（从 `sprite_grid` 拆出来，2026-11-03）
======================================================

为什么单开一个模块：`sprite_grid.py` 原本 1498 行（技术债阈值 1500），
这轮加"声明优先 / 写法纠正"两层提示之后跨过了阈值 —— 而"这句话该怎么说"
本来就和"控件怎么摆"是两件事。判据一条没动：
`tests/test_spec_self_consistency.py` 与 `tests/test_sprite_tag_ownership.py`。

判定顺序（从硬到软）：

  ⓪ **项目自己声明过**（`layeredimage` / `image`）→ 闭嘴；
     属性名写错才说"项目里声明的是 base/angry/…"
  ①′ 只差**写法**（大小写 / 下划线）→ 说清正确写法
  ①  角色表认领了 → 不吭声
  ②  有真立绘素材、角色表没认领 → 说清有几张、指向角色表
  ③  都没有 → 老文案（去资源映射认领）

⚠ **引擎 lint 的 `is not an image` 不能压在这一层前面**（2026-11-05 用户
纠正）：`show lan aa happy` 里 `aa` 是项目变量里的服装代号（`CC_OUTFIT_CYCLE`
的"普通"），**不对应任何 png**；lint 查的是 image 注册表，对 layeredimage
的属性组合没有意义 —— 真机 949 条这种"错"，游戏照跑。引擎只用来判
**项目从没声明过的 tag**（那时它的否定才是真否定）。

⚠ **判不了就闭嘴**：`symbols` 为 None / 一张表都没有时一律返回空串 ——
假警报比不提示更糟（用户会去改本来没问题的东西）。
"""

from __future__ import annotations

from storygraph.core.safeguard import swallow


#: `show` 里在这些词之后的东西**不是图像名**：`at` 变换 / `with` 转场 /
#: `behind` 层 / `as` 别名 / `onlayer` / `zorder`（2026-11-04 地狱测试）。
#: 卡片本来就把它们分列存，但 `known_tag_warning` 是公开函数 —— 有人把
#: 整条 show 尾巴递进来时不许把关键字当成属性去报错。
_SHOW_CLAUSE_WORDS = frozenset({'at', 'with', 'behind', 'as', 'onlayer',
                                'zorder'})


def _image_part_of_spec(spec) -> str:
    """
    `spec` → **图像名那一截**（`lan base at hell_pose` → `lan base`）。

    判不了的形态（`expression holo(...)`、带引号/括号的表达式）→ 空串：
    "判不了就闭嘴"（假警报比不提示更糟）。图像名里不可能有引号/括号，
    所以这个判据不会误伤真名字。
    """
    text = str(spec or '').strip()
    if text.endswith(':'):                     # ATL 块头 `show cg cg1@half:`
        text = text[:-1].rstrip()
    if not text:
        return ''
    # `show` 的这几种形态**不是图像名**：`expression` 是任意 displayable
    # 表达式，`screen` / `layer` 后面跟的是屏幕名 / 图层名。它们出现在
    # 立绘格里本来就说明这一格不该按"角色 tag"去判 —— 判不了就闭嘴。
    if text.startswith(('expression ', 'screen ', 'layer ')):
        return ''
    if any(ch in text for ch in '()"\''):
        return ''
    parts = []
    for word in text.split():
        if word in _SHOW_CLAUSE_WORDS:
            break
        parts.append(word)
    return ' '.join(parts)


def known_tag_warning(spec, symbols) -> str:
    """
    `spec` 的首词**不是已知角色 tag** 时给的一句话（空串 = 没问题 / 判不了）。

    为什么判据是"角色 tag"而不是随便一个 image tag：这一格是**立绘**。
    选了背景 / CG / 界面图写进 `show` 里，引擎不会报错但演出是错的
    （用户 2026-10-30："不能直接放立绘图，直接放角色那个头像图就能动"
    —— 反方向同样成立：立绘格里放背景图是同一个错误）。

    ⚠ **判不了就闭嘴**（与缩略图"没有"同一条纪律）：项目没打开时
    `symbols` 是 None；项目有图但没有角色表时 `Symbols.is_actor_tag`
    会按"非背景/CG/UI"粗判 —— 这两种情况都不许报"未知角色"（假警报
    比不提示更糟，用户会去改本来没问题的东西）。

    2026-11-03 分了三层（用户实测两轮："qing base 明明是我的立绘，
    预览里有图，却说不是已知的角色立绘标签，让我去资源映射"）：

      ⓪ **项目脚本自己声明过这个 tag**（`layeredimage qing:` / `image qing …`）
         → **闭嘴**；属性名写错时只说"没有这个属性，项目里声明的是 base/angry/…"。
         这一层必须排最前：`definitions.rpy` 里就写着
         `layeredimage qing:` + `attribute base default: "normal"`，
         `show qing base` 在引擎里**完全合法**。判成"不是立绘"说明工具
         没读项目自己的声明，不是用户写错了。
      ① 角色表认领了 → 不吭声；
      ② **有真立绘素材、只是角色表没认领** → 说清"它有几张、都是什么属性"
         并指向角色表的「立绘 tag」列（不是资源映射 —— 素材本来就在）；
      ③ 都没有 → 老文案（去资源映射认领）。
    """
    spec = _image_part_of_spec(spec)
    if not spec or symbols is None:
        return ''
    parts = spec.split()
    # ⚠ `@` 后面是 **AT 变换**（`show qing base@left`、`show cg cg1@half:`），
    # 不是属性名的一部分（2026-11-03 地狱测试 I10 抓到：`base@left` 被当成
    # 一个叫 `base@left` 的属性，于是提示"没有这个属性"—— 假警报）。
    tag = parts[0].split('@')[0].strip()
    #: **全部**属性词（不是只取第二个）—— 2026-11-04 真机：
    #: `lan aa happy` 报了、`lan happy aa` 没报，同一个东西两种结论。
    wanted = [p.split('@')[0].strip() for p in parts[1:]]
    wanted = [w for w in wanted if w]
    if not tag:
        return ''
    try:
        if not (symbols.has('image') or symbols.has('character')):
            return ''                              # 无从判定：不猜
        # ① **项目自己声明过的**（layeredimage / image 语句）先判。
        #    为什么引擎不能压在这一层前面（2026-11-05 用户纠正）：
        #    `show lan aa happy` 里 `aa` 是项目**变量**里的服装代号
        #    （`CC_OUTFIT_CYCLE` 的"普通"），不对应任何 png；lint 的
        #    "is not an image" 查的是 image 注册表，对 layeredimage 的
        #    属性组合没有意义 —— 真机 949 条这种"错"，游戏照跑。
        declared = _declared_attr_hint(tag, wanted, symbols)
        if declared is not None:
            return declared
        # ⓪ **引擎的裁决**：只用于项目**没声明过**的 tag —— 此时 lint 的
        #    否定才是真否定（`image` 注册表里根本没有这个 tag）。
        verdict = _engine_verdict(spec, tag, wanted, symbols)
        if verdict:
            return verdict
        # ①′ **只差写法**（大小写 / 下划线）—— 引擎确实不认，但要说清正确写法，
        # 而不是笼统一句"不是立绘"（地狱测试 I10 抓到：粘贴进卡片的名字
        # 常带 `qing_base` 这种下划线写法，`show` 里必须用空格）。
        spell = _spelling_hint(tag, symbols)
        if spell:
            return spell
        if symbols.is_actor_tag(tag):
            return ''
        # ② **有真立绘素材、只是角色表没认领**
        orphan = _orphan_sprite_hint(tag, wanted, symbols)
        if orphan:
            return orphan
    except Exception as e:                         # noqa: BLE001 - 符号表怪就当判不了
        swallow('ui_qt.sprite_grid.known_tag_warning', e)
        return ''
    # ③ 素材也没有 → 老文案（去资源映射认领）
    return (f'「{tag}」不是已知的角色立绘标签 —— 这一格要的是立绘；'
            f'如果它其实属于某个角色，去「资源映射」把它认成角色/立绘。')


def _engine_verdict(spec: str, tag: str, wanted, symbols) -> str:
    """
    引擎说这个 image 名**不存在**时的那句话（空串 = 引擎没否定它）。

    ⚠ 只用于**项目没声明过这个 tag** 的情况（2026-11-05 用户纠正）：

    引擎跑 lint 的原话是 `<file>:<line> 'lan aa happy' is not an image.` ——
    它查的是 **image 注册表**（`image` / `layeredimage` 注册的名字），
    而 `layeredimage` 的属性组合**根本不注册成独立图片名**；
    运行时 `LayeredImage._duplicate` 对认不出的属性**只画一行调试字、不报错**
    （真机 `sr_general` 949 条这种"错"，游戏照跑，`aa` 是 `CC_OUTFIT_CYCLE`
    里的服装代号、不对应任何 png）。所以对**已声明**的 tag，这一层会把
    项目合法的写法判死 —— 它永远排在 `_declared_attr_hint` 之后。

    对**没声明过**的 tag（`bg_1` 这种），lint 的否定才是真否定：注册表里
    确实没有这个 tag。

    ⚠ 没有缓存（没跑过 / 跑失败）→ `symbols.engine_bad_keys()` 是空集，
    这一层**不吭声**（**不是**"引擎说没问题"）。
    """
    try:
        from storygraph.assets.engine_images import spec_key
        bad = symbols.engine_bad_keys()
    except Exception as e:                     # noqa: BLE001 - 没有这一路就跳过
        swallow('ui_qt.sprite_hints.engine_verdict', e)
        return ''
    if not bad or spec_key(spec) not in bad:
        return ''
    # ⚠ **只在裁决新鲜时引用它**（2026-11-05 L1-3）：`.rpy` 改过之后旧裁决会
    # 说反话 —— 那比"没有裁决"更糟。`stale` / `unknown` → 这一层闭嘴，
    # 交给下面几层（它们看的是**当前**的声明 / 表 / 定义位）。
    try:
        state = symbols.engine_verdict_state()
    except Exception:                          # noqa: BLE001 - 老 Symbols 没有这一路
        state = 'fresh'                        # 没有这一层 = 不因它降级
    if state != 'fresh':
        return ''
    # ⚠⚠ **tag 有声明时不采信 lint 的否定**（2026-11-05 第三轮纠正，
    # 引擎源码 `renpy/common/00layeredimage_ren.py:734-740`）：
    #
    #     if unknown and args.lint:          # 只在 lint 时把未知属性留着
    #         args.args = tuple(unknown)
    #     if unknown and config.developer:   # 运行时只打一句 developer 警告
    #         message = [..., "unknown attributes:", ...]
    #     return rv                          # ← **照常渲染，不抛**
    #
    # 所以 `show lan aa happy`（`aa` 没在 `layeredimage lan` 里声明）**能跑**：
    # 未知属性被忽略，画面照常出。lint 那句 `is not an image` 是**静态局限**
    # —— 拿它当"最硬裁决"会把能跑的东西判成错的（真机 `aa` 就是这么被误报的）。
    # 只有 tag **压根没声明**时（`bg_1` 那种），lint 的否定才是真否定。
    try:
        if symbols.declared_attrs_of(tag):
            return ''
    except Exception as e:                     # noqa: BLE001 - 判不了就退回老路
        # 老 Symbols 没有这一路（测试替身 / 半初始化的会话）：退回"按用法判"。
        # ⚠ 不能只 `pass` —— 静默吞异常出事时没有任何痕迹（`test_code_health`
        #   当场红）。
        swallow('ui_qt.sprite_hints.engine_verdict.declared', e)
    shown = '」「'.join(sorted(set(wanted))) if wanted else ''
    label = f'{tag} {shown}' if shown else tag
    return (f'**引擎说**「{label}」不是一个图像（lint 原话：'
            f'`is not an image`）—— 这个名字在 `image` / `layeredimage` 里'
            f'没有定义。要么补声明，要么换一个已有的。')


def _knows_tag(tag: str, symbols) -> bool:
    """项目认识这个 tag 吗（声明 / 素材 / 角色表任一路）。判不了 = False。"""
    tag = str(tag or '').strip()
    if not tag:
        return False
    try:
        if symbols.declared_attrs_of(tag) or symbols.attrs_of_tag(tag) \
                or symbols.has_sprites(tag):
            return True
        return bool(symbols.is_actor_tag(tag))
    except Exception as e:                         # noqa: BLE001
        swallow('ui_qt.sprite_grid._knows_tag', e)
        return False


def _spelling_hint(tag: str, symbols) -> str:
    """
    **只差写法**（大小写 / 下划线）时给的那句话（空串 = 不是这种情况）。

    为什么单独一条（2026-11-03，地狱测试 I10）：这两种写法在引擎里确实
    不合法（Ren'Py 的 image name 区分大小写、用空格分隔），但用户是**粘贴**
    进来的 —— 告诉他"不是立绘、去资源映射"等于让他去改一个根本没错的地方。
    说清"应该写成 `qing base`"才是他要的。
    """
    tag = str(tag or '').strip()
    if not tag:
        return ''
    # 下划线写法：`qing_base` → `qing base`
    if '_' in tag:
        head, _, rest = tag.partition('_')
        if head and rest and _knows_tag(head, symbols):
            return (f'「{tag}」应该写成「{head} {rest}」—— '
                    f'`show` 里的 image name 用**空格**分隔，不是下划线。')
    # 大小写：`Qing` → `qing`
    low = tag.lower()
    if low != tag and _knows_tag(low, symbols):
        return (f'「{tag}」的大小写不对 —— 项目里写的是「{low}」。'
                f'Ren\'Py 的 image name **区分大小写**。')
    return ''


def _declared_attr_hint(tag: str, attrs, symbols):
    """
    项目脚本声明过 `tag` 时的那句话：**属性都对 = 空串（闭嘴）**，
    有认不出的 = 一句"这几个没有"。

    ⚠⚠ **必须看全部属性词，不能只看第二个**（2026-11-04 真机事故）：

        lan aa happy   → 提示「lan 没有「aa」这个属性」   ← 报了
        lan happy aa   → 什么都不说                      ← 没报

    同一个东西、只是顺序不同，两种结论。根因是从前只把 `parts[1]` 当
    "那个属性"拿去比 —— `aa` 排在第二位就撞上，排在第三位就漏掉。
    判据：`tests/test_sprite_hint_detector.py::TestOrderDoesNotMatter`。

    **什么算"认识"**（2026-11-05 用户："`aa` 是服装的定义！它根本不对应
    任何 image"）：

      · `layeredimage` / `image` 声明的属性名；
      · 项目 `init python` / `define` 里**定义过**的标识符字面量
        （通用扫描，不认识具体结构 —— `CC_OUTFIT_CYCLE` 的值 `aa` 照样收；
        不从"剧本里用过"取证，那是循环论证）；
      · **有同名图片**：按 `layeredimage` 自己的 `image_format` 展开后文件
        真在（声明漏写 `attribute` 时，图仍是证据）。

    认不出的词再给一句"运行时会忽略它、实际显示哪张图" —— 这是引擎的
    真实行为（`LayeredImage._duplicate` 对未知属性只画调试字，不报错）。

    返回 `None` = "项目没声明过这个 tag"，交给后面的层去判 ——
    用 `None` 而不是 `''`，因为"合法，什么都别说"与"不归我管"是两回事。
    """
    declared = list(symbols.declared_attrs_of(tag))
    if not declared:
        return None
    known = set(declared) | set(symbols.attrs_of_tag(tag))
    # 项目 `init python` / `define` 里**定义过**的代号词（`aa` 这种服装代号）。
    # ⚠ 只看定义位置，不看"剧本里用过" —— 后者会把写错的词也放过。
    try:
        known |= set(symbols.defined_words())
    except Exception as e:                         # noqa: BLE001 - 老 Symbols 没有这一路
        swallow('ui_qt.sprite_hints.defined_words', e)
    # "有图"也算证据：声明可能漏写 attribute，但图在（真机 `lan lose`）。
    for word in attrs or ():
        if word and word not in known:
            try:
                if symbols.token_image_path(tag, word):
                    known.add(word)
            except Exception as e:                 # noqa: BLE001 - 老 Symbols 没有这一路
                swallow('ui_qt.sprite_hints.token_image', e)
    # **排序 + 去重**：`「aa」「i11」` 与 `「i11」「aa」` 是同一件事，
    # 文案也得一样 —— 否则"同样的东西"仍然给出两种措辞（判据会红）。
    bad = sorted({a for a in (attrs or ()) if a and a not in known})
    if not bad:
        return ''                                  # 都对 → 不吭声
    shown = ' / '.join(declared[:8]) + (' …' if len(declared) > 8 else '')
    quoted = '」「'.join(bad)
    msg = (f'「{tag}」没有「{quoted}」这个属性 —— 项目里声明的是：{shown}。')
    fix = _near_miss_hint(bad, known)
    if fix:
        msg += fix
    preview = _resolved_preview(symbols, tag, attrs)
    if preview:
        msg += (f' 认不出的词运行时会被忽略，实际显示 `{preview}`'
                f'（引擎不报错，但演出不是你要的）。')
    return msg + '换一个，或者在剧本里补一条 attribute。'


def _near_miss_hint(bad, known) -> str:
    """
    认不出的词里，有没有只是**写法不同**的（`base_hand_down` vs `base_handdown`）。

    用户 2026-11-05（截图）：

    > 「为什么检测不到！明明是有的！！！」

    —— 有的，是拼法差一个下划线。Ren'Py 的 image attribute 名里 `_` 就是名字的
    一部分：多 / 少一个下划线在引擎里是**两个不同属性**（不报错，只是被忽略，
    于是画面不是作者要的那张）。所以这一层不是"再报一次错"，而是把
    **正确写法**说出来。

    判据只有"忽略大小写与 `_` / `-` 之后相等"（`app.beats.attr_word_key`），
    **不做**前缀 / 子串 / 编辑距离 —— 猜一个名字比说不知道坏得多
    （`happy` 与 `close_happy` 在这种判据下是两个词，不会被搅在一起）。
    """
    from storygraph.app.beats import attr_word_key

    pairs = []
    for word in bad or ():
        key = attr_word_key(word)
        if not key:
            continue
        for cand in sorted(known):
            if cand != word and attr_word_key(cand) == key:
                pairs.append((word, cand))
                break
    if not pairs:
        return ''
    shown = '；'.join(f'「{word}」其实写作「{cand}」' for word, cand in pairs)
    return (f'（**有**，只是写法不同：{shown} —— 图像属性名里 `_` 也是名字的一部分，'
            f'多一个下划线就是另一个属性。）')


def _resolved_preview(symbols, tag: str, attrs) -> str:
    """
    `show <tag> <attrs>` **实际会显示的第一张图**（项目相对路径；判不了 = ''）。

    走 `layeredimage` 的组条件 / 默认属性（`Symbols.resolved_paths_of`），
    不是按文件名猜 —— 用户要的是"找到对应的图"，这句就是证据。
    """
    try:
        paths = symbols.resolved_paths_of(tag, list(attrs or ()))
    except Exception as e:                         # noqa: BLE001 - 老 Symbols 没有这一路
        swallow('ui_qt.sprite_hints.resolved_preview', e)
        return ''
    return paths[0] if paths else ''


def _orphan_sprite_hint(tag: str, attrs_wanted, symbols) -> str:
    """
    `tag` **有立绘素材、但没有角色认领**时的那句话（空串 = 不是这种情况）。

    只说三件**可判定**的事，绝不猜"它属于哪个角色"（铁律：猜一个名字比说
    不知道坏得多 —— 用户自己知道「青」是哪个变量）：

      1. 它有哪几张（属性词，最多列 6 个）；
      2. `want`（`spec` 的第二个词）不在其中时，直接说"没有这个属性"
         —— 真机 `qing base` 就是这种：`qing` 有 normal/speechless/stare/
         thinking，唯独没有 `base`；
      3. 下一步是**角色表**的「立绘 tag」列，不是资源映射。
    """
    if not symbols.has_sprites(tag):
        return ''
    attrs = list(symbols.attrs_of_tag(tag))
    shown = ' / '.join(attrs[:6]) + (' …' if len(attrs) > 6 else '')
    count = len(attrs) or 1
    # ⚠ 同样要**看全部属性词**（见 `_declared_attr_hint` 的说明）
    bad = sorted({a for a in (attrs_wanted or ())
                  if a and attrs and a not in attrs})
    if bad:
        quoted = '」「'.join(bad)
        return (f'「{tag}」确实有立绘（{count} 张：{shown}），'
                f'但**没有「{quoted}」这个属性**。'
                f'挑一个已有的，或者把那张图放进 '
                f'`…/characters/{tag}/{bad[0]}.png` 再刷新资源表。')
    return (f'「{tag}」在资源表里有 {count} 张立绘（{shown}），'
            f'但**角色表里没有角色认领这个 tag** —— '
            f'去「角色表」给对应角色填上「立绘 tag = {tag}」，'
            f'这一格就会认出来。')
