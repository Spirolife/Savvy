#!/usr/bin/env bash
# Publish the Android app as a GitHub Release, where Obtainium on the phone
# finds it and offers the update (docs/android-setup.md, "Installing the app").
#
#   android/release.sh                  # notes list the commits since the last release
#   android/release.sh "What changed"   # your own notes instead
#
# Builds and tests the committed app with android/build.sh, signs it with the
# key in android/.android-home (it never leaves this PC), tags the commit
# v0.1.<commit count>, pushes only that tag, and uploads Savvy-<version>.apk
# with its sha256. The repo is public, so the APK is too; it holds no keys and
# only works with a pairing code from your PC.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

die() { echo "release: $*" >&2; exit 1; }

command -v gh >/dev/null || die "needs the GitHub CLI (gh)"
gh auth status >/dev/null 2>&1 || die "gh isn't signed in: run 'gh auth login'"
[ -f "$here/.android-home/debug.keystore" ] || die "no signing key at android/.android-home/debug.keystore.
A new key would change the app's SHA-1 (breaking Google sign-in) and the phone
would refuse the update. Restore the backed-up key first."
[ -z "$(git status --porcelain -- android)" ] || die "android/ has uncommitted changes; commit them first so the release matches a commit"

count="$(git rev-list --count HEAD)"
version="0.1.$count"
tag="v$version"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "$tag already exists: commit something new first"

prev="$(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null || true)"
if [ $# -gt 0 ]; then
    notes="$*"
elif [ -n "$prev" ]; then
    notes="Changes since $prev:"$'\n\n'"$(git log --no-merges --format='- %s' "$prev..HEAD")"
else
    notes="$(git log --no-merges --format='- %s' -20)"
fi

echo "Building and testing Savvy $version..."
SAVVY_VERSION_NAME="$version" "$here/build.sh" testDebugUnitTest assembleDebug

out="$here/build/release"
mkdir -p "$out"
apk="$out/Savvy-$version.apk"
cp "$here/app/build/outputs/apk/debug/app-debug.apk" "$apk"
(cd "$out" && sha256sum "Savvy-$version.apk" > "Savvy-$version.apk.sha256")

git tag -a "$tag" -m "Savvy for Android $version"
git push origin "refs/tags/$tag"
gh release create "$tag" "$apk" "$apk.sha256" \
    --title "Savvy for Android $version" \
    --notes "$notes"$'\n\n'"Update in Obtainium, or download \`Savvy-$version.apk\` below and open it on the phone."
echo "Published $tag."
