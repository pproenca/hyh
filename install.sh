#!/usr/bin/env bash
# install.sh - Install hyh CLI tool
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/pproenca/hyh/master/install.sh | bash
#
# With a specific version:
#   curl -fsSL https://raw.githubusercontent.com/pproenca/hyh/master/install.sh | bash -s -- 2.0.0
#
# From git (development):
#   curl -fsSL https://raw.githubusercontent.com/pproenca/hyh/master/install.sh | bash -s -- --git
#   curl -fsSL https://raw.githubusercontent.com/pproenca/hyh/master/install.sh | bash -s -- --git v2.0.0

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PACKAGE_NAME="hyh"
CLI_NAME="hyh"
REPO="pproenca/hyh"
USE_GIT=false
VERSION=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --git)
      USE_GIT=true
      shift
      ;;
    *)
      VERSION="$1"
      shift
      ;;
  esac
done

info() { echo -e "${BLUE}==>${NC} $*"; }
success() { echo -e "${GREEN}==>${NC} $*"; }
warn() { echo -e "${YELLOW}==>${NC} $*"; }
error() { echo -e "${RED}==>${NC} $*" >&2; }

# Check if a command exists
has() { command -v "$1" >/dev/null 2>&1; }

# Detect OS and architecture
detect_platform() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Linux*)  os="linux" ;;
    Darwin*) os="macos" ;;
    *)       error "Unsupported OS: $os"; exit 1 ;;
  esac

  case "$arch" in
    x86_64|amd64)  arch="x86_64" ;;
    arm64|aarch64) arch="aarch64" ;;
    *)             error "Unsupported architecture: $arch"; exit 1 ;;
  esac

  echo "${os}-${arch}"
}

# Install uv if not present
ensure_uv() {
  if has uv; then
    info "uv is already installed: $(uv --version)"
    return 0
  fi

  info "Installing uv (Astral's Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # Source the env to get uv in PATH for this session
  if [[ -f "$HOME/.local/bin/env" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/.local/bin/env"
  elif [[ -f "$HOME/.cargo/env" ]]; then
    # shellcheck source=/dev/null
    source "$HOME/.cargo/env"
  fi

  # Add to PATH for this session
  export PATH="$HOME/.local/bin:$PATH"

  if ! has uv; then
    error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
    exit 1
  fi

  success "uv installed successfully"
}

# Install hyh using uv
install_hyh() {
  local install_spec

  if [[ "$USE_GIT" == true ]]; then
    # Install from git (development)
    if [[ -n "$VERSION" ]]; then
      install_spec="git+https://github.com/${REPO}@${VERSION}"
      info "Installing ${CLI_NAME} ${VERSION} from git..."
    else
      install_spec="git+https://github.com/${REPO}"
      info "Installing ${CLI_NAME} (latest) from git..."
    fi
  else
    # Install from PyPI (default)
    if [[ -n "$VERSION" ]]; then
      install_spec="${PACKAGE_NAME}==${VERSION}"
      info "Installing ${CLI_NAME} ${VERSION} from PyPI..."
    else
      install_spec="${PACKAGE_NAME}"
      info "Installing ${CLI_NAME} (latest) from PyPI..."
    fi
  fi

  # Uninstall existing version if present
  if uv tool list 2>/dev/null | grep -q "^${PACKAGE_NAME}\|^hyh " "; then
    info "Removing existing ${CLI_NAME} installation..."
    uv tool uninstall "${PACKAGE_NAME}" >/dev/null 2>&1 || true
    uv tool uninstall hyh >/dev/null 2>&1 || true
  fi

  # Install hyh
  uv tool install "$install_spec"

  # Ensure PATH includes tool bin directory
  local tool_bin="$HOME/.local/bin"
  if [[ ":$PATH:" != *":$tool_bin:"* ]]; then
    export PATH="$tool_bin:$PATH"
    warn "Added $tool_bin to PATH for this session"
    warn "Add this to your shell profile for persistence:"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
  fi
}

# Verify installation
verify_installation() {
  if ! has "$CLI_NAME"; then
    error "Installation failed: ${CLI_NAME} not found in PATH"
    error "Try opening a new terminal or running: export PATH=\"\$HOME/.local/bin:\$PATH\""
    exit 1
  fi

  local version
  version=$("$CLI_NAME" --version 2>/dev/null || "$CLI_NAME" --help | head -1)
  success "${CLI_NAME} installed successfully!"
  echo ""
  info "Version: $version"
  info "Location: $(which "$CLI_NAME")"
  echo ""
  info "Quick start:"
  echo "    ${CLI_NAME} ping          # Check daemon status"
  echo "    ${CLI_NAME} --help        # Show all commands"
  echo ""
}

# Shell profile detection and PATH instructions
show_shell_instructions() {
  local shell_profile=""
  local shell_name=""

  if [[ -n "${ZSH_VERSION:-}" ]] || [[ "$SHELL" == *"zsh"* ]]; then
    shell_name="zsh"
    shell_profile="$HOME/.zshrc"
  elif [[ -n "${BASH_VERSION:-}" ]] || [[ "$SHELL" == *"bash"* ]]; then
    shell_name="bash"
    if [[ -f "$HOME/.bash_profile" ]]; then
      shell_profile="$HOME/.bash_profile"
    else
      shell_profile="$HOME/.bashrc"
    fi
  elif [[ "$SHELL" == *"fish"* ]]; then
    shell_name="fish"
    shell_profile="$HOME/.config/fish/config.fish"
  fi

  if [[ -n "$shell_profile" ]]; then
    info "To make hyh permanently available, add to $shell_profile:"
    if [[ "$shell_name" == "fish" ]]; then
      echo "    fish_add_path \$HOME/.local/bin"
    else
      echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
    echo ""
  fi
}

main() {
  echo ""
  echo "╔════════════════════════════════════════════╗"
  echo "║  hyh installer                     ║"
  echo "║  CLI orchestration for agentic workflows   ║"
  echo "╚════════════════════════════════════════════╝"
  echo ""

  detect_platform >/dev/null  # Validate platform early
  ensure_uv
  install_hyh
  verify_installation

  # Show shell instructions if PATH wasn't already set
  if ! grep -q '\.local/bin' "$HOME/.zshrc" 2>/dev/null && \
     ! grep -q '\.local/bin' "$HOME/.bashrc" 2>/dev/null && \
     ! grep -q '\.local/bin' "$HOME/.bash_profile" 2>/dev/null; then
    show_shell_instructions
  fi
}

main "$@"
