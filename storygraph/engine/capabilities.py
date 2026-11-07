"""
引擎**能回答什么**——能力令牌（三态，不是 bool）
=============================================

> 2026-11-05（P0-1）。见 `docs/设计_引擎无关IR与退化链_2026-11-05.md` §3。

## 为什么是三态

| 值 | 意思 | UI |
|---|---|---|
| `True` | 这次**真采到了** | 正常 |
| `False` | adapter 声明引擎/版本**根本不支持** | "这个引擎没有这项" |
| `None` | 声明支持，但**这次失败**（探针异常 / dump 没产出） | "这次没采到" |

⚠ **`False` 与 `None` 在 UI 上必须分开**。把它们并成一个 bool 是这一层最
常见的塌方：用户看到"没有这项"会去改项目，而实际只是这次没跑成。

## 不要塞进这里的东西

**新鲜度不是能力**（两个轴）：

- capability = 引擎**能不能**回答；
- freshness = 这份回答**还算不算数**（工程改过没有）。

后者见 `decide.py` 的 `rung(..., freshness=...)`。

## 这一层不认识"值"

它只回答"这个令牌填成什么"。取值的代码在 `pipeline/engine_collect.py`
（那里才知道探针 / `--json-dump` 各产出了什么）。所以这里**没有任何
`storygraph.*` 的 import**。
"""

from __future__ import annotations

# ---- 令牌清单（字符串常量，避免拼错） --------------------------------------
#
# 为什么要常量而不是裸字符串：`capabilities['CAP_IMAGE_REGISTRY']` 拼错一个
# 字母不会报错，只会让那项永远是 None —— "神奇地少了点东西"，最难查。

#: `renpy.list_images()`
CAP_IMAGE_REGISTRY = 'CAP_IMAGE_REGISTRY'
#: `renpy.get_available_image_tags()`
CAP_IMAGE_TAGS = 'CAP_IMAGE_TAGS'
#: 探针拿到的 `attributes`（group / attribute / default 的完整定义）
CAP_LAYERED_ATTRIBUTES = 'CAP_LAYERED_ATTRIBUTES'
#: `when` / `if_all` / `if_any` / `image_format` 这些**条件与格式**信息
#: （P0 恒为 `False`：目前的探针不采它）
CAP_LAYERED_CONDITIONALS = 'CAP_LAYERED_CONDITIONALS'
#: `renpy.statements.registry`（引擎注册了什么语句 —— 判"认不认识写法"的权威名单）
CAP_STATEMENT_REGISTRY = 'CAP_STATEMENT_REGISTRY'
#: `--json-dump` 的 `transform`
CAP_TRANSFORM_TABLE = 'CAP_TRANSFORM_TABLE'
#: `--json-dump` 的 `screen`
CAP_SCREEN_TABLE = 'CAP_SCREEN_TABLE'
#: `--json-dump` 的 `define`
CAP_DEFINES = 'CAP_DEFINES'
#: `renpy.get_all_labels()`
CAP_LABELS = 'CAP_LABELS'
#: lint **跑过**且输出解析出了诊断（**不看 exit code**：语义错误时 exit 1 正常）
CAP_LINT_DIAGS = 'CAP_LINT_DIAGS'
#: store 变量值（P0 恒为 `False`：store 采集不在这片）
CAP_STORE_SNAPSHOT = 'CAP_STORE_SNAPSHOT'

#: 全部令牌（顺序 = 人类阅读顺序：图像 → 结构 → 清单 → 诊断 → 运行期）
ALL_CAPABILITIES = (
    CAP_IMAGE_REGISTRY,
    CAP_IMAGE_TAGS,
    CAP_LAYERED_ATTRIBUTES,
    CAP_LAYERED_CONDITIONALS,
    CAP_STATEMENT_REGISTRY,
    CAP_TRANSFORM_TABLE,
    CAP_SCREEN_TABLE,
    CAP_DEFINES,
    CAP_LABELS,
    CAP_LINT_DIAGS,
    CAP_STORE_SNAPSHOT,
)

