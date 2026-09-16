# Rebuilding the bundled ArgyllCMS tools

The bundled Apple Silicon versions of `spotread` and `ccxxmake` were built from
the official ArgyllCMS 3.5.0 source archive plus the patch stored in this
directory.

## Source inputs

- Archive: `../source/Argyll_V3.5.0_src.zip`
- Archive SHA-256:
  `f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f`
- Patch: `source-patch/macos-multiple-instruments.patch`

The archive was downloaded from the official ArgyllCMS source page:
<https://www.argyllcms.com/downloadsrc.html>.

## Prerequisites

- Apple Silicon macOS
- Xcode command-line tools
- Jam (`/opt/homebrew/bin/jam` was used for the verified build)

## Build procedure

From the repository root:

```sh
mkdir -p /tmp/argyll-build
unzip vendor/argyll/source/Argyll_V3.5.0_src.zip -d /tmp/argyll-build
cd /tmp/argyll-build/Argyll_V3.5.0
patch -p1 < /path/to/colourspace_patch_rx/vendor/argyll/macos-arm64/source-patch/macos-multiple-instruments.patch
/opt/homebrew/bin/jam -q -fJambase -j4
```

Replace `/path/to/colourspace_patch_rx` with the repository's absolute path.
The required outputs are:

- `spectro/spotread`
- `spectro/ccxxmake`

Copy those two files into `vendor/argyll/macos-arm64/bin/`, preserve executable
permissions, and rerun the application regression tests.

The build was verified on 2026-09-16. A full `jam install` is not required for
these two tools; in the verified environment that target later failed while
generating the unrelated `RefMediumGamut.gam` file, after both required
executables had already linked successfully.
