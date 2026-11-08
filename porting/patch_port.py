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
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

SAFE_DEBLOAT_KEYWORDS = [
    # Chinese / Regional Bloat & Ads
    "BaiduIME", "SogouInput", "IflytekIME", "MiShop", "Youpin", "GameCenter",
    "MiFinance", "Wallet", "HybridPlatform", "AnalyticsCore", "MSA", "msa",
    "mab", "Updater", "SystemUpdate", "Ota", "Weibo", "Bilibili", "Alipay",
    "Taobao", "JD", "Meituan", "BrowserVideo", "MiCredit", "PaymentService",
    "HeytapBrowser", "HeytapMarket", "KeKeMarket", "NearmeGameCenter", "OppoShop",
    # Xiaomi Global Bloat, Ads & Crashing Services
    "Joyose", "MiLink", "Lyra", "uceShimService", "NtnSatApp", "CatchLog", "MiBugReport",
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


def get_partition_sdk(sys_root: Path) -> int:
    bp = sys_root / "build.prop"
    if bp.is_file():
        for ln in bp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("ro.build.version.sdk="):
                try:
                    return int(ln.split("=", 1)[1].strip())
                except ValueError:
                    return 0
    return 0


def is_64bit_only_system(sys_root: Path) -> bool:
    bp = sys_root / "build.prop"
    if not bp.is_file():
        return False
    txt = bp.read_text(encoding="utf-8", errors="ignore")
    for ln in txt.splitlines():
        if ln.startswith("ro.system.product.cpu.abilist32="):
            return ln.split("=", 1)[1].strip() == ""
    return not os.path.lexists(sys_root / "bin" / "linker") and os.path.lexists(sys_root / "bin" / "linker64")


def collect_exported_elf_symbols(final_dir: Path, is_64: bool = True) -> tuple[set[str], set[str]]:
    """Return (available_sonames, exported_symbol_names) across final system/system_ext/product/apex libs."""
    readelf = shutil.which("readelf")
    sys_root = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")
    lib_sub = "lib64" if is_64 else "lib"
    search_dirs = [
        sys_root / lib_sub,
        sys_root / lib_sub / "bootstrap",
        final_dir / "system_ext" / lib_sub,
        final_dir / "product" / lib_sub,
        final_dir / "vendor" / lib_sub,
    ]
    for apex_root in (sys_root / "apex", final_dir / "system_ext" / "apex"):
        if apex_root.is_dir():
            for sub in apex_root.iterdir():
                if sub.is_dir() and (sub / lib_sub).is_dir():
                    search_dirs.append(sub / lib_sub)

    sonames: set[str] = {
        "libc.so", "libm.so", "libdl.so", "libdl_android.so", "ld-android.so",
        "libnativehelper.so", "libdexfile.so", "libart.so", "libicu.so",
    }
    exported: set[str] = set()
    if not readelf:
        return sonames, exported

    for d in search_dirs:
        if not d.is_dir():
            continue
        for so in d.rglob("*.so"):
            if not so.is_file():
                continue
            sonames.add(so.name)
            res = subprocess.run([readelf, "--dyn-syms", "-W", str(so)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            for ln in (res.stdout or "").splitlines():
                cols = ln.split()
                if len(cols) >= 8 and cols[6] != "UND":
                    sym = cols[7].split("@")[0]
                    if sym:
                        exported.add(sym)
    return sonames, exported


def elf_und_symbols_resolvable(cand_so: Path, avail_sonames: set[str], exported_syms: set[str]) -> tuple[bool, list[str]]:
    """Verify every DT_NEEDED library and non-Bionic UND symbol in cand_so resolves cleanly in final."""
    readelf = shutil.which("readelf")
    if not readelf or not cand_so.is_file():
        return False, ["readelf unavailable or candidate missing"]

    d_res = subprocess.run([readelf, "-d", "-W", str(cand_so)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    needed = re.findall(r"\(NEEDED\)\s+Shared library:\s+\[([^\]]+)\]", d_res.stdout or "")
    missing_libs = [lib for lib in needed if lib not in avail_sonames]
    if missing_libs:
        return False, [f"missing DT_NEEDED: {missing_libs}"]

    s_res = subprocess.run([readelf, "--dyn-syms", "-W", str(cand_so)], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    bionic_tags = ("@LIBC", "@LIBM", "@LIBDL")
    unresolved = []
    for ln in (s_res.stdout or "").splitlines():
        cols = ln.split()
        if len(cols) >= 8 and cols[6] == "UND" and cols[4] != "WEAK":
            raw_sym = cols[7]
            if any(t in raw_sym for t in bionic_tags):
                continue
            sym = raw_sym.split("@")[0]
            if sym and sym not in exported_syms:
                unresolved.append(sym)
    return len(unresolved) == 0, unresolved


def extract_file_from_apex(apex_path: Path, inner_rel_path: str) -> bytes | None:
    """Read a file (e.g. 'bin/netbpfload') from an unpacked APEX dir, .apex, or .capex WITHOUT modifying the APEX."""
    rel_clean = inner_rel_path.lstrip("/")
    if apex_path.is_dir():
        cand = apex_path / rel_clean
        return cand.read_bytes() if cand.is_file() else None
    if not apex_path.is_file() or not zipfile.is_zipfile(apex_path):
        return None

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        outer_zip = apex_path
        with zipfile.ZipFile(outer_zip, "r") as zf:
            names = zf.namelist()
            if "original_apex" in names:
                zf.extract("original_apex", td_path)
                outer_zip = td_path / "original_apex"

        with zipfile.ZipFile(outer_zip, "r") as zf:
            if "apex_payload.img" not in zf.namelist():
                return None
            zf.extract("apex_payload.img", td_path)

        payload_img = td_path / "apex_payload.img"
        with open(payload_img, "rb") as pf:
            pf.seek(1024)
            is_erofs = pf.read(4) == b"\xe2\xe1\xf5\xe0"

        if is_erofs:
            if shutil.which("dump.erofs"):
                res = subprocess.run(
                    ["dump.erofs", "--cat", f"--path=/{rel_clean}", str(payload_img)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0 and res.stdout:
                    return res.stdout
            if shutil.which("extract.erofs"):
                ex_dir = td_path / "ex"
                subprocess.run(
                    ["extract.erofs", "-i", str(payload_img), "-o", str(ex_dir), "-x"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                cand = ex_dir / rel_clean
                if cand.is_file():
                    return cand.read_bytes()
        elif shutil.which("debugfs") or Path("/usr/sbin/debugfs").is_file():
            debugfs_bin = shutil.which("debugfs") or "/usr/sbin/debugfs"
            out_f = td_path / "extracted_bin"
            subprocess.run(
                [debugfs_bin, "-R", f"dump /{rel_clean} {out_f}", str(payload_img)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if out_f.is_file() and out_f.stat().st_size > 0:
                return out_f.read_bytes()
    return None


def patch_netbpfload_bytes(raw: bytes) -> tuple[bytes, list[str]]:
    """Patch BOTH Android 16 netbpfload kernel version gates (4.19 & 5.4) to ARM64 NOP (1f 20 03 d5)."""
    if len(raw) < 64 or raw[:4] != b"\x7fELF" or raw[4] != 2 or struct.unpack_from("<H", raw, 18)[0] != 183:
        raise RuntimeError("ملف netbpfload ليس ملف ELF64 AArch64 صالحاً!")

    ARM64_NOP = b"\x1f\x20\x03\xd5"
    buf = bytearray(raw)
    patched_sites: list[str] = []

    # 1. Exact audited Android 16 (OS3.0.304.0.WPQMIXM) dual-gate offsets:
    #    0xdcf0: 69 17 00 54 (b.ls 0xdfdc -> "Android V requires kernel 4.19.")
    #    0xdd14: e0 16 00 36 (tbz w0, #0, 0xdff0 -> "Android 25Q2 requires kernel 5.4.")
    if (
        len(buf) > 0xdd18
        and bytes(buf[0xdcf0:0xdcf4]) == b"\x69\x17\x00\x54"
        and bytes(buf[0xdd14:0xdd18]) == b"\xe0\x16\x00\x36"
    ):
        buf[0xdcf0:0xdcf4] = ARM64_NOP
        buf[0xdd14:0xdd18] = ARM64_NOP
        patched_sites.extend(["0xdcf0(b.ls->4.19)", "0xdd14(tbz->5.4)"])
        return bytes(buf), patched_sites

    # 2. General AArch64 ELF CFG scanner for future HyperOS 3 / Android 16 builds
    e_phoff = struct.unpack_from("<Q", raw, 32)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", raw, 54)
    loads = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_flags, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from("<IIQQQQQQ", raw, off)
        if p_type == 1:  # PT_LOAD
            loads.append((p_offset, p_vaddr, p_filesz, p_flags))

    def foff_to_va(foff: int) -> int | None:
        for p_off, p_va, p_fsz, _ in loads:
            if p_off <= foff < p_off + p_fsz:
                return p_va + (foff - p_off)
        return None

    def va_to_foff(va: int) -> int | None:
        for p_off, p_va, p_fsz, _ in loads:
            if p_va <= va < p_va + p_fsz:
                return p_off + (va - p_va)
        return None

    gate_Needles = [
        ("4.19", b"requires kernel 4.19"),
        ("5.4", b"requires kernel 5.4"),
    ]
    for label, needle in gate_Needles:
        idx = raw.find(needle)
        if idx < 0:
            continue
        # Walk back to start of C string
        s_start = raw.rfind(b"\x00", 0, idx) + 1
        str_va = foff_to_va(s_start)
        if str_va is None:
            continue

        # Scan executable PT_LOAD segments for ADRP + ADD referencing str_va
        err_vas = []
        for p_off, p_va, p_fsz, p_flags in loads:
            if not (p_flags & 1):  # PF_X
                continue
            for pos in range(p_off, p_off + p_fsz - 8, 4):
                insn1, insn2 = struct.unpack_from("<II", raw, pos)
                if (insn1 & 0x9F000000) == 0x90000000:  # ADRP
                    rd = insn1 & 0x1F
                    immlo = (insn1 >> 29) & 0x3
                    immhi = (insn1 >> 5) & 0x7FFFF
                    imm = (immhi << 2) | immlo
                    if imm & (1 << 20):
                        imm -= 1 << 21
                    pc = p_va + (pos - p_off)
                    page = (pc & ~0xFFF) + (imm << 12)
                    if page == (str_va & ~0xFFF) and (insn2 & 0xFFC00000) == 0x91000000:  # ADD imm12
                        rn = (insn2 >> 5) & 0x1F
                        imm12 = (insn2 >> 10) & 0xFFF
                        if rn == rd and (page + imm12) == str_va:
                            err_vas.append(pc)

        for err_va in err_vas:
            # Error basic block starts up to 16 bytes before ADRP (e.g. mov w0, #7 log level)
            bb_min, bb_max = err_va - 20, err_va + 4
            scan_start_foff = max(0, (va_to_foff(err_va) or 0) - 0x1000)
            scan_end_foff = va_to_foff(err_va) or 0
            for pos in range(scan_start_foff & ~3, scan_end_foff, 4):
                pc = foff_to_va(pos)
                if pc is None:
                    continue
                insn = struct.unpack_from("<I", buf, pos)[0]
                target = None
                if (insn & 0xFF000010) == 0x54000000:  # B.cond
                    imm19 = (insn >> 5) & 0x7FFFF
                    if imm19 & (1 << 18):
                        imm19 -= 1 << 19
                    target = pc + (imm19 << 2)
                elif (insn & 0x7E000000) == 0x34000000:  # CBZ / CBNZ
                    imm19 = (insn >> 5) & 0x7FFFF
                    if imm19 & (1 << 18):
                        imm19 -= 1 << 19
                    target = pc + (imm19 << 2)
                elif (insn & 0x7E000000) == 0x36000000:  # TBZ / TBNZ
                    imm14 = (insn >> 5) & 0x3FFF
                    if imm14 & (1 << 13):
                        imm14 -= 1 << 14
                    target = pc + (imm14 << 2)
                if target is not None and bb_min <= target <= bb_max:
                    buf[pos : pos + 4] = ARM64_NOP
                    patched_sites.append(f"0x{pos:x}(->{label})")

    if len(patched_sites) < 2:
        raise RuntimeError(f"تعذر العثور على بوابتي فحص الكيرنل (4.19 و 5.4) داخل netbpfload (تم العثور على: {patched_sites})")
    return bytes(buf), patched_sites


def patch_android16_netbpfload_for_414(final_dir: Path):
    """Apply clean bind-mount bypass for Android 16 (SDK 36+) netbpfload on Linux 4.14 without touching APEX signatures."""
    final_sys = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")
    f_sdk = get_partition_sdk(final_sys)
    if f_sdk < 36:
        return

    print(f"=== [ANDROID-16] تطبيق حل netbpfload_414 النظيف لنواة 4.14 (SDK {f_sdk}) ===", flush=True)
    apex_dir = final_sys / "apex"
    tethering_apex = None
    if apex_dir.is_dir():
        for cand in sorted(apex_dir.iterdir()):
            if "tethering" in cand.name.lower():
                tethering_apex = cand
                break
    if not tethering_apex:
        raise RuntimeError("تعذر العثور على حزمة com.android.tethering داخل system/apex في روم Android 16!")

    raw_netbpfload = extract_file_from_apex(tethering_apex, "bin/netbpfload")
    if not raw_netbpfload:
        raise RuntimeError(f"تعذر استخراج bin/netbpfload من {tethering_apex.name}!")

    patched_bytes, patched_sites = patch_netbpfload_bytes(raw_netbpfload)
    dst_bin = final_sys / "bin" / "netbpfload_414"
    dst_bin.parent.mkdir(parents=True, exist_ok=True)
    dst_bin.write_bytes(patched_bytes)
    dst_bin.chmod(0o755)
    print(f"  [BPF-FIX] تم استخراج وترقيع البوابتين {patched_sites} إلى NOP في {dst_bin} دون المساس بتوقيع {tethering_apex.name}", flush=True)

    # Register exact SELinux bpfloader_exec label & 0755 mode for /system/.../bin/netbpfload_414
    sys_part_root = final_dir / "system"
    rel_to_part = dst_bin.relative_to(sys_part_root).as_posix()
    fs_cfg = final_dir / "config" / "system_fs_config"
    fc_cfg = final_dir / "config" / "system_file_contexts"
    if fs_cfg.is_file():
        fs_entry = f"system/{rel_to_part} 0 2000 0755"
        txt = fs_cfg.read_text(encoding="utf-8", errors="ignore")
        if f"system/{rel_to_part} " not in txt:
            with open(fs_cfg, "a", encoding="utf-8") as f:
                f.write(fs_entry + "\n")
    if fc_cfg.is_file():
        escaped_key = f"/system/{re.escape(rel_to_part)}"
        fc_entry = f"{escaped_key} u:object_r:bpfloader_exec:s0"
        txt = fc_cfg.read_text(encoding="utf-8", errors="ignore")
        if escaped_key not in txt and f"/system/{rel_to_part} " not in txt:
            with open(fc_cfg, "a", encoding="utf-8") as f:
                f.write(fc_entry + "\n")

    # Allow init to bind-mount over bpfloader_exec in plat_sepolicy.cil
    plat_cil = final_sys / "etc" / "selinux" / "plat_sepolicy.cil"
    mounton_rule = "(allow init bpfloader_exec (file (mounton)))"
    if plat_cil.is_file():
        cil_txt = plat_cil.read_text(encoding="utf-8", errors="ignore")
        if mounton_rule not in cil_txt:
            with open(plat_cil, "a", encoding="utf-8") as f:
                f.write(f"\n{mounton_rule}\n")
            print(f"  [BPF-SELINUX] تمت إضافة {mounton_rule} في plat_sepolicy.cil", flush=True)

    # Inject bind-mount right before `exec_start bpfloader` in init.rc (keep netbpfload.rc header untouched!)
    init_rc = final_sys / "etc" / "init" / "hw" / "init.rc"
    bind_line = "    mount none /system/bin/netbpfload_414 /apex/com.android.tethering/bin/netbpfload bind"
    if init_rc.is_file():
        rc_txt = init_rc.read_text(encoding="utf-8", errors="ignore")
        if bind_line not in rc_txt:
            new_rc, n_sub = re.subn(
                r"^(\s*exec_start\s+bpfloader\b)",
                f"{bind_line}\n\\1",
                rc_txt,
                count=1,
                flags=re.MULTILINE,
            )
            if n_sub == 0:
                raise RuntimeError("تعذر العثور على سطر `exec_start bpfloader` داخل init.rc!")
            init_rc.write_text(new_rc, encoding="utf-8")
            print("  [BPF-INIT] تم حقن الربط (bind-mount) لـ netbpfload_414 قبل exec_start bpfloader في init.rc", flush=True)


def fix_joyeuse_nxp_nfc(base_dir: Path, final_dir: Path, only_64bit: bool):
    """Purge conflicting STMicroelectronics NFC (kunzite) and transplant joyeuse NXP NFC (NQNfcNci) with clean oat/."""
    # 1. Purge STMicroelectronics NFC APKs & JNI libs (keep framework/*.jar untouched to preserve BOOTCLASSPATH)
    st_removed = 0
    app_roots = [
        final_dir / "system" / "system" / "app",
        final_dir / "system" / "system" / "priv-app",
        final_dir / "system" / "app",
        final_dir / "system" / "priv-app",
        final_dir / "system_ext" / "app",
        final_dir / "system_ext" / "priv-app",
        final_dir / "product" / "app",
        final_dir / "product" / "priv-app",
    ]
    st_app_patterns = ("nfc_st", "nfcst", "stnfcextension", "stnfc")
    for ar in app_roots:
        if not ar.is_dir():
            continue
        for entry in list(ar.iterdir()):
            low = entry.name.lower()
            if any(pat in low for pat in st_app_patterns):
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
                st_removed += 1

    for lib_dir in (
        final_dir / "system" / "system" / "lib64",
        final_dir / "system_ext" / "lib64",
        final_dir / "product" / "lib64",
    ):
        if lib_dir.is_dir():
            for so in list(lib_dir.glob("*nfc*st*.so")) + list(lib_dir.glob("*st21nfc*.so")) + list(lib_dir.glob("*stnfc*.so")):
                so.unlink(missing_ok=True)
                st_removed += 1

    if st_removed:
        print(f"  [NFC-CLEAN] تم تطهير {st_removed} حزمة/مكتبة STMicroelectronics NFC متعارضة مع شريحة NXP في joyeuse", flush=True)

    # 2. Transplant NXP NQNfcNci* app from Base if missing in Final, and ALWAYS strip stale oat/
    base_sys = (base_dir / "system" / "system") if (base_dir / "system" / "system" / "build.prop").is_file() else (base_dir / "system")
    final_sys = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")

    part_pairs = [
        ("system", base_sys, final_sys),
        ("system_ext", base_dir / "system_ext", final_dir / "system_ext"),
        ("product", base_dir / "product", final_dir / "product"),
    ]

    base_nqnfc_cands = [
        (label, sub, cand, f_root)
        for label, b_root, f_root in part_pairs
        for sub in ("app", "priv-app")
        if (b_root / sub).is_dir()
        for cand in (b_root / sub).iterdir()
        if "nqnfcnci" in cand.name.lower() and cand.is_dir()
    ]

    if base_nqnfc_cands:
        # Remove Port's modern AIDL NQNfcNci* (including product/pangu/system) so joyeuse uses Base's HIDL NQNfcNci*
        extra_app_dirs = [
            f_root / sub
            for _, _, f_root in part_pairs
            for sub in ("app", "priv-app")
        ] + [
            final_dir / "product" / "pangu" / "system" / "app",
            final_dir / "product" / "pangu" / "system" / "priv-app",
        ]
        for ad in extra_app_dirs:
            if ad.is_dir():
                for p in list(ad.iterdir()):
                    if "nqnfcnci" in p.name.lower() and p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
        for label, sub, cand, f_root in base_nqnfc_cands:
            dst = f_root / sub / cand.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(cand, dst, symlinks=True, dirs_exist_ok=True)
            shutil.rmtree(dst / "oat", ignore_errors=True)
            print(f"  [NFC-NXP] تم استبدال/زرع {cand.name} من Base في {label}/{sub} (مع حذف oat/ للتوافق مع ART الجديد)", flush=True)

    # Always strip stale oat/ from any NQNfcNci* across final (including pangu overlay)
    for ad in (
        *(f_root / sub for _, _, f_root in part_pairs for sub in ("app", "priv-app")),
        final_dir / "product" / "pangu" / "system" / "app",
        final_dir / "product" / "pangu" / "system" / "priv-app",
    ):
        if ad.is_dir():
            for p in ad.iterdir():
                if "nqnfcnci" in p.name.lower() and p.is_dir():
                    shutil.rmtree(p / "oat", ignore_errors=True)

    # Also ensure any existing NQNfcNci in final from cross-SDK transplant has no stale oat/ if transplanted
    lib_subs = ("lib64",) if only_64bit else ("lib64", "lib")
    copied_nfc_libs = 0
    for _, b_root, f_root in part_pairs:
        for lsub in lib_subs:
            b_ld = b_root / lsub
            f_ld = f_root / lsub
            if not b_ld.is_dir():
                continue
            for nq_so in list(b_ld.glob("*nqnfc*.so")) + list(b_ld.glob("libnfc_nci_jni*.so")):
                if nq_so.is_file() and not (f_ld / nq_so.name).exists():
                    f_ld.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(nq_so, f_ld / nq_so.name, follow_symlinks=False)
                    copied_nfc_libs += 1
        # Copy libnfc-*.conf and NXP/NFC permission XMLs if missing
        b_etc = b_root / "etc"
        f_etc = f_root / "etc"
        if b_etc.is_dir():
            for cfg in b_etc.glob("libnfc*.conf"):
                if cfg.is_file() and not (f_etc / cfg.name).exists():
                    f_etc.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(cfg, f_etc / cfg.name)
            b_perm = b_etc / "permissions"
            f_perm = f_etc / "permissions"
            if b_perm.is_dir():
                for xml in list(b_perm.glob("*nfc*.xml")) + list(b_perm.glob("*nxp*.xml")):
                    if "st21" in xml.name.lower() or "stnfc" in xml.name.lower():
                        continue
                    if xml.is_file() and not (f_perm / xml.name).exists():
                        f_perm.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(xml, f_perm / xml.name)
    if copied_nfc_libs:
        print(f"  [NFC-NXP] تم نقل {copied_nfc_libs} مكتبة NXP NFC أصلية (libnqnfc*) لشريحة joyeuse", flush=True)


def fix_sm6250_ril_and_region(base_dir: Path, final_dir: Path):
    """Enforce HIDL RIL/OEMHook fallback (ro.board.api_level=29), remove crashing uceShimService/NtnSatApp, and align init.region.sh."""
    vendor_dir = final_dir / "vendor"
    vendor_bp = vendor_dir / "build.prop"
    if vendor_bp.is_file():
        # Preserve ro.board.api_level=29 & ro.product.first_api_level=29 so Android 16 QtiTelephony.apk
        # (QtiRadioFactory) & qcrilmsgtunnel.apk (QcrilOemhookMsgTunnel) use HIDL qtiradio@1.0..2.4 & qcrilhook@1.0
        cur_board_api = ""
        for ln in vendor_bp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("ro.board.api_level="):
                cur_board_api = ln.split("=", 1)[1].strip()
                break
        try:
            api_int = int(cur_board_api) if cur_board_api else 29
        except ValueError:
            api_int = 29
        if api_int > 30 or not cur_board_api:
            set_or_update_props(vendor_bp, {"ro.board.api_level": "29", "ro.product.first_api_level": "29"})
            print("  [RIL-HIDL] تم تثبيت ro.board.api_level=29 في vendor/build.prop لتفعيل وضع HIDL التلقائي في QtiTelephony و qcrilmsgtunnel", flush=True)
        else:
            print(f"  [RIL-HIDL] تم التحقق من ro.board.api_level={api_int} (<=30) لتشغيل شبكة الاتصال عبر HIDL على SM6250", flush=True)

    # Ensure upper partitions never override ro.board.api_level with Port SoC's level (34/35/36)
    for upper_bp in (
        final_dir / "system" / "system" / "build.prop",
        final_dir / "system" / "build.prop",
        final_dir / "system_ext" / "etc" / "build.prop",
        final_dir / "product" / "etc" / "build.prop",
        final_dir / "mi_ext" / "etc" / "build.prop",
    ):
        if upper_bp.is_file():
            lines = upper_bp.read_text(encoding="utf-8", errors="ignore").splitlines()
            filtered = [ln for ln in lines if not ln.strip().startswith("ro.board.api_level=")]
            if len(filtered) != len(lines):
                upper_bp.write_text("\n".join(filtered) + "\n", encoding="utf-8")

    # Always remove uceShimService & NtnSatApp (crash on SM6250 non-AIDL/non-satellite modem regardless of debloat flag)
    for crash_app in ("uceShimService", "NtnSatApp"):
        for sub in ("app", "priv-app"):
            cand = final_dir / "system_ext" / sub / crash_app
            if cand.exists():
                shutil.rmtree(cand, ignore_errors=True) if cand.is_dir() else cand.unlink(missing_ok=True)
                print(f"  [RIL-FIX] تم حذف الخدمة المتعارضة مع مودم SM6250: system_ext/{sub}/{crash_app}", flush=True)

    # Align vendor/bin/init.region.sh (_eea_global -> _global) when Port ROM is a Global (MIXM / _global) build
    region_sh = vendor_dir / "bin" / "init.region.sh"
    if region_sh.is_file():
        port_is_global = False
        port_is_eea = False
        for bp in (
            final_dir / "mi_ext" / "etc" / "build.prop",
            final_dir / "product" / "etc" / "build.prop",
            final_dir / "system" / "system" / "build.prop",
        ):
            if not bp.is_file():
                continue
            txt = bp.read_text(encoding="utf-8", errors="ignore")
            if "EUXM" in txt or "_eea_global" in txt:
                port_is_eea = True
            elif "MIXM" in txt or "_global" in txt:
                port_is_global = True
        if port_is_global and not port_is_eea:
            sh_txt = region_sh.read_text(encoding="utf-8", errors="ignore")
            if "_eea_global" in sh_txt:
                region_sh.write_text(sh_txt.replace("_eea_global", "_global"), encoding="utf-8")
                region_sh.chmod(0o755)
                print("  [REGION-FIX] تم تحديث vendor/bin/init.region.sh من _eea_global إلى _global لمطابقة روم MIXM العالمي", flush=True)


def transplant_miatoll_hal_fixes(base_dir: Path, final_dir: Path):
    """Transplant proven SM6250/miatoll fixes from Base ROM:
       1. USB ConfigFS init scripts when vendor.usb.use_gadget_hal=0 (fixes USB MTP & ADB)
       2. Linux 4.14 cgroup v1 + uclamp task_profiles.json (fixes Android 15/16 Cached App Freezer)
       3. Qualcomm OMX libstagefright*.so when Base & Port share identical Android SDK version (fixes HW video)
       4. Camera MiaFrame v10 JNI libs (verified via ELF UND symbol resolution across SDKs) + camera permissions XML
       5. Sysconfig xd_port_fixes.xml + Pangu merge + joyeuse NXP NFC transplant & ST NFC purge
    """
    final_sys = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")
    base_sys = (base_dir / "system" / "system") if (base_dir / "system" / "system" / "build.prop").is_file() else (base_dir / "system")
    if not base_sys.is_dir() or not final_sys.is_dir():
        return

    only_64 = is_64bit_only_system(final_sys)
    lib_subs = ("lib64",) if only_64 else ("lib", "lib64")
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
    b_sdk, f_sdk = str(get_partition_sdk(base_sys) or ""), str(get_partition_sdk(final_sys) or "")
    if b_sdk and b_sdk == f_sdk:
        copied_sf = 0
        for lib_sub in lib_subs:
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

    # 4. Camera MiaFrame v10 algoup/mianode JNI fix (with strict ELF UND symbol verification when crossing SDKs!)
    avail_64_sonames, exported_64_syms = collect_exported_elf_symbols(final_dir, is_64=True)
    copied_cam_jni = 0
    for lib_sub in lib_subs:
        for cam_so in ("libcamera_algoup_jni.xiaomi.so", "libcamera_mianode_jni.xiaomi.so"):
            src_so = base_dir / "system_ext" / lib_sub / cam_so
            dst_so = final_dir / "system_ext" / lib_sub / cam_so
            if not src_so.is_file() or not dst_so.parent.is_dir():
                continue
            if b_sdk != f_sdk and lib_sub == "lib64":
                ok_elf, missing_syms = elf_und_symbols_resolvable(src_so, avail_64_sonames, exported_64_syms)
                if not ok_elf:
                    print(f"  [CAMERA-SAFE] الإبقاء على {cam_so} الأصلي لـ SDK {f_sdk} بسبب اختلاف رموز C++: {missing_syms[:5]}", flush=True)
                    continue
            shutil.copy2(src_so, dst_so)
            copied_cam_jni += 1
    if copied_cam_jni:
        print(f"  [CAMERA-FIX] تم زرع {copied_cam_jni} مكتبة algoup/mianode JNI متوافقة مع MiaFrame v10 بعد فحص روابط ELF (SDK {f_sdk})", flush=True)

    # Copy MiuiCamera privapp-permissions XMLs (e.g. privapp-permissions-xd-miuicamera.xml) from Base if missing
    b_pperm = base_dir / "product" / "etc" / "permissions"
    f_pperm = final_dir / "product" / "etc" / "permissions"
    if b_pperm.is_dir() and f_pperm.is_dir():
        for cam_xml in b_pperm.glob("*miuicamera*.xml"):
            if cam_xml.is_file() and not (f_pperm / cam_xml.name).exists():
                shutil.copy2(cam_xml, f_pperm / cam_xml.name)
                print(f"  [CAMERA-PERM] تم نقل صلاحيات الكاميرا {cam_xml.name} إلى product/etc/permissions", flush=True)

    # 5. Disable crashing CollectRunningTasksService on boot (xd_port_fixes.xml)
    syscfg_dir = final_dir / "system_ext" / "etc" / "sysconfig"
    if syscfg_dir.is_dir():
        b_xd = base_dir / "system_ext" / "etc" / "sysconfig" / "xd_port_fixes.xml"
        if b_xd.is_file() and "CollectRunningTasksService" in b_xd.read_text(encoding="utf-8", errors="ignore"):
            shutil.copy2(b_xd, syscfg_dir / "xd_port_fixes.xml")
        else:
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

    # 6. Merge product/pangu/system -> product if pangu split exists, then purge ST NFC & transplant joyeuse NXP NFC
    pangu_sys = final_dir / "product" / "pangu" / "system"
    if pangu_sys.is_dir():
        for sub in pangu_sys.iterdir():
            dest_sub = final_dir / "product" / sub.name
            if sub.is_dir():
                shutil.copytree(sub, dest_sub, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(sub, dest_sub, follow_symlinks=False)
        shutil.rmtree(final_dir / "product" / "pangu", ignore_errors=True)
        print("  [PANGU-MERGE] تم دمج product/pangu/system داخل product لتقليل التكرار", flush=True)

    fix_joyeuse_nxp_nfc(base_dir, final_dir, only_64bit=only_64)
    fix_sm6250_ril_and_region(base_dir, final_dir)
    patch_android16_netbpfload_for_414(final_dir)
    if only_64:
        transplant_32bit_vendor_hal_bridge(base_dir, final_dir)


def transplant_32bit_vendor_hal_bridge(base_dir: Path, final_dir: Path):
    """Bridge 32-bit vendor HALs (audio.service, media.omx, cas@1.2) onto a 64-bit-only Port system
    without modifying any signed APEX or enabling 32-bit Zygote/ART."""
    vendor_dir = final_dir / "vendor"
    elf32_hals: list[str] = []
    for vsub in ("bin/hw", "bin"):
        vd = vendor_dir / vsub
        if not vd.is_dir():
            continue
        for item in sorted(vd.iterdir()):
            if item.is_file() and not item.is_symlink():
                try:
                    with open(item, "rb") as f:
                        hdr = f.read(6)
                    if hdr[:5] == b"\x7fELF\x01":
                        elf32_hals.append(f"{vsub}/{item.name}")
                except OSError:
                    pass
    if not elf32_hals:
        return

    base_sys = (base_dir / "system" / "system") if (base_dir / "system" / "system" / "build.prop").is_file() else (base_dir / "system")
    final_sys = (final_dir / "system" / "system") if (final_dir / "system" / "system" / "build.prop").is_file() else (final_dir / "system")
    if not base_sys.is_dir():
        return

    print(
        f"=== [32BIT-HAL-BRIDGE] اكتشاف {len(elf32_hals)} خدمة عتاد 32-بت في vendor (منها Audio/OMX) على نظام 64-bit-only ===",
        flush=True,
    )

    # 1. Extract 32-bit runtime linker from Base com.android.runtime.apex and redirect config -> /system/etc/ld.config.arm
    rt_apex = None
    if (base_sys / "apex").is_dir():
        for cand in sorted((base_sys / "apex").iterdir()):
            if "com.android.runtime" in cand.name.lower() or "com.google.android.runtime" in cand.name.lower():
                rt_apex = cand
                break
    if not rt_apex:
        raise RuntimeError("تعذر العثور على com.android.runtime.apex في الروم الأساسي لاستخراج مفسر 32-بت!")

    old_cfg_bytes = b"/linkerconfig/ld.config.txt\x00"
    new_cfg_bytes = b"/system/etc/ld.config.arm\x00\x00\x00"
    assert len(old_cfg_bytes) == len(new_cfg_bytes) == 28

    for l_name in ("linker", "linker_asan"):
        raw_lnk = extract_file_from_apex(rt_apex, f"bin/{l_name}")
        if not raw_lnk:
            if l_name == "linker":
                raise RuntimeError(f"تعذر استخراج bin/{l_name} من {rt_apex.name}!")
            continue
        if old_cfg_bytes not in raw_lnk:
            if l_name == "linker":
                raise RuntimeError("تعذر العثور على مسار /linkerconfig/ld.config.txt داخل مفسر 32-بت!")
            continue
        patched_lnk = raw_lnk.replace(old_cfg_bytes, new_cfg_bytes, 1)
        dst_lnk = final_sys / "bin" / l_name
        if os.path.lexists(dst_lnk):
            dst_lnk.unlink()
        dst_lnk.write_bytes(patched_lnk)
        dst_lnk.chmod(0o755)

    cd32 = extract_file_from_apex(rt_apex, "bin/crash_dump32")
    if cd32:
        dst_cd = final_sys / "bin" / "crash_dump32"
        if not os.path.lexists(dst_cd):
            dst_cd.write_bytes(cd32)
            dst_cd.chmod(0o755)

    # Copy bootstrap 32-bit linker binaries for early-init (e.g., boringssl_self_test32)
    b_boot_bin = base_sys / "bin" / "bootstrap"
    f_boot_bin = final_sys / "bin" / "bootstrap"
    if b_boot_bin.is_dir():
        f_boot_bin.mkdir(parents=True, exist_ok=True)
        for bl in ("linker", "linker_asan"):
            if (b_boot_bin / bl).is_file() and not os.path.lexists(f_boot_bin / bl):
                shutil.copy2(b_boot_bin / bl, f_boot_bin / bl, follow_symlinks=False)
                (f_boot_bin / bl).chmod(0o755)

    # 2. Transplant 32-bit Bionic bootstrap + LLNDK/system libs (/system/lib & /system_ext/lib)
    def merge_lib32_dir(src_root: Path, dst_root: Path):
        if not src_root.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(src_root, followlinks=False):
            # Skip kernel modules symlink/dir (GKI /system_dlkm/lib/modules)
            dirnames[:] = [d for d in dirnames if d != "modules"]
            rel_d = Path(dirpath).relative_to(src_root)
            target_d = dst_root / rel_d
            if not os.path.lexists(target_d):
                target_d.mkdir(parents=True, exist_ok=True)
            for dname in list(dirnames):
                s_sub = Path(dirpath) / dname
                t_sub = target_d / dname
                if s_sub.is_symlink():
                    if not os.path.lexists(t_sub):
                        os.symlink(os.readlink(s_sub), t_sub)
                    dirnames.remove(dname)
            for fname in filenames:
                if fname == "modules":
                    continue
                s_f = Path(dirpath) / fname
                t_f = target_d / fname
                if os.path.lexists(t_f):
                    continue
                if s_f.is_symlink():
                    os.symlink(os.readlink(s_f), t_f)
                elif s_f.is_file():
                    shutil.copy2(s_f, t_f, follow_symlinks=False)

    merge_lib32_dir(base_sys / "lib", final_sys / "lib")
    merge_lib32_dir(base_dir / "system_ext" / "lib", final_dir / "system_ext" / "lib")
    copied_32 = sum(1 for p in (final_sys / "lib").rglob("*.so") if not p.is_symlink()) if (final_sys / "lib").is_dir() else 0

    # Extract 32-bit ICU core .so from Base com.android.i18n.apex for transitive VNDK dependencies (e.g. libxml2.so)
    if (base_sys / "apex").is_dir():
        for cand in sorted((base_sys / "apex").iterdir()):
            if "i18n" in cand.name.lower():
                for icu_so in ("libandroidicu.so", "libicu.so", "libicui18n.so", "libicuuc.so"):
                    dst_icu = final_sys / "lib" / icu_so
                    if not dst_icu.exists():
                        raw_icu = extract_file_from_apex(cand, f"lib/{icu_so}")
                        if raw_icu:
                            dst_icu.parent.mkdir(parents=True, exist_ok=True)
                            dst_icu.write_bytes(raw_icu)
                            copied_32 += 1
                break

    # 3. Generate isolated 32-bit vendor HAL linker configuration (/system/etc/ld.config.arm)
    vndk_ver = "30"
    v_bp = vendor_dir / "build.prop"
    if v_bp.is_file():
        for ln in v_bp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.strip().startswith("ro.vndk.version="):
                val = ln.split("=", 1)[1].strip()
                if val:
                    vndk_ver = val
                    break

    vndk_lib = f"/apex/com.android.vndk.v{vndk_ver}/lib"
    vndk_root = f"/apex/com.android.vndk.v{vndk_ver}"
    ld_cfg_arm = f"""# Isolated 32-bit Native Vendor HAL Linker Config (64-bit-only Android 15/16 Port)
dir.vendor = /vendor/
dir.odm = /odm/
dir.system = /system/

[vendor]
additional.namespaces = sphal,vndk,system

namespace.default.isolated = false
namespace.default.visible = true
namespace.default.search.paths = /vendor/lib:/vendor/lib/vndk:/vendor/lib/vndk-sp:/vendor/lib/hw:/vendor/lib/egl:/odm/lib:{vndk_lib}:/system/lib/bootstrap:/system/lib:/system_ext/lib
namespace.default.permitted.paths = /vendor:/odm:{vndk_root}:/system/lib:/system_ext/lib
namespace.default.links = sphal,vndk,system
namespace.default.link.sphal.allow_all_shared_libs = true
namespace.default.link.vndk.allow_all_shared_libs = true
namespace.default.link.system.allow_all_shared_libs = true

namespace.sphal.isolated = false
namespace.sphal.visible = true
namespace.sphal.search.paths = /odm/lib:/vendor/lib:/vendor/lib/egl:/vendor/lib/hw:/vendor/lib/vndk:/vendor/lib/vndk-sp:{vndk_lib}:/system/lib/bootstrap:/system/lib
namespace.sphal.permitted.paths = /odm:/vendor:{vndk_root}:/system/lib
namespace.sphal.links = default,vndk,system
namespace.sphal.link.default.allow_all_shared_libs = true
namespace.sphal.link.vndk.allow_all_shared_libs = true
namespace.sphal.link.system.allow_all_shared_libs = true

namespace.vndk.isolated = false
namespace.vndk.visible = true
namespace.vndk.search.paths = {vndk_lib}:/vendor/lib/vndk:/vendor/lib/vndk-sp:/vendor/lib:/system/lib/bootstrap:/system/lib:/system_ext/lib
namespace.vndk.permitted.paths = {vndk_root}:/vendor:/odm:/system/lib:/system_ext/lib
namespace.vndk.links = default,sphal,system
namespace.vndk.link.default.allow_all_shared_libs = true
namespace.vndk.link.sphal.allow_all_shared_libs = true
namespace.vndk.link.system.allow_all_shared_libs = true

namespace.system.isolated = false
namespace.system.visible = true
namespace.system.search.paths = /system/lib/bootstrap:/system/lib:/system_ext/lib:{vndk_lib}:/vendor/lib
namespace.system.permitted.paths = /system/lib:/system_ext/lib:{vndk_root}:/vendor:/odm
namespace.system.links = default,sphal,vndk
namespace.system.link.default.allow_all_shared_libs = true
namespace.system.link.sphal.allow_all_shared_libs = true
namespace.system.link.vndk.allow_all_shared_libs = true

[odm]
additional.namespaces = sphal,vndk,system

namespace.default.isolated = false
namespace.default.visible = true
namespace.default.search.paths = /odm/lib:/vendor/lib:/vendor/lib/vndk:/vendor/lib/vndk-sp:/vendor/lib/hw:{vndk_lib}:/system/lib/bootstrap:/system/lib
namespace.default.permitted.paths = /odm:/vendor:{vndk_root}:/system/lib
namespace.default.links = sphal,vndk,system
namespace.default.link.sphal.allow_all_shared_libs = true
namespace.default.link.vndk.allow_all_shared_libs = true
namespace.default.link.system.allow_all_shared_libs = true

namespace.sphal.isolated = false
namespace.sphal.visible = true
namespace.sphal.search.paths = /odm/lib:/vendor/lib:/vendor/lib/egl:/vendor/lib/hw:{vndk_lib}:/system/lib/bootstrap:/system/lib
namespace.sphal.permitted.paths = /odm:/vendor:{vndk_root}:/system/lib

namespace.vndk.isolated = false
namespace.vndk.visible = true
namespace.vndk.search.paths = {vndk_lib}:/system/lib/bootstrap:/system/lib:/vendor/lib
namespace.vndk.permitted.paths = {vndk_root}:/system/lib:/vendor:/odm

namespace.system.isolated = false
namespace.system.visible = true
namespace.system.search.paths = /system/lib/bootstrap:/system/lib:/system_ext/lib:{vndk_lib}
namespace.system.permitted.paths = /system/lib:/system_ext/lib:{vndk_root}

[system]
additional.namespaces = sphal,vndk

namespace.default.isolated = false
namespace.default.visible = true
namespace.default.search.paths = /system/lib/bootstrap:/system/lib:/system_ext/lib:/product/lib:{vndk_lib}:/vendor/lib:/vendor/lib/hw
namespace.default.permitted.paths = /system:/system_ext:/product:{vndk_root}:/vendor:/odm
namespace.default.links = sphal,vndk
namespace.default.link.sphal.allow_all_shared_libs = true
namespace.default.link.vndk.allow_all_shared_libs = true

namespace.sphal.isolated = false
namespace.sphal.visible = true
namespace.sphal.search.paths = /odm/lib:/vendor/lib:/vendor/lib/egl:/vendor/lib/hw:{vndk_lib}:/system/lib/bootstrap:/system/lib
namespace.sphal.permitted.paths = /odm:/vendor:{vndk_root}:/system/lib

namespace.vndk.isolated = false
namespace.vndk.visible = true
namespace.vndk.search.paths = {vndk_lib}:/system/lib/bootstrap:/system/lib:/vendor/lib
namespace.vndk.permitted.paths = {vndk_root}:/system/lib:/vendor:/odm
"""
    cfg_arm_path = final_sys / "etc" / "ld.config.arm"
    cfg_arm_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_arm_path.write_text(ld_cfg_arm, encoding="utf-8")
    cfg_arm_path.chmod(0o644)

    # 4. Preserve exact SELinux labels (e.g. same_process_hal_file, vndk_sp_file) for /lib/ from Base file_contexts
    for p_name in ("system", "system_ext"):
        b_fc = base_dir / "config" / f"{p_name}_file_contexts"
        f_fc = final_dir / "config" / f"{p_name}_file_contexts"
        if b_fc.is_file() and f_fc.is_file():
            existing = set(
                ln.split()[0] for ln in f_fc.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()
            )
            extra_fc = []
            for ln in b_fc.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = ln.strip().split()
                if len(parts) >= 2 and ("/lib/" in parts[0] or parts[0].endswith("/lib") or "linker" in parts[0]):
                    if parts[0] not in existing:
                        extra_fc.append(ln)
                        existing.add(parts[0])
            if extra_fc:
                with open(f_fc, "a", encoding="utf-8") as f:
                    f.write("\n".join(extra_fc) + "\n")

    print(
        f"  [32BIT-HAL-BRIDGE] تم زرع مفسر 32-بت (/system/bin/linker -> ld.config.arm) + {copied_32} مكتبة 32-بت لتشغيل الصوت والفيديو دون المساس بـ Zygote64!",
        flush=True,
    )


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
                elif fc_key.endswith("/netbpfload_414"):
                    inherited_ctx = "u:object_r:bpfloader_exec:s0"
                elif re.search(r"/bin/(bootstrap/)?linker(64|_asan|_asan64|_hwasan64)?$", fc_key):
                    inherited_ctx = "u:object_r:system_linker_exec:s0"
                elif re.search(r"/bin/crash_dump(32|64)$", fc_key):
                    inherited_ctx = "u:object_r:crash_dump_exec:s0"
                elif "/etc/ld.config" in fc_key:
                    inherited_ctx = "u:object_r:system_linker_config_file:s0"
                elif part_name not in ("vendor", "odm") and ("/lib/bootstrap" in fc_key or "/lib64/bootstrap" in fc_key):
                    inherited_ctx = "u:object_r:system_bootstrap_lib_file:s0"
                elif part_name not in ("vendor", "odm") and (re.search(r"/lib(64)?(/|$)", fc_key)) and inherited_ctx == default_ctx:
                    inherited_ctx = "u:object_r:system_lib_file:s0"
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
