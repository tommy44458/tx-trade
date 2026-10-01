# Changelog

The product version is managed in `version.json`. Dates record release preparation; public availability is determined by the official download page.

## [Unreleased]

### Added

- Added a canonical product version, version consistency checks, and a local preparation workflow for Chinese and English release notes.
- Added About and Changelog entries to desktop settings, displaying the actual application version and release notes in the selected language.
- API and local service version information now follows the product version, including consistent beta version notation.
- Added macOS Apple Silicon installer builds in GitHub Actions and a signed draft release workflow; public distribution still requires Apple credentials and installation acceptance.
- Added native update checks, downloads, progress, and installation controls. Release updates wait for model work to finish and back up local data; test packages keep updates disabled.

### Unreleased baseline

- The existing local desktop packaging baseline is 0.2.0; no published release history has been established.
