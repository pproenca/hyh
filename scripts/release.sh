#!/usr/bin/env bash
#
# Release automation script for hyh
# Usage: ./scripts/release.sh [major|minor|patch|alpha|beta|rc|stable]
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Valid bump types
VALID_TYPES="major minor patch alpha beta rc stable"

usage() {
    echo "Usage: $0 <bump-type>"
    echo ""
    echo "Bump types:"
    echo "  alpha   - Increment alpha version (0.1.0a1 -> 0.1.0a2)"
    echo "  beta    - Move to beta (0.1.0a2 -> 0.1.0b1)"
    echo "  rc      - Move to release candidate (0.1.0b1 -> 0.1.0rc1)"
    echo "  stable  - Move to stable (0.1.0rc1 -> 0.1.0)"
    echo "  patch   - Increment patch (0.1.0 -> 0.1.1)"
    echo "  minor   - Increment minor (0.1.0 -> 0.2.0)"
    echo "  major   - Increment major (0.1.0 -> 1.0.0)"
    exit 1
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Check arguments
if [[ $# -ne 1 ]]; then
    usage
fi

BUMP_TYPE="$1"

# Validate bump type
if [[ ! " $VALID_TYPES " =~ " $BUMP_TYPE " ]]; then
    log_error "Invalid bump type: $BUMP_TYPE"
    usage
fi

# Check git is clean
if [[ -n $(git status --porcelain) ]]; then
    log_error "Working directory is not clean. Commit or stash changes first."
fi

# Check we're on a valid branch
CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "master" && "$CURRENT_BRANCH" != "main" ]]; then
    log_warning "Not on master/main branch (current: $CURRENT_BRANCH)"
    read -rp "Continue anyway? [y/N] " response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Get current version
CURRENT_VERSION=$(uv version --short 2>/dev/null || echo "unknown")
log_info "Current version: $CURRENT_VERSION"

# Preview version bump
log_info "Previewing version bump..."
NEW_VERSION=$(uv version --bump "$BUMP_TYPE" --dry-run 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[a-z0-9]*' | tail -1)
echo ""
echo -e "  ${YELLOW}$CURRENT_VERSION${NC} -> ${GREEN}$NEW_VERSION${NC}"
echo ""

# Confirm
read -rp "Proceed with release? [y/N] " response
if [[ ! "$response" =~ ^[Yy]$ ]]; then
    log_info "Release cancelled."
    exit 0
fi

# Bump version
log_info "Bumping version..."
uv version --bump "$BUMP_TYPE"

# Get the actual new version
NEW_VERSION=$(uv version --short)
log_success "Version bumped to $NEW_VERSION"

# Generate changelog entry from commits
log_info "Generating changelog entry..."
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
DATE=$(date +%Y-%m-%d)

# Create temporary changelog entry
TEMP_CHANGELOG=$(mktemp)
{
    echo "## [$NEW_VERSION] - $DATE"
    echo ""

    # Group commits by type
    if [[ -n "$LAST_TAG" ]]; then
        COMMIT_RANGE="$LAST_TAG..HEAD"
    else
        COMMIT_RANGE="HEAD"
    fi

    # Added (feat:)
    ADDED=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^feat" 2>/dev/null || true)
    if [[ -n "$ADDED" ]]; then
        echo "### Added"
        echo ""
        echo "$ADDED"
        echo ""
    fi

    # Fixed (fix:)
    FIXED=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^fix" 2>/dev/null || true)
    if [[ -n "$FIXED" ]]; then
        echo "### Fixed"
        echo ""
        echo "$FIXED"
        echo ""
    fi

    # Changed (refactor:, perf:, chore:)
    CHANGED=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^refactor\|^perf\|^chore" 2>/dev/null || true)
    if [[ -n "$CHANGED" ]]; then
        echo "### Changed"
        echo ""
        echo "$CHANGED"
        echo ""
    fi

    # Documentation (docs:)
    DOCS=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^docs" 2>/dev/null || true)
    if [[ -n "$DOCS" ]]; then
        echo "### Documentation"
        echo ""
        echo "$DOCS"
        echo ""
    fi
} > "$TEMP_CHANGELOG"

# Update CHANGELOG.md
if [[ -f "CHANGELOG.md" ]]; then
    log_info "Updating CHANGELOG.md..."
    # Insert after "## [Unreleased]" line
    sed -i.bak "/^## \[Unreleased\]/r $TEMP_CHANGELOG" CHANGELOG.md
    rm -f CHANGELOG.md.bak

    # Update comparison links at bottom
    if grep -q "\[Unreleased\]:" CHANGELOG.md; then
        # Update unreleased link
        sed -i.bak "s|\[Unreleased\]:.*|\[Unreleased\]: https://github.com/pproenca/hyh/compare/v$NEW_VERSION...HEAD|" CHANGELOG.md
        # Add new version link if not exists
        if ! grep -q "\[$NEW_VERSION\]:" CHANGELOG.md; then
            if [[ -n "$LAST_TAG" ]]; then
                echo "[$NEW_VERSION]: https://github.com/pproenca/hyh/compare/$LAST_TAG...v$NEW_VERSION" >> CHANGELOG.md
            else
                echo "[$NEW_VERSION]: https://github.com/pproenca/hyh/releases/tag/v$NEW_VERSION" >> CHANGELOG.md
            fi
        fi
        rm -f CHANGELOG.md.bak
    fi
fi
rm -f "$TEMP_CHANGELOG"

# Commit
log_info "Committing changes..."
git add -A
git commit -m "chore: release v$NEW_VERSION"

# Tag
log_info "Creating tag v$NEW_VERSION..."
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"

# Build
log_info "Building package..."
uv build --no-sources

log_success "Build complete! Artifacts in dist/"

# Push
read -rp "Push to remote? [y/N] " response
if [[ "$response" =~ ^[Yy]$ ]]; then
    log_info "Pushing to remote..."
    git push
    git push --tags
    log_success "Pushed to remote"
fi

# Publish
read -rp "Publish to PyPI? [y/N] " response
if [[ "$response" =~ ^[Yy]$ ]]; then
    log_info "Publishing to PyPI..."
    uv publish
    log_success "Published to PyPI!"
else
    log_info "Skipping publish. Run 'uv publish' manually when ready."
fi

echo ""
log_success "Release v$NEW_VERSION complete!"
echo ""
echo "Next steps:"
echo "  - Verify the release on PyPI: https://pypi.org/project/hyh/"
echo "  - Create GitHub release: https://github.com/pproenca/hyh/releases/new?tag=v$NEW_VERSION"
