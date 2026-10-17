from pathlib import Path
anchors = {
 'storygraph/app/beats.py': ['def attr_slot_rows', 'if not toks or symbols is None', 'if not group_rows and groups', "if e.get('group')", "slot['extras']", 'if not group_rows:', 'def expression_groups_for', 'overlay = EF.rename_overlay', 'def beat_sprite_rows', 'tag = parts[0]'],
 'storygraph/ui_qt/sprite_grid.py': ['def _fallback_label', 'if rows and len(rows) != len(self._slot_btns)', 'lambda _=False, g=group', 'def _on_add', 'SpriteCardWidget(', 'self._expr_btn.setText'],
 'storygraph/ui_qt/dialog_learn.py': ['def _build_manage_page', 'def _registry_report_now', 'def _refresh_manage', 'def _delete_selected_statement', 'def _clear_groups', 'def _reset_all', 'def _save_columns_now', 'def _build_columns_page', '只保存候选分栏'],
 'storygraph/story/statement_registry.py': ['def declare_attr_groups', 'def clear_attr_groups', 'def reset_declarations', 'def format_registry_report', 'def remove_statement', '清空分组', 'def _backup_manifest'],
 'storygraph/authoring/learn_statement.py': ['def _role_table', 'def _collect_slots', 'def _finish_slots', 'def choice_sources', '角色表（变量名', 'cast_ids', 'name_to_id'],
 'storygraph/ui_qt/param_editor.py': ['def _on_import', '拿进来就是', 'def data(', '这一列叫什么', '不用再让任何人回答', '剧本里怎么写'],
 'storygraph/ui_qt/window_graph_ops.py': ['def show_asset_overview', 'row.get("name")', 'def learn_custom_statement', 'replace=True'],
 'storygraph/ui_qt/main_window.py': ['自定义动作', 'def set_speakers', 'for v in values or'],
 'storygraph/app/session_assets.py': ['def characters', 'return sorted', 'def participants'],
 'storygraph/assets/asset_registry_constants.py': ["'Attributes': '表情'", "'character':", "'image':"],
 'storygraph/assets/asset_registry.py': ['def write_dict_to_excel', 'hidden = True', "column_dimensions['A']"],
 'storygraph/story/project_conventions.py': ['def find_manifest', "root / 'game'"],
 'storygraph/parsing/character_script_linker.py': ['def character_activity_ranking', 'variable_name', 'display_name'],
 'storygraph/story/layered_model.py': ['def _pose_from_groups', '姿势/服装'],
 'storygraph/core/engine_facts.py': ['def rename_overlay', '不参与', 'def grouped_choices'],
}
for f, pats in anchors.items():
    lines = Path(f).read_text(encoding='utf-8').splitlines()
    print('###', f)
    for pat in pats:
        hits = [(i+1, l.strip()) for i, l in enumerate(lines) if pat in l]
        for n, l in hits[:4]:
            print(f'{n}: {l[:120]}')
