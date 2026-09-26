"""
character_script_linker.py
==========================
角色-剧本-翻译 三层引用关系管理器。

数据来源：renpy_parser.ParseResult（角色定义 + 对话 + 翻译单元）
数据去向：asset_panel / asset_engine / 未来的双向同步

职责：
  · character_var → [DialogueLine] 反向索引
  · character_var → {label} 出现位置索引
  · 未定义角色检测（剧本引用了未 define 的变量）
  · 未使用角色检测（定义了但从未说话）
  · 翻译完成度统计（需要 parse_translation_files 的输出）
  · 角色活跃度排名（按对话行数）
  · 导出为 Excel 行（供双向同步使用）
  · 与 AssetRegistry 的差异对比（为双向同步预留）

注意：
  · 本类不做字段级合并，只暴露差异。
  · 双向同步的字段合并策略由 AssetEngine 决定。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional


class CharacterScriptLinker:
    """
    角色与剧本的引用关系。

    使用方式：
        linker = CharacterScriptLinker(parse_result, translations)
        linker.link()
        unused = linker.find_unused_characters()
        undefined = linker.find_undefined_characters()
    """

    def __init__(self, parse_result, translations: Optional[dict] = None):
        """
        parse_result: renpy_parser.ParseResult
        translations: parse_translation_files() 的返回值（可为 None）
        """
        self.parse_result = parse_result
        self.translations = translations or {}

        # character_var -> [DialogueLine]
        self.character_dialogues: dict = defaultdict(list)
        # character_var -> {label, ...}
        self.character_labels: dict = defaultdict(set)

        # 缓存（link 后失效并重建）
        self._undefined_cache: Optional[list] = None

    # ==================== 建立索引 ====================

    def link(self):
        """
        建立所有反向索引。可重复调用（幂等）。
        在 parse_result 或 translations 更新后调用。
        """
        self.character_dialogues.clear()
        self.character_labels.clear()
        self._undefined_cache = None

        for d in self.parse_result.dialogues:
            if not d.character_var:
                continue
            self.character_dialogues[d.character_var].append(d)
            if d.label:
                self.character_labels[d.character_var].add(d.label)

    # ==================== 未定义角色 ====================

    def find_undefined_characters(self) -> list:
        """
        剧本引用了、但未在 ParseResult.characters 中定义的变量。

        返回 [{'variable': str, 'dialogue_count': int,
               'locations': [str, ...]}, ...]
        按对话数降序。
        """
        if self._undefined_cache is not None:
            return self._undefined_cache

        defined = set(self.parse_result.characters.keys())
        result = []
        for var_name, dialogues in self.character_dialogues.items():
            if var_name in defined:
                continue
            locations = [
                f"{d.source_file}:{d.source_line}"
                for d in dialogues[:5]  # 只取前 5 条，避免列表爆炸
            ]
            result.append({
                'variable': var_name,
                'dialogue_count': len(dialogues),
                'locations': locations,
            })

        result.sort(key=lambda x: -x['dialogue_count'])
        self._undefined_cache = result
        return result

    # ==================== 未使用角色 ====================

    def find_unused_characters(self) -> list:
        """
        定义了但从未说话过的角色。

        返回 [{'variable': str, 'name': str,
               'source_file': str, 'source_line': int}, ...]
        """
        used = set(self.character_dialogues.keys())
        result = []
        for var_name, ch in self.parse_result.characters.items():
            if var_name in used:
                continue
            result.append({
                'variable': var_name,
                'name': ch.display_name or var_name,
                'source_file': ch.source_file,
                'source_line': ch.source_line,
            })
        return result

    # ==================== 引用位置查询 ====================

    def references_of_character(self, var_name: str) -> list:
        """返回该角色所有对话行的位置字符串列表。"""
        return [
            f"{d.source_file}:{d.source_line}"
            for d in self.character_dialogues.get(var_name, [])
        ]

    def labels_of_character(self, var_name: str) -> list:
        """返回该角色出现过的所有 label（已排序）。"""
        return sorted(self.character_labels.get(var_name, set()))

    def dialogues_of_character(self, var_name: str) -> list:
        """返回该角色的所有 DialogueLine（原始对象）。"""
        return list(self.character_dialogues.get(var_name, []))

    # ==================== 翻译状态 ====================

    def translation_status(self) -> dict:
        """
        翻译完成度。

        translations 的结构（来自 parse_translation_files）：
            {unit_id: {'original': str, 'translated': str, 'language': str}}

        返回：
            {
                'total_units': int,           # 剧本中的翻译单元总数
                'translated_units': int,      # 已有译文的单元数
                'completion': float,          # 0.0 ~ 1.0
                'per_language': {lang: {'total', 'translated', 'completion'}}
            }

        注意：'total_units' 是剧本侧的翻译单元总数。
        某语言的 'total' 是该语言在 tl/ 中覆盖到的单元数，
        可能小于 total_units（未翻译的部分不会出现在 tl/ 文件里）。
        """
        units = self.parse_result.translation_units
        total = len(units)

        per_lang = defaultdict(lambda: {'total': 0, 'translated': 0})

        for unit in units:
            info = self.translations.get(unit.identifier)
            if not info:
                continue
            lang = info.get('language', 'unknown')
            per_lang[lang]['total'] += 1
            if info.get('translated'):
                per_lang[lang]['translated'] += 1

        per_language = {}
        for lang, d in per_lang.items():
            per_language[lang] = {
                'total': d['total'],
                'translated': d['translated'],
                'completion': (d['translated'] / total) if total else 0.0,
            }

        # 主语言取覆盖最多的那个
        if per_language:
            main_lang = max(
                per_language.keys(),
                key=lambda l: per_language[l]['translated'],
            )
            translated = per_language[main_lang]['translated']
        else:
            translated = 0

        return {
            'total_units': total,
            'translated_units': translated,
            'completion': (translated / total) if total else 0.0,
            'per_language': per_language,
        }

    # ==================== 角色活跃度 ====================

    def character_activity_ranking(self) -> list:
        """
        按对话行数排序的角色列表。

        返回 [{'variable_name', 'display_name',
               'dialogue_count', 'label_count'}, ...]
        """
        ranking = []
        for var_name, ch in self.parse_result.characters.items():
            ranking.append({
                'variable_name': var_name,
                'display_name': ch.display_name or var_name,
                'dialogue_count': len(
                    self.character_dialogues.get(var_name, [])
                ),
                'label_count': len(
                    self.character_labels.get(var_name, set())
                ),
            })
        ranking.sort(key=lambda x: -x['dialogue_count'])
        return ranking

    # ==================== 导出为 Excel 行 ====================

    def export_to_character_rows(self) -> list:
        """
        将解析出的角色定义转换为 characters.xlsx 格式的行列表。
        用于：
          · 首次生成 Excel 时填充角色表
          · 双向同步时作为 rpy 侧的"当前值"

        额外字段（下划线开头）供上层合并逻辑使用，不写入 Excel：
          '_dialogue_count'  对话行数
          '_source_file'     定义所在文件
          '_source_line'     定义所在行
          '_in_auto_block'   是否位于 AUTO-GENERATED 标记段内
                             （需要上层提供 block 范围判断，
                              此处留空，由 engine 填充）
        """
        rows = []
        for var_name, ch in self.parse_result.characters.items():
            rows.append({
                'ID': var_name,
                'Name': ch.display_name or var_name,
                'Color': ch.color,
                'WhatColor': ch.what_color,
                'ImageTag': ch.image_tag,
                'SideImage': '',
                'DefaultTransform': '',
                'Notes': f"{ch.source_file}:{ch.source_line}",
                '_dialogue_count': ch.dialogue_count,
                '_source_file': ch.source_file,
                '_source_line': ch.source_line,
                '_in_auto_block': False,   # 由 engine 填充
            })
        rows.sort(key=lambda r: -r['_dialogue_count'])
        return rows

    # ==================== 与 AssetRegistry 差异对比 ====================

    def diff_with_registry(self, registry) -> dict:
        """
        对比脚本角色（parse_result）与 Excel 注册表角色。

        参数：
          registry: AssetRegistry 实例，或 None

        返回：
          {
            'only_in_script': [(var, source), ...],
            'only_in_excel':  [(var, source), ...],
            'both':           [(var, source), ...],
          }

        'source' 是字符串，说明每个角色来自哪一侧：
          · only_in_script → 'script'
          · only_in_excel  → 'excel'
          · both           → 'script+excel' 或 'excel+script'（看哪个先出现）

        注意：本方法**不做字段级 diff**，只做存在性 diff。
        字段级合并由上层（如 AssetEngine._merge_characters）处理。
        """
        script_ids = set(self.parse_result.characters.keys())
        excel_ids = set(registry.characters.keys()) if registry else set()

        def _script_source(var):
            ch = self.parse_result.characters[var]
            return f"{ch.source_file}:{ch.source_line}"

        def _excel_source(var):
            row = registry.characters[var]
            return row.get('Notes', '') or 'excel'

        only_script = [
            (v, _script_source(v)) for v in sorted(script_ids - excel_ids)
        ]
        only_excel = [
            (v, _excel_source(v)) for v in sorted(excel_ids - script_ids)
        ]
        both = [
            (v, 'script+excel') for v in sorted(script_ids & excel_ids)
        ]

        return {
            'only_in_script': only_script,
            'only_in_excel': only_excel,
            'both': both,
        }

    # ==================== 汇总统计 ====================

    def summary(self) -> dict:
        """一次性返回主要统计数字，供 asset_panel 使用。"""
        return {
            'total_characters': len(self.parse_result.characters),
            'used_characters': len(self.character_dialogues),
            'undefined_characters': len(self.find_undefined_characters()),
            'unused_characters': len(self.find_unused_characters()),
            'total_dialogues': len(self.parse_result.dialogues),
            'translation_status': self.translation_status(),
        }