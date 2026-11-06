#!/usr/bin/env python3
"""
Unified Android ROM Unpacker for Porting Kitchen (joyeuse / miatoll)
Supports:
  - OTA / Recovery .zip with payload.bin
  - Recovery .zip with *.new.dat.br / *.new.dat + *.transfer.list
  - Fastboot .tgz / .tar.gz / .zip with super.img(.br/.zst/.gz) or raw/sparse *.img
  - Filesystems: EROFS (via extract.erofs) & EXT4 (via loop-mount + xattr/SELinux capture)
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

SPARSE_MAGIC = 0xED26FF3A
EROFS_MAGIC = 0xE0F5E1E2
EXT4_MAGIC = 0xEF53

BASE_LOGICAL_PARTS = ["vendor", "odm", "product", "system_ext", "system"]
BASE_FIRMWARE_IMGS = ["boot", "dtbo", "vbmeta", "vbmeta_system", "vbmeta_vendor"]

PORT_LOGICAL_PARTS = [
    "system",
    "system_ext",
    "product",
    "mi_ext",
    "odm",
    "my_product",
    "my_engineering",
    "my_stock",
    "my_heytap",
    "my_carrier",
    "my_region",
    "my_bigball",
    "my_manifest",
]


def run(cmd, check=True, capture=False):
    print(f"  [CMD] {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def sdat2img(transfer_list_path: Path, new_dat_path: Path, output_img_path: Path):
    """Pure Python implementation of Android sdat2img."""
    BLOCK_SIZE = 4096
    with open(transfer_list_path, "r", encoding="utf-8") as f:
        version = int(f.readline().strip())
        _new_blocks = int(f.readline().strip())
        if version >= 2:
            f.readline()
            f.readline()
        commands = []
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ")
            cmd = parts[0]
            if cmd in ("new", "erase", "zero") and len(parts) >= 2:
                nums = [int(x) for x in parts[1].split(",")]
                num_Pairs = nums[0]
                ranges = [(nums[i], nums[i + 1]) for i in range(1, len(nums), 2)]
                assert len(ranges) == num_Pairs // 2
                commands.append((cmd, ranges))

    max_block = max((end for cmd, rngs in commands if cmd == "new" for _, end in rngs), default=0)
    with open(new_dat_path, "rb") as src, open(output_img_path, "wb") as dst:
        if max_block > 0:
            dst.truncate(max_block * BLOCK_SIZE)
        for cmd, ranges in commands:
            if cmd == "new":
                for begin, end in ranges:
                    count = end - begin
                    dst.seek(begin * BLOCK_SIZE)
                    remaining = count * BLOCK_SIZE
                    while remaining > 0:
                        chunk = src.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        dst.write(chunk)
                        remaining -= len(chunk)


def ensure_raw_image(img_path: Path) -> Path:
    """Convert Android sparse image to raw image if needed."""
    with open(img_path, "rb") as f:
        hdr = f.read(4)
    if len(hdr) == 4 and struct.unpack("<I", hdr)[0] == SPARSE_MAGIC:
        raw_path = img_path.with_suffix(".raw.img")
        run(["simg2img", str(img_path), str(raw_path)])
        img_path.unlink()
        raw_path.rename(img_path)
    return img_path


def detect_fs_type(img_path: Path) -> str:
    with open(img_path, "rb") as f:
        f.seek(1024)
        erofs_hdr = f.read(4)
        if len(erofs_hdr) == 4 and struct.unpack("<I", erofs_hdr)[0] == EROFS_MAGIC:
            return "erofs"
        f.seek(1080)
        ext4_hdr = f.read(2)
        if len(ext4_hdr) == 2 and struct.unpack("<H", ext4_hdr)[0] == EXT4_MAGIC:
            return "ext4"
    return "unknown"


EXT4_DUMP_HELPER = r"""
import os, re, stat, struct, sys

mnt_dir = sys.argv[1]
part_name = sys.argv[2]
fs_cfg_out = sys.argv[3]
fc_out = sys.argv[4]

