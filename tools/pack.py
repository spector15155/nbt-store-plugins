#!/usr/bin/env python3
"""Download pinned upstream release assets and pack one store plugin.

This does not compile anything. Each plugin directory is packed on its own.
"""
import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"[a-z][a-z0-9-]{0,30}")
VERSION = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ASSET_NAME = re.compile(r"[A-Za-z0-9._-]{1,80}")
DEST = re.compile(r"[A-Za-z0-9._-]{1,80}")
NPM = re.compile(r"Npm(?!Manager)[A-Za-z0-9]{1,37}")
FILE_LIMIT = 32 * 1024 * 1024
PACKAGE_LIMIT = 128 * 1024 * 1024


class PackError(Exception):
    pass


def plugins_dir(root):
    return Path(root) / "plugins"


def load_plugins(root):
    found = []
    base = plugins_dir(root)
    if not base.is_dir():
        raise PackError("plugins/ is missing")
    for path in sorted(p for p in base.iterdir() if p.is_dir()):
        manifest = path / "plugin.json"
        if not manifest.is_file():
            raise PackError(f"{path.name}: plugin.json is missing")
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PackError(f"{path.name}: plugin.json is not valid JSON ({exc})") from exc
        if not isinstance(doc, dict):
            raise PackError(f"{path.name}: plugin.json must be an object")
        doc["_dir"] = path.name
        found.append(doc)
    if not found:
        raise PackError("no plugins declared")
    return found


def version_ok(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        return False
    return all(0 <= int(part) <= 999 for part in value.split("."))


def require(condition, message):
    if not condition:
        raise PackError(message)


def expected_names(doc):
    kind = doc.get("kind")
    if kind == "android-auto":
        identifier = "NbtAad"
        return ["nbt-aad", f"lib{identifier}.so", f"{identifier}.manifest", f"{identifier}.xml",
                f"{identifier}_ALL_Texts.zip", f"{identifier}_ALL_Images.zip"]
    if kind == "pluginapp":
        identifier = doc.get("packageId")
        return [f"lib{identifier}.so", f"{identifier}.manifest", f"{identifier}.xml",
                f"{identifier}_ALL_Texts.zip", f"{identifier}_ALL_Images.zip"]
    if kind == "program":
        return [doc.get("binary")]
    raise PackError(f"{doc.get('id')}: unknown kind")


def validate_plugin(doc, seen_ids, seen_packages):
    identifier = doc.get("id")
    require(isinstance(identifier, str) and ID.fullmatch(identifier), f"{doc.get('_dir')}: id must be a short lowercase slug")
    require(identifier == doc.get("_dir"), f"{identifier}: directory name must match id")
    require(identifier not in seen_ids, f"{identifier}: duplicate id")
    seen_ids.add(identifier)
    require(isinstance(doc.get("name"), str) and 1 <= len(doc["name"]) <= 48 and "\n" not in doc["name"],
            f"{identifier}: name must be one line of at most 48 characters")
    require(version_ok(doc.get("version")), f"{identifier}: version must be major.minor.patch with components 0..999")
    require(isinstance(doc.get("summary"), str) and 1 <= len(doc["summary"]) <= 200 and "\n" not in doc["summary"],
            f"{identifier}: summary must be one line of at most 200 characters")
    require(isinstance(doc.get("license"), str) and doc["license"].strip(), f"{identifier}: license is required")
    notice = doc.get("notice")
    require(isinstance(notice, str) and notice.startswith("Required Notice:"),
            f"{identifier}: notice must start with 'Required Notice:'")
    upstream = doc.get("upstream")
    require(isinstance(upstream, dict), f"{identifier}: upstream object is required")
    repository = upstream.get("repository")
    tag = upstream.get("tag")
    require(isinstance(repository, str) and REPO.fullmatch(repository), f"{identifier}: upstream.repository must be owner/name")
    require(isinstance(tag, str) and tag and ".." not in tag and "/" not in tag and len(tag) <= 80,
            f"{identifier}: upstream.tag must be one path segment")
    assets = doc.get("assets")
    require(isinstance(assets, list) and assets, f"{identifier}: at least one upstream asset is required")
    kind = doc.get("kind")
    package_id = doc.get("packageId")
    if kind == "android-auto":
        require(package_id == "NbtAad", f"{identifier}: android-auto uses package id NbtAad")
    elif kind == "pluginapp":
        require(isinstance(package_id, str) and NPM.fullmatch(package_id),
                f"{identifier}: pluginapp package id must be Npm* and not NpmManager")
        require(package_id not in seen_packages, f"{identifier}: duplicate package id {package_id}")
        seen_packages.add(package_id)
    elif kind == "program":
        require("packageId" not in doc, f"{identifier}: a program has no PluginApp package id")
        binary = doc.get("binary")
        require(isinstance(binary, str) and DEST.fullmatch(binary), f"{identifier}: binary name is invalid")
    else:
        raise PackError(f"{identifier}: kind must be android-auto, pluginapp, or program")
    if kind != "pluginapp" and isinstance(package_id, str):
        require(package_id not in seen_packages, f"{identifier}: duplicate package id {package_id}")
        seen_packages.add(package_id)
    dests = []
    for asset in assets:
        require(isinstance(asset, dict), f"{identifier}: asset entry must be an object")
        name = asset.get("name")
        digest = asset.get("sha256")
        require(isinstance(name, str) and ASSET_NAME.fullmatch(name), f"{identifier}: asset name is invalid")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), f"{identifier}: asset {name} needs a sha256")
        listed = []
        if "dest" in asset:
            require("members" not in asset, f"{identifier}: asset {name} cannot be both a file and a zip")
            listed.append(asset["dest"])
        else:
            members = asset.get("members")
            require(isinstance(members, dict) and members, f"{identifier}: asset {name} needs dest or members")
            for source, dest in members.items():
                require(isinstance(source, str) and isinstance(dest, str), f"{identifier}: member mapping must be strings")
                require(not source.startswith("/") and ".." not in source.split("/"), f"{identifier}: unsafe zip path {source}")
                listed.append(dest)
        for dest in listed:
            require(isinstance(dest, str) and DEST.fullmatch(dest), f"{identifier}: destination name is invalid")
            require(dest != "package.npm" and not dest.endswith(".sh") and "hmiplugins" not in dest.lower(),
                    f"{identifier}: {dest} cannot be packaged")
            dests.append(dest)
    require(sorted(dests) == sorted(expected_names(doc)),
            f"{identifier}: packaged files must be exactly {', '.join(expected_names(doc))}")
    require(len(dests) == len(set(dests)), f"{identifier}: duplicate destination name")


