"""Integration test: download pinned zstd, cross-build, run installer under QEMU.
Run explicitly: python3 tests/test_installer_arm64.py
All writes use a temporary mock block directory, never real device nodes.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tar = root / 'zstd.tar.gz'
        urllib.request.urlretrieve('https://github.com/facebook/zstd/releases/download/v1.5.6/zstd-1.5.6.tar.gz', tar)
        with tarfile.open(tar) as archive:
            archive.extractall(root, filter='data')
        lib = root / 'zstd-1.5.6/lib'
        binary = root / 'update-binary'
        sources = sorted((lib / 'common').glob('*.c')) + sorted((lib / 'decompress').glob('*.c'))
        subprocess.run(['aarch64-linux-gnu-gcc', '-static', '-O3', '-Wall', '-Wextra', '-Werror',
            '-DZSTD_DISABLE_ASM', '-I'+str(lib), '-I'+str(lib/'common'),
            str(REPO / 'porting/recovery_updater.c'), *map(str,sources), '-o', str(binary)], check=True)
        subprocess.run(['file', str(binary)], check=True)
        output = root / 'output'
        output.mkdir()
        with zipfile.ZipFile(output / 'fixture.zip', 'w', compression=zipfile.ZIP_STORED) as zf:
            zf.write(binary, 'META-INF/com/google/android/update-binary')
            zf.writestr('META-INF/com/google/android/updater-script', '#MAGISK\n')
            for name in ('boot.img', 'dtbo.img', 'vbmeta.img', 'vbmeta_system.img'):
                zf.writestr(name, b'fixture')
            compressed = subprocess.run(['zstd','-q','-c'], input=b'fixture super', capture_output=True, check=True).stdout
            zf.writestr('super.img.zst', compressed)
        spec = importlib.util.spec_from_file_location('checks', REPO / 'porting/simulate_boot_check.py')
        checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checks)
        ok, message = checks.check_stage6_post_repack(root)
        print(message)
        if not ok:
            raise SystemExit(1)
        blocks = root / 'rejected-device-blocks'
        blocks.mkdir()
        env = dict(os.environ, MIATOLL_BLOCK_DIR=str(blocks), MIATOLL_HWNAME_OVERRIDE='kunzite')
        result = subprocess.run(['qemu-aarch64-static', str(binary), '3', '0', str(output/'fixture.zip')],
            env=env, capture_output=True, timeout=30)
        assert result.returncode != 0, 'Unsupported device was accepted'
        assert not list(blocks.iterdir()), 'Unsupported device test wrote files'
        print('PASS: AArch64 static build, byte-exact fixture flash, unsupported-device rejection')

if __name__ == '__main__':
    main()
