#!/usr/bin/env bash
# Build the Expo web bundle and ship it to the box that serves /app.
#
#   scripts/deploy_webapp.sh                 # build + leak check + deploy
#   scripts/deploy_webapp.sh --build-only    # build + leak check, no ssh
#   scripts/deploy_webapp.sh --check DIR     # leak check an existing build
#
# The API's /app route has pointed operators here since it landed, but the
# script never existed, so every web deploy so far was a hand-run
# export + tar + scp. Two things go wrong when that is done by hand:
#
# 1. `expo export` reads mobile/app/.env, which holds the laptop's
#    EXPO_PUBLIC_DEV_API_TOKEN and an EXPO_PUBLIC_API_URL that is not always
#    production. Anything EXPO_PUBLIC_* that the code references is inlined
#    into a bundle served to anyone who loads /app. So the build runs with
#    EXPO_NO_DOTENV=1 and a scrubbed environment, and takes its EXPO_PUBLIC_*
#    values from the eas.json profile instead — the same public values the
#    phone builds ship.
# 2. A half-copied directory is a broken site. The bundle is unpacked next to
#    the live one and swapped in with two renames, and the previous build is
#    kept for rollback.
#
# The leak check is the backstop for (1): it fails the run if the build
# contains the local dev token's value or the name of any server-side secret,
# and (after a build) if the profile's API URL did not make it into the bundle.
# There is no "no localhost" rule: client.ts carries a legitimate
# http://localhost:8000 fallback and Firebase ships localhost strings, so that
# rule could only ever cry wolf.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${REPO}/mobile/app"
PROFILE="${EAS_PROFILE:-preview}"
BOX="${TRADER_BOX:-ubuntu@51.159.101.227}"
SSH_KEY="${TRADER_SSH_KEY:-${HOME}/.ssh/id_prod_scaleway}"
REMOTE_ROOT="/opt/ai-trader"
REMOTE_OWNER="deploy"
PUBLIC_URL="${TRADER_PUBLIC_URL:-https://trader.fusapp.com}"
KEEP_BACKUPS=3

# The only env var NAME the bundle may not contain. Expo inlines EXPO_PUBLIC_*
# values for every variable the code reads, so any reference to this one is
# a credential on its way into the browser. Server-side secret names are not
# listed: settings.tsx legitimately tells the operator "DEV_API_TOKEN lives in
# secrets.env", so a name rule there could only cry wolf. Those are covered by
# value instead (secret_values below).
FORBIDDEN_NAMES=(EXPO_PUBLIC_DEV_API_TOKEN)
# Values shorter than this are too likely to collide with bundle text.
MIN_SECRET_LEN=12

die() { echo "deploy_webapp: $*" >&2; exit 1; }

# NAME=value for every credential-looking line in the laptop's .env files,
# so the check can look for the secrets themselves. EXPO_PUBLIC_* values are
# public by design (Firebase's web config is meant to ship) — except the dev
# token, which is the credential this whole check exists for.
secret_values() {
  local f
  local -a files
  # Colon-separated override exists for the tests.
  IFS=: read -ra files <<<"${WEBAPP_SECRET_ENV_FILES:-${APP_DIR}/.env:${REPO}/agent/.env}"
  for f in "${files[@]}"; do
    [[ -f "${f}" ]] || continue
    grep -E '^[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*=' "${f}" \
      | grep -vE '^EXPO_PUBLIC_' || true
    grep -E '^EXPO_PUBLIC_DEV_API_TOKEN=' "${f}" || true
  done | tr -d "\"'" | awk -F= -v min="${MIN_SECRET_LEN}" \
    'length(substr($0, index($0, "=") + 1)) >= min'
}

# Never echoes a value — only the variable's name.
leak_check() {
  local dir="$1" line name value hits=0
  [[ -f "${dir}/index.html" ]] || die "leak check: ${dir} has no index.html — not a web build"

  while IFS= read -r line; do
    name="${line%%=*}"
    value="${line#*=}"
    if grep -rqF -- "${value}" "${dir}"; then
      echo "LEAK: the value of ${name} (from a local .env) is in the build" >&2
      hits=$((hits + 1))
    fi
  done < <(secret_values)
  for name in "${FORBIDDEN_NAMES[@]}"; do
    if grep -rqw -- "${name}" "${dir}"; then
      echo "LEAK: ${name} is referenced in the build" >&2
      hits=$((hits + 1))
    fi
  done
  if [[ -n "${EXPECT_API_URL:-}" ]] && ! grep -rqF -- "${EXPECT_API_URL}" "${dir}/_expo"; then
    echo "WRONG TARGET: ${EXPECT_API_URL} is not in the bundle — built against another API?" >&2
    hits=$((hits + 1))
  fi
  [[ "${hits}" -eq 0 ]] || die "leak check failed (${hits} finding(s)) — not deploying"
  echo "leak check: clean (${dir})"
}

