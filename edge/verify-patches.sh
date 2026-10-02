#!/usr/bin/env bash
# Checks that api/patches and control/patches apply cleanly to the pinned upstream commits and
# (if runtime/upstream exists) that the patched clones contain no changes missing from the patches.
# Run before every commit that touches upstream code. Exit code != 0 on any mismatch.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
eval "$(grep -E '^(BACKEND|WEB)_(REPO|COMMIT)=' setup.sh | sed 's/ *#.*//')"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
rc=0

check () {  # name repo commit patchdir runtime_dir
  local name=$1 repo=$2 commit=$3 patches=$4 rt=$5 src=$2
  [ -d "$rt/.git" ] && src="$rt"   # local clone has all upstream objects, no network needed
  git -c init.defaultBranch=main -c advice.detachedHead=false clone -q "$src" "$TMP/$name"
  git -C "$TMP/$name" checkout -q "$commit"
  if ! git -C "$TMP/$name" -c user.name=verify -c user.email=verify@local am -q "$patches"/*.patch; then
    echo "FAIL $name: patch series does not apply to ${commit:0:7}"; rc=1; return
  fi
  echo "ok   $name: $(ls "$patches"/*.patch | wc -l) patches apply to ${commit:0:7}"
  [ -d "$rt/.git" ] || return 0
  # compare the tracked tree (ignores files setup.sh copies in, e.g. Dockerfile, nginx.conf)
  local dirty
  dirty="$(git -C "$rt" status --porcelain --untracked-files=all \
           | grep -vE '^\?\? (Dockerfile|nginx.conf)$' || true)"
  if [ -n "$dirty" ]; then
    echo "FAIL $name: uncommitted changes in $rt:"; echo "$dirty" | sed 's/^/       /'; rc=1
  fi
  # the temp clone was made from $rt, so its HEAD commit is available here
  if ! git -C "$TMP/$name" diff --quiet HEAD "$(git -C "$rt" rev-parse HEAD)"; then
    echo "FAIL $name: $rt has commits that are not exported as patches:"
    git -C "$TMP/$name" diff --stat HEAD "$(git -C "$rt" rev-parse HEAD)" | sed 's/^/       /'; rc=1
  fi
}

check backend "$BACKEND_REPO" "$BACKEND_COMMIT" "$ROOT/api/patches"     runtime/upstream/backend
check web     "$WEB_REPO"     "$WEB_COMMIT"     "$ROOT/control/patches" runtime/upstream/web
exit $rc
