"""Fail-closed donor adbd patcher; preserves signed APEX and privilege dropping."""
import hashlib
import re
import struct
import subprocess
import tempfile
from pathlib import Path


def patch_auth(raw):
    if raw[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', raw, 18)[0] != 183:
        raise ValueError('Expected little-endian ARM64 ELF')
    with tempfile.TemporaryDirectory() as td:
        binary = Path(td) / 'adbd'
        binary.write_bytes(raw)
        symbols = subprocess.check_output(['aarch64-linux-gnu-nm', '-S', str(binary)], text=True)
        found = re.search(r'^([0-9a-f]+)\s+0*1\s+\w\s+auth_required$', symbols, re.M)
        if not found:
            raise ValueError('No verified one-byte auth_required symbol')
        target = int(found[1], 16)
        disasm = subprocess.check_output(['aarch64-linux-gnu-objdump', '-d', str(binary)], text=True)
    # Restrict to main: direct ADRP + optional ADD + STRB, resetting register
    # knowledge at every unknown instruction. No heuristic byte substitutions.
    main = re.search(r'<main>:\n(.*?)(?=\n[0-9a-f]+ <|\Z)', disasm, re.S)
    if not main:
        raise ValueError('No disassemblable main')
    addresses = {}
    edits = []
    for line in main[1].splitlines():
        m = re.match(r'\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(\w+)\s+(.*)', line)
        if not m:
            continue
        pc, word, op, operands = int(m[1],16), int(m[2],16), m[3], m[4]
        if op in ('adrp', 'adr'):
            a = re.match(r'x(\d+),\s*([0-9a-f]+)', operands)
            if a:
                addresses[int(a[1])] = int(a[2],16)
                continue
        if op == 'add':
            a = re.match(r'x(\d+),\s*x(\d+),\s*#0x([0-9a-f]+)', operands)
            if a and int(a[2]) in addresses:
                addresses[int(a[1])] = addresses[int(a[2])] + int(a[3],16)
                continue
        if op == 'strb':
            a = re.match(r'w\d+,\s*\[x(\d+)(?:,\s*#(?:0x([0-9a-f]+)|(\d+)))?\]', operands)
            if a and addresses.get(int(a[1]), -1) + (int(a[2],16) if a[2] else int(a[3] or 0)) == target:
                edits.append((pc, word | 31))
                continue
        addresses.clear()
    if not edits:
        raise ValueError('Unsupported adbd instruction layout; refusing guessed patch')
    patched = bytearray(raw)
    phoff = struct.unpack_from('<Q', raw, 32)[0]
    phsize, phnum = struct.unpack_from('<HH', raw, 54)
    def offset(addr):
        for i in range(phnum):
            typ, _, off, va, _, size, _, _ = struct.unpack_from('<IIQQQQQQ', raw, phoff+i*phsize)
            if typ == 1 and va <= addr < va+size:
                return off+addr-va
        raise ValueError('Address outside file-backed LOAD')
    patched[offset(target)] = 0
    for pc, word in edits:
        struct.pack_into('<I', patched, offset(pc), word)
    return bytes(patched), {'sha256_original': hashlib.sha256(raw).hexdigest(),
        'sha256_patched': hashlib.sha256(patched).hexdigest(), 'store_count': len(edits)}
