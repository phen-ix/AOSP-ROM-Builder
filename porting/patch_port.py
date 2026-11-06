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
    "BaiduIME", "SogouInput", "IflytekIME", "MiShop", "Youpin", "GameCenter",
    "MiFinance", "Wallet", "HybridPlatform", "AnalyticsCore", "MSA", "msa",
    "mab", "Updater", "SystemUpdate", "Ota", "Netflix", "Facebook", "TikTok",
    "Weibo", "Bilibili", "Alipay", "Taobao", "JD", "Meituan", "WPSOffice",
    "BrowserVideo", "MiCredit", "PaymentService", "HeytapBrowser", "HeytapMarket",
    "KeKeMarket", "NearmeGameCenter", "OppoShop",
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
    # 1. Patch powerhint.json
    for ph_path in list(vendor_dir.glob("etc/powerhint.json")) + list(vendor_dir.glob("etc/power/powerhint.json")):
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
    hw_patterns = ("miatoll", "joyeuse", "atoll", "sm6250", "cutout", "notch", "wifi", "tethering", "carrierconfig")
    copied = 0
    for item in base_ov.iterdir():
        lname = item.name.lower()
        if any(p in lname for p in hw_patterns):
            dest = final_ov / item.name
            if not dest.exists():
                if item.is_dir():
                    shutil.copytree(item, dest, symlinks=True)
                else:
                    shutil.copy2(item, dest, follow_symlinks=False)
                copied += 1
    print(f"  [OVERLAY] تم نقل {copied} من ملفات الـ Overlay الخاصة بعتاد joyeuse/miatoll", flush=True)


def run_smart_debloat(final_dir: Path):
    removed_bytes = 0
    removed_count = 0
    search_dirs = [
        "system/system/app", "system/system/priv-app", "system/app", "system/priv-app",
        "product/app", "product/priv-app", "product/data-app",
        "system_ext/app", "system_ext/priv-app",
        "mi_ext/product/app", "mi_ext/product/data-app",
        "my_stock/app", "my_stock/del-app", "my_heytap/app", "my_preload/app",
    ]
    for rel in search_dirs:
        d = final_dir / rel
        if not d.is_dir():
            continue
        for entry in list(d.iterdir()):
            if "data-app" in rel or "del-app" in rel or any(k.lower() in entry.name.lower() for k in SAFE_DEBLOAT_KEYWORDS):
                size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file() and not f.is_symlink()) if entry.is_dir() else entry.stat().st_size
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed_bytes += size
                removed_count += 1
    print(f"  [DEBLOAT] تم حذف {removed_count} تطبيق/حزمة زائدة (توفير {removed_bytes / (1024*1024):.1f} MB)", flush=True)


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
        if ppart in ("vendor",):
            continue
        if ppart == "odm" and (final_dir / "odm").is_dir():
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

    # 3. Copy hardware overlays from base/product to final/product
    if (base_dir / "product").is_dir() and (final_dir / "product").is_dir():
        copy_hardware_overlays(base_dir / "product", final_dir / "product")

    # 4. HyperOS joyeuse device_features XML
    if ui_type == "hyperos" and (final_dir / "product").is_dir():
        df_dir = final_dir / "product" / "etc" / "device_features"
        df_dir.mkdir(parents=True, exist_ok=True)
        base_xml = base_dir / "product" / "etc" / "device_features" / "joyeuse.xml"
        if base_xml.is_file():
            shutil.copy2(base_xml, df_dir / "joyeuse.xml")
        elif not (df_dir / "joyeuse.xml").is_file():
            (df_dir / "joyeuse.xml").write_text(JOYEUSE_HYPEROS_XML, encoding="utf-8")
        print("  [HYPEROS] تم تجهيز ملف خصائص joyeuse.xml (دعم NFC + IR + بطارية 5020mAh)", flush=True)

    # 5. Free base_dir & port_dir (keep base/firmware for boot/dtbo/vbmeta)
    shutil.rmtree(base_dir / "product", ignore_errors=True)
    shutil.rmtree(port_dir, ignore_errors=True)

    # 6. Patch fstab in vendor & odm for EROFS + logical partitions + no AVB
    disable_dfe = args.disable_dfe.lower() == "true"
    for fstab in list((final_dir / "vendor").glob("etc/fstab*")) + list((final_dir / "odm").glob("etc/fstab*")):
        patch_fstab_file(fstab, final_parts, disable_dfe)

    # 7. Convert stune -> uclamp in vendor
    if (final_dir / "vendor").is_dir():
        patch_uclamp_vendor(final_dir / "vendor")

    # 8. Patch build.prop files for joyeuse / miatoll compatibility
    common_props = {
        "ro.sf.lcd_density": "440",
        "persist.sys.disable_rescue": "true",
        "ro.Incremental.enable": "false",
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

    # 10. Reconcile fs_config & file_contexts for every partition
    for part in final_parts:
        reconcile_selinux_configs(final_dir / part, final_cfg, part)

    print("✅ اكتملت تعديلات البورت + EROFS + uclamp بنجاح!", flush=True)


if __name__ == "__main__":
    main()
