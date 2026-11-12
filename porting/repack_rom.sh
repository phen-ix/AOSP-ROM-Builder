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

echo "=== [1/4] بناء صور البارتيشنات بصيغة EROFS المتوافقة مع كيرنل 4.14 (lz4hc,9 @ 4KB cluster) ==="
mapfile -t PARTS < <(grep -v '^[[:space:]]*$' "${FINAL_DIR}/logical_partitions.txt")
: > "${WORKDIR}/erofs_summaries.txt"

TOTAL_LOGICAL_BYTES=0
LPMAKE_PART_ARGS=()

for part in "${PARTS[@]}"; do
  echo "  -> جاري ضغط بارتيشن [${part}] بصيغة EROFS..."
  mkfs.erofs \
    -zlz4hc,9 \
    -T 1700000000 \
    --ignore-mtime \
    --workers="$(nproc)" \
    --mount-point="/${part}" \
    --fs-config-file="${FINAL_DIR}/config/${part}_fs_config" \
    --file-contexts="${FINAL_DIR}/config/${part}_file_contexts" \
    "${IMG_OUT}/${part}.img" \
    "${FINAL_DIR}/${part}"

  fsck.erofs "${IMG_OUT}/${part}.img" >/dev/null
  {
    echo "=== EROFS [${part}] ==="
    dump.erofs -s "${IMG_OUT}/${part}.img"
  } >> "${WORKDIR}/erofs_summaries.txt"
  echo "     [FSCK] صورة ${part}.img اجتازت فحص سلامة EROFS بنجاح"

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
  echo "::error::حجم البارتيشنات ($TOTAL_LOGICAL_BYTES bytes) تجاوز السعة القصوى لـ super في miatoll ($MIATOLL_QTI_GROUP_SIZE bytes)!"
  exit 1
fi

echo "=== [2/4] تجميع حاوية super.img الموحدة لعائلة joyeuse / miatoll ==="
lpmake \
  --metadata-size 65536 \
  --super-name super \
  --metadata-slots 2 \
  --device "super:${MIATOLL_SUPER_SIZE}" \
  --group "qti_dynamic_partitions:${MIATOLL_QTI_GROUP_SIZE}" \
  "${LPMAKE_PART_ARGS[@]}" \
  --output "${ZIP_STAGE}/super.img"

lpdumps "${ZIP_STAGE}/super.img" > "${WORKDIR}/super_lpdump.txt"
rm -rf "$IMG_OUT"

echo "=== [3/4] ضغط super.img بصيغة Zstandard السريعة (super.img.zst < 4 GiB) ==="
zstd -3 -T0 --rm "${ZIP_STAGE}/super.img" -o "${ZIP_STAGE}/super.img.zst"
ls -lh "${ZIP_STAGE}/super.img.zst"

# نسخ ملفات الإقلاع والـ vbmeta
for fw in boot.img dtbo.img vbmeta.img vbmeta_system.img vbmeta_vendor.img; do
  if [ -f "${FW_DIR}/${fw}" ]; then
    cp -f "${FW_DIR}/${fw}" "${ZIP_STAGE}/${fw}"
  fi
done

echo "=== [4/4] تجهيز مثبت الريكفري الثابت ARM64 (Static ELF64 update-binary) وحزم الـ ZIP ==="
if [ ! -x "/usr/local/bin/miatoll-update-binary-arm64" ]; then
  echo "::error::مثبت الريكفري المترجم /usr/local/bin/miatoll-update-binary-arm64 غير موجود!"
  exit 1
fi

cp -f "/usr/local/bin/miatoll-update-binary-arm64" "${ZIP_STAGE}/META-INF/com/google/android/update-binary"
chmod 0755 "${ZIP_STAGE}/META-INF/com/google/android/update-binary"
printf '#MAGISK\n' > "${ZIP_STAGE}/META-INF/com/google/android/updater-script"

cat > "${ZIP_STAGE}/flash_fastboot_linux.sh" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "Flashing joyeuse / miatoll HyperOS 3.0 Port ROM via Fastboot..."
if [ -f super.img.zst ] && [ ! -f super.img ]; then
  echo "Decompressing super.img.zst..."
  zstd -d super.img.zst -o super.img
fi
[ -f vbmeta.img ] && fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
[ -f vbmeta_system.img ] && fastboot --disable-verity --disable-verification flash vbmeta_system vbmeta_system.img
[ -f dtbo.img ] && fastboot flash dtbo dtbo.img
[ -f boot.img ] && fastboot flash boot boot.img
fastboot flash super super.img
echo "Done! Run 'fastboot -w' if clean flashing, then 'fastboot reboot'."
EOF
chmod +x "${ZIP_STAGE}/flash_fastboot_linux.sh"

cat > "${ZIP_STAGE}/flash_fastboot_windows.bat" << 'EOF'
@echo off
echo Flashing joyeuse / miatoll HyperOS 3.0 Port ROM via Fastboot...
if exist super.img.zst if not exist super.img (
  echo Decompressing super.img.zst...
  zstd -d super.img.zst -o super.img
)
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
# تخزين مباشر بدون ضغط مزدوج (-0) لأن super.img.zst و boot.img مضغوطة مسبقاً ولضمان البث المباشر في الريكفري
zip -0 -r "$FINAL_ZIP_PATH" .
popd >/dev/null

rm -rf "$ZIP_STAGE"
ls -lh "$FINAL_ZIP_PATH"
echo "✅ الملف النهائي جاهز للتفليش: $FINAL_ZIP_PATH"
