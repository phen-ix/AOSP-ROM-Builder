# 🚀 AOSP-ROM-Builder & Full UI ROM Porter (`joyeuse / miatoll`)

مستودع متكامل على **GitHub Actions** يجمع بين بيئتين احترافيتين لهاتف **Redmi Note 9 Pro (`joyeuse` / عائلة `miatoll`)**:

1. **🔥 مطبخ بورت الواجهات الكاملة (`.github/workflows/port-rom.yml`):**
   يعمل **مجاناً 100% على سيرفرات GitHub العادية** خلال **15–25 دقيقة** لعمل بورت للواجهات الكاملة (**HyperOS 2/3، OxygenOS، ColorOS، RealmeUI، NothingOS، Pixel، أو Lunaris AOSP**) مع التحويل التلقائي الكامل إلى **`EROFS` + `uclamp`** بدلاً من `ext4` و `stune`!
2. **🏗️ بناء سورس AOSP الكامل (`.github/workflows/build.yml`):**
   لبناء رومات الأندرويد من السورس الكامل (`repo sync` + `m bacon`) عند ربط سيرفر `Crave.io` أو `Google Cloud`.

---

## 📱 أولاً: طريقة عمل بورت لواجهة كاملة من الموبايل (`port-rom.yml`)

### ما الذي يقوم به مطبخ البورت تلقائياً؟
- **يدعم كل صيغ الرومات:** يفك تلقائياً ملفات `payload.bin`، أو `super.img(.br/.zst)`، أو `*.new.dat.br`، أو أرشيفات شاومي `.tgz`، سواء كانت `EXT4` أو `EROFS` مع الحفاظ التام على صلاحيات **`SELinux` (`file_contexts` & `fs_config`)**.
- **تحويل كامل إلى `EROFS` (`lz4hc,9`):** يضغط كافة البارتيشنات (`system`, `system_ext`, `product`, `vendor`, `odm`, بالإضافة لبارتيشنات الواجهات مثل `mi_ext` في HyperOS و `my_*` في OxygenOS/ColorOS) لتناسب مساحة `super` في `miatoll` (**8.0 GiB**) ويعدّل ملفات `fstab` في الـ `vendor` والـ `ramdisk`.
- **تحويل جدولة المعالج من `stune` إلى `uclamp`:** يعدّل تلقائياً `powerhint.json` وملفات `init.target.rc` لاستخدام `/dev/cpuctl/*/cpu.uclamp.*` بدلاً من `/dev/stune`.
- **دعم كيرنل `EROFS + uclamp`:** يترجم تلقائياً كيرنل `phen-ix/nexusbase` (الداعم لـ `uclamp`) مع دمج نظام ملفات `EROFS` وحقنه داخل `boot.img` عبر `magiskboot` وتعطيل `AVB/DM-Verity` في `vbmeta.img` (أو يتيح لك حقن أي كيرنل AnyKernel3 خارجي برابط مباشر).
- **ضبط خصائص `joyeuse`:** يفعّل ملفات `joyeuse.xml` (دعم **NFC**، **IR**، البطارية **5020mAh**، البصمة الجانبية، وكثافة الشاشة **440 DPI**) وينتج ملف `.zip` هجين يُفلش مباشرة من **OrangeFox / TWRP** أو **Fastboot** ويرفعه برابط مباشر على **GoFile**!

### خطوات تشغيل البورت من تطبيق GitHub:
1. افتح تبويب **Actions** ثم اختر **Full UI ROM Porter (joyeuse / miatoll - EROFS + uclamp)**.
2. اضغط **Run workflow** واملأ الخانات التالية:
   - **`rom_name`:** اسم البورت (مثال: `HyperOS-2.0` أو `OxygenOS-16` أو `NothingOS-3.0`).
   - **`base_rom_url`:** رابط مباشر لروم خاص بـ `miatoll / joyeuse` **بنفس إصدار الأندرويد** (مثلاً لو البورت Android 15 استخدم Base ROM مبني على Android 15، ولو البورت Android 16 استخدم Base ROM مبني على Android 16 لضمان توافق الـ Vendor مع النظام).
   - **`port_rom_url`:** رابط مباشر لروم الواجهة الكاملة المراد سحبها (يفضل من هاتف بمعالج Snapdragon قريب).
   - **`ui_type`:** اتركه `auto` ليكتشف نوع الواجهة تلقائياً.
   - **`kernel_mode`:** اختر `compile_nexusbase` لترجمة الكيرنل الداعم لـ `EROFS + uclamp`، أو `custom_url` لو معك رابط كيرنل AnyKernel3 جاهز.
3. بعد انتهاء العملية بنجاح (✅)، افتح صفحة البناء وستجد **جدول الملخص ورابط التحميل المباشر من GoFile** في الواجهة أمامك!

---

## 🛠️ ثانياً: بناء سورس AOSP الكامل (`build.yml`)

مخصص لبناء سورس رومات الأندرويد الكامل عبر `.github/workflows/build.yml` عند ربط `Self-Hosted Runner` أو `Crave.io`:

1. افتح `.github/workflows/build.yml` وعدّل قسم **`env:`** بروابط المانيفست وشجرة الجهاز وأمر `LUNCH_COMBO` (مثال لـ Lunaris 3.13: `lineage_miatoll-bp4a-userdebug`).
2. من تبويب **Actions** اختر **AOSP ROM Builder** واضغط **Run workflow**.
