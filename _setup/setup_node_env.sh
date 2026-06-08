#!/usr/bin/env bash
# Node.js environment setup — Linux and macOS
#
# Ensures npm dependencies are installed for a skill directory.
# Optionally installs a specific Node version via nvm or fnm when .nvmrc / .node-version is present.
#
# Usage:
#   ./setup_node_env.sh [skill_dir]
#
# Arguments:
#   skill_dir   Path to the skill directory (default: current directory)
#
# Exit codes:
#   0  Success
#   1  Error

set -euo pipefail

SKILL_DIR="${1:-$PWD}"
SKILL_DIR="$(cd "$SKILL_DIR" && pwd)"

log() { echo "[setup_node_env] $*"; }
err() { echo "[setup_node_env] ERROR: $*" >&2; exit 1; }

# ── 1. Ensure node is available ──────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  # Try nvm
  if [ -f "$HOME/.nvm/nvm.sh" ]; then
    log "Loading nvm …"
    # shellcheck disable=SC1090
    source "$HOME/.nvm/nvm.sh"
  fi
  # Try fnm
  if ! command -v node &>/dev/null && command -v fnm &>/dev/null; then
    log "Loading fnm …"
    eval "$(fnm env)"
  fi
  if ! command -v node &>/dev/null; then
    err "node is not installed. Install Node.js from https://nodejs.org or via nvm/fnm."
  fi
fi

NODE_VERSION="$(node --version)"
log "node $NODE_VERSION found at $(command -v node)"

# ── 2. Switch Node version if .nvmrc / .node-version is present ──────────────
VERSION_FILE=""
if [ -f "$SKILL_DIR/.nvmrc" ]; then
  VERSION_FILE="$SKILL_DIR/.nvmrc"
elif [ -f "$SKILL_DIR/.node-version" ]; then
  VERSION_FILE="$SKILL_DIR/.node-version"
fi

if [ -n "$VERSION_FILE" ]; then
  REQUIRED_VERSION="$(cat "$VERSION_FILE" | tr -d '[:space:]')"
  log "Required Node version: $REQUIRED_VERSION"
  if command -v nvm &>/dev/null 2>&1 || [ -f "$HOME/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1090
    source "$HOME/.nvm/nvm.sh" 2>/dev/null || true
    nvm use "$REQUIRED_VERSION" 2>/dev/null || nvm install "$REQUIRED_VERSION" && nvm use "$REQUIRED_VERSION"
    log "Using node $(node --version) via nvm"
  elif command -v fnm &>/dev/null; then
    eval "$(fnm env)"
    fnm use "$REQUIRED_VERSION" 2>/dev/null || fnm install "$REQUIRED_VERSION" && fnm use "$REQUIRED_VERSION"
    log "Using node $(node --version) via fnm"
  else
    log "No version manager found; using system node $NODE_VERSION (required: $REQUIRED_VERSION)"
  fi
fi

# ── 3. Install npm dependencies ───────────────────────────────────────────────
if [ -f "$SKILL_DIR/package.json" ]; then
  log "Running npm install in $SKILL_DIR …"
  cd "$SKILL_DIR"
  npm install
  log "npm install complete."
else
  log "No package.json found in $SKILL_DIR — skipping npm install."
fi

log "Done."
