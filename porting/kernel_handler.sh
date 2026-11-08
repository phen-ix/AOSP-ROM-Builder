#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${1:?Missing WORKDIR}"
KERNEL_MODE="${2:-keep_base}"
CUSTOM_KERNEL_URL="${3:-}"
DISABLE_DFE="${4:-false}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FW_DIR="${WORKDIR}/base/firmware"
OUT_FW="${WORKDIR}/final_firmware"
mkdir -p "$OUT_FW"

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
# [2/4] تعديل fstab داخل ramdisk لدعم EROFS والبارتيشنات المنطقية الجديدة وإلغاء AVB
# ==============================================================================
if [ -f "ramdisk.cpio" ]; then
  echo "=== [2/4] تحديث fstab داخل ramdisk.cpio لدعم EROFS والواجهات الكاملة ==="
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

for fstab in pathlib.Path(".").rglob("fstab*"):
    if fstab.is_file():
        mod.patch_fstab_file(fstab, logical_parts, disable_dfe)

# Ensure fstab.qcom exists for androidboot.hardware=qcom first-stage mount
if pathlib.Path("fstab.default").is_file() and not pathlib.Path("fstab.qcom").exists():
    import shutil
    shutil.copy2("fstab.default", "fstab.qcom")
    print("  [FSTAB] تم إنشاء fstab.qcom داخل ramdisk لضمان إقلاع المرحلة الأولى لـ qcom", flush=True)
if pathlib.Path("fstab.qcom").is_file():
    fsr = pathlib.Path("first_stage_ramdisk")
    fsr.mkdir(exist_ok=True)
    if not (fsr / "fstab.qcom").exists():
        import shutil
        shutil.copy2("fstab.qcom", fsr / "fstab.qcom")
PYEOF

  sudo find . -mindepth 1 | LC_ALL=C sort | sudo cpio -o -H newc > ../ramdisk.cpio.new 2>/dev/null
  popd >/dev/null
  mv -f ramdisk.cpio.new ramdisk.cpio
  sudo rm -rf ramdisk_root
fi

# ==============================================================================
# [3/4] معالجة الكيرنل حسب الوضع المختار (keep_base / custom_url / compile_nexusbase)
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
    # تعامل معه كملف boot.img مباشر
    mkdir -p "${BOOT_TMP}/cboot"
    cp "$K_DL" "${BOOT_TMP}/cboot/boot.img"
    pushd "${BOOT_TMP}/cboot" >/dev/null
    magiskboot unpack boot.img
    [ -f kernel ] && cp -f kernel "${BOOT_TMP}/kernel"
    [ -f dtb ] && [ -f "${BOOT_TMP}/dtb" ] && cp -f dtb "${BOOT_TMP}/dtb"
    popd >/dev/null
  fi

elif [ "$KERNEL_MODE" = "compile_phenix_new" ]; then
  echo "=== [3/4] ترجمة كيرنل phen-ix/new (فرع uclamp-erofs: KCAL + uclamp + EROFS) ==="
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu gcc-arm-linux-gnueabi \
    bc flex bison libssl-dev libelf-dev python3 ccache

  CLANG_DIR="${WORKDIR}/clang12"
  git clone --depth=1 -b lineage-20.0 \
    https://github.com/LineageOS/android_prebuilts_clang_kernel_linux-x86_clang-r416183b.git "$CLANG_DIR"
  export PATH="${CLANG_DIR}/bin:${PATH}"

  K_SRC="${WORKDIR}/kernel_src"
  git clone --depth=1 -b uclamp-erofs https://github.com/phen-ix/new.git "$K_SRC"

  pushd "$K_SRC" >/dev/null
  make O=out ARCH=arm64 CC=clang LLVM=1 LLVM_IAS=1 vendor/xiaomi/miatoll_defconfig
  make -j"$(nproc)" O=out ARCH=arm64 \
    CC=clang LD=ld.lld AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump STRIP=llvm-strip \
    CROSS_COMPILE=aarch64-linux-gnu- CROSS_COMPILE_ARM32=arm-linux-gnueabi- CROSS_COMPILE_COMPAT=arm-linux-gnueabi- \
    Image.gz dtbs

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

  if compgen -G "out/arch/arm64/boot/dts/qcom/*.dtbo" >/dev/null && [ -f "scripts/dtc/mkdtboimg.py" ]; then
    python3 scripts/dtc/mkdtboimg.py create "${OUT_FW}/dtbo.img.new" --page_size=4096 out/arch/arm64/boot/dts/qcom/*.dtbo && \
      mv -f "${OUT_FW}/dtbo.img.new" "${OUT_FW}/dtbo.img" && \
      echo "  [DTBO] تم توليد dtbo.img المخصص لـ joyeuse/miatoll بنجاح" || true
  fi
  if [ -f "out/System.map" ]; then
    cp -f "out/System.map" "${WORKDIR}/kernel_System.map"
  fi
  popd >/dev/null
  rm -rf "$K_SRC" "$CLANG_DIR"

elif [ "$KERNEL_MODE" = "compile_nexusbase" ]; then
  echo "=== [3/4] ترجمة كيرنل phen-ix/nexusbase (uclamp) مع دمج دعم EROFS ==="
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    clang lld llvm gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu gcc-arm-linux-gnueabi ccache

  K_SRC="${WORKDIR}/kernel_src"
  git clone --depth=1 -b kernel.lnx.4.14.r15-rel https://github.com/phen-ix/nexusbase.git "$K_SRC"

  # دمج fs/erofs من مستودعك kernel_xiaomi_sm6250xx إذا لم يكن موجوداً في nexusbase
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

echo "=== [4/4] إعادة تجميع boot.img وتعطيل فحص AVB في vbmeta ==="
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
        # إنشاء صورة AVB0 معطلة التحقق (4KB) إذا لم تكن مرفقة بالروم الأساسي
        buf = bytearray(4096)
        buf[0:4] = b"AVB0"
        buf[4:8] = struct.pack(">I", 1)
        buf[120:124] = struct.pack(">I", 3)
        buf[128:144] = b"avbtool 1.2.0\x00\x00\x00"
        p.write_bytes(buf)
        print(f"  [AVB] تم إنشاء {name} جاهز بمعامل تعطيل التحقق (flags=0x03)")
PYAVB

echo "✅ اكتمل تجهيز الكيرنل وملفات الإقلاع بنجاح!"
