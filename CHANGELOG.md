# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0a2] - 2025-12-23

### Changed

- Harden GitHub workflow permissions (deny-all default, explicit per-job)
- Add `persist-credentials: false` to all checkouts
- Replace tag-triggered publish with manual dispatch release workflow
- Add Sigstore attestations for PyPI packages
- Disable uv cache in release workflow (cache-poisoning mitigation)

### Fixed

- Remove pre-commit hooks incompatible with freethreaded Python 3.14

## [0.1.0a1] - 2025-12-22

### Added

- Initial alpha release
- Task state management with dependency-aware execution (DAG validation)
- Thread-safe operations for concurrent task handling
- Client-daemon architecture via Unix sockets
- Command execution runtimes (local and Docker)
- Git integration for safe operations
- Dynamic versioning via `importlib.metadata`
- Automated release workflow script

[0.1.0a1]: https://github.com/pproenca/hyh/releases/tag/v0.1.0a1
[0.1.0a2]: https://github.com/pproenca/hyh/releases/tag/v0.1.0a2
[unreleased]: https://github.com/pproenca/hyh/compare/v0.1.0a2...HEAD
