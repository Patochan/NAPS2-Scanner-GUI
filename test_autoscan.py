import atexit
import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
import unittest

# autoscan.pyw has no .py extension, so load it explicitly.
# Importing it runs its startup code (reads config.json, creates folders), so load a
# copy from a temp folder to keep the real config.json untouched.
_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autoscan.pyw")
_work_dir = tempfile.mkdtemp(prefix="autoscan_test_")
atexit.register(shutil.rmtree, _work_dir, True)
_path = os.path.join(_work_dir, "autoscan.pyw")
shutil.copy(_src, _path)
_loader = importlib.machinery.SourceFileLoader("autoscan", _path)
_spec = importlib.util.spec_from_loader("autoscan", _loader)
autoscan = importlib.util.module_from_spec(_spec)
_loader.exec_module(autoscan)


class InterleavePagesTest(unittest.TestCase):
    def test_back_stack_is_scanned_bottom_up(self):
        # Sheets 1-3 are scanned front first, back last-sheet first
        result = autoscan.interleave_pages(["F1", "F2", "F3"], ["B3", "B2", "B1"])
        self.assertEqual(result, ["F1", "B1", "F2", "B2", "F3", "B3"])

    def test_uneven_page_counts(self):
        result = autoscan.interleave_pages(["F1", "F2"], ["B1"])
        self.assertEqual(result, ["F1", "B1", "F2"])


class ParseProfilesXmlTest(unittest.TestCase):
    XML = """<?xml version="1.0"?>
<ScanProfiles>
  <ScanProfile>
    <DisplayName>Glass 600</DisplayName>
    <PaperSource>Glass</PaperSource>
    <Resolution>Dpi600</Resolution>
    <Device><Name>Printer A</Name></Device>
  </ScanProfile>
  <ScanProfile>
    <DisplayName>Feeder 300</DisplayName>
    <PaperSource>Feeder</PaperSource>
    <Resolution>Dpi300</Resolution>
    <IsDefault>true</IsDefault>
    <Device><Name>Printer A</Name></Device>
  </ScanProfile>
</ScanProfiles>
"""

    def test_profiles_are_grouped_and_sorted_adf_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = os.path.join(tmp, "profiles.xml")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(self.XML)
            result = autoscan.parse_profiles_xml(xml_path)

        profiles = result["Printer A"]
        self.assertEqual([p["display_name"] for p in profiles], ["Feeder 300", "Glass 600"])
        self.assertEqual(profiles[0]["source_type"], "adf")
        self.assertEqual(profiles[0]["label"], "300 DPI (Standard)")
        self.assertTrue(profiles[0]["is_default"])
        self.assertEqual(profiles[1]["source_type"], "flatbed")


if __name__ == "__main__":
    unittest.main()
