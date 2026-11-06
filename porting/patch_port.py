#!/usr/bin/env python3
"""
OEM Full-UI & Custom ROM Porting Patcher for Xiaomi Redmi Note 9 Pro (joyeuse / miatoll)
Features:
  1. Merges Base (miatoll vendor/odm/overlays) + Port (system/system_ext/product/mi_ext/my_*)
  2. Patches fstab.qcom / fstab.atoll for EROFS + OEM logical partitions + strips AVB/DM-Verity
  3. Converts PowerHAL (powerhint.json) & init (*.rc) from legacy schedtune (stune) to uclamp
  4. Adapts OEM UIs (HyperOS / OxygenOS-ColorOS / NothingOS / AOSP-Lunaris) for joyeuse (DPI 440, NFC, RescueParty off)
  5. Smart safe debloat to fit miatoll's 8.0 GiB (8,589,934,592 bytes) super partition
  6. Auto-reconciles fs_config & file_contexts (with regex escaping) so SELinux never bootloops
"""

import argparse
import json
import os
import re
import shutil
import stat
from pathlib import Path

SAFE_DEBLOAT_KEYWORDS = [
    # Chinese / Regional Bloat & Ads
    "BaiduIME", "SogouInput", "IflytekIME", "MiShop", "Youpin", "GameCenter",
    "MiFinance", "Wallet", "HybridPlatform", "AnalyticsCore", "MSA", "msa",
    "mab", "Updater", "SystemUpdate", "Ota", "Weibo", "Bilibili", "Alipay",
    "Taobao", "JD", "Meituan", "BrowserVideo", "MiCredit", "PaymentService",
    "HeytapBrowser", "HeytapMarket", "KeKeMarket", "NearmeGameCenter", "OppoShop",
    # Xiaomi Global Bloat, Ads & Crashing Services
    "Joyose", "MiLink", "Lyra", "uceShimService", "CatchLog", "MiBugReport",
    "MIService", "MIUIYellowPage", "MITSMClient", "MIUIMiPicks", "MiPicks",
    "MIBrowser", "BrowserGlobal", "MIUIMusic", "MIUIVideo", "MiVideo",
    "GlobalWPSLITE", "WPSOffice", "MISTORE_OVERSEA", "MICOMMUNITY", "PocoBBS",
    "MIUIHealth", "SmartHome", "MIDrop", "MiDrop", "PlayAutoInstallStub",
    # Heavy Wallpapers & Lockscreen Magazine
    "SuperWallpaper", "MiGalleryLockScreen", "FashionGallery", "WallpaperCarousel",
    # Voice / Speech AI Bloat (Selected for removal; core MiSound/AudioFX stays protected)
    "SpeechServicesByGoogle", "GoogleTTS", "HotwordEnrollment", "AiAsstVision",
    "VoiceAssist", "VoiceTrigger", "MiBrainSpeech",
    # Secondary Downloadable Google Apps (Core GMS/Play Store/WebView/Gboard stay protected)
    "YouTube", "YTMusic", "Maps", "Gmail2", "Drive", "Photos", "Videos",
    "Tachyon", "Meet", "GoogleOne", "GoogleNews", "Magazines", "Podcasts",
    "TrichromeChrome", "Chromecast", "AndroidAuto", "Gearhead",
    # Third-Party & European Carrier Bloat
    "Netflix", "Facebook", "facebook-", "TikTok", "Instagram", "Spotify",
    "Booking", "LinkedIn", "Snapchat", "AliExpress", "MicrosoftOneDrive",
    "LinkToWindows", "AmazonAppManager", "amazon.appmanager", "ebay.carrier",
    "ChiliCompanion", "AppBox", "AppCloud", "AppCenter", "sfrjeux", "bygIgnite",
    "myapps", "OrangeManualSelector",
]

# Essential system, GMS, WebView, Input, Audio & Utility packages that must NEVER be deleted
PROTECTED_CORE_KEYWORDS = [
    "GmsCore", "PrebuiltGmsCore", "GoogleServicesFramework", "Phonesky",
    "GoogleLoginService", "GoogleBackupTransport", "GoogleContactsSyncAdapter",
    "GoogleCalendarSyncAdapter", "GoogleOneTimeInitializer", "GooglePartnerSetup",
    "SetupWizard", "GoogleRestore", "AndroidPlatformServices",
    "GoogleWebView", "WebViewGoogle", "TrichromeLibrary",
    "LatinIMEGoogle", "Gboard", "Velvet",
    "GoogleDialer", "GoogleContacts", "PrebuiltBugle", "Messages",
    "MiSound", "Misound", "AudioFX", "MusicFX", "Dolby", "Dirac",
    "MiuiCamera", "XMRemoteController", "MIUIScreenRecorder", "Calculator",
    "DeskClock", "SoundRecorder", "Weather", "MIUICompass", "MiuiScanner",
    "NQNfcNci", "SecureElement", "SecurityCenter", "MIUISecurity",
    "MiuiHome", "MIUISystemUI", "Settings", "PermissionController",
    "PackageInstaller", "DocumentsUI", "DownloadProvider", "MediaProvider",
    "TelephonyProvider", "TeleService", "Telecom",
]

