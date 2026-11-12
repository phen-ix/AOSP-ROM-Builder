import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('patch_port', Path(__file__).resolve().parents[1] / 'porting/patch_port.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

class Phase3Tests(unittest.TestCase):
    def test_fstab_avb_keys_verifyatboot(self):
        with tempfile.TemporaryDirectory() as tmp:
            fstab = Path(tmp) / 'fstab.default'
            fstab.write_text('/dev/block/userdata /data ext4 noatime wait,avb_keys=/avb/q-gsi.avbpubkey,verifyatboot,avb=vbmeta,verify=/dev/block/verity,avb,quota,fileencryption=aes-256-xts\n')
            patch.patch_fstab_file(fstab, ['system', 'vendor'], False)
            data_line = next(line for line in fstab.read_text().splitlines() if '/data' in line)
            self.assertEqual(data_line.split()[4], 'wait,quota,fileencryption=aes-256-xts')
            self.assertNotIn('_keys=', fstab.read_text())

    def test_literal_context_escape(self):
        import re
        for name in ['a+b.apk', 'file[1](x).xml', 'a-b c#d', 'foo\\bar']:
            self.assertIsNotNone(re.fullmatch(patch.escape_fc(name), name))

    def test_fold_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ['system/system', 'product/pangu/system/etc', 'mi_ext/product/app', 'config']:
                (root / name).mkdir(parents=True)
            (root / 'mi_ext/product/app/test.apk').write_bytes(b'apk')
            (root / 'product/pangu/system/etc/test.xml').write_text('test')
            (root / 'config/mi_ext_fs_config').write_text('mi_ext/product/app/test.apk 1000 1000 0644\n')
            (root / 'config/mi_ext_file_contexts').write_text('/mi_ext/product/app/test\\.apk u:object_r:system_file:s0\n')
            patch.fold_split_layout(root)
            self.assertFalse((root / 'mi_ext').exists())
            self.assertEqual((root / 'product/app/test.apk').read_bytes(), b'apk')
            self.assertEqual((root / 'system/system/etc/test.xml').read_text(), 'test')
            self.assertEqual((root / 'product/pangu/system').readlink().as_posix(), '/system')
            self.assertIn('product/app/test.apk 1000 1000', (root / 'config/product_fs_config').read_text())
            self.assertIn('/product/app/test\\.apk', (root / 'config/product_file_contexts').read_text())

    def test_vendor_zygote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'vendor').mkdir()
            (root / 'vendor/default.prop').write_text('ro.zygote=zygote64_32\n')
            patch.harden_runtime_properties(root)
            text = (root / 'vendor/default.prop').read_text()
            self.assertIn('ro.zygote=zygote64\n', text)
            self.assertIn('ro.vendor.product.cpu.abilist32=\n', text)

if __name__ == '__main__':
    unittest.main()
