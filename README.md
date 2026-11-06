# 🚀 AOSP-ROM-Builder

مشروع تلقائي مبني على **GitHub Actions** لبناء رومات الأندرويد المفتوحة المصدر (**AOSP** والرومات المبنية عليها مثل **LineageOS** و **PixelExperience** و **crDroid**) سحابياً على سيرفرات Ubuntu.

---

## ✨ مميزات سير العمل (`build.yml`)

1. **تفريغ المساحة التلقائي (Disk Cleanup):** يحذف الحزم المثبتة مسبقاً غير الضرورية (`.NET`، `Android SDK`، صور `Docker`، وأدوات التطوير الضخمة) لتوفير ما يقارب **100 إلى 115 جيجابايت** من المساحة الحرة قبل بدء المزامنة.
2. **إعداد البيئة الكامل:** يثبت تلقائياً كافة حزم بناء AOSP الأساسية على Ubuntu (`git`، `repo`، `openjdk-17-jdk`، `python3`، `gnupg`، `build-essential`، `ccache`، ومكتبات 32/64-bit).
3. **مزامنة خفيفة وسريعة (Shallow Sync):** يستخدم الأمر المحسّن `--depth=1 --no-tags --no-clone-bundle` لتقليص حجم السورس وتجنب امتلاء القرص.
4. **تشغيل يدوي مرن (`workflow_dispatch`):** يمكنك تثبيت روابط جهازك داخل ملف الإعدادات، أو تمريرها مباشرة من واجهة GitHub قبل بدء البناء بدون تعديل الكود.

---

## 🛠️ الخطوة الأولى: أين أضع روابط السورس كود لجهازي؟

افتح الملف الموجود في المسار التالي داخل المستودع:

```text
.github/workflows/build.yml
```

ابحث عن قسم **`env:`** في أعلى الملف (حوالي السطر 43)، وستجد الخانات الفارغة الجاهزة للتعبئة:

```yaml
env:
  # 1. رابط المانيفست (سورس الروم) والفرع المطلوب
  MANIFEST_URL: ""            # ضع هنا رابط مستودع الروم
  MANIFEST_BRANCH: ""         # ضع هنا اسم الفرع (Branch)

  # 2. شجرة الجهاز (Device Tree)
  DEVICE_TREE_URL: ""         # ضع هنا رابط شجرة جهازك
  DEVICE_TREE_BRANCH: ""      # اختياري: فرع شجرة الجهاز
  DEVICE_TREE_PATH: ""        # مسار وضع الشجرة، مثال: device/xiaomi/lavender

  # 3. شجرة الـ Vendor والـ Kernel (اختياري - اتركها فارغة إن كانت تُجلب تلقائياً)
  VENDOR_TREE_URL: ""
  VENDOR_TREE_BRANCH: ""
  VENDOR_TREE_PATH: ""

  KERNEL_TREE_URL: ""
  KERNEL_TREE_BRANCH: ""
  KERNEL_TREE_PATH: ""

  # 4. إعدادات أمر الـ Lunch وهدف البناء
  LUNCH_COMBO: ""             # مثال: lineage_lavender-userdebug
  BUILD_TARGET: "bacon"       # مثال: bacon أو otapackage
```

### 📝 جدول توضيحي للمتغيرات مع مثال عملي

| المتغير | الوظيفة | مثال عملي (LineageOS لجهاز `lavender`) |
| :--- | :--- | :--- |
| `MANIFEST_URL` | رابط المانيفست الخاص بالروم | `https://github.com/LineageOS/android.git` |
| `MANIFEST_BRANCH` | فرع إصدار الروم | `lineage-21.0` |
| `DEVICE_TREE_URL` | رابط شجرة الجهاز (Device Tree) | `https://github.com/LineageOS/android_device_xiaomi_lavender.git` |
| `DEVICE_TREE_PATH` | المسار داخل السورس (`device/الشركة/الاسم_الرمزي`) | `device/xiaomi/lavender` |
| `VENDOR_TREE_URL` | *(اختياري)* رابط ملفات الـ Vendor | `https://github.com/TheMuppets/proprietary_vendor_xiaomi_lavender.git` |
| `VENDOR_TREE_PATH` | *(اختياري)* مسار الـ Vendor | `vendor/xiaomi/lavender` |
| `KERNEL_TREE_URL` | *(اختياري)* رابط نواة الجهاز (Kernel) | `https://github.com/LineageOS/android_kernel_xiaomi_sdm660.git` |
| `KERNEL_TREE_PATH` | *(اختياري)* مسار النواة | `kernel/xiaomi/sdm660` |
| `LUNCH_COMBO` | صيغة اختيار الجهاز ونوع البناء | `lineage_lavender-userdebug` |
| `BUILD_TARGET` | أمر إخراج ملف الروم النهائي | `bacon` (أو `otapackage` لـ AOSP الخام) |