JOYEUSE_HYPEROS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<features>
    <bool name="is_xiaomi">true</bool>
    <bool name="is_redmi">true</bool>
    <integer name="cpu_max_freq">230</integer>
    <integer name="battery_capacity_typ">5020</integer>
    <bool name="support_nfc">true</bool>
    <bool name="support_se_route">true</bool>
    <bool name="support_ir">true</bool>
    <bool name="support_fod">false</bool>
    <bool name="is_BackFingerprint">false</bool>
    <bool name="support_fp_nav_event_name_list">true</bool>
    <integer name="finger_wakeup_type">2</integer>
    <bool name="support_screen_light">true</bool>
    <bool name="support_autobrightness_optimize">true</bool>
    <bool name="support_aod">false</bool>
    <bool name="is_full_screen">true</bool>
</features>
"""

UCLAMP_INIT_RC_BLOCK = """
# ============================================================================
# Auto-injected uclamp configuration for joyeuse / miatoll (replaces stune)
# ============================================================================
on property:sys.boot_completed=1
    write /proc/sys/kernel/sched_util_clamp_min_rt_default 0
    write /dev/cpuctl/top-app/cpu.uclamp.max max
    write /dev/cpuctl/top-app/cpu.uclamp.min 15
    write /dev/cpuctl/top-app/cpu.uclamp.latency_sensitive 1
    write /dev/cpuctl/foreground/cpu.uclamp.max 85
    write /dev/cpuctl/foreground/cpu.uclamp.min 0
    write /dev/cpuctl/background/cpu.uclamp.max 50
    write /dev/cpuctl/system-background/cpu.uclamp.max 50
    write /dev/cpuctl/dex2oat/cpu.uclamp.max 60
