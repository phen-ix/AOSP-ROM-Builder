import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

PORTING = Path(__file__).resolve().parents[1] / 'porting'
sys.path.insert(0, str(PORTING))
from boot_header import set_selinux_mode
from check_base_bridge import check_base_bridge
from simulate_boot_check import check_kernel_modules


class BuildAFixesTests(unittest.TestCase):
    def test_permissive_header_idempotent_and_preserves_fields(self):
        with tempfile.TemporaryDirectory() as td:
            header = Path(td) / 'header'
            header.write_text('pagesize=4096\ncmdline=console=ttyMSM0 androidboot.selinux=enforcing androidboot.hardware=qcom\nname=miatoll\n')
            set_selinux_mode(header, 'permissive')
            set_selinux_mode(header, 'permissive')
            text = header.read_text()
            self.assertEqual(text.count('androidboot.selinux='), 1)
            self.assertIn('androidboot.selinux=permissive', text)
            self.assertIn('console=ttyMSM0', text)
            self.assertIn('name=miatoll\n', text)
            set_selinux_mode(header, 'enforcing')
            self.assertNotIn('selinux=permissive', header.read_text())

    def test_missing_header_fails(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):
                set_selinux_mode(Path(td) / 'absent', 'permissive')

    def test_modules_matching_and_mismatch_modes(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / 'kernel_release.txt').write_text('4.14.209-test\n')
            modules = work / 'final/vendor/lib/modules'
            modules.mkdir(parents=True)
            ko = modules / 'wlan.ko'
            ko.write_bytes(b'\x7fELF\x00vermagic=4.14.209-test SMP preempt mod_unload aarch64\x00')
            self.assertTrue(check_kernel_modules(work, 'compile_phenix_new')[0])
            ko.write_bytes(b'\x7fELF\x00vermagic=4.14.999-wrong SMP\x00')
            self.assertFalse(check_kernel_modules(work, 'compile_phenix_new')[0])
            self.assertTrue(check_kernel_modules(work, 'custom_url')[0])
            self.assertTrue(check_kernel_modules(work, 'keep_base')[0])
            self.assertIn('wlan.ko', (work / 'kernel_modules.txt').read_text())

    def test_vendor_dlkm_scanned_and_missing_vermagic_fails(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / 'kernel_release.txt').write_text('4.14.209-test\n')
            modules = work / 'final/vendor_dlkm/lib/modules'
            modules.mkdir(parents=True)
            (modules / 'bad.ko').write_bytes(b'no modinfo')
            self.assertFalse(check_kernel_modules(work, 'compile_phenix_new')[0])
            self.assertIn('MISSING', (work / 'kernel_modules.txt').read_text())

    def test_bridge_preflight_positive_and_negative(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            package = work / 'base/system/system/apex/com.android.runtime.apex'
            package.parent.mkdir(parents=True)
            package.touch()
            lib = work / 'base/system/system/lib'
            lib.mkdir()
            self.assertTrue(check_base_bridge(work, lambda *args: b'\x7fELF\x01test'))
            self.assertFalse(check_base_bridge(work, lambda *args: b'\x7fELF\x02test'))
            lib.rmdir()
            self.assertFalse(check_base_bridge(work, lambda *args: b'\x7fELF\x01test'))


if __name__ == '__main__':
    unittest.main()
