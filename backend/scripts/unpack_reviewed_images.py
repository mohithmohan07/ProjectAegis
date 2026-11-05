"""Reconstruct a bounded, hash-checked image bundle; never execute its contents."""
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

NAME = re.compile(r"[0-9a-f]{64}\.jpg")

def unpack(bundle, destination):
    bundle, destination = Path(bundle), Path(destination)
    assert not bundle.is_symlink() and not destination.is_symlink()
    index = json.loads((bundle / 'bundle-index.json').read_text())
    manifest = json.loads((destination / 'manifest.json').read_text())
    assert index['schema'] == 1 and manifest['schema'] == 1 and manifest['job_id'] == 0
    assets = {a['filename']: a for a in manifest['assets']}
    assert len(assets) == len(manifest['assets'])
    names = index['filenames']
    assert len(names) == len(set(names)) and all(NAME.fullmatch(n) and n in assets for n in names)
    assert 0 < index['bytes'] <= 64 * 1024 * 1024
    parts = []
    for i, chunk in enumerate(index['chunks']):
        assert chunk['filename'] == f'part-{i:03d}.bin'
        p = bundle / chunk['filename']
        assert not p.is_symlink() and p.is_file()
        b = p.read_bytes()
        assert 0 < len(b) == chunk['bytes'] <= 1024 * 1024
        assert hashlib.sha256(b).hexdigest() == chunk['sha256']
        parts.append(b)
    archive = b''.join(parts)
    assert len(archive) == index['bytes']
    assert hashlib.sha256(archive).hexdigest() == index['sha256']
    validated = []
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        infos = z.infolist()
        assert len(infos) == len(names) and {x.filename for x in infos} == set(names)
        for info in infos:
            assert NAME.fullmatch(info.filename) and not info.is_dir()
            mode = info.external_attr >> 16
            assert not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG)
            assert not (info.flag_bits & 1)
            a = assets[info.filename]
            assert 0 < info.file_size == a['bytes'] <= 16 * 1024 * 1024
            b = z.read(info)
            assert len(b) == a['bytes'] and hashlib.sha256(b).hexdigest() == a['sha256'] == info.filename[:-4]
            assert b.startswith(b'\xff\xd8') and b.endswith(b'\xff\xd9')
            p = destination / info.filename
            assert p.parent.resolve() == destination.resolve() and not p.is_symlink()
            if p.exists():
                assert p.read_bytes() == b
            validated.append((p, b))
    # No extraction until the entire archive and every destination have passed.
    for p, b in validated:
        if not p.exists():
            with p.open('xb') as f:
                f.write(b)
    # Existing individual image objects must also match the same final manifest.
    for name, a in assets.items():
        assert NAME.fullmatch(name)
        p = destination / name
        assert not p.is_symlink() and p.is_file()
        b = p.read_bytes()
        assert len(b) == a['bytes'] and hashlib.sha256(b).hexdigest() == a['sha256'] == name[:-4]
    print(f'Validated {len(assets)} reviewed images, reconstructed {len(validated)} bundled images.')

if __name__ == '__main__':
    assert len(sys.argv) == 3
    unpack(sys.argv[1], sys.argv[2])
