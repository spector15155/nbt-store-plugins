import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pack


def elf(kind):
    data = bytearray(64)
    data[0:7] = b"\x7fELF\x01\x01\x01"
    data[16:18] = kind.to_bytes(2, "little")
    data[18:20] = (40).to_bytes(2, "little")
    return bytes(data)


def empty_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("entry.txt", b"ok")
    return buffer.getvalue()


def manifest(identifier):
    return f"""<?xml version="1.0"?>
<plugin><name>{identifier}</name><type>App</type><file>{{pluginDir}}/{identifier}</file>
<config><parameters>
<DescFile>{{pluginDir}}/{identifier}.xml</DescFile>
<TextDBFile>{{pluginDir}}/{identifier}_ALL_Texts.zip</TextDBFile>
<ImageDBFile>{{pluginDir}}/{identifier}_ALL_Images.zip</ImageDBFile>
</parameters></config></plugin>""".encode()


def xml():
    return b"<?xml version='1.0'?><pluginApps><pluginApp></pluginApp></pluginApps>"


class PackTests(unittest.TestCase):
    def plugin(self, kind="pluginapp"):
        files = {
            "libNpmDemo.so": elf(3),
            "NpmDemo.manifest": manifest("NpmDemo"),
            "NpmDemo.xml": xml(),
            "NpmDemo_ALL_Texts.zip": empty_zip(),
            "NpmDemo_ALL_Images.zip": empty_zip(),
        }
        doc = {
            "id": "demo", "name": "Demo", "version": "1.2.3", "kind": kind,
            "packageId": "NpmDemo", "summary": "Example", "license": "Example",
            "notice": "Required Notice: Example",
            "upstream": {"repository": "example/demo", "tag": "v1.2.3"},
            "assets": [], "_dir": "demo",
        }
        if kind == "program":
            doc.pop("packageId")
            doc["binary"] = "aa-tile"
            files = {"aa-tile": elf(2)}
        return doc, files

    def test_pluginapp_zip_is_one_package(self):
        doc, files = self.plugin()
        blob = pack.zip_bytes(doc, files)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.namelist(), [
                "package.npm", "libNpmDemo.so", "NpmDemo.manifest", "NpmDemo.xml",
                "NpmDemo_ALL_Texts.zip", "NpmDemo_ALL_Images.zip"])
            text = archive.read("package.npm").decode()
        self.assertIn("NpmDemo 1.2.3 pluginapp", text)
        self.assertNotIn("aa-tile", text)

    def test_program_does_not_include_scripts(self):
        doc, files = self.plugin("program")
        blob = pack.zip_bytes(doc, files)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.namelist(), ["package.npm", "aa-tile"])

    def test_two_plugins_stay_separate(self):
        first, files = self.plugin()
        second, program = self.plugin("program")
        second["id"] = "aa-tile"
        second["_dir"] = "aa-tile"
        left = pack.zip_bytes(first, files)
        right = pack.zip_bytes(second, program)
        self.assertNotEqual(hashlib.sha256(left).digest(), hashlib.sha256(right).digest())
        with zipfile.ZipFile(io.BytesIO(left)) as archive:
            self.assertNotIn("aa-tile", archive.namelist())
        with zipfile.ZipFile(io.BytesIO(right)) as archive:
            self.assertNotIn("libNpmDemo.so", archive.namelist())

    def test_members_reject_undeclared_and_unsafe_paths(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("nbt-aa-tile/aa-tile", elf(2))
            archive.writestr("nbt-aa-tile/install-boot.sh", b"#!/bin/sh\n")
        selected = pack.take_bytes(payload.getvalue(), {"name": "tile.zip", "members": {"nbt-aa-tile/aa-tile": "aa-tile"}})
        self.assertEqual(list(selected), ["aa-tile"])
        with self.assertRaises(pack.PackError):
            pack.take_bytes(payload.getvalue(), {"name": "tile.zip", "members": {"../aa-tile": "aa-tile"}})

    def test_script_destination_is_rejected(self):
        doc, _files = self.plugin("program")
        doc["assets"] = [{"name": "tile.zip", "sha256": "a" * 64, "members": {"nbt-aa-tile/install-boot.sh": "install-boot.sh"}}]
        with self.assertRaises(pack.PackError):
            pack.validate_plugin(doc, set(), set())

    def test_tag_selects_one_plugin(self):
        self.assertEqual(pack.parse_tag("aa/v1.9.38"), ("aa", "1.9.38"))
        self.assertEqual(pack.parse_tag("aa-tile/v5.0.0"), ("aa-tile", "5.0.0"))
        with self.assertRaises(pack.PackError):
            pack.parse_tag("v1.9.38")

    def test_catalog_json_is_two_distinct_plugins(self):
        docs = pack.load_plugins(pack.ROOT)
        pack.validate_catalog(docs)
        self.assertEqual([doc["id"] for doc in docs], ["aa", "aa-tile"])
        self.assertEqual(docs[0]["kind"], "android-auto")
        self.assertEqual(docs[1]["kind"], "program")
        aa_dests = []
        for asset in docs[0]["assets"]:
            aa_dests.extend([asset["dest"]] if "dest" in asset else asset["members"].values())
        tile_dests = list(docs[1]["assets"][0]["members"].values())
        self.assertIn("nbt-aad", aa_dests)
        self.assertNotIn("aa-tile", aa_dests)
        self.assertEqual(tile_dests, ["aa-tile"])

    def test_hash_pin_is_enforced(self):
        doc, files = self.plugin("program")
        body = files["aa-tile"]
        doc["assets"] = [{"name": "aa-tile", "sha256": "0" * 64, "dest": "aa-tile"}]
        with self.assertRaises(pack.PackError):
            pack.collect_files(doc, lambda _url: body)


if __name__ == "__main__":
    unittest.main()
