from storygraph.app import stage_entry as SE
from storygraph.pipeline.continuity import entry_for_file
R = r'D:\千连万理\sr_general'
SE.clear_cache()
sig, rep = SE.analyze(R)
print('章节', len(rep.chapters), '指纹', sig[:8])
for name in ('chapter1_1', 'chapter1_2', 'chapter2_1', 'chapter2_1_1', 'chapter2_1_2', 'chapter2_3'):
    path = rf'{R}\game\chapter\{name}.rpy'
    e = entry_for_file(rep, path)
    print(f'{name:14s}', ('—' if e is None else f'{e.scene or "（无背景）":10s} | {e.why}'))