def validate_catalog(docs):
    seen_ids, seen_packages = set(), set()
    android = [doc["id"] for doc in docs if doc.get("kind") == "android-auto"]
    for doc in docs:
        validate_plugin(doc, seen_ids, seen_packages)
    require(len(android) <= 1, "only one android-auto plugin is allowed")


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "nbt-store-plugins"})
    with urllib.request.urlopen(request, timeout=60) as response:
        chunks, total = [], 0
        while True:
            block = response.read(64 * 1024)
            if not block:
                break
            total += len(block)
            if total > FILE_LIMIT:
                raise PackError(f"asset exceeds {FILE_LIMIT} bytes")
            chunks.append(block)
    return b"".join(chunks)


def asset_url(repository, tag, name):
    return f"https://github.com/{repository}/releases/download/{tag}/{name}"


def take_bytes(payload, asset):
    if "dest" in asset:
        return {asset["dest"]: payload}
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise PackError(f"{asset['name']} is not a zip") from exc
    selected = {}
    infos = {info.filename: info for info in archive.infolist()}
    for source, dest in asset["members"].items():
        if source.startswith("/") or ".." in source.split("/"):
            raise PackError(f"unsafe zip path {source}")
        info = infos.get(source)
        if info is None or info.is_dir():
            raise PackError(f"{asset['name']} does not contain {source}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise PackError(f"{asset['name']} contains a symlink at {source}")
        if info.file_size <= 0 or info.file_size > FILE_LIMIT:
            raise PackError(f"{source} is empty or larger than {FILE_LIMIT} bytes")
        selected[dest] = archive.read(info)
    return selected


def elf_kind(name, data, executable):
    kind = 2 if executable else 3
    if (len(data) < 20 or data[:7] != b"\x7fELF\x01\x01\x01" or
            int.from_bytes(data[16:18], "little") != kind or int.from_bytes(data[18:20], "little") != 40):
        raise PackError(f"{name} is not an ARM ELF32 LE {'executable' if executable else 'shared object'}")


def check_pluginapp_files(identifier, files):
    manifest = ET.fromstring(files[f"{identifier}.manifest"])
    expected = {
        "name": identifier, "type": "App", "file": "{pluginDir}/" + identifier,
        "config/parameters/DescFile": "{pluginDir}/" + identifier + ".xml",
        "config/parameters/TextDBFile": "{pluginDir}/" + identifier + "_ALL_Texts.zip",
        "config/parameters/ImageDBFile": "{pluginDir}/" + identifier + "_ALL_Images.zip",
    }
    if any(manifest.findtext(key) != value for key, value in expected.items()):
        raise PackError(f"{identifier}: unexpected PluginApp manifest")
    root = ET.fromstring(files[f"{identifier}.xml"])
    if root.tag != "pluginApps":
        raise PackError(f"{identifier}: expected PluginApp XML")
    for app in root.findall("pluginApp"):
        for section in ("actions", "models"):
            nodes = app.find(section)
            if nodes is not None:
                ids = [node.get("id") for node in nodes]
                if len(ids) != len(set(ids)):
                    raise PackError(f"{identifier}: duplicate HMI identifiers")
    for suffix in ("_ALL_Texts.zip", "_ALL_Images.zip"):
        name = identifier + suffix
        try:
            archive = zipfile.ZipFile(io.BytesIO(files[name]))
        except zipfile.BadZipFile as exc:
            raise PackError(f"{name} is not a zip") from exc
        if archive.testzip() or sum(info.file_size for info in archive.infolist()) > FILE_LIMIT:
            raise PackError(f"{name} is invalid or too large")


def check_files(doc, files):
    require(list(files) == expected_names(doc) or set(files) == set(expected_names(doc)),
            f"{doc['id']}: file set does not match the kind")
    if sum(len(data) for data in files.values()) > PACKAGE_LIMIT:
        raise PackError(f"{doc['id']}: package exceeds {PACKAGE_LIMIT} bytes")
    if doc["kind"] == "program":
        elf_kind(doc["binary"], files[doc["binary"]], True)
        return
    identifier = doc["packageId"]
    elf_kind(f"lib{identifier}.so", files[f"lib{identifier}.so"], False)
    if doc["kind"] == "android-auto":
        elf_kind("nbt-aad", files["nbt-aad"], True)
    check_pluginapp_files(identifier, files)


def package_npm(doc, files):
    kind = "android-auto" if doc["kind"] == "android-auto" else doc["kind"]
    label = doc["packageId"] if doc["kind"] != "program" else doc["id"]
    lines = ["NBTPM-PACKAGE-1", f"{label} {doc['version']} {kind}", "firmware deferred", "requires 0"]
    for name in expected_names(doc):
        data = files[name]
        lines.append(f"{hashlib.sha256(data).hexdigest()} {len(data)} {name}")
    return ("\n".join(lines) + "\n").encode("ascii")


def zip_bytes(doc, files):
    check_files(doc, files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.npm", package_npm(doc, files))
        for name in expected_names(doc):
            archive.writestr(name, files[name])
    return buffer.getvalue()


def collect_files(doc, fetcher):
    files = {}
    repository = doc["upstream"]["repository"]
    tag = doc["upstream"]["tag"]
    for asset in doc["assets"]:
        payload = fetcher(asset_url(repository, tag, asset["name"]))
        digest = hashlib.sha256(payload).hexdigest()
        if digest != asset["sha256"]:
            raise PackError(f"{doc['id']}: {asset['name']} sha256 is {digest}, pin is {asset['sha256']}")
        got = take_bytes(payload, asset)
        overlap = set(files).intersection(got)
        if overlap:
            raise PackError(f"{doc['id']}: duplicate file {sorted(overlap)[0]}")
        files.update(got)
    return files


def pack_plugin(doc, fetcher=download):
    return zip_bytes(doc, collect_files(doc, fetcher))


def find_plugin(docs, identifier):
    matches = [doc for doc in docs if doc["id"] == identifier]
    if len(matches) != 1:
        raise PackError(f"unknown plugin {identifier}")
    return matches[0]


def parse_tag(tag):
    plugin_id, separator, version = tag.partition("/")
    require(separator == "/" and version.startswith("v"), "tag must be <plugin-id>/v<version>")
    version = version[1:]
    require(ID.fullmatch(plugin_id) and version_ok(version), f"tag {tag} is not <plugin-id>/v<version>")
    return plugin_id, version


def write_release_notes(doc, package, path):
    repository = doc["upstream"]["repository"]
    tag = doc["upstream"]["tag"]
    text = "\n".join([
        doc["summary"],
        "",
        f"Upstream: https://github.com/{repository}/releases/tag/{tag}",
        f"Package sha256: {hashlib.sha256(package).hexdigest()}",
        "",
        doc["notice"],
        f"License: {doc['license']}. Noncommercial use only.",
        "",
    ])
    Path(path).write_text(text, encoding="utf-8")


def cmd_check(root):
    docs = load_plugins(root)
    validate_catalog(docs)
    for doc in docs:
        package = pack_plugin(doc)
        print(f"{doc['id']} {doc['version']} {len(package)} bytes")


def cmd_release(root, tag, output):
    docs = load_plugins(root)
    validate_catalog(docs)
    plugin_id, version = parse_tag(tag)
    doc = find_plugin(docs, plugin_id)
    require(doc["version"] == version, f"{plugin_id}: tag version {version} does not match plugin.json {doc['version']}")
    package = pack_plugin(doc)
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    asset = destination / f"{plugin_id}.zip"
    asset.write_bytes(package)
    (destination / "title.txt").write_text(doc["name"] + "\n", encoding="utf-8")
    (destination / "asset-name.txt").write_text(asset.name + "\n", encoding="utf-8")
    write_release_notes(doc, package, destination / "NOTES.md")
    print(asset)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "release"))
    parser.add_argument("tag", nargs="?")
    parser.add_argument("output", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            cmd_check(ROOT)
        else:
            if not args.tag or not args.output:
                parser.error("release needs a tag and an output directory")
            cmd_release(ROOT, args.tag, args.output)
    except (PackError, ET.ParseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
