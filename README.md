# Harness

**Harness** is a thread-safe daemon for orchestrating dev workflows. It uses a Unix socket server with a "dumb client" architecture: the CLI client has zero validation logic (stdlib only, <50ms startup), while all Pydantic validation happens in the daemon.

- **Runtime:** Python 3.13t (free-threaded, no GIL)
- **Build System:** uv

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/pproenca/harness/master/install.sh | bash
```

## Development

```bash
make install              # Install dependencies
make test                 # Run all tests
make check                # Run lint + typecheck + test
```
