#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${1:?Missing WORKDIR}"
KERNEL_MODE="${2:-keep_base}"
CUSTOM_KERNEL_URL="${3:-}"
DISABLE_DFE="${4:-false}"
SELINUX_MODE="${5:-enforcing}"
case "$SELINUX_MODE" in enforcing|permissive) ;; *) echo "::error::Invalid SELinux mode"; exit 1 ;; esac
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$KERNEL_MODE" = "custom_url" ] && [ -z "$CUSTOM_KERNEL_URL" ]; then
  echo "::error::custom_url requires an explicit kernel URL; refusing keep_base fallback"
  exit 1
fi

FW_DIR="${WORKDIR}/base/firmware"
OUT_FW="${WORKDIR}/final_firmware"
mkdir -p "$OUT_FW"
printf '%s\n' "$KERNEL_MODE" > "$WORKDIR/kernel_mode.txt"
rm -f "$WORKDIR/kernel_release.txt"

# نسخ ملفات الإقلاع المتوفرة من الروم الأساسي
for img in boot.img dtbo.img vbmeta.img vbmeta_system.img vbmeta_vendor.img; do
  if [ -f "${FW_DIR}/${img}" ]; then
    cp -f "${FW_DIR}/${img}" "${OUT_FW}/${img}"
  fi
done

if [ ! -f "${OUT_FW}/boot.img" ]; then
  echo "::error::لم يتم العثور على ملف boot.img داخل الروم الأساسي (Base ROM)!"
  exit 1
fi

BOOT_TMP="$(mktemp -d)"
trap 'sudo rm -rf "$BOOT_TMP"' EXIT

echo "=== [1/4] فك boot.img عبر magiskboot ==="
cp "${OUT_FW}/boot.img" "${BOOT_TMP}/boot.img"
pushd "$BOOT_TMP" >/dev/null
magiskboot unpack -h boot.img

# ==============================================================================
# [2/4] تعديل fstab الموجود فعلياً داخل ramdisk (مثل fstab.default) لدعم EROFS
# تنبيه حرج: لا ننشئ fstab.qcom مختصراً داخل ramdisk إذا لم يكن موجوداً بالأصل
# حتى لا يحجب /vendor/etc/fstab.qcom المسؤول عن ربط /data (FBEv2) و /metadata!
# ==============================================================================
if [ -f "ramdisk.cpio" ]; then
  echo "=== [2/4] تحديث fstab داخل ramdisk.cpio لدعم EROFS بدون حجب /vendor/etc/fstab.qcom ==="
  mkdir -p ramdisk_root
  pushd ramdisk_root >/dev/null
  sudo cpio -idmu < ../ramdisk.cpio 2>/dev/null || true

  sudo python3 - "$WORKDIR/final/logical_partitions.txt" "$DISABLE_DFE" "$SCRIPT_DIR" << 'PYEOF'
import importlib.util, pathlib, sys

parts_file = pathlib.Path(sys.argv[1])
disable_dfe = sys.argv[2].lower() == "true"
script_dir = pathlib.Path(sys.argv[3])
logical_parts = [p.strip() for p in parts_file.read_text().splitlines() if p.strip()]

