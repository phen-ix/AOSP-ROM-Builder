#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== [1/4] تثبيت الحزم الأساسية وأدوات البناء المتقاطع (APT Packages) ==="
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  aria2 attr bc binutils binutils-aarch64-linux-gnu bison brotli build-essential \
  ccache cpio curl device-tree-compiler e2fsprogs file flex gcc-aarch64-linux-gnu \
  gcc-arm-linux-gnueabi git jq libc6-dev-arm64-cross libelf-dev libssl-dev \
  lz4 p7zip-full python3 qemu-user-static rsync secilc unzip zip zstd

BIN_DIR="/usr/local/bin"
TMP_TOOLS="$(mktemp -d)"
trap 'rm -rf "$TMP_TOOLS"' EXIT

echo "=== [2/4] تحميل وتثبيت أدوات الفك والضغط الحديثة (EROFS / Payload / MagiskBoot / LPTools) ==="

# 1. payload-dumper-go v2.1.0
curl -fsSL "https://github.com/ssut/payload-dumper-go/releases/download/2.1.0/payload-dumper-go_2.1.0_linux_amd64.tar.gz" \
  | tar -xz -C "$TMP_TOOLS" payload-dumper-go
sudo install -m 0755 "$TMP_TOOLS/payload-dumper-go" "$BIN_DIR/payload-dumper-go"

# 2. sekaiacg erofs-utils v1.8.10
curl -fsSL "https://github.com/sekaiacg/erofs-tools/releases/download/v1.8.10-251217/erofs-utils-v1.8.10-gee46dd74-251217-Linux_x86_64.zip" \
  -o "$TMP_TOOLS/erofs.zip"
unzip -q "$TMP_TOOLS/erofs.zip" -d "$TMP_TOOLS/erofs"
for bin in extract.erofs mkfs.erofs dump.erofs fsck.erofs; do
  sudo install -m 0755 "$TMP_TOOLS/erofs/$bin" "$BIN_DIR/$bin"
done

# 3. magiskboot v31.1
curl -fsSL "https://github.com/topjohnwu/Magisk/releases/download/v31.1/Magisk-v31.1.apk" \
  -o "$TMP_TOOLS/magisk.apk"
unzip -p "$TMP_TOOLS/magisk.apk" lib/x86_64/libmagiskboot.so > "$TMP_TOOLS/magiskboot"
sudo install -m 0755 "$TMP_TOOLS/magiskboot" "$BIN_DIR/magiskboot"

# 4. Android 15/16 Dynamic Partition Tools (lpunpack, lpmake, lpdumps, simg2img, img2simg)
LP_BASE="https://raw.githubusercontent.com/Rprop/aosp15_partition_tools/master/linux_glibc_x86_64"
for tool in lpunpack lpmake lpdumps simg2img img2simg; do
  curl -fsSL "${LP_BASE}/${tool}" -o "$TMP_TOOLS/${tool}"
  sudo install -m 0755 "$TMP_TOOLS/${tool}" "$BIN_DIR/${tool}"
done

echo "=== [3/4] ترجمة مثبت الريكفري الثابت ARM64 (Static AArch64 Zstd + ZIP64 update-binary) ==="
curl -fsSL "https://github.com/facebook/zstd/releases/download/v1.5.6/zstd-1.5.6.tar.gz" \
  | tar -xz -C "$TMP_TOOLS"
ZSTD_LIB="$TMP_TOOLS/zstd-1.5.6/lib"
aarch64-linux-gnu-gcc -static -O3 -Wall -Wextra -Werror \
  -DZSTD_DISABLE_ASM \
  -I"$ZSTD_LIB" -I"$ZSTD_LIB/common" \
  "$SCRIPT_DIR/recovery_updater.c" \
  "$ZSTD_LIB"/common/*.c \
  "$ZSTD_LIB"/decompress/*.c \
  -o "$TMP_TOOLS/miatoll-update-binary-arm64"
aarch64-linux-gnu-strip "$TMP_TOOLS/miatoll-update-binary-arm64"
sudo install -m 0755 "$TMP_TOOLS/miatoll-update-binary-arm64" "$BIN_DIR/miatoll-update-binary-arm64"
file "$BIN_DIR/miatoll-update-binary-arm64"

echo "=== [4/4] التحقق من جاهزية الأدوات ==="
payload-dumper-go -version || true
mkfs.erofs --version || true
echo "✅ تم تجهيز أدوات البورت ومثبت الريكفري الثابت ARM64 بنجاح!"
