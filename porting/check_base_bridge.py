#!/usr/bin/env python3
"""Fail early if the unpacked Base lacks the prerequisites for the 32-bit bridge."""
import argparse
from pathlib import Path
from patch_port import extract_file_from_apex


def check_base_bridge(workdir: Path, extractor=extract_file_from_apex):
    base = workdir / 'base'
    sysroot = base / 'system/system' if (base / 'system/system').is_dir() else base / 'system'
    packages = sorted({p for tree in (sysroot, base / 'system_ext') if tree.is_dir()
        for p in tree.rglob('*runtime*')
        if p.name.startswith(('com.android.runtime', 'com.google.android.runtime'))
        and (p.is_dir() or p.suffix in ('.apex', '.capex'))})
    found = False
    lines = [f'system_root={sysroot}', f'system_lib_exists={(sysroot / "lib").is_dir()}']
    for package in packages:
        try:
            raw = extractor(package, 'bin/linker')
            elf32 = bool(raw and raw[:5] == b'\x7fELF\x01')
            lines.append(f'runtime={package}: bin/linker_exists={bool(raw)}, ELF32={elf32}')
            found |= elf32
        except Exception as exc:
            lines.append(f'runtime={package}: extraction_error={exc}')
    lines.append(f'runtime_32bit_linker_found={found}')
    (workdir / 'base_bridge_check.txt').write_text('\n'.join(lines) + '\n')
    for line in lines:
        print('[BASE 32-BIT BRIDGE] ' + line, flush=True)
    ok = found and (sysroot / 'lib').is_dir()
    if not ok:
        print('::error::Base 32-bit bridge prerequisites missing: require an ELF32 bin/linker in runtime APEX and /system/lib. Stopping before donor download.', flush=True)
    return ok


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workdir', required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if check_base_bridge(args.workdir) else 1)