spec = importlib.util.spec_from_file_location("patch_port", str(script_dir / "patch_port.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

patched_any = False
for fstab in sorted(pathlib.Path(".").rglob("fstab*")):
    if fstab.is_file() and fstab.name != "fstab.emmc":
        mod.patch_fstab_file(fstab, logical_parts, disable_dfe)
        patched_any = True

if not patched_any:
    print("No existing ramdisk fstab; leaving vendor fstab authoritative")
PYEOF

  sudo find . -mindepth 1 | LC_ALL=C sort | sudo cpio -o -H newc > ../ramdisk.cpio.new 2>/dev/null
  popd >/dev/null
  mv -f ramdisk.cpio.new ramdisk.cpio
  sudo rm -rf ramdisk_root
fi

# ==============================================================================
# [3/4] معالجة الكيرنل حسب الوضع المختار
# ==============================================================================
if [ "$KERNEL_MODE" = "custom_url" ] && [ -n "$CUSTOM_KERNEL_URL" ]; then
  echo "=== [3/4] تحميل وحقن الكيرنل المخصص الداعم لـ EROFS + uclamp ==="
  K_DL="${BOOT_TMP}/custom_kernel_pkg"
  aria2c -x8 -s8 --check-certificate=false -d "$BOOT_TMP" -o "custom_kernel_pkg" "$CUSTOM_KERNEL_URL" \
    || curl -fsSL "$CUSTOM_KERNEL_URL" -o "$K_DL"

  if file "$K_DL" | grep -qi "Zip archive"; then
    mkdir -p "${BOOT_TMP}/ak3"
    unzip -q "$K_DL" -d "${BOOT_TMP}/ak3"
    K_IMG=$(find "${BOOT_TMP}/ak3" -maxdepth 3 -type f \( -name "Image.gz-dtb" -o -name "Image.gz" -o -name "Image" -o -name "zImage" \) | head -n 1)
    if [ -n "$K_IMG" ]; then
      cp -f "$K_IMG" "${BOOT_TMP}/kernel"
      echo "  [KERNEL] تم استبدال النواة من AnyKernel3: $(basename "$K_IMG")"
    else
      echo "::error::Custom kernel ZIP has no supported kernel image; refusing Base fallback"
      exit 1
    fi
    AK_DTB=$(find "${BOOT_TMP}/ak3" -maxdepth 3 -type f \( -name "dtb" -o -name "dtb.img" \) | head -n 1)
    if [ -n "$AK_DTB" ] && [ -f "${BOOT_TMP}/dtb" ]; then
      cp -f "$AK_DTB" "${BOOT_TMP}/dtb"
    fi
    AK_DTBO=$(find "${BOOT_TMP}/ak3" -maxdepth 3 -type f -name "dtbo.img" | head -n 1)
    if [ -n "$AK_DTBO" ]; then
      cp -f "$AK_DTBO" "${OUT_FW}/dtbo.img"
      echo "  [KERNEL] تم تحديث dtbo.img من حزمة AnyKernel3"
    fi
  else
    mkdir -p "${BOOT_TMP}/cboot"
    cp "$K_DL" "${BOOT_TMP}/cboot/boot.img"
    pushd "${BOOT_TMP}/cboot" >/dev/null
    magiskboot unpack boot.img
    [ -f kernel ] && cp -f kernel "${BOOT_TMP}/kernel"
    [ -f dtb ] && [ -f "${BOOT_TMP}/dtb" ] && cp -f dtb "${BOOT_TMP}/dtb"
    popd >/dev/null
  fi

elif [ "$KERNEL_MODE" = "compile_phenix_new" ]; then
  echo "=== [3/4] ترجمة كيرنل phen-ix/new (فرع uclamp-erofs: KCAL + uclamp + EROFS + PSI + MEMCG) ==="
  CLANG_DIR="${WORKDIR}/clang12"
  git clone --depth=1 -b lineage-20.0 \
    https://github.com/LineageOS/android_prebuilts_clang_kernel_linux-x86_clang-r416183b.git "$CLANG_DIR"
  export PATH="${CLANG_DIR}/bin:${PATH}"

  K_SRC="${WORKDIR}/kernel_src"
  git clone --depth=1 -b uclamp-erofs https://github.com/phen-ix/new.git "$K_SRC"

  pushd "$K_SRC" >/dev/null
  make O=out ARCH=arm64 CC=clang LLVM=1 LLVM_IAS=1 vendor/xiaomi/miatoll_defconfig

  # تفعيل خيارات Android 16 / HyperOS 3.0 الحرجة (PSI لـ lmkd، MEMCG، ZRAM LZ4، عدادات VM، وعمال EROFS)
  ./scripts/config --file out/.config \
    -e PSI \
    -d PSI_DEFAULT_DISABLED \
    -e CGROUP_SCHED \
    -e FAIR_GROUP_SCHED \
    -e MEMCG \
    -e MEMCG_SWAP \
    -e MEMCG_SWAP_ENABLED \
    -e VM_EVENT_COUNTERS \
    -e UTS_NS \
    -e CRYPTO_LZ4 \
    -e LZ4_COMPRESS \
    -e LZ4_DECOMPRESS \
    -e EXT4_ENCRYPTION \
    -e EROFS_FS \
    -e EROFS_FS_XATTR \
    -e EROFS_FS_POSIX_ACL \
    -e EROFS_FS_SECURITY \
    -e EROFS_FS_ZIP \
    -e EROFS_FS_PCPU_KTHREAD \
    -e EROFS_FS_PCPU_KTHREAD_HIPRI \
    -e IKCONFIG \
    -e IKCONFIG_PROC

  make O=out ARCH=arm64 CC=clang LLVM=1 LLVM_IAS=1 olddefconfig
  make -j"$(nproc)" O=out ARCH=arm64 \
    CC=clang LD=ld.lld AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump STRIP=llvm-strip \
    CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_ARM32=arm-linux-gnueabi- CROSS_COMPILE_COMPAT=arm-linux-gnueabi- \
    Image.gz dtbs
  if [ ! -s out/include/config/kernel.release ]; then
    echo "::error::Missing out/include/config/kernel.release after kernel build"
    exit 1
  fi
  cp out/include/config/kernel.release "$WORKDIR/kernel_release.txt"

  if [ -f "out/arch/arm64/boot/Image.gz" ]; then
    cp -f "out/arch/arm64/boot/Image.gz" "${BOOT_TMP}/kernel"
  elif [ -f "out/arch/arm64/boot/Image" ]; then
    cp -f "out/arch/arm64/boot/Image" "${BOOT_TMP}/kernel"
  else
    echo "❌ فشل العثور على صورة النواة المترجمة Image.gz!"
    exit 1
  fi

  if [ -f "out/arch/arm64/boot/dts/qcom/cust-atoll-ab.dtb" ]; then
    cp -f "out/arch/arm64/boot/dts/qcom/cust-atoll-ab.dtb" "${BOOT_TMP}/dtb"
    echo "  [DTB] تم تحديث cust-atoll-ab.dtb من كيرنل phen-ix/new"
  fi

  # حزم ملفات DTBO الأربعة الخاصة بعائلة miatoll فقط (منع حزم لوحات atoll المرجعية الخاطئة!)
  MIATOLL_DTBOS=(
    "out/arch/arm64/boot/dts/qcom/curtana-atoll-ab-idp-overlay.dtbo"
    "out/arch/arm64/boot/dts/qcom/excalibur-atoll-ab-idp-overlay.dtbo"
    "out/arch/arm64/boot/dts/qcom/gram-atoll-ab-idp-overlay.dtbo"
    "out/arch/arm64/boot/dts/qcom/joyeuse-atoll-ab-idp-overlay.dtbo"
  )
  for dtbo_f in "${MIATOLL_DTBOS[@]}"; do
    if [ ! -f "$dtbo_f" ]; then
      echo "::error::ملف شجرة العتاد الفرعي مفقود: $dtbo_f"
      exit 1
    fi
  done
  python3 scripts/dtc/mkdtboimg.py create "${OUT_FW}/dtbo.img.new" --page_size=4096 "${MIATOLL_DTBOS[@]}"
  mv -f "${OUT_FW}/dtbo.img.new" "${OUT_FW}/dtbo.img"
  echo "  [DTBO] تم توليد dtbo.img الدقيق لـ miatoll فقط (4 لوحات: curtana, excalibur, gram, joyeuse)"

  if [ -f "out/System.map" ]; then
    cp -f "out/System.map" "${WORKDIR}/kernel_System.map"
  fi
  if [ -f "out/.config" ]; then
    cp -f "out/.config" "${WORKDIR}/kernel_dot_config"
  fi
  popd >/dev/null
  rm -rf "$K_SRC" "$CLANG_DIR"

elif [ "$KERNEL_MODE" = "compile_nexusbase" ]; then
  echo "=== [3/4] ترجمة كيرنل phen-ix/nexusbase (uclamp) مع دمج دعم EROFS ==="
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    clang lld llvm gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu gcc-arm-linux-gnueabi ccache

  K_SRC="${WORKDIR}/kernel_src"
  git clone --depth=1 -b kernel.lnx.4.14.r15-rel https://github.com/phen-ix/nexusbase.git "$K_SRC"

  if [ ! -d "${K_SRC}/fs/erofs" ]; then
    echo "  [EROFS] جلب تعريفات نظام ملفات EROFS لنواة 4.14..."
    EROFS_DONOR="$(mktemp -d)"
    git clone --depth=1 --filter=blob:none --sparse -b 16 https://github.com/phen-ix/kernel_xiaomi_sm6250xx.git "$EROFS_DONOR"
    pushd "$EROFS_DONOR" >/dev/null
    git sparse-checkout set fs/erofs include/uapi/linux/erofs_fs.h include/trace/events/erofs.h
    popd >/dev/null
    cp -rf "${EROFS_DONOR}/fs/erofs" "${K_SRC}/fs/erofs"
    [ -f "${EROFS_DONOR}/include/uapi/linux/erofs_fs.h" ] && cp -f "${EROFS_DONOR}/include/uapi/linux/erofs_fs.h" "${K_SRC}/include/uapi/linux/"
    [ -f "${EROFS_DONOR}/include/trace/events/erofs.h" ] && cp -f "${EROFS_DONOR}/include/trace/events/erofs.h" "${K_SRC}/include/trace/events/"
    rm -rf "$EROFS_DONOR"

    grep -q 'fs/erofs/Kconfig' "${K_SRC}/fs/Kconfig" || sed -i '/^endmenu/i source "fs/erofs/Kconfig"' "${K_SRC}/fs/Kconfig"
    grep -q 'CONFIG_EROFS_FS' "${K_SRC}/fs/Makefile" || echo 'obj-$(CONFIG_EROFS_FS) += erofs/' >> "${K_SRC}/fs/Makefile"
  fi

  cat >> "${K_SRC}/arch/arm64/configs/cust_defconfig" << 'KCFG'
CONFIG_EROFS_FS=y
CONFIG_EROFS_FS_XATTR=y
CONFIG_EROFS_FS_POSIX_ACL=y
CONFIG_EROFS_FS_SECURITY=y
CONFIG_EROFS_FS_ZIP=y
CONFIG_UCLAMP_TASK=y
CONFIG_UCLAMP_TASK_GROUP=y
# CONFIG_SCHED_TUNE is not set
KCFG

  pushd "$K_SRC" >/dev/null
  make O=out ARCH=arm64 cust_defconfig
  make -j"$(nproc)" O=out ARCH=arm64 \
    CC=clang LD=ld.lld AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump STRIP=llvm-strip \
    CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_ARM32=arm-linux-gnueabi- \
    Image.gz dtbs
  if [ ! -s out/include/config/kernel.release ]; then
    echo "::error::Missing out/include/config/kernel.release after kernel build"
    exit 1
  fi
  cp out/include/config/kernel.release "$WORKDIR/kernel_release.txt"

  if [ -f "out/arch/arm64/boot/Image.gz" ]; then
    cp -f "out/arch/arm64/boot/Image.gz" "${BOOT_TMP}/kernel"
  elif [ -f "out/arch/arm64/boot/Image" ]; then
    cp -f "out/arch/arm64/boot/Image" "${BOOT_TMP}/kernel"
  fi
  find out/arch/arm64/boot/dts -name "*.dtb" -exec cat {} + > "${BOOT_TMP}/dtb.new" || true
  if [ -s "${BOOT_TMP}/dtb.new" ] && [ -f "${BOOT_TMP}/dtb" ]; then
    mv -f "${BOOT_TMP}/dtb.new" "${BOOT_TMP}/dtb"
  fi
  popd >/dev/null
  rm -rf "$K_SRC"
else
  echo "=== [3/4] الاحتفاظ بنواة الروم الأساسي مع تحديث الـ ramdisk لدعم EROFS ==="
fi

# For external/base kernels derive release from the actual selected unpacked kernel.
if [ ! -s "$WORKDIR/kernel_release.txt" ]; then
  python3 - "$BOOT_TMP/kernel" "$WORKDIR/kernel_release.txt" << 'PYRELEASE'
import gzip, pathlib, re, sys
raw = pathlib.Path(sys.argv[1]).read_bytes()
if raw.startswith(b"\x1f\x8b"):
    raw = gzip.decompress(raw)
match = re.search(rb"Linux version ([^\s\x00]+)", raw)
release = match.group(1).decode("ascii", errors="strict") if match else "UNAVAILABLE"
pathlib.Path(sys.argv[2]).write_text(release + "\n")
print("[KERNEL RELEASE] " + release)
PYRELEASE
fi

echo "=== [4/4] إعادة تجميع boot.img وتعطيل فحص AVB في vbmeta ==="
python3 "$SCRIPT_DIR/boot_header.py" "${BOOT_TMP}/header" "$SELINUX_MODE"
magiskboot repack boot.img new-boot.img
cp -f new-boot.img "${OUT_FW}/boot.img"
popd >/dev/null

# تعطيل فحص AVB/DM-Verity داخل vbmeta.img و vbmeta_system.img (Flags = 0x03 عند الإزاحة 120..123)
python3 - "$OUT_FW" << 'PYAVB'
import pathlib, struct, sys

fw = pathlib.Path(sys.argv[1])
for name in ("vbmeta.img", "vbmeta_system.img", "vbmeta_vendor.img"):
    p = fw / name
    if p.is_file():
        data = bytearray(p.read_bytes())
        if len(data) >= 256 and data[:4] == b"AVB0":
            data[120:124] = struct.pack(">I", 3)
            p.write_bytes(data)
            print(f"  [AVB] تم تعطيل Verity/Verification في {name} (flags=0x03)")
    elif name in ("vbmeta.img", "vbmeta_system.img"):
        buf = bytearray(4096)
        buf[0:4] = b"AVB0"
        buf[4:8] = struct.pack(">I", 1)
        buf[120:124] = struct.pack(">I", 3)
        buf[128:144] = b"avbtool 1.2.0\x00\x00\x00"
        p.write_bytes(buf)
        print(f"  [AVB] تم إنشاء {name} جاهز بمعامل تعطيل التحقق (flags=0x03)")
PYAVB

echo "✅ اكتمل تجهيز الكيرنل وملفات الإقلاع بنجاح!"
