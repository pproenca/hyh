#!/usr/bin/env bash
#
# hyh setup - one command to orchestrate your AI workflows
#
# Usage:
#   curl -sSL https://hyh.dev/setup | bash
#   curl -sSL https://raw.githubusercontent.com/pproenca/hyh/master/scripts/setup.sh | bash
#
# Or locally:
#   ./scripts/setup.sh
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Colors & Formatting
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}▸${RESET} $1"; }
success() { echo -e "${GREEN}✓${RESET} $1"; }
warn()    { echo -e "${YELLOW}!${RESET} $1"; }
error()   { echo -e "${RED}✗${RESET} $1" >&2; }
step()    { echo -e "\n${BOLD}$1${RESET}"; }

# ─────────────────────────────────────────────────────────────────────────────
# Dependency checks
# ─────────────────────────────────────────────────────────────────────────────
check_deps() {
  local missing=()

  command -v git >/dev/null 2>&1 || missing+=("git")
  command -v uv >/dev/null 2>&1 || missing+=("uv")

  if [[ ${#missing[@]} -gt 0 ]]; then
    error "Missing dependencies: ${missing[*]}"
    echo ""
    echo "Install them first:"
    [[ " ${missing[*]} " =~ " uv " ]] && echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    [[ " ${missing[*]} " =~ " git " ]] && echo "  brew install git  # or apt install git"
    exit 1
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# JSON helpers (pure bash, no jq dependency)
# ─────────────────────────────────────────────────────────────────────────────
json_has_key() {
  local file="$1" key="$2"
  grep -q "\"$key\"" "$file" 2>/dev/null
}

# ─────────────────────────────────────────────────────────────────────────────
# Main setup
# ─────────────────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BOLD}hyh${RESET} ${DIM}— hold your horses${RESET}"
  echo -e "${DIM}AI workflow orchestration for Claude Code${RESET}"
  echo ""

  check_deps

  # ─────────────────────────────────────────────────────────────────────────
  step "1. Installing hyh"
  # ─────────────────────────────────────────────────────────────────────────

  if uvx hyh --help >/dev/null 2>&1; then
    info "Updating hyh..."
  else
    info "Installing hyh..."
  fi

  if uv tool install hyh --upgrade --quiet 2>/dev/null; then
    success "hyh installed"
  else
    # Fallback: install from GitHub
    info "Installing from GitHub..."
    uv tool install "git+https://github.com/pproenca/hyh.git" --upgrade --quiet
    success "hyh installed from source"
  fi

  # ─────────────────────────────────────────────────────────────────────────
  step "2. Configuring project"
  # ─────────────────────────────────────────────────────────────────────────

  # Find git root
  if ! GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null); then
    warn "Not in a git repository"
    GIT_ROOT="."
  fi

  CLAUDE_DIR="$GIT_ROOT/.claude"
  SETTINGS_FILE="$CLAUDE_DIR/settings.json"

  mkdir -p "$CLAUDE_DIR"

  # ─────────────────────────────────────────────────────────────────────────
  # Handle settings.json - merge or create
  # ─────────────────────────────────────────────────────────────────────────

  HYH_MCP_CONFIG='"hyh": {
      "command": "uvx",
      "args": ["hyh", "mcp"]
    }'

  if [[ -f "$SETTINGS_FILE" ]]; then
    if json_has_key "$SETTINGS_FILE" '"hyh"'; then
      success "MCP server already configured"
    else
      info "Adding hyh to existing settings..."

      # Backup
      cp "$SETTINGS_FILE" "$SETTINGS_FILE.bak"

      # Try to add hyh to mcpServers
      if json_has_key "$SETTINGS_FILE" "mcpServers"; then
        # Add to existing mcpServers
        if command -v python3 >/dev/null 2>&1; then
          python3 << 'PYTHON'
import json
import sys

settings_file = sys.argv[1] if len(sys.argv) > 1 else ".claude/settings.json"

with open(settings_file) as f:
    settings = json.load(f)

if "mcpServers" not in settings:
    settings["mcpServers"] = {}

settings["mcpServers"]["hyh"] = {
    "command": "uvx",
    "args": ["hyh", "mcp"]
}

with open(settings_file, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
PYTHON
          success "Added hyh to mcpServers"
        else
          warn "Could not auto-merge. Add manually to $SETTINGS_FILE:"
          echo "    $HYH_MCP_CONFIG"
        fi
      else
        # No mcpServers key, need to add it
        warn "No mcpServers in settings. Add manually:"
        echo "    \"mcpServers\": { $HYH_MCP_CONFIG }"
      fi
    fi
  else
    # Create new settings file
    info "Creating $SETTINGS_FILE"
    cat > "$SETTINGS_FILE" << 'EOF'
{
  "mcpServers": {
    "hyh": {
      "command": "uvx",
      "args": ["hyh", "mcp"]
    }
  }
}
EOF
    success "Created Claude settings"
  fi

  # ─────────────────────────────────────────────────────────────────────────
  step "3. Creating plan templates"
  # ─────────────────────────────────────────────────────────────────────────

  PLANS_DIR="$GIT_ROOT/docs/plans"
  TEMPLATE="$PLANS_DIR/.template.xml"

  mkdir -p "$PLANS_DIR"

  if [[ -f "$TEMPLATE" ]]; then
    success "Plan template exists"
  else
    info "Creating plan template..."
    cat > "$TEMPLATE" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!--
  hyh Plan Template

  Copy this file and rename to: YYYY-MM-DD-feature-name.xml
  Then import with: uvx hyh plan import --file docs/plans/your-plan.xml
-->
<plan goal="Describe what this plan achieves">
  <!--
  Dependencies: which tasks must complete before others can start
  <dep from="2" to="1"/> means Task 2 depends on Task 1
  -->
  <dependencies>
    <dep from="2" to="1"/>
  </dependencies>

  <task id="1" role="implementer" model="haiku">
    <description>Short description of the task</description>

    <!-- Tools the agent can use: Read, Edit, Bash, Grep, Glob, etc. -->
    <tools>Read, Edit, Bash</tools>

    <!-- Files the agent should focus on -->
    <scope>
      <include>src/module/file.py</include>
      <include>tests/test_file.py</include>
      <exclude>src/unrelated/</exclude>
    </scope>

    <!-- What the agent receives from previous tasks -->
    <interface>
      <input>Description of inputs/context</input>
      <output>What this task produces</output>
    </interface>

    <!-- Step-by-step instructions -->
    <instructions><![CDATA[
1. Write a failing test for the feature
2. Run tests to verify failure: pytest tests/ -v
3. Implement the minimal code to pass
4. Run tests to verify success
5. Commit with message "feat(scope): description"
    ]]></instructions>

    <!-- Constraints or guidelines -->
    <constraints>Follow existing patterns in codebase</constraints>

    <!-- Commands to verify task completion -->
    <verification>
      <command>pytest tests/ -v</command>
      <command>ruff check src/</command>
    </verification>

    <!-- What defines success -->
    <success>All tests pass, no lint errors</success>
  </task>

  <task id="2" role="implementer" model="haiku">
    <description>Task that depends on Task 1</description>
    <tools>Read, Edit, Bash</tools>
    <scope>
      <include>src/module/</include>
    </scope>
    <instructions><![CDATA[
1. Build on the work from Task 1
2. Add additional functionality
3. Test and commit
    ]]></instructions>
    <verification>
      <command>pytest tests/ -v</command>
    </verification>
    <success>Feature complete and tested</success>
  </task>
</plan>
EOF
    success "Created docs/plans/.template.xml"
  fi

  # ─────────────────────────────────────────────────────────────────────────
  step "4. Verifying installation"
  # ─────────────────────────────────────────────────────────────────────────

  if uvx hyh --help >/dev/null 2>&1; then
    success "hyh is working"
  else
    error "hyh installation failed"
    exit 1
  fi

  # ─────────────────────────────────────────────────────────────────────────
  # Done!
  # ─────────────────────────────────────────────────────────────────────────
  echo ""
  echo -e "${GREEN}${BOLD}Setup complete!${RESET}"
  echo ""
  echo -e "${DIM}Quick start:${RESET}"
  echo ""
  echo "  1. Create a plan:"
  echo -e "     ${DIM}cp docs/plans/.template.xml docs/plans/my-feature.xml${RESET}"
  echo ""
  echo "  2. Import it:"
  echo -e "     ${DIM}uvx hyh plan import --file docs/plans/my-feature.xml${RESET}"
  echo ""
  echo "  3. Run Claude:"
  echo -e "     ${DIM}claude${RESET}"
  echo ""
  echo "  4. Tell Claude:"
  echo -e "     ${DIM}\"Execute the hyh workflow - claim tasks, follow instructions, mark complete\"${RESET}"
  echo ""
  echo -e "${DIM}Commands:${RESET}"
  echo "  uvx hyh get-state       # View workflow state"
  echo "  uvx hyh task claim      # Claim next available task"
  echo "  uvx hyh task complete   # Mark current task done"
  echo "  uvx hyh plan reset      # Clear workflow state"
  echo ""
}

main "$@"
