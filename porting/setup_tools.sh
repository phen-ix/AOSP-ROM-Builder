#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/3] تثبيت الحزم الأساسية لبيئة البورت (APT Packages) ==="
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  aria2 attr bc binutils bison brotli build-essential cpio curl \
  device-tree-compiler e2fsprogs file flex git jq libelf-dev libssl-dev \
  lz4 p7zip-full python3 qemu-user-static rsync secilc unzip zip zstd

BIN_DIR="/usr/local/bin"
TMP_TOOLS="$(mktemp -d)"
trap 'rm -rf "$TMP_TOOLS"' EXIT

echo "=== [2/3] تحميل وتثبيت أدوات الفك والضغط الحديثة (EROFS / Payload / MagiskBoot / LPTools) ==="

# 1. payload-dumper-go v2.1.0 (فك payload.bin بسرعات عالية وتعدد أنوية)
curl -fsSL "https://github.com/ssut/payload-dumper-go/releases/download/2.1.0/payload-dumper-go_2.1.0_linux_amd64.tar.gz" \
  | tar -xz -C "$TMP_TOOLS" payload-dumper-go
sudo install -m 0755 "$TMP_TOOLS/payload-dumper-go" "$BIN_DIR/payload-dumper-go"

# 2. sekaiacg erofs-utils v1.8.10 (extract.erofs + mkfs.erofs مع حفظ fs_config و file_contexts)
curl -fsSL "https://github.com/sekaiacg/erofs-tools/releases/download/v1.8.10-251217/erofs-utils-v1.8.10-gee46dd74-251217-Linux_x86_64.zip" \
  -o "$TMP_TOOLS/erofs.zip"
unzip -q "$TMP_TOOLS/erofs.zip" -d "$TMP_TOOLS/erofs"
for bin in extract.erofs mkfs.erofs dump.erofs fsck.erofs; do
  sudo install -m 0755 "$TMP_TOOLS/erofs/$bin" "$BIN_DIR/$bin"
done

# 3. magiskboot v31.1 (لفك وإعادة حزم boot.img وحقن الكيرنل وتعديل ramdisk fstab)
curl -fsSL "https://github.com/topjohnwu/Magisk/releases/download/v31.1/Magisk-v31.1.apk" \
  -o "$TMP_TOOLS/magisk.apk"
unzip -p "$TMP_TOOLS/magisk.apk" lib/x86_64/libmagiskboot.so > "$TMP_TOOLS/magiskboot"
sudo install -m 0755 "$TMP_TOOLS/magiskboot" "$BIN_DIR/magiskboot"

# 4. Android 15/16 Dynamic Partition Tools (lpunpack, lpmake, simg2img, img2simg)
LP_BASE="https://raw.githubusercontent.com/Rprop/aosp15_partition_tools/master/linux_glibc_x86_64"
for tool in lpunpack lpmake lpdumps simg2img img2simg; do
  curl -fsSL "${LP_BASE}/${tool}" -o "$TMP_TOOLS/${tool}"
  sudo install -m 0755 "$TMP_TOOLS/${tool}" "$BIN_DIR/${tool}"
done

echo "=== [3/3] التحقق من جاهزية الأدوات ==="
payload-dumper-go -version || true
mkfs.erofs --version || true
echo "✅ تم تجهيز أدوات البورت بنجاح!"