بعد وضع روابطك بين علامتي التنصيص `""`، اضغط على زر **Commit changes** لحفظ التعديلات.

---

## ▶️ الخطوة الثانية: كيف أشغّل البناء يدوياً عبر `workflow_dispatch`؟

بعد حفظ الروابط (أو حتى إذا أردت إدخالها مباشرة بدون تعديل الملف)، اتبع هذه الخطوات البسيطة:

1. ادخل إلى مستودع **`AOSP-ROM-Builder`** في حسابك على GitHub.
2. اضغط على تبويب **Actions** في الشريط العلوي.
   *(ملاحظة: إذا ظهر لك زر أخضر يقول `I understand my workflows, go ahead and enable them` اضغط عليه لتفعيلها).*
3. من القائمة الجانبية اليسرى، اضغط على اسم سير العمل **AOSP ROM Builder**.
4. على اليمين، ستجد زر **Run workflow** (قائمة منسدلة) — اضغط عليه:
   - تأكد من اختيار الفرع **`main`**.
   - إذا كنت قد وضعت روابطك مسبقاً داخل ملف `build.yml`، اترك الحقول النصية فارغة كما هي.
   - **(اختياري):** يمكنك تفعيل خيار **`clean_repo_after_sync`** لتوفير **25–35 جيجابايت إضافية** عبر حذف مجلد `.repo` فور انتهاء المزامنة وقبل بدء الترجمة.
   - **(اختياري):** يمكنك تفعيل خيار **`upload_to_gofile`** لرفع ملف الروم `.zip` تلقائياً إلى GoFile وطباعة رابط التحميل المباشر داخل سجل البناء (Log).
5. اضغط على الزر الأخضر **Run workflow** لبدء العملية!

---

## 📦 الخطوة الثالثة: تحميل الروم بعد انتهاء البناء

1. بعد اكتمال جميع الخطوات بنجاح (علامة ✅ خضراء)، افتح صفحة عملية البناء المكتملة.
2. انزل إلى أسفل الصفحة عند قسم **Artifacts**.
3. اضغط على **`AOSP-ROM-Output`** لتحميل ملف الروم النهائي وصور الإقلاع (`boot.img` / `recovery.img`).
   - أو افتح خطوة **رفع ملف الروم إلى GoFile** (إن كنت قد فعّلتها) لنسخ رابط التحميل المباشر.

---

## 💡 نصائح تقنية هامة لنجاح البناء

- **اجعل المستودع عاماً (Public):** يمنحك GitHub دقائق بناء **مجانية وغير محدودة** للمستودعات العامة، بينما تستهلك المستودعات الخاصة (Private) من الباقة الشهرية المحدودة وتخضع لحد أقصى صغير لمساحة الـ Artifacts.
- **حدود سيرفرات GitHub المجانية:** السيرفر المجاني القياسي يوفر حوالي **105–115 جيجابايت** بعد التنظيف، وذاكرة **16GB RAM**، ومهلة زمنية أقصاها **6 ساعات**. هذا مناسب جداً لبناء إصدارات أندرويد الخفيفة والمتوسطة وصور GSI؛ أما لرومات Android 14/15/16 الضخمة التي تتطلب أكثر من 200GB و32GB RAM، فيمكنك ربط سيرفر خاص (**Self-Hosted Runner**) وتغيير السطر `runs-on: ubuntu-24.04` إلى `runs-on: self-hosted`.
