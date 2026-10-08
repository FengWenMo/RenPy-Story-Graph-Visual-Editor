#!/usr/bin/env python
"""
探针：`scan_assets(root, audio_dirs=[项目外目录])` 为什么扫不出东西
==================================================================

对账 `tests/test_asset_registry.py::TestScanAssets::test_custom_audio_dirs`
（长期红的既有失败）。逐条打印这个 mp3 在扫描里经过的每个判据，
好定位是"没枚举到"还是"被某个过滤器挡了"。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storygraph.assets.asset_registry import (          # noqa: E402
    SUPPORTED_AUDIO_EXT, _classify_audio, _is_scan_noise,
    _path_under_skip_dir, scan_assets,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix='probe_audio_dirs_'))
    root = tmp / 'game'
    (root / 'images' / 'bg').mkdir(parents=True)
    (root / 'audio').mkdir(parents=True)
    (root / 'audio' / 'bgm_theme.ogg').write_bytes(b'1')
    (root / 'audio' / 'sfx_click.wav').write_bytes(b'1')

    extra = tmp / 'extra_audio'
    extra.mkdir()
    mp3 = extra / 'theme_song.mp3'
    mp3.write_bytes(b'1')

    print('—— 逐条判据 ——')
    print(f'  suffix={mp3.suffix.lower()!r} 在 SUPPORTED_AUDIO_EXT ? '
          f'{mp3.suffix.lower() in SUPPORTED_AUDIO_EXT}')
    print(f'  _is_scan_noise({mp3.name!r}) = {_is_scan_noise(mp3.name)}')
    print(f'  _path_under_skip_dir = {_path_under_skip_dir(mp3, root)}')
    try:
        rel = mp3.relative_to(root)
    except ValueError:
        rel = mp3
    print(f'  rel_path = {rel}')
    print(f'  _classify_audio(rel) = {_classify_audio(rel)!r}')

    print('—— 实际扫描 ——')
    auto = scan_assets(root)
    print(f'  自动探测           : music={len(auto["audio"]["music"])} '
          f'sound={len(auto["audio"]["sound"])}')
    given = scan_assets(root, audio_dirs=[extra])
    print(f'  audio_dirs=[extra] : music={len(given["audio"]["music"])} '
          f'sound={len(given["audio"]["sound"])}')
    print(f'  音乐 ID = {[a["ID"] for a in given["audio"]["music"]]}')
    print(f'  音效 ID = {[a["ID"] for a in given["audio"]["sound"]]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
