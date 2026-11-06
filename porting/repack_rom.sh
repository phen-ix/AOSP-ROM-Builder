#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${1:?Missing WORKDIR}"
ROM_LABEL="${2:-OEM-Port}"

FINAL_DIR="${WORKDIR}/final"
FW_DIR="${WORKDIR}/final_firmware"
IMG_OUT="${WORKDIR}/erofs_images"
ZIP_STAGE="${WORKDIR}/zip_stage"
OUT_DIR="${WORKDIR}/output"

mkdir -p "$IMG_OUT" "$ZIP_STAGE/META-INF/com/google/android" "$OUT_DIR"

# الأبعاد الرسمية الدقيقة لبارتيشن super في عائلة joyeuse / miatoll (8.0 GiB)
MIATOLL_SUPER_SIZE=8589934592
MIATOLL_QTI_GROUP_SIZE=8585740288

echo "=== [1/3] بناء صور البارتيشنات بصيغة EROFS المضغوطة (lz4hc,9) ==="
mapfile -t PARTS < <(grep -v '^[[:space:]]*$' "${FINAL_DIR}/logical_partitions.txt")

TOTAL_LOGICAL_BYTES=0
LPMAKE_PART_ARGS=()

for part in "${PARTS[@]}"; do
  echo "  -> جاري ضغط بارتيشن [${part}] بصيغة EROFS..."
  mkfs.erofs \
    -zlz4hc,9 \
    -C262144 \
    -T 1700000000 \
    --ignore-mtime \
    --workers="$(nproc)" \
    --mount-point="/${part}" \
    --fs-config-file="${FINAL_DIR}/config/${part}_fs_config" \
    --file-contexts="${FINAL_DIR}/config/${part}_file_contexts" \
    "${IMG_OUT}/${part}.img" \
    "${FINAL_DIR}/${part}"

  # حذف المجلد المفكوك فور تحويله لتوفير مساحة القرص
  rm -rf "${FINAL_DIR:?}/${part}"

  P_SIZE=$(stat -c%s "${IMG_OUT}/${part}.img")
  TOTAL_LOGICAL_BYTES=$((TOTAL_LOGICAL_BYTES + P_SIZE))
  echo "     حجم ${part}.img (EROFS) = $((P_SIZE / 1024 / 1024)) MB"

  LPMAKE_PART_ARGS+=(
    --partition "${part}:readonly:${P_SIZE}:qti_dynamic_partitions"
    --image "${part}=${IMG_OUT}/${part}.img"
  )
done

echo "=== إجمالي حجم البارتيشنات المنطقية = $((TOTAL_LOGICAL_BYTES / 1024 / 1024)) MB من أصل $((MIATOLL_QTI_GROUP_SIZE / 1024 / 1024)) MB ==="
if [ "$TOTAL_LOGICAL_BYTES" -ge "$MIATOLL_QTI_GROUP_SIZE" ]; then
  echo "::error::حجم البارتيشنات ($TOTAL_LOGICAL_BYTES bytes) تجاوز السعة القصوى لـ super في miatoll ($MIATOLL_QTI_GROUP_SIZE bytes)! يرجى تفعيل خيار Debloat."
  exit 1
fi

echo "=== [2/3] تجميع حاوية super.img الموحدة لعائلة joyeuse / miatoll ==="
lpmake \
  --metadata-size 65536 \
  --super-name super \
  --metadata-slots 2 \
  --device "super:${MIATOLL_SUPER_SIZE}" \
  --group "qti_dynamic_partitions:${MIATOLL_QTI_GROUP_SIZE}" \
  "${LPMAKE_PART_ARGS[@]}" \
  --output "${ZIP_STAGE}/super.img"

# حذف الصور المنفردة بعد دمجها في super.img لتوفير 5GB+
rm -rf "$IMG_OUT"

# نسخ ملفات الإقلاع والـ vbmeta
for fw in boot.img dtbo.img vbmeta.img vbmeta_system.img vbmeta_vendor.img; do
  if [ -f "${FW_DIR}/${fw}" ]; then
    cp -f "${FW_DIR}/${fw}" "${ZIP_STAGE}/${fw}"
  fi
done

echo "=== [3/3] إنشاء ملف الـ ZIP الهجين (يعمل من الريكفري OrangeFox/TWRP ومن Fastboot) ==="

cat > "${ZIP_STAGE}/META-INF/com/google/android/updater-script" << 'EOF'
#MAGISK
EOF

cat > "${ZIP_STAGE}/META-INF/com/google/android/update-binary" << 'EOF'
#!/sbin/sh
OUTFD="$2"
ZIPFILE="$3"

