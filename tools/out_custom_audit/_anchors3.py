from pathlib import Path
for f,pats in {
 'storygraph/ui_qt/dialog_learn.py':['def _refresh_cut','def _why_not'],
 'storygraph/authoring/pattern.py':['def assign_tokens','if (kind == .choice. and slot.get(.limit.)','tokens\[ti\] not in allowed'],
 'storygraph/assets/asset_registry_constants.py':['ASSET_FIELD_LABELS','Name','ImageTag'],
 'storygraph/story/project_conventions.py':['def find_manifest'],
 'storygraph/story/statement_registry.py':['path = root / MANIFEST_NAMES\[0\]','data\[ATTR_GROUPS_KEY\] = clean','existing = data.get\(ATTR_GROUPS_KEY\)'],
}.items():
    lines=Path(f).read_text(encoding='utf-8').splitlines(); print('###',f)
    for p in pats:
        import re
        for i,l in enumerate(lines,1):
            if re.search(p,l): print(f'{i}: {l.strip()[:120]}')