def get_selinux(path):
    try:
        val = os.getxattr(path, b"security.selinux", follow_symlinks=False)
        return val.rstrip(b"\x00").decode("utf-8", errors="ignore")
    except OSError:
        return "u:object_r:system_file:s0"

def get_caps(path):
    try:
        data = os.getxattr(path, b"security.capability", follow_symlinks=False)
        if len(data) >= 20:
            magic_etc, low, _, high, _ = struct.unpack("<IIIII", data[:20])
            caps = (high << 32) | low
            if caps:
                return f" capabilities=0x{caps:x}"
    except OSError:
        pass
    return ""

fs_lines = []
fc_lines = []

root_st = os.lstat(mnt_dir)
root_ctx = get_selinux(mnt_dir)
fs_lines.append(f"/ {root_st.st_uid} {root_st.st_gid} {stat.S_IMODE(root_st.st_mode):04o}")
fs_lines.append(f"{part_name}/ {root_st.st_uid} {root_st.st_gid} {stat.S_IMODE(root_st.st_mode):04o}")
fc_lines.append(f"/ {root_ctx}")
fc_lines.append(f"/{re.escape(part_name)}(/.*)? {root_ctx}")

for dirpath, dirnames, filenames in os.walk(mnt_dir, followlinks=False):
    entries = sorted(dirnames + filenames)
    for name in entries:
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, mnt_dir).replace("\\", "/")
        st = os.lstat(full)
        mode = stat.S_IMODE(st.st_mode)
        caps = "" if stat.S_ISLNK(st.st_mode) else get_caps(full)
        ctx = get_selinux(full)
        fs_lines.append(f"{part_name}/{rel} {st.st_uid} {st.st_gid} {mode:04o}{caps}")
        fc_lines.append(f"/{re.escape(part_name)}/{re.escape(rel)} {ctx}")

with open(fs_cfg_out, "w", encoding="utf-8") as f:
    f.write("\n".join(fs_lines) + "\n")
with open(fc_out, "w", encoding="utf-8") as f:
    f.write("\n".join(fc_lines) + "\n")