ui_print() {
  echo "ui_print $1" > "/proc/self/fd/$OUTFD"
  echo "ui_print" > "/proc/self/fd/$OUTFD"
}

find_block() {
  local name="$1"
  for base in /dev/block/bootdevice/by-name /dev/block/by-name /dev/block/platform/soc/1d84000.ufshc/by-name; do
    if [ -e "${base}/${name}" ]; then
      echo "${base}/${name}"
      return 0
    fi
  done
  return 1
}

flash_entry() {
  local file="$1"
  local part="$2"
  if unzip -l "$ZIPFILE" "$file" >/dev/null 2>&1; then
    local blk
    blk=$(find_block "$part") || {
      ui_print "! تحذير: لم يتم العثور على مسار البارتيشن $part"
      return 1
    }
    ui_print "- جاري تفليش ${part}..."
    unzip -p "$ZIPFILE" "$file" > "$blk"
  fi
}

ui_print "=========================================="
ui_print "   joyeuse / miatoll EROFS + uclamp Port  "
ui_print "=========================================="

HW=$(getprop ro.boot.hwname)
DEV=$(getprop ro.product.device)
ui_print "- الجهاز المكتشف: ${HW:-$DEV}"

flash_entry "vbmeta.img" "vbmeta"
flash_entry "vbmeta_system.img" "vbmeta_system"
flash_entry "vbmeta_vendor.img" "vbmeta_vendor" || true
flash_entry "dtbo.img" "dtbo"
flash_entry "boot.img" "boot"

ui_print "- جاري تفليش حاوية النظام super.img (EROFS)..."
ui_print "  (قد يستغرق ذلك 1-2 دقيقة، يرجى الانتظار)"
flash_entry "super.img" "super"

ui_print "=========================================="
ui_print "✅ اكتمل تفليش الروم بنجاح!"
ui_print "ℹ️ عند التحويل من روم مختلف يُنصح بعمل Format Data"
ui_print "=========================================="
exit 0
EOF
chmod +x "${ZIP_STAGE}/META-INF/com/google/android/update-binary"

# إضافة سكربتات التفليش عبر Fastboot للكمبيوتر أيضاً (اختياري للمستخدم)
cat > "${ZIP_STAGE}/flash_fastboot_linux.sh" << 'EOF'
#!/usr/bin/env bash
set -e
echo "Flashing joyeuse / miatoll Port ROM via Fastboot..."
[ -f vbmeta.img ] && fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
[ -f vbmeta_system.img ] && fastboot --disable-verity --disable-verification flash vbmeta_system vbmeta_system.img
[ -f dtbo.img ] && fastboot flash dtbo dtbo.img
[ -f boot.img ] && fastboot flash boot boot.img
fastboot flash super super.img
echo "Done! Format data (fastboot -w) if clean flashing, then run: fastboot reboot"
EOF
chmod +x "${ZIP_STAGE}/flash_fastboot_linux.sh"

cat > "${ZIP_STAGE}/flash_fastboot_windows.bat" << 'EOF'
@echo off
echo Flashing joyeuse / miatoll Port ROM via Fastboot...
if exist vbmeta.img fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
if exist vbmeta_system.img fastboot --disable-verity --disable-verification flash vbmeta_system vbmeta_system.img
if exist dtbo.img fastboot flash dtbo dtbo.img
if exist boot.img fastboot flash boot boot.img
fastboot flash super super.img
echo Done! Run "fastboot -w" if clean flashing, then "fastboot reboot".
pause
EOF

DATE_TAG="$(date -u +%Y%m%d-%H%M)"
SAFE_LABEL="$(echo "$ROM_LABEL" | tr ' /:' '---' | tr -cd 'A-Za-z0-9._-')"
FINAL_ZIP_NAME="${SAFE_LABEL}-joyeuse-miatoll-EROFS-uclamp-${DATE_TAG}.zip"
FINAL_ZIP_PATH="${OUT_DIR}/${FINAL_ZIP_NAME}"

pushd "$ZIP_STAGE" >/dev/null
# استخدام مستوى ضغط سريع -1 لأن بارتيشنات EROFS مضغوطة مسبقاً بـ lz4hc,9
zip -1 -r "$FINAL_ZIP_PATH" .
popd >/dev/null

rm -rf "$ZIP_STAGE"
ls -lh "$FINAL_ZIP_PATH"
echo "✅ الملف النهائي جاهز للتفليش: $FINAL_ZIP_PATH"
