# Bundled ArgyllCMS tools

This directory contains locally built Apple Silicon binaries from ArgyllCMS
3.5.0 (4 February 2026):

- `spotread`
- `ccxxmake`

The build includes two macOS device-enumeration fixes recorded in
`source-patch/macos-multiple-instruments.patch`. It was verified on macOS with
two X-Rite i1Display3 devices and one X-Rite ColorMunki connected at the same
time. All three devices enumerated and produced readings independently.

The complete official 3.5.0 source archive is included at
`../source/Argyll_V3.5.0_src.zip`. Its SHA-256 is
`f8576ce5589fd15620abb73ff049ea31f55ddbd1bba6d1ffa87452658e7bc85f`.
See `BUILDING.md` for the reproducible build procedure.

Upstream source: https://www.argyllcms.com/downloadsrc.html

ArgyllCMS is copyright Graeme W. Gill and other contributors. See
`License.txt` and `License2.txt`. Distribution of these modified binaries must
include access to the complete corresponding source and comply with the
applicable ArgyllCMS license terms.
