# NBT store plugins

This repository publishes the packages shown in the NBT store. It does not contain plugin source and it does not compile anything. CI downloads pinned GitHub release assets and packs **one plugin per release**.

`aa` and `aa-tile` are two plugins. They are never combined into one package.

| Plugin | Release tag | Package | Upstream |
| --- | --- | --- | --- |
| Android Auto | `aa/v1.9.38` | Daemon `nbt-aad` plus the iDrive plugin files | [Sq00ky/Supra-iDrive6-Android-Auto](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto) tag `brwsr-ap` |
| Android Auto tile | `aa-tile/v5.0.0` | The `aa-tile` program only | [deathwalker80/nbt-aa-tile](https://github.com/deathwalker80/nbt-aa-tile) tag `v5` (upstream build 5) |

A catalog version is `major.minor.patch`. Upstream build 5 is catalog version `5.0.0`. The bytes stay the upstream program; this repository does not rebuild it.

## What a release contains

Pushing the tag `plugin-id/vX.Y.Z` creates a GitHub release whose title is the plugin's display name. The release has exactly one asset, `plugin-id.zip`.

Android Auto's zip contains `package.npm`, `nbt-aad`, `libNbtAad.so`, `NbtAad.manifest`, `NbtAad.xml`, and the two resource zips. `pf_aa` is not part of that plugin.

The tile zip contains `package.npm` and `aa-tile`. Boot scripts, `hook.txt`, and `aa-menu-look` are not part of that plugin. Those files install or edit the head unit by themselves; the store package does not carry them. The current head-unit installer accepts a PluginApp and the Android Auto package. It does not yet install this program package.

`package.npm` records the file sizes and SHA-256 hashes. Its firmware line says `firmware deferred`: one car's fingerprint does not belong in a package shared by every car. The head unit decides compatibility when the package is offered.

## Add a plugin

Open a pull request that adds `plugins/<id>/plugin.json` and nothing else from the build. Do not commit source trees, object files, or downloaded binaries. CI downloads the pinned assets, checks the hashes, and packs the plugin. A pull request does not publish a release.

```json
{
  "id": "my-plugin",
  "name": "My plugin",
  "version": "1.0.0",
  "kind": "pluginapp",
  "packageId": "NpmMyPlugin",
  "license": "PolyForm-Noncommercial-1.0.0",
  "notice": "Required Notice: state the copyright the upstream license requires",
  "summary": "One line that will appear in the release notes.",
  "upstream": {"repository": "owner/name", "tag": "v1.0.0"},
  "assets": [
    {
      "name": "NpmMyPlugin.zip",
      "sha256": "64 lowercase hex characters",
      "members": {
        "NpmMyPlugin/libNpmMyPlugin.so": "libNpmMyPlugin.so",
        "NpmMyPlugin/NpmMyPlugin.manifest": "NpmMyPlugin.manifest",
        "NpmMyPlugin/NpmMyPlugin.xml": "NpmMyPlugin.xml",
        "NpmMyPlugin/NpmMyPlugin_ALL_Texts.zip": "NpmMyPlugin_ALL_Texts.zip",
        "NpmMyPlugin/NpmMyPlugin_ALL_Images.zip": "NpmMyPlugin_ALL_Images.zip"
      }
    }
  ]
}
```

Rules CI enforces:

- The directory name, the `id`, and the release tag agree. The tag pushed after merge is `my-plugin/v1.0.0`.
- `version` is `major.minor.patch`, each component from 0 to 999, and it matches that tag.
- `kind` is `pluginapp` for an `Npm*` add-on. `packageId` matches `Npm` plus letters and digits, and it is not `NpmManager`. `android-auto` is reserved for `aa`.
- `upstream.repository` is `owner/name` on GitHub. `tag` is the existing release tag. CI downloads `https://github.com/owner/name/releases/download/<tag>/<asset>`.
- Every asset has the SHA-256 of the exact bytes CI must download. If the upstream tag moves, the pull request must update the hash and the reviewers can see that.
- List every file that goes into the package and no others. A whole-file asset uses `"dest": "filename"` instead of `members`.
- A `pluginapp` package is exactly `lib<PackageId>.so`, `<PackageId>.manifest`, `<PackageId>.xml`, `<PackageId>_ALL_Texts.zip`, and `<PackageId>_ALL_Images.zip`.
- Libraries and `nbt-aad` must be ARM ELF32 little-endian. The manifest and XML must be the PluginApp files the head unit already expects. Resource zips must be intact.
- The license must allow this repository to redistribute the bytes. Put that license name in `license` and the notice its text requires in `notice`.

CI rejects install scripts, `hook.txt`, `hmiplugins`, symlinks, extra files, a missing hash, and a package that mixes two plugins. Do not ask CI to clone a source tree or run a compiler. Packaging is the only step.

After the pull request is merged, a maintainer pushes `plugin-id/vX.Y.Z` on that commit. That tag is the only thing that publishes the release.

## License

Each package keeps the license of its upstream project. Android Auto and the tile are PolyForm Noncommercial 1.0.0. Noncommercial use only.

Required Notice: Copyright (c) 2026 nanaisu - NBT-AAD (https://github.com/Sq00ky/Supra-iDrive6-Android-Auto)

This catalog is not affiliated with or endorsed by the NBT-AAD author, BMW, MINI, or Google.