#: 每个令牌一句话（CLI / 报告直接用，别在别处另写一份措辞）
NOTES = {
    CAP_IMAGE_REGISTRY: '引擎认得哪些 image 名（`renpy.list_images()`）',
    CAP_IMAGE_TAGS: '引擎认得哪些 image tag（`get_available_image_tags()`）',
    CAP_LAYERED_ATTRIBUTES: 'layeredimage 的 group / attribute / default',
    CAP_LAYERED_CONDITIONALS: 'layeredimage 的 when / if_all / if_any / image_format',
    CAP_STATEMENT_REGISTRY: '引擎注册了哪些语句（判"认不认识写法"的权威名单）',
    CAP_TRANSFORM_TABLE: '引擎的 transform 清单（`--json-dump`）',
    CAP_SCREEN_TABLE: '引擎的 screen 清单（`--json-dump`）',
    CAP_DEFINES: '引擎的 define 清单（`--json-dump`）',
    CAP_LABELS: '引擎的 label 清单（`renpy.get_all_labels()`）',
    CAP_LINT_DIAGS: 'lint 跑过且解析出了诊断（不看 exit code）',
    CAP_STORE_SNAPSHOT: 'store 变量快照（P0 不做）',
}

#: 三态里"这份能力**明确没有**"的那一档（`False`）。
#: `None` 是"这次没采到"，用 `is None` 判，**不要**用 `not v` 混在一起。
NOT_SUPPORTED = False
UNKNOWN = None


def normalize(value):
    """
    任意值 → 三态（`True` / `False` / `None`）。

    ⚠ **只认这三个**：`1` / `'true'` / 非空字符串一律当 `None`（"说不清"）。
    为什么要这么严：这个值会直接进 JSON 与 UI，宽松转换会把"我不知道"
    悄悄变成"支持"或"不支持"，而那正是这一层要防的事。
    """
    if value is True or value is False:
        return value
    if value is None:
        return None
    return None


def is_available(value) -> bool:
    """`True` 才算"这次能回答"（`None` / `False` 都不算）。"""
    return value is True


def is_missing(value) -> bool:
    """`False`（引擎没有）或 `None`（这次没采到）都算"用不上"。"""
    return value is not True


def describe(caps, *, limit: int = 0) -> str:
    """
    一行给人看的能力摘要（CLI / 报告用）。

    例：`CAP_IMAGE_REGISTRY=True CAP_LABELS=None …（None 单独列）`
    """
    caps = caps if isinstance(caps, dict) else {}
    yes, no, unknown = [], [], []
    for name in ALL_CAPABILITIES:
        if name not in caps:
            continue
        val = normalize(caps.get(name))
        if val is True:
            yes.append(name)
        elif val is False:
            no.append(name)
        else:
            unknown.append(name)
    parts = [f'{n}=True' for n in yes] + [f'{n}=False' for n in no]
    if unknown:
        parts.append('没采到：' + ' '.join(unknown))
    if limit and len(parts) > limit:
        parts = parts[:limit] + [f'…（共 {len(parts)} 项）']
    return ' '.join(parts) if parts else '（没有能力记录）'


def missing_tokens(caps) -> list:
    """哪些令牌**不是 `True`**（`False` 与 `None` 都在里面；顺序同 `ALL_CAPABILITIES`）。"""
    caps = caps if isinstance(caps, dict) else {}
    return [n for n in ALL_CAPABILITIES if n in caps
            and normalize(caps.get(n)) is not True]


def unknown_tokens(caps) -> list:
    """`caps` 里**没登记过**的令牌（不报错，但要能看见 —— 让 adapter 自己解释）。"""
    caps = caps if isinstance(caps, dict) else {}
    return [str(k) for k in caps if str(k) not in ALL_CAPABILITIES]


__all__ = [
    'ALL_CAPABILITIES', 'CAP_DEFINES', 'CAP_IMAGE_REGISTRY', 'CAP_IMAGE_TAGS',
    'CAP_LABELS', 'CAP_LAYERED_ATTRIBUTES', 'CAP_LAYERED_CONDITIONALS',
    'CAP_LINT_DIAGS', 'CAP_SCREEN_TABLE', 'CAP_STATEMENT_REGISTRY',
    'CAP_STORE_SNAPSHOT', 'CAP_TRANSFORM_TABLE', 'NOTES', 'NOT_SUPPORTED',
    'UNKNOWN', 'describe', 'is_available', 'is_missing', 'missing_tokens',
    'normalize', 'unknown_tokens',
]
