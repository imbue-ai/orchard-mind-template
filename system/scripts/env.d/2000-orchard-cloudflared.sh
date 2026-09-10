#!/usr/bin/env bash
# env.d unit: cloudflared, the tunnel binary orchard's orchard-tunnel program
# shells out to (see system/apps/orchard/src/orchard/tunnel_runner.py). Not
# apt-installable in the pinned snapshot mirror, so it is fetched here from
# Cloudflare's own GitHub releases, same shape as the Fortress unit
# (1000-playwright-fortress.sh): pinned version + sha256, per-arch, a fast
# satisfied-check, no marker files.
#
# env.d contract: idempotent with a fast satisfied-check -- NO marker files.
# The converger re-runs every unit on every boot; a satisfied unit exits 0 in
# milliseconds, and version stability comes from the pins below (a re-run
# never silently changes versions -- only a pin bump, landed via
# update-self/update-installed-template, does).
set -euo pipefail

_log() {
    printf '[env.d/orchard-cloudflared] %s\n' "$*"
}

readonly _CLOUDFLARED_VERSION="2026.9.0"
readonly _CLOUDFLARED_AMD64_URL="https://github.com/cloudflare/cloudflared/releases/download/${_CLOUDFLARED_VERSION}/cloudflared-linux-amd64"
readonly _CLOUDFLARED_AMD64_SHA256="53b7a7a5420d188758d24341294acb0d1bca54296548ac05e38811a694ac6134"
readonly _CLOUDFLARED_ARM64_URL="https://github.com/cloudflare/cloudflared/releases/download/${_CLOUDFLARED_VERSION}/cloudflared-linux-arm64"
readonly _CLOUDFLARED_ARM64_SHA256="98aca3173f73248fad6180fc75dade2d186a6e54fa807e088108cb4345de8efe"
readonly _CLOUDFLARED_INSTALL_PATH="/usr/local/bin/cloudflared"

main() {
    # Fast satisfied-check: any cloudflared already on PATH is good enough --
    # orchard-tunnel just needs the binary, not this exact pinned build.
    if command -v cloudflared >/dev/null 2>&1; then
        _log "cloudflared already installed ($(command -v cloudflared)), satisfied"
        return 0
    fi

    local url sha256
    case "$(uname -m)" in
        x86_64) url="$_CLOUDFLARED_AMD64_URL"; sha256="$_CLOUDFLARED_AMD64_SHA256" ;;
        aarch64) url="$_CLOUDFLARED_ARM64_URL"; sha256="$_CLOUDFLARED_ARM64_SHA256" ;;
        *) _log "unsupported architecture $(uname -m); orchard-tunnel will fail to start"; return 1 ;;
    esac

    _log "downloading cloudflared ${_CLOUDFLARED_VERSION} from $url"
    local tmp
    tmp="$(mktemp)"
    # shellcheck disable=SC2064
    trap "rm -f '$tmp'" RETURN
    if ! curl -fsSL -o "$tmp" "$url"; then
        _log "download FAILED; the next converge retries"
        return 1
    fi
    if [ "$(sha256sum "$tmp" | awk '{print $1}')" != "$sha256" ]; then
        _log "SHA256 mismatch -- refusing to install"
        return 1
    fi
    install -m 0755 "$tmp" "$_CLOUDFLARED_INSTALL_PATH"
    _log "install complete ($_CLOUDFLARED_INSTALL_PATH)"
}

main "$@"
