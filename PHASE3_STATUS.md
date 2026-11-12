# Phase 3 — implementation and test status

## Completed locally
- Fold donor mi_ext and product/pangu/system trees into target partitions, remapping matching filesystem metadata.
- Vendor/ODM-aware fallback SELinux contexts and vendor-like root GID.
- ARM64-only zygote properties while retaining the existing native 32-bit HAL bridge.
- Remove reboot_on_failure from selected BoringSSL and HyperOS BPF loader services.
- Kernel configuration hardening, embedded IKCONFIG checks, and four-entry DTBO packing/validation.
- EROFS lz4hc,9 without large clusters; super.img.zst and ZIP_STORED packaging.
- Static ARM64 recovery installer with streaming Zstandard and ZIP64 support.
- Donor-specific fail-closed early ADB patching, init bind mount and adbd_exec context. Signed APEX is not modified.
- Base compatibility matrix copying where absent, vendor-ndk 30/system-sdk 29 entries, donor property cleanup, device XML replacement, expanded hardware overlay selection.
- Remove stale precompiled policy; refresh policy/mapping hashes after compilation; reconcile property namespaces and explicit vendor overlay/permissions contexts.
- Additional kernel, DTBO, layout, property-type and EROFS feature checks.

## Tests actually executed
- python3 -m unittest discover -s tests -v: 3 tests PASS (layout and metadata, regex escaping, vendor zygote).
- python3 tests/test_installer_arm64.py: PASS. Cross-built static AArch64 ELF against zstd 1.5.6 with -Wall -Wextra -Werror. QEMU ran the installer against temporary mock partitions and verified byte-exact output. Unsupported-device rejection PASS.
- ARM64 assembly fixture for auth_required patch: PASS; malformed ELF rejected.
- python3 -m py_compile porting/*.py: PASS.
- bash -n porting/*.sh: PASS.
- git diff --check: PASS.

## Not yet validated
- Full donor adbd extraction and patching. Unsupported instruction layouts intentionally stop the build; no foreign daemon or guessed patch is substituted.
- Complete ROM processing, real split-policy compilation/type audit against the donor/Base combination, VINTF runtime compatibility and full dependency resolution on resulting trees.
- Full workflow, resulting EROFS/super/boot/DTBO package checks and upload.
- Physical device boot, radio, camera, sensors, display, encryption and suspend behavior.
- QEMU tests are synthetic installer tests, not Android boot emulation or proof the ROM boots.
- The existing ELF scanner is not proof that all Android linker namespaces and runtime-loaded libraries resolve.

## Security
The optional early ADB mode intentionally disables ADB authentication. Use only on a trusted test device/USB host; do not treat this development image as production-secure. It keeps ro.secure=1 and ro.debuggable=0 and does not intentionally grant root ADB.

## Current blocker
Authenticated GitHub repository lookup using previous session credentials returned HTTP 404, while unauthenticated public reads returned HTTP 200. Push and workflow dispatch were not performed. The native GitHub connector reports unsupported in this environment. Repository write and Actions dispatch access must be restored, or the patch can be applied manually by the repository owner.

## Resume
1. Restore authenticated repository write access and Actions dispatch access for phen-ix/AOSP-ROM-Builder. Do not put tokens in tracked files.
2. Push the local commits to the intended branch.
3. Dispatch .github/workflows/port-rom.yml using its Base/Port defaults and compile_phenix_new.
4. Monitor all stages and repair observed failures, particularly any unsupported donor adbd layout or policy/type errors.
5. Deliver only the actual resulting ROM link with checksums and an explicit device-test disclaimer.