"""


def detect_ui_type(final_dir: Path, forced: str) -> str:
    if forced and forced != "auto":
        return forced
    if (final_dir / "mi_ext").is_dir():
        return "hyperos"
    if (final_dir / "my_product").is_dir():
        return "oplus"
    for bp in final_dir.glob("*/**/build.prop"):
        txt = bp.read_text(encoding="utf-8", errors="ignore")
        if "ro.mi.os.version" in txt or "ro.miui.ui.version" in txt:
            return "hyperos"
        if "ro.build.version.oplusrom" in txt or "ro.oplus.version" in txt:
            return "oplus"
        if "ro.nothing.version" in txt:
            return "nothingos"
    return "aosp"


def patch_fstab_file(fstab_path: Path, logical_parts: list[str], disable_dfe: bool):
    if not fstab_path.is_file():
        return
    print(f"  [FSTAB] تحديث {fstab_path} لدعم EROFS وإزالة AVB...", flush=True)
    lines = fstab_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    kept_lines = []

    logical_set = set(logical_parts)
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept_lines.append(line)
            continue
        cols = stripped.split()
        if len(cols) >= 5:
            src, mnt, fstype, mnt_flags, fs_flags = cols[0], cols[1], cols[2], cols[3], cols[4]
            if src in logical_set or "logical" in fs_flags:
                # سنكتب أسطر البارتيشنات المنطقية الموحدة بالأعلى بصيغة EROFS + EXT4 بدون AVB
                continue
            # إزالة قيود avb/verity من أي بارتيشن آخر
            fs_flags = re.sub(r",?avb(?:=[^,\s]+)?", "", fs_flags)
            fs_flags = re.sub(r",?avb_keys=[^,\s]+", "", fs_flags)
            fs_flags = re.sub(r",?verify(?:=[^,\s]+)?", "", fs_flags)
            if disable_dfe and mnt == "/data":
                fs_flags = re.sub(r",?fileencryption=[^,\s]+", "", fs_flags)
                fs_flags = re.sub(r",?metadata_encryption=[^,\s]+", "", fs_flags)
                fs_flags = re.sub(r",?keydirectory=[^,\s]+", "", fs_flags)
            cols[4] = fs_flags.strip(",") or "defaults"
            kept_lines.append("\t".join(cols))
        else:
            kept_lines.append(line)

    logical_header = [
        "# Auto-generated EROFS + EXT4 logical partitions for joyeuse/miatoll (AVB disabled)"
    ]
    for part in logical_parts:
        mnt_point = "/system" if part == "system" else f"/{part}"
        logical_header.append(f"{part}\t{mnt_point}\terofs\tro\twait,logical,first_stage_mount")
        logical_header.append(f"{part}\t{mnt_point}\text4\tro,barrier=1,discard\twait,logical,first_stage_mount")

    new_content = "\n".join(logical_header + [""] + kept_lines) + "\n"
    fstab_path.write_text(new_content, encoding="utf-8")


def patch_uclamp_vendor(vendor_dir: Path):
    print("=== تحويل إعدادات الأداء في Vendor من stune إلى uclamp ===", flush=True)
    # 1. Patch powerhint.json across vendor/etc/** (including etc/perf/powerhint.json on QTI)
    for ph_path in sorted(set(vendor_dir.rglob("powerhint.json"))):
        try:
            data = json.loads(ph_path.read_text(encoding="utf-8", errors="ignore"))
            nodes = data.get("Nodes", [])
            updated_nodes = []
            has_latency_node = False
            for node in nodes:
                path = node.get("Path", "")
                if "schedtune.boost" in path:
                    node["Path"] = "/dev/cpuctl/top-app/cpu.uclamp.min"
                    new_vals = []
                    for v in node.get("Values", []):
                        try:
                            iv = int(float(v))
                            if iv == 1:
                                new_vals.append("25")
                            elif iv < 0:
                                new_vals.append("0")
                            else:
                                new_vals.append(str(min(iv, 100)))
                        except ValueError:
                            new_vals.append("0")
                    node["Values"] = new_vals or ["25", "0"]
                    updated_nodes.append(node)
                elif "schedtune.prefer_idle" in path:
                    node["Path"] = "/dev/cpuctl/top-app/cpu.uclamp.latency_sensitive"
                    has_latency_node = True
                    updated_nodes.append(node)
                elif "/dev/stune/" in path:
                    # Drop unsupported legacy stune nodes
                    continue
                else:
                    updated_nodes.append(node)

            if not has_latency_node:
                updated_nodes.append({
                    "Name": "TAUclampLatencySensitive",
                    "Path": "/dev/cpuctl/top-app/cpu.uclamp.latency_sensitive",
                    "Values": ["1", "0"],
                    "DefaultIndex": 0,
                    "ResetOnInit": True,
                })
            data["Nodes"] = updated_nodes
            ph_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"  [UCLAMP] تم تحويل {ph_path.name} إلى مسارات /dev/cpuctl/*/cpu.uclamp.*", flush=True)
        except Exception as e:
            print(f"  [WARN] تعذر تعديل {ph_path}: {e}")

    # 2. Patch init *.rc files
    rc_files = list(vendor_dir.glob("etc/init/**/*.rc"))
    injected_block = False
    for rc in rc_files:
        txt = rc.read_text(encoding="utf-8", errors="ignore")
        changed = False
        new_lines = []
        for line in txt.splitlines():
            if "/dev/stune" in line or "schedtune" in line:
                new_lines.append(f"    # [disabled for uclamp] {line.strip()}")
                changed = True
            else:
                new_lines.append(line)
        if rc.name == "init.target.rc" and "cpu.uclamp.min" not in txt:
            new_lines.append(UCLAMP_INIT_RC_BLOCK)
            injected_block = True
            changed = True
        if changed:
            rc.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    if not injected_block:
        uclamp_rc = vendor_dir / "etc" / "init" / "init.miatoll.uclamp.rc"
        uclamp_rc.parent.mkdir(parents=True, exist_ok=True)
        uclamp_rc.write_text(UCLAMP_INIT_RC_BLOCK, encoding="utf-8")


def set_or_update_props(prop_file: Path, updates: dict[str, str]):
    if not prop_file.is_file():
        return
    lines = prop_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(line)
            continue
        key = s.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    prop_file.write_text("\n".join(out) + "\n", encoding="utf-8")


def copy_hardware_overlays(base_product: Path, final_product: Path):
    base_ov = base_product / "overlay"
    final_ov = final_product / "overlay"
    if not base_ov.is_dir() or not final_ov.is_dir():
        return
    # DevicesOverlay.apk is signed with Xiaomi's official key and carries LCD brightness curves,
    # cutout dimensions, and side-fingerprint coordinates for joyeuse/miatoll.
    force_replace = {"devicesoverlay.apk"}
    hw_patterns = ("miatoll", "joyeuse", "atoll", "sm6250", "cutout", "notch", "wifi", "tethering", "carrierconfig")
    copied = 0
    for item in base_ov.iterdir():
        lname = item.name.lower()
        dest = final_ov / item.name
        if lname in force_replace:
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.copy2(item, dest, follow_symlinks=False)
            copied += 1
            print(f"  [OVERLAY] تم استبدال {item.name} بنسخة joyeuse/miatoll الرسمية (سطوع شاشة LCD + البصمة الجانبية)", flush=True)
        elif any(p in lname for p in hw_patterns) and not dest.exists():
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=True)
            else:
                shutil.copy2(item, dest, follow_symlinks=False)
            copied += 1
    print(f"  [OVERLAY] إجمالي ملفات الـ Overlay المنقولة لعتاد joyeuse/miatoll = {copied}", flush=True)


def transplant_miatoll_hal_fixes(base_dir: Path, final_dir: Path):
    """Transplant proven SM6250/miatoll fixes from Base ROM:
       1. USB ConfigFS init scripts when vendor.usb.use_gadget_hal=0 (fixes USB MTP & ADB)
       2. Linux 4.14 cgroup v1 + uclamp task_profiles.json (fixes Android 15 Cached App Freezer)
       3. Qualcomm OMX libstagefright*.so when Base & Port share identical Android SDK version (fixes HW video)
    """
    final_sys = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")
    base_sys = (base_dir / "system" / "system") if (base_dir / "system" / "system" / "build.prop").is_file() else (base_dir / "system")
    if not base_sys.is_dir() or not final_sys.is_dir():
        return

    vendor_bp = (final_dir / "vendor" / "build.prop").read_text(encoding="utf-8", errors="ignore") if (final_dir / "vendor" / "build.prop").is_file() else ""

    # 1. USB ConfigFS init scripts for SM6250 (use_gadget_hal=0)
    if "vendor.usb.use_gadget_hal=0" in vendor_bp:
        for usb_rc in ("init.usb.rc", "init.usb.configfs.rc"):
            src = base_sys / "etc" / "init" / "hw" / usb_rc
            dst = final_sys / "etc" / "init" / "hw" / usb_rc
            if src.is_file() and dst.parent.is_dir():
                shutil.copy2(src, dst)
                print(f"  [USB-FIX] تم نقل {usb_rc} المتوافق مع SM6250 لتشغيل MTP و ADB عبر السلك", flush=True)

    # 2. Linux 4.14 cgroup v1 freezer + uclamp task_profiles.json
    b_tp = base_sys / "etc" / "task_profiles.json"
    f_tp = final_sys / "etc" / "task_profiles.json"
    if b_tp.is_file() and "UClampMin" in b_tp.read_text(encoding="utf-8", errors="ignore"):
        shutil.copy2(b_tp, f_tp)
        print("  [CGROUP-FIX] تم نقل task_profiles.json المتوافق مع نواة 4.14 (uclamp + cgroup v1 freezer)", flush=True)

    # 3. Qualcomm OMX libstagefright*.so video playback/encoding fix (only when SDK matches, e.g. 35 == 35)
    def get_sdk(sys_root: Path) -> str:
        bp = sys_root / "build.prop"
        if bp.is_file():
            for ln in bp.read_text(encoding="utf-8", errors="ignore").splitlines():
                if ln.startswith("ro.build.version.sdk="):
                    return ln.split("=", 1)[1].strip()
        return ""

    b_sdk, f_sdk = get_sdk(base_sys), get_sdk(final_sys)
    if b_sdk and b_sdk == f_sdk:
        copied_sf = 0
        for lib_sub in ("lib", "lib64"):
            b_ldir = base_sys / lib_sub
            f_ldir = final_sys / lib_sub
            if not b_ldir.is_dir() or not f_ldir.is_dir():
                continue
            for sf in b_ldir.glob("libstagefright*.so"):
                if sf.is_file() and not sf.is_symlink():
                    shutil.copy2(sf, f_ldir / sf.name)
                    copied_sf += 1
        if copied_sf:
            print(f"  [VIDEO-FIX] تم زرع {copied_sf} مكتبة libstagefright متوافقة مع OMX لمعالج SM6250 (SDK {f_sdk})", flush=True)

        # 4. Camera MiaFrame v10 algoup/mianode JNI fix from MiatollCave 2026-11-06
        copied_cam_jni = 0
        for lib_sub in ("lib", "lib64"):
            for cam_so in ("libcamera_algoup_jni.xiaomi.so", "libcamera_mianode_jni.xiaomi.so"):
                src_so = base_dir / "system_ext" / lib_sub / cam_so
                dst_so = final_dir / "system_ext" / lib_sub / cam_so
                if src_so.is_file() and dst_so.parent.is_dir():
                    shutil.copy2(src_so, dst_so)
                    copied_cam_jni += 1
        if copied_cam_jni:
            print(f"  [CAMERA-FIX] تم زرع {copied_cam_jni} مكتبة algoup/mianode JNI متوافقة مع MiaFrame v10 لكاميرا miatoll", flush=True)

    # 5. Disable crashing CollectRunningTasksService on boot (xd_port_fixes.xml)
    syscfg_dir = final_dir / "system_ext" / "etc" / "sysconfig"
    if syscfg_dir.is_dir():
        (syscfg_dir / "xd_port_fixes.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<config>\n'
            '    <component-override package="com.xiaomi.xmsf">\n'
            '        <component class="com.xiaomi.xmsf.eventtracking.CollectRunningTasksService" enabled="false" />\n'
            '    </component-override>\n'
            '</config>\n',
            encoding="utf-8",
        )
        print("  [BOOT-FIX] تم حقن xd_port_fixes.xml لمنع انهيار CollectRunningTasksService عند الإقلاع", flush=True)

    # 6. Merge product/pangu/system -> product for NFC (NQNfcNci) if pangu split exists
    pangu_sys = final_dir / "product" / "pangu" / "system"
    if pangu_sys.is_dir():
        for sub in pangu_sys.iterdir():
            dest_sub = final_dir / "product" / sub.name
            if sub.is_dir():
                shutil.copytree(sub, dest_sub, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(sub, dest_sub, follow_symlinks=False)
        shutil.rmtree(final_dir / "product" / "pangu", ignore_errors=True)
        print("  [NFC-FIX] تم دمج product/pangu/system داخل product لتفعيل NFC وتقليل التكرار", flush=True)


def transplant_vndk_and_selinux_mappings(base_dir: Path, final_dir: Path):
    """Transplant missing VNDK APEX (e.g. v30 for SM6250) and SELinux CIL mapping files from Base ROM."""
    vendor_dir = final_dir / "vendor"
    vndk_ver = "30"
    for vp in (vendor_dir / "build.prop", vendor_dir / "default.prop"):
        if vp.is_file():
            for line in vp.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("ro.vndk.version="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        vndk_ver = val
                        break

    final_sys_root = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")
    base_sys_root = (base_dir / "system" / "system") if (base_dir / "system" / "system" / "build.prop").is_file() else (base_dir / "system")

    # 1. Check & transplant com.android.vndk.v<VER>
    target_apex_dir = final_dir / "system_ext" / "apex"
    if not target_apex_dir.is_dir():
        target_apex_dir = final_sys_root / "system_ext" / "apex"
    target_apex_dir.mkdir(parents=True, exist_ok=True)

    has_vndk = any(
        f"vndk.v{vndk_ver}" in p.name or f"vndk.{vndk_ver}" in p.name
        for d in (final_dir / "system_ext" / "apex", final_sys_root / "system_ext" / "apex", final_sys_root / "apex")
        if d.is_dir()
        for p in d.iterdir()
    )
    if not has_vndk:
        for src_d in (base_dir / "system_ext" / "apex", base_sys_root / "system_ext" / "apex", base_sys_root / "apex"):
            if not src_d.is_dir():
                continue
            for cand in src_d.iterdir():
                if f"vndk.v{vndk_ver}" in cand.name or f"vndk.{vndk_ver}" in cand.name:
                    dest = target_apex_dir / cand.name
                    if cand.is_dir():
                        shutil.copytree(cand, dest, symlinks=True)
                    else:
                        shutil.copy2(cand, dest, follow_symlinks=False)
                    # Also carry over exact SELinux file_contexts & fs_config entries (e.g. vndk_sp_file) from Base
                    for suffix in ("fs_config", "file_contexts"):
                        b_cfg = base_dir / "config" / f"system_ext_{suffix}"
                        f_cfg = final_dir / "config" / f"system_ext_{suffix}"
                        if b_cfg.is_file() and f_cfg.is_file():
                            existing = set(f_cfg.read_text(encoding="utf-8", errors="ignore").splitlines())
                            extra = [
                                ln for ln in b_cfg.read_text(encoding="utf-8", errors="ignore").splitlines()
                                if (cand.name in ln or re.escape(cand.name) in ln) and ln not in existing
                            ]
                            if extra:
                                with open(f_cfg, "a", encoding="utf-8") as out_f:
                                    out_f.write("\n".join(extra) + "\n")
                    print(f"  [VNDK] تم زرع حزمة التوافق المفقودة {cand.name} (+ سياقات SELinux) من الروم الأساسي لمنع الـ Bootloop!", flush=True)
                    has_vndk = True
                    break
            if has_vndk:
                break

    # 2. Check & transplant SELinux mapping/<vers>.cil across system, system_ext, product
    vers_txt = vendor_dir / "etc" / "selinux" / "plat_sepolicy_vers.txt"
    vers = vers_txt.read_text(encoding="utf-8", errors="ignore").strip() if vers_txt.is_file() else f"{vndk_ver}.0"
    pairs = [
        ("system", base_sys_root / "etc" / "selinux" / "mapping", final_sys_root / "etc" / "selinux" / "mapping"),
        ("system_ext", base_dir / "system_ext" / "etc" / "selinux" / "mapping", final_dir / "system_ext" / "etc" / "selinux" / "mapping"),
        ("product", base_dir / "product" / "etc" / "selinux" / "mapping", final_dir / "product" / "etc" / "selinux" / "mapping"),
    ]
    for label, b_map, f_map in pairs:
        if not b_map.is_dir():
            continue
        f_map.mkdir(parents=True, exist_ok=True)
        for cand in b_map.glob(f"{vers}*"):
            dest = f_map / cand.name
            if not dest.exists() and cand.is_file():
                shutil.copy2(cand, dest)
                print(f"  [SELINUX-MAP] تم زرع ملف ربط السياسة {label}/mapping/{cand.name} من الروم الأساسي", flush=True)


def run_smart_debloat(final_dir: Path):
    removed_bytes = 0
    removed_count = 0

    def is_protected(name: str) -> bool:
        low = name.lower()
        # Never confuse Chrome browser (TrichromeChrome) with TrichromeLibrary/WebView
        if "trichromechrome" in low or low in ("chrome", "chromestub"):
            return False
        return any(pk.lower() in low for pk in PROTECTED_CORE_KEYWORDS)

    def matches_bloat(name: str, rel_dir: str) -> bool:
        if is_protected(name):
            return False
        low = name.lower()
        if "data-app" in rel_dir or "del-app" in rel_dir:
            return True
        if low in ("chrome", "chromestub"):
            return True
        return any(k.lower() in low for k in SAFE_DEBLOAT_KEYWORDS)

    search_dirs = [
        "system/system/app", "system/system/priv-app", "system/app", "system/priv-app",
        "product/app", "product/priv-app", "product/data-app",
        "system_ext/app", "system_ext/priv-app",
        "mi_ext/product/app", "mi_ext/product/priv-app", "mi_ext/product/data-app",
        "my_stock/app", "my_stock/del-app", "my_heytap/app", "my_preload/app",
    ]
    for rel in search_dirs:
        d = final_dir / rel
        if not d.is_dir():
            continue
        for entry in list(d.iterdir()):
            if matches_bloat(entry.name, rel):
                size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file() and not f.is_symlink()) if entry.is_dir() else entry.stat().st_size
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed_bytes += size
                removed_count += 1

    # Remove carrier bloat APKs inside opcust (Vodafone, Orange, Amazon, eBay, etc.)
    for opcust_rel in ("product/opcust", "mi_ext/product/opcust"):
        op_dir = final_dir / opcust_rel
        if op_dir.is_dir():
            for apk in list(op_dir.rglob("*.apk")):
                if not is_protected(apk.name):
                    removed_bytes += apk.stat().st_size
                    removed_count += 1
                    apk_parent = apk.parent
                    apk.unlink(missing_ok=True)
                    shutil.rmtree(apk_parent / "oat", ignore_errors=True)

    # Remove heavy video / animated / editor wallpaper packs while keeping lightweight static defaults
    heavy_wp_bytes = 0
    for wp_rel in ("product/media/wallpaper", "mi_ext/product/media/wallpaper"):
        wp_dir = final_dir / wp_rel
        if not wp_dir.is_dir():
            continue
        for sub_name in ("wallpaper_video", "sysui_editor_wallpaper_group", "super_wallpaper"):
            sub_d = wp_dir / sub_name
            if sub_d.is_dir():
                sz = sum(f.stat().st_size for f in sub_d.rglob("*") if f.is_file() and not f.is_symlink())
                shutil.rmtree(sub_d, ignore_errors=True)
                heavy_wp_bytes += sz
        for wf in list(wp_dir.rglob("*")):
            if not wf.is_file() or wf.is_symlink():
                continue
            wlow = wf.name.lower()
            if "default" in wlow:
                continue
            if wlow.endswith((".mp4", ".webm", ".mkv", ".mov")) or wf.stat().st_size > 4 * 1024 * 1024:
                heavy_wp_bytes += wf.stat().st_size
                wf.unlink(missing_ok=True)

    removed_bytes += heavy_wp_bytes
    print(
        f"  [DEBLOAT] تم حذف {removed_count} حزمة زائدة + الخلفيات الثقيلة ({heavy_wp_bytes / (1024*1024):.1f} MB) "
        f"| إجمالي التوفير = {removed_bytes / (1024*1024):.1f} MB (مع حماية خدمات Google الأساسية و MiSound و WebView)",
        flush=True,
    )


def reconcile_selinux_configs(part_dir: Path, config_dir: Path, part_name: str):
    """Ensure every new/modified file in part_dir exists in <part>_fs_config and <part>_file_contexts."""
    fs_cfg = config_dir / f"{part_name}_fs_config"
    fc_cfg = config_dir / f"{part_name}_file_contexts"

    known_fs = set()
    if fs_cfg.is_file():
        for line in fs_cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                known_fs.add(line.split()[0].rstrip("/"))

    known_fc_map = {}
    if fc_cfg.is_file():
        for line in fc_cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                raw_pat = parts[0]
                unescaped = re.sub(r"\\(.)", r"\1", raw_pat).rstrip("/")
                known_fc_map[unescaped] = parts[-1]

    default_ctx = f"u:object_r:{'vendor_file' if part_name in ('vendor', 'odm') else 'system_file'}:s0"
    new_fs_lines = []
    new_fc_lines = []

    for dirpath, dirnames, filenames in os.walk(part_dir, followlinks=False):
        for name in sorted(dirnames + filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(part_dir).as_posix()
            fs_key = f"{part_name}/{rel}"
            fc_key = f"/{part_name}/{rel}"

            st = os.lstat(full)
            is_dir = stat.S_ISDIR(st.st_mode)
            is_lnk = stat.S_ISLNK(st.st_mode)

            if fs_key not in known_fs:
                if is_lnk:
                    uid, gid, mode = 0, 0, 0o777
                elif is_dir:
                    uid, gid, mode = 0, (2000 if "/bin" in fc_key else 0), 0o755
                elif "/bin/" in fc_key or "/xbin/" in fc_key or rel.endswith(".sh"):
                    uid, gid, mode = 0, 2000, 0o755
                else:
                    uid, gid, mode = 0, 0, 0o644
                new_fs_lines.append(f"{fs_key} {uid} {gid} {mode:04o}")
                known_fs.add(fs_key)

            if fc_key not in known_fc_map:
                # Inherit SELinux context from nearest parent directory
                parent = str(Path(fc_key).parent)
                inherited_ctx = default_ctx
                while parent and parent != "/":
                    if parent in known_fc_map:
                        inherited_ctx = known_fc_map[parent]
                        break
                    if f"{parent}(/.*)?" in known_fc_map:
                        inherited_ctx = known_fc_map[f"{parent}(/.*)?"]
                        break
                    parent = str(Path(parent).parent)
                if "/etc/init/" in fc_key and fc_key.endswith(".rc"):
                    inherited_ctx = "u:object_r:vendor_configs_file:s0" if part_name == "vendor" else "u:object_r:system_file:s0"
                elif "/etc/selinux/" in fc_key and fc_key.endswith(".cil"):
                    inherited_ctx = "u:object_r:sepolicy_file:s0"
                new_fc_lines.append(f"/{re.escape(part_name)}/{re.escape(rel)} {inherited_ctx}")
                known_fc_map[fc_key] = inherited_ctx

    if new_fs_lines:
        with open(fs_cfg, "a", encoding="utf-8") as f:
            f.write("\n".join(new_fs_lines) + "\n")
    if new_fc_lines:
        with open(fc_cfg, "a", encoding="utf-8") as f:
            f.write("\n".join(new_fc_lines) + "\n")
    print(f"  [SELINUX] {part_name}: تمت مزامنة {len(new_fs_lines)} مسار جديد في fs_config/file_contexts", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--ui-type", default="auto", choices=["auto", "hyperos", "oplus", "nothingos", "aosp"])
    parser.add_argument("--enable-debloat", default="true")
    parser.add_argument("--enable-adb-debug", default="true")
    parser.add_argument("--disable-dfe", default="false")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    base_dir = workdir / "base"
    port_dir = workdir / "port"
    final_dir = workdir / "final"
    final_cfg = final_dir / "config"
    final_dir.mkdir(parents=True, exist_ok=True)
    final_cfg.mkdir(parents=True, exist_ok=True)

    # 1. Move Base hardware partitions (vendor, odm)
    for bpart in ("vendor", "odm"):
        src = base_dir / bpart
        if src.is_dir():
            shutil.move(str(src), str(final_dir / bpart))
            for cfg in (base_dir / "config").glob(f"{bpart}_*"):
                shutil.copy2(cfg, final_cfg / cfg.name)

    # 2. Move Port system & UI partitions
    port_parts = [
        p.strip() for p in (port_dir / "extracted_partitions.txt").read_text(encoding="utf-8").splitlines()
        if p.strip()
    ]
    for ppart in port_parts:
        # Never take lower-half SoC/board partitions (vendor, odm, *_dlkm) from Port ROM;
        # on miatoll/joyeuse, /odm symlinks to /vendor/odm with SM6250 + NFC configs.
        if ppart in ("vendor", "odm", "vendor_dlkm", "odm_dlkm"):
            print(f"  [SKIP] تجاهل بارتيشن العتاد [{ppart}] من روم البورت للحفاظ على توافق معالج joyeuse/SM6250", flush=True)
            continue
        src = port_dir / ppart
        if src.is_dir():
            shutil.move(str(src), str(final_dir / ppart))
            for cfg in (port_dir / "config").glob(f"{ppart}_*"):
                shutil.copy2(cfg, final_cfg / cfg.name)

    # Ordered list of all final logical partitions
    preferred_order = [
        "system", "system_ext", "product", "vendor", "odm", "mi_ext",
        "my_product", "my_engineering", "my_stock", "my_heytap",
        "my_carrier", "my_region", "my_bigball", "my_manifest",
    ]
    final_parts = [p for p in preferred_order if (final_dir / p).is_dir()]
    (final_dir / "logical_partitions.txt").write_text("\n".join(final_parts) + "\n", encoding="utf-8")

    ui_type = detect_ui_type(final_dir, args.ui_type)
    print(f"=== نوع الواجهة المكتشفة للبورت: [{ui_type.upper()}] | البارتيشنات: {final_parts} ===", flush=True)

    # 3. Copy hardware overlays + transplant missing VNDK APEX, SELinux mappings & SM6250 HAL fixes from Base
    if (base_dir / "product").is_dir() and (final_dir / "product").is_dir():
        copy_hardware_overlays(base_dir / "product", final_dir / "product")
    transplant_vndk_and_selinux_mappings(base_dir, final_dir)
    transplant_miatoll_hal_fixes(base_dir, final_dir)

    # 4. HyperOS joyeuse/miatoll device_features XMLs
    if ui_type == "hyperos" and (final_dir / "product").is_dir():
        df_dir = final_dir / "product" / "etc" / "device_features"
        df_dir.mkdir(parents=True, exist_ok=True)
        base_df = base_dir / "product" / "etc" / "device_features"
        for dev_codename in ("joyeuse", "miatoll", "curtana", "excalibur", "gram"):
            cand = base_df / f"{dev_codename}.xml"
            if not cand.is_file():
                cand = base_df / "joyeuse.xml"
            xml_content = cand.read_text(encoding="utf-8", errors="ignore") if cand.is_file() else JOYEUSE_HYPEROS_XML
            (df_dir / f"{dev_codename}.xml").write_text(xml_content, encoding="utf-8")
        print("  [HYPEROS] تم تجهيز ملفات خصائص عائلة joyeuse/miatoll الكاملة (بصمة جانبية + NFC + IR + بطارية 5020mAh)", flush=True)

    # 5. Free base_dir & port_dir (keep base/firmware for boot/dtbo/vbmeta)
    for bcleanup in ("product", "system_ext", "system", "odm"):
        shutil.rmtree(base_dir / bcleanup, ignore_errors=True)
    shutil.rmtree(port_dir, ignore_errors=True)

    # 6. Patch fstab in vendor & odm for EROFS + logical partitions + no AVB
    disable_dfe = args.disable_dfe.lower() == "true"
    for fstab in list((final_dir / "vendor").glob("etc/fstab*")) + list((final_dir / "odm").glob("etc/fstab*")):
        patch_fstab_file(fstab, final_parts, disable_dfe)

    # 7. Convert stune -> uclamp in vendor
    if (final_dir / "vendor").is_dir():
        patch_uclamp_vendor(final_dir / "vendor")

    # 8. Patch build.prop files for joyeuse / miatoll compatibility (side fingerprint, 440 DPI, 60Hz LCD, I/O & Preread fixes)
    common_props = {
        "ro.sf.lcd_density": "440",
        "persist.sys.disable_rescue": "true",
        "ro.Incremental.enable": "false",
        "ro.hardware.fp.fod": "false",
        "ro.hardware.fp.sideCap": "true",
        "ro.vendor.display.default_fps": "60",
        # Fix 5-second iorapd/preread crash loop (/dev/iorap_dev absent on SM6250) & debug monitors
        "persist.sys.stability.PrereadEnable": "false",
        "persist.sys.app_resurrection.enable": "false",
        "persist.sys.debug.app.mtbf_test": "false",
        "persist.sys.perfdebug.monitor.enable": "0",
        # Prevent flagship 12GB RAM background app pre-starting & UFS I/O thrashing on 6GB joyeuse
        "ro.config.low_ram.threshold_gb": "0",
        "persist.sys.app_dexfile_preload.enable": "false",
        "persist.sys.spc.proc_restart_enable": "false",
        "persist.sys.preload.enable": "false",
        "persist.sys.precache.enable": "false",
        "persist.sys.prestart.proc": "false",
        "persist.sys.prestart.feedback.enable": "false",
        "persist.sys.performance.appLaunchPreload.enable": "false",
        "persist.sys.smartcache.enable": "false",
        "persist.sys.art_startup_class_preload.enable": "false",
    }
    if args.enable_adb_debug.lower() == "true":
        common_props.update({
            "ro.adb.secure": "0",
            "ro.secure": "0",
            "ro.debuggable": "1",
            "persist.sys.usb.config": "mtp,adb",
        })

    sys_bp = final_dir / "system" / "system" / "build.prop"
    if not sys_bp.is_file():
        sys_bp = final_dir / "system" / "build.prop"
    set_or_update_props(sys_bp, {
        **common_props,
        "ro.product.system.model": "Redmi Note 9 Pro",
        "ro.product.model": "Redmi Note 9 Pro",
    })

    prod_bp = final_dir / "product" / "etc" / "build.prop"
    if not prod_bp.is_file():
        prod_bp = final_dir / "product" / "build.prop"
    set_or_update_props(prod_bp, {
        **common_props,
        "ro.product.product.model": "Redmi Note 9 Pro",
        "ro.product.marketname": "Redmi Note 9 Pro",
    })

    # 9. Smart safe debloat
    if args.enable_debloat.lower() == "true":
        run_smart_debloat(final_dir)

    # 10. Pre-compile & auto-heal SELinux split CIL policies via secilc before sealing fs_config
    if shutil.which("secilc"):
        import importlib.util
        sim_spec = importlib.util.spec_from_file_location("simulate_boot_check", str(Path(__file__).parent / "simulate_boot_check.py"))
        sim_mod = importlib.util.module_from_spec(sim_spec)
        sim_spec.loader.exec_module(sim_mod)
        ok_cil, cil_msg = sim_mod.heal_and_compile_selinux(final_dir)
        print(f"  [SECILC] {cil_msg}", flush=True)
        if not ok_cil:
            raise RuntimeError(cil_msg)

    # 11. Reconcile fs_config & file_contexts for every partition
    for part in final_parts:
        reconcile_selinux_configs(final_dir / part, final_cfg, part)

    print("✅ اكتملت تعديلات البورت + EROFS + uclamp بنجاح!", flush=True)


if __name__ == "__main__":
    main()
