from pathlib import Path
for f, pats in {
 'storygraph/ui_qt/dialog_chars.py': ['def _label', 'self.target.addItem', 'self._rows.get(var)', 'new_name'],
 'storygraph/ui_qt/asset_picker.py': ['def _short_label', 'return str(aid)'],
 'storygraph/story/dsl.py': ['def actor_image_tag', 'def actor_display', 'return (row.get'],
 'storygraph/story/beat_build.py': ["'ImageTag'"],
 'storygraph/app/beats.py': ["'ImageTag'", 'def participants', 'def card_note', 'def speaker_missing_warning'],
}.items():
    lines=Path(f).read_text(encoding='utf-8').splitlines()
    print('###',f)
    for p in pats:
        for i,l in enumerate(lines,1):
            if p in l: print(f'{i}: {l.strip()[:130]}')
