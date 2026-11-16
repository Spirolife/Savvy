#!/usr/bin/env bash
# Build the Savvy Android app inside the savvy-android-build container
# (android/tools/Containerfile), so the host needs no JDK or Android SDK.
#
#   android/build.sh                      # assembleDebug -> app/build/outputs/apk/debug/app-debug.apk
#   android/build.sh testDebugUnitTest    # any Gradle tasks
#
# Runs are serialized with a lock, so several people (or agents) can call it at once.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
image=localhost/savvy-android-build

if ! podman image exists "$image"; then
    echo "Building the $image image (one time, ~1 GB)..." >&2
    podman build -t "$image" -f "$here/tools/Containerfile" "$here/tools"
fi
podman volume exists savvy-gradle-cache || podman volume create savvy-gradle-cache >/dev/null

if [ $# -eq 0 ]; then set -- assembleDebug; fi

exec 9>"$here/.build.lock"
flock 9
podman run --rm -v "$here":/work:z -v savvy-gradle-cache:/root/.gradle -w /work "$image" \
    gradle --no-daemon --console=plain -q --warning-mode=none "$@"
