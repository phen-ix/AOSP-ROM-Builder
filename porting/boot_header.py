"""Edit the magiskboot text header, preserving other header fields."""
import argparse
from pathlib import Path


def set_selinux_mode(header: Path, mode: str):
    if mode not in ('enforcing', 'permissive'):
        raise ValueError('Invalid SELinux mode')
    if not header.is_file():
        raise RuntimeError(f'magiskboot header missing: {header}')
    lines = header.read_text().splitlines()
    indices = [i for i, line in enumerate(lines) if line.startswith('cmdline=')]
    if len(indices) != 1:
        raise RuntimeError('Expected exactly one cmdline= in magiskboot header')
    i = indices[0]
    tokens = [token for token in lines[i].partition('=')[2].split()
              if not token.startswith('androidboot.selinux=')]
    tokens.append('androidboot.selinux=' + mode)
    lines[i] = 'cmdline=' + ' '.join(tokens)
    header.write_text('\n'.join(lines) + '\n')
    print(f'[BOOT HEADER] {lines[i]}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('header', type=Path)
    parser.add_argument('mode', choices=['enforcing', 'permissive'])
    args = parser.parse_args()
    set_selinux_mode(args.header, args.mode)