"""


def extract_partition_fs(img_path: Path, part_name: str, out_role_dir: Path):
    ensure_raw_image(img_path)
    fs_type = detect_fs_type(img_path)
    print(f"=== فك بارتيشن [{part_name}] (نوع الملفات: {fs_type}) ===", flush=True)

    config_dir = out_role_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target_img = img_path.parent / f"{part_name}.img"
    if img_path != target_img:
        if target_img.exists():
            target_img.unlink()
        img_path.rename(target_img)

    if fs_type == "erofs":
        run(["extract.erofs", "-i", str(target_img), "-x", "-T4", "-f", "-o", str(out_role_dir)])
    elif fs_type == "ext4":
        mnt_dir = Path(tempfile.mkdtemp(prefix=f"mnt_{part_name}_"))
        part_out = out_role_dir / part_name
        if part_out.exists():
            shutil.rmtree(part_out)
        part_out.mkdir(parents=True, exist_ok=True)
        try:
            run(["sudo", "mount", "-o", "ro,loop", str(target_img), str(mnt_dir)])
            run(["sudo", "cp", "-a", f"{mnt_dir}/.", str(part_out)])
            uid_gid = f"{os.getuid()}:{os.getgid()}"
            run(["sudo", "chown", "-hR", uid_gid, str(part_out)])

            helper_script = config_dir / f"_dump_{part_name}.py"
            helper_script.write_text(EXT4_DUMP_HELPER, encoding="utf-8")
            run([
                "sudo", "python3", str(helper_script),
                str(mnt_dir),
                part_name,
                str(config_dir / f"{part_name}_fs_config"),
                str(config_dir / f"{part_name}_file_contexts"),
            ])
            helper_script.unlink(missing_ok=True)
            run(["sudo", "chown", uid_gid, str(config_dir / f"{part_name}_fs_config"), str(config_dir / f"{part_name}_file_contexts")])
        finally:
            run(["sudo", "umount", "-l", str(mnt_dir)], check=False)
            shutil.rmtree(mnt_dir, ignore_errors=True)
    else:
        print(f"  [WARN] تخطي {part_name}: صيغة غير معروفة ({fs_type})")

    target_img.unlink(missing_ok=True)


def unpack_super_img(super_path: Path, raw_dir: Path):
    print(f"=== فك حاوية super ({super_path.name}) ===", flush=True)
    if super_path.name.endswith(".br"):
        out_s = raw_dir / "super.img"
        run(["brotli", "-df", str(super_path), "-o", str(out_s)])
        super_path.unlink()
        super_path = out_s
    elif super_path.name.endswith(".zst"):
        out_s = raw_dir / "super.img"
        run(["zstd", "-df", str(super_path), "-o", str(out_s)])
        super_path.unlink()
        super_path = out_s
    elif super_path.name.endswith(".gz"):
        out_s = raw_dir / "super.img"
        run(["gzip", "-dc", str(super_path)], check=True)

    ensure_raw_image(super_path)
    lp_out = raw_dir / "lp_parts"
    lp_out.mkdir(parents=True, exist_ok=True)
    run(["lpunpack", str(super_path), str(lp_out)])
    super_path.unlink(missing_ok=True)

    for p in sorted(lp_out.glob("*.img")):
        if p.stat().st_size == 0:
            p.unlink()
            continue
        clean_name = re.sub(r"_[ab]\.img$", ".img", p.name)
        if p.name.endswith("_b.img") and (lp_out / clean_name).exists():
            p.unlink()
            continue
        dest = raw_dir / clean_name
        if dest.exists():
            dest.unlink()
        p.rename(dest)
    shutil.rmtree(lp_out, ignore_errors=True)


def unpack_archive(archive_path: Path, role: str, workdir: Path):
    wanted_logical = BASE_LOGICAL_PARTS if role == "base" else PORT_LOGICAL_PARTS
    wanted_fw = BASE_FIRMWARE_IMGS if role == "base" else []

    role_dir = workdir / role
    raw_dir = role_dir / "_raw"
    fw_dir = role_dir / "firmware"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fw_dir.mkdir(parents=True, exist_ok=True)

    name_lower = archive_path.name.lower()

    # 1. ZIP Archive
    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()

            # Case A: payload.bin OTA
            payload_entries = [n for n in names if n.endswith("payload.bin")]
            if payload_entries:
                print(f"=== اكتشاف payload.bin داخل {archive_path.name} ===", flush=True)
                res = run(["payload-dumper-go", "-l", str(archive_path)], capture=True)
                avail = set()
                for line in res.stdout.splitlines():
                    for candidate in set(wanted_logical + wanted_fw):
                        if re.search(rf"\b{re.escape(candidate)}\b", line):
                            avail.add(candidate)
                targets = sorted(avail) if avail else (wanted_logical + wanted_fw)
                run([
                    "payload-dumper-go",
                    "-c", "4",
                    "-o", str(raw_dir),
                    "-p", ",".join(targets),
                    str(archive_path),
                ], check=False)

            else:
                # Extract firmware imgs if base
                for n in names:
                    bn = Path(n).name
                    stem = bn[:-4] if bn.endswith(".img") else ""
                    if stem in wanted_fw:
                        with zf.open(n) as s, open(fw_dir / bn, "wb") as d:
                            shutil.copyfileobj(s, d)

                # Case B: super.img* inside zip
                super_entries = [n for n in names if Path(n).name.startswith("super.img")]
                if super_entries:
                    s_entry = super_entries[0]
                    s_path = raw_dir / Path(s_entry).name
                    with zf.open(s_entry) as s, open(s_path, "wb") as d:
                        shutil.copyfileobj(s, d)
                    unpack_super_img(s_path, raw_dir)
                else:
                    # Case C: *.new.dat(.br) + *.transfer.list OR direct *.img
                    for part in wanted_logical:
                        br_match = [n for n in names if Path(n).name == f"{part}.new.dat.br"]
                        dat_match = [n for n in names if Path(n).name == f"{part}.new.dat"]
                        tl_match = [n for n in names if Path(n).name == f"{part}.transfer.list"]
                        img_match = [n for n in names if Path(n).name in (f"{part}.img", f"{part}_a.img")]

                        if (br_match or dat_match) and tl_match:
                            print(f"=== استخراج وتحويل {part}.new.dat ===", flush=True)
                            tl_path = raw_dir / f"{part}.transfer.list"
                            dat_path = raw_dir / f"{part}.new.dat"
                            with zf.open(tl_match[0]) as s, open(tl_path, "wb") as d:
                                shutil.copyfileobj(s, d)
                            if br_match:
                                br_path = raw_dir / f"{part}.new.dat.br"
                                with zf.open(br_match[0]) as s, open(br_path, "wb") as d:
                                    shutil.copyfileobj(s, d)
                                run(["brotli", "-df", str(br_path), "-o", str(dat_path)])
                                br_path.unlink(missing_ok=True)
                            else:
                                with zf.open(dat_match[0]) as s, open(dat_path, "wb") as d:
                                    shutil.copyfileobj(s, d)
                            sdat2img(tl_path, dat_path, raw_dir / f"{part}.img")
                            tl_path.unlink(missing_ok=True)
                            dat_path.unlink(missing_ok=True)
                        elif img_match:
                            with zf.open(img_match[0]) as s, open(raw_dir / f"{part}.img", "wb") as d:
                                shutil.copyfileobj(s, d)

    # 2. Fastboot .tgz / .tar.gz Archive
    elif name_lower.endswith((".tgz", ".tar.gz", ".tar")):
        print(f"=== فك أرشيف Fastboot ({archive_path.name}) ===", flush=True)
        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                bn = Path(member.name).name
                stem = bn[:-4] if bn.endswith(".img") else ""
                if stem in wanted_fw:
                    src = tf.extractfile(member)
                    if src:
                        with open(fw_dir / bn, "wb") as d:
                            shutil.copyfileobj(src, d)
                elif bn.startswith("super.img") or stem in wanted_logical:
                    src = tf.extractfile(member)
                    if src:
                        out_f = raw_dir / bn
                        with open(out_f, "wb") as d:
                            shutil.copyfileobj(src, d)
        for s_cand in list(raw_dir.glob("super.img*")):
            unpack_super_img(s_cand, raw_dir)

    else:
        raise RuntimeError(f"صيغة ملف الروم غير مدعومة: {archive_path.name}")

    # Move firmware imgs from raw_dir to fw_dir if extracted by payload-dumper-go
    for fw in wanted_fw:
        cand = raw_dir / f"{fw}.img"
        if cand.exists():
            cand.rename(fw_dir / f"{fw}.img")

    # Delete original downloaded archive early to free 5-6 GB disk space!
    archive_path.unlink(missing_ok=True)

    # Extract each logical partition filesystem
    extracted_parts = []
    for part in wanted_logical:
        p_img = raw_dir / f"{part}.img"
        if p_img.exists() and p_img.stat().st_size > 4096:
            extract_partition_fs(p_img, part, role_dir)
            if (role_dir / part).is_dir():
                extracted_parts.append(part)

    shutil.rmtree(raw_dir, ignore_errors=True)
    (role_dir / "extracted_partitions.txt").write_text("\n".join(extracted_parts) + "\n", encoding="utf-8")
    print(f"✅ اكتمل فك [{role}]: البارتيشنات المستخرجة = {extracted_parts}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to ROM archive (.zip / .tgz)")
    parser.add_argument("--role", required=True, choices=["base", "port"])
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    unpack_archive(Path(args.input), args.role, Path(args.workdir))


if __name__ == "__main__":
    main()
