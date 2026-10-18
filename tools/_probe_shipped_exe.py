import json, glob, os, sys

out = sys.argv[1]
proj = sys.argv[2]
d = json.load(open(os.path.join(out, 'build_report.json'), encoding='utf-8'))
sc = d['build']['stats']['scripts']
built = {os.path.normcase(os.path.abspath(str(e))) for e in sc}
disk = {os.path.normcase(os.path.abspath(f))
        for f in glob.glob(os.path.join(proj, 'game', '**', '*.rpy'), recursive=True)}
print('built:', len(built), 'on disk:', len(disk))
print('没进 build 的文件:')
for f in sorted(disk - built):
    print('  ', f, os.path.getsize(f), 'bytes')
print('build 里有但盘上没有:')
for f in sorted(built - disk):
    print('  ', f)