# EXPO_PUBLIC_* from the eas.json build profile, as KEY=VALUE lines.
profile_env() {
  command -v jq >/dev/null || die "jq is required to read eas.json"
  jq -er --arg p "${PROFILE}" \
    '.build[$p].env // error("no env for profile \($p)") | to_entries[]
     | select(.key | startswith("EXPO_PUBLIC_")) | "\(.key)=\(.value)"' \
    "${APP_DIR}/eas.json"
}

build() {
  local out="$1" line
  local -a envs=()
  # A while-read loop, not mapfile: macOS still ships bash 3.2.
  while IFS= read -r line; do envs+=("${line}"); done < <(profile_env)
  [[ "${#envs[@]}" -gt 0 ]] || die "profile ${PROFILE} defines no EXPO_PUBLIC_* values"
  echo "==> expo export (web, profile=${PROFILE}, ${#envs[@]} public vars, .env ignored)"
  # env -i drops whatever EXPO_PUBLIC_* the operator's shell has exported;
  # EXPO_NO_DOTENV stops expo from reading mobile/app/.env behind our back.
  (cd "${APP_DIR}" && env -i \
    HOME="${HOME}" PATH="${PATH}" TERM="${TERM:-dumb}" \
    EXPO_NO_DOTENV=1 NODE_ENV=production CI=1 \
    "${envs[@]}" \
    npx expo export --platform web --output-dir "${out}" --clear)
}

deploy() {
  local dir="$1" stamp tarball
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  tarball="${WORK}/webapp.tgz"
  tar -C "${dir}" -czf "${tarball}" .
  echo "==> uploading $(du -h "${tarball}" | cut -f1) to ${BOX}"
  scp -q -i "${SSH_KEY}" "${tarball}" "${BOX}:/tmp/webapp-${stamp}.tgz"

  # shellcheck disable=SC2087  # expanded locally on purpose
  ssh -i "${SSH_KEY}" "${BOX}" bash -s <<EOF
set -euo pipefail
cd "${REMOTE_ROOT}"
src="/tmp/webapp-${stamp}.tgz"
trap 'rm -f "\${src}"; sudo rm -rf webapp.new' EXIT
sudo rm -rf webapp.new
sudo mkdir webapp.new
sudo tar -C webapp.new -xzf "\${src}"
sudo test -f webapp.new/index.html
sudo chown -R ${REMOTE_OWNER}:${REMOTE_OWNER} webapp.new
if [ -d webapp ]; then sudo mv webapp "webapp.bak-${stamp}"; fi
sudo mv webapp.new webapp
ls -d webapp.bak-* 2>/dev/null | sort | head -n -${KEEP_BACKUPS} | xargs -r sudo rm -rf
echo "swapped in; rollback: sudo rm -rf ${REMOTE_ROOT}/webapp && sudo mv ${REMOTE_ROOT}/webapp.bak-${stamp} ${REMOTE_ROOT}/webapp"
EOF
}

smoke() {
  local entry code
  entry="$(grep -oE '/app/_expo/static/js/web/[^"]+\.js' "$1/index.html" | head -1)"
  [[ -n "${entry}" ]] || die "smoke: no JS entry in index.html"
  for path in /app "${entry}"; do
    code="$(curl -s -o /dev/null -w '%{http_code}' -A 'Mozilla/5.0' "${PUBLIC_URL}${path}")"
    [[ "${code}" == 200 ]] || die "smoke: ${PUBLIC_URL}${path} -> ${code}"
  done
  echo "smoke: ${PUBLIC_URL}/app and its entry bundle serve 200"
}

main() {
  case "${1:-}" in
    --check)
      [[ -n "${2:-}" ]] || die "--check needs a build directory"
      leak_check "$2"
      return
      ;;
    --build-only | "") ;;
    *) die "unknown argument: $1" ;;
  esac

  local out
  WORK="$(mktemp -d -t webapp-build.XXXXXX)"
  trap 'rm -rf "${WORK}"' EXIT
  out="${WORK}/dist"
  build "${out}"
  EXPECT_API_URL="$(profile_env | sed -n 's/^EXPO_PUBLIC_API_URL=//p')" leak_check "${out}"
  if [[ "${1:-}" == --build-only ]]; then
    echo "build-only: $(du -sh "${out}" | cut -f1) built and checked, not deployed"
    return
  fi
  deploy "${out}"
  smoke "${out}"
}

main "$@"
