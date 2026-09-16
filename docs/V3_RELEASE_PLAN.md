# ColourSpace Patch Rx v3 Release Plan

Last updated: 2026-09-16

## Current state

The measurement, correction, report, export, and cross-platform compatibility
work is functionally complete. The current private evaluation build is
`3.0.0-beta.1`.

This beta was initially intended for the developer's own use. It remains the
validated baseline while the repository is prepared for a public GitHub
prerelease.

## Licensing decision

The application uses `spotread` and `ccxxmake` from ArgyllCMS. The bundled
Apple Silicon copies are locally built from ArgyllCMS 3.5.0 and include a
macOS multi-instrument patch.

Decision recorded on 2026-09-16: release the complete project under
`AGPL-3.0-or-later`. The repository includes the official ArgyllCMS 3.5.0
source archive, the local macOS multi-instrument patch, upstream license texts,
and reproducible build instructions for the bundled tools.

This record is an engineering compliance checklist, not legal advice.

References:

- <https://www.argyllcms.com/doc/ArgyllDoc.html>
- <https://www.argyllcms.com/commercialuse.html>

## Private beta.1 scope

- Version displayed by the application: `3.0.0-beta.1`
- macOS bundle marketing version: `3.0.0`
- macOS bundle build number: `30001`
- Distribution: private/local only
- GitHub release: none
- Purpose: extended real-world use and regression discovery

The beta may use the currently bundled, locally built Apple Silicon ArgyllCMS
tools because their complete corresponding source, local modifications,
licenses, and build instructions are now included in the repository. Asset and
release-artifact audits must still pass before publication.

## Public v3.0.0 release gates

Complete each gate in order. Do not treat a later gate as complete while an
earlier one remains unresolved.

### 1. Licensing

- [x] Select `AGPL-3.0-or-later` for the complete project.
- [x] Add the project `LICENSE` file.
- [x] Add third-party notices and attribution for software dependencies.
- [x] Provide the corresponding ArgyllCMS source and build instructions.
- [x] Update the About dialog and README to match the chosen licensing model.
- Audit every bundled image/document and remove anything without documented
  redistribution permission.

Owner decision recorded on 2026-09-16: retain the seven existing test-pattern
images and accept the unresolved redistribution risk. The images are identified
as third-party material and excluded from the project's AGPL grant in
`THIRD_PARTY_NOTICES.md`. This notice does not itself provide redistribution
permission.

### 2. Source and Git hygiene

- Commit all intended source, tests, resources, build definitions, and
  dependency files on the development branch.
- Confirm that no caches, build output, credentials, personal paths, or test
  packages are tracked.
- Confirm that the working tree is clean before release builds.
- Preserve the existing public version with an appropriate historical tag if
  its version can be verified.

### 3. Code freeze and regression testing

- Stop adding features.
- Run the automated test suite.
- Test instrument scanning and multiple connected instruments.
- Test Correction, Manual Measurement, and Report workflows.
- Test CCMX selection, enable/disable behavior, and exported files.
- Test pause, resume, stop, repeated measurements, and application shutdown.
- Confirm that no `spotread` process remains after closing the application.

### 4. Version and documentation

- Change the application version from the beta identifier to `3.0.0`.
- Update About/copyright information.
- Update README installation, feature, platform, dependency, and licensing
  information.
- Prepare concise v3.0.0 release notes and known limitations.
- Make DMG, EXE, source archive, and application metadata agree on the version.

### 5. Release builds

- Build the Apple Silicon macOS DMG on macOS.
- Build the Windows EXE on Windows using Python 3.11, the pinned dependencies
  in `requirements-windows.txt`, and `build-windows.ps1`.
- If distributing macOS publicly, decide whether to use Developer ID signing
  and notarization; document any Gatekeeper limitations if not used.
- Record checksums for final downloadable artifacts.

### 6. Artifact validation

- Install and run the generated DMG rather than only running from source.
- Run the generated EXE rather than only running from source.
- Prefer clean machines or clean user accounts without project virtual
  environments.
- Repeat a short real-instrument scan, measurement, report export, and clean
  shutdown test on both platforms.
- Check version information and included third-party notices inside each
  artifact.

### 7. GitHub publication

- Commit the final release state on the development branch.
- Merge the reviewed release state into `main`.
- Create an annotated `v3.0.0` tag on the exact tested commit.
- Push `main` and the tag.
- Create the GitHub Release from that tag.
- Attach the tested DMG, EXE, required source package, checksums, and release
  notes.

No push, tag, or GitHub Release should be created automatically without the
owner's explicit confirmation.

## Beta feedback to record

During private use, record only reproducible issues that matter for release:

- platform and OS version;
- instrument model and number of connected instruments;
- selected CCMX state;
- page and operation being performed;
- exact status/error message;
- whether the problem repeats after restarting;
- relevant exported files or screenshots.

Fix beta defects on the development branch, rerun the appropriate regression
tests, and increment to `beta.2` only when a new private build is needed.
