#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")" || exit 1

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'check.sh must run inside the git repository of the project\n'
    exit 1
fi

workdirs=()
cleanup() {
    [ ${#workdirs[@]} -gt 0 ] && rm -rf "${workdirs[@]}"
    return 0
}
trap cleanup EXIT

WORKDIR=""
make_workdir() {
    WORKDIR=$(mktemp -d) || return 1
    workdirs+=("$WORKDIR")
}

APP_ID="io.github.gor3pig.Custicon"
failures=()

step() {
    local label="$1"
    shift
    printf '\n\033[1;34m▶ %s\033[0m\n' "$label"
    if "$@"; then
        printf '\033[1;32m✔ %s\033[0m\n' "$label"
    else
        printf '\033[1;31m✘ %s\033[0m\n' "$label"
        failures+=("$label")
    fi
}

no_comments_in_sources() {
    local found
    found=$(git ls-files --cached --others --exclude-standard -- '*.py' '*meson.build' \
        | xargs -r grep -nE '^\s*(#|""")')
    if [ -n "$found" ]; then
        printf '%s\n' "$found"
        return 1
    fi
}

no_long_dashes() {
    local found
    found=$(git ls-files --cached --others --exclude-standard -- . ':!COPYING' \
        | xargs -r grep -nIE $'\xe2\x80\x94|\xe2\x80\x93')
    if [ -n "$found" ]; then
        printf '%s\n' "$found"
        return 1
    fi
}

versions_match() {
    local meson config release
    meson=$(sed -n "s/^  version: '\(.*\)',$/\1/p" meson.build)
    config=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' src/custicon/config.py)
    release=$(sed -n 's/^ *<release version="\([^"]*\)".*/\1/p' "data/$APP_ID.metainfo.xml.in" | head -n 1)
    printf 'meson.build %s, config.py %s, metainfo %s\n' "$meson" "$config" "$release"
    [ -n "$meson" ] && [ "$meson" = "$config" ] && [ "$meson" = "$release" ]
}

validate_desktop_file() {
    local workdir status
    make_workdir || return 1
    workdir=$WORKDIR
    cp "data/$APP_ID.desktop.in" "$workdir/$APP_ID.desktop"
    desktop-file-validate "$workdir/$APP_ID.desktop"
}

validate_metainfo() {
    local workdir
    make_workdir || return 1
    workdir=$WORKDIR
    cp "data/$APP_ID.metainfo.xml.in" "$workdir/$APP_ID.metainfo.xml"
    appstreamcli validate --no-net --explain "$workdir/$APP_ID.metainfo.xml"
}

flatpak_repository_files_match() {
    local failed=0 key url file fingerprint command
    key=$(base64 -w0 build-aux/custicon.gpg)
    url=$(sed -n 's/^Url=//p' build-aux/custicon.flatpakrepo)
    for file in build-aux/custicon.flatpakref build-aux/custicon.flatpakrepo; do
        if [ "$(sed -n 's/^GPGKey=//p' "$file")" != "$key" ]; then
            printf '%s: the key is not the one of build-aux/custicon.gpg\n' "$file"
            failed=1
        fi
        if [ "$(sed -n 's/^Url=//p' "$file")" != "$url" ]; then
            printf '%s: the repository address differs from custicon.flatpakrepo\n' "$file"
            failed=1
        fi
    done
    if [ "$(sed -n 's/^Name=//p' build-aux/custicon.flatpakref)" != "$APP_ID" ]; then
        printf 'custicon.flatpakref: the application name is not %s\n' "$APP_ID"
        failed=1
    fi
    if ! grep -qxF 'Branch=master' build-aux/custicon.flatpakref; then
        printf 'custicon.flatpakref: the branch must be master\n'
        failed=1
    fi
    command="flatpak install --user ${url%repo/}custicon.flatpakref"
    for file in data/website/index.html data/website/fr/index.html; do
        if ! grep -qF -- "<code>$command</code>" "$file" || ! grep -qF -- "data-copy=\"$command\"" "$file"; then
            printf '%s: the install command must be: %s\n' "$file" "$command"
            failed=1
        fi
    done
    if grep -qE -- "--repo-url|--gpg-keys" .github/workflows/release.yml; then
        printf 'release.yml: the bundle is not signed, so it must not embed the repository address or key\n'
        failed=1
    fi
    fingerprint=$(gpg --show-keys --with-colons build-aux/custicon.gpg | sed -n 's/^fpr:::::::::\([0-9A-F]*\):$/\1/p' | head -n 1)
    if [ -z "$fingerprint" ] || ! grep -qxF "  SIGNING_KEY_ID: $fingerprint" .github/workflows/pages.yml; then
        printf 'pages.yml: SIGNING_KEY_ID must be the fingerprint of build-aux/custicon.gpg (%s)\n' "$fingerprint"
        failed=1
    fi
    return $failed
}

validate_manifest() {
    python3 - "build-aux/$APP_ID.json" <<'PY'
import json
import sys

EXPECTED = [
    "--share=ipc",
    "--socket=fallback-x11",
    "--socket=wayland",
    "--nosocket=x11",
    "--device=dri",
    "--filesystem=home:ro",
    "--talk-name=org.gtk.vfs.Metadata",
    "--talk-name=org.gtk.vfs.Daemon",
]
arguments = json.load(open(sys.argv[1]))["finish-args"]
if sorted(arguments) != sorted(EXPECTED):
    print("finish-args differ from the reviewed permissions:", sorted(set(arguments) ^ set(EXPECTED)))
    sys.exit(1)
PY
}

translatable_files_are_listed() {
    local missing=0 file
    while IFS= read -r file; do
        if ! grep -qxF "$file" po/POTFILES.in; then
            printf 'Missing from po/POTFILES.in: %s\n' "$file"
            missing=1
        fi
    done < <(git ls-files --cached --others --exclude-standard -- 'src/*.py' \
        | xargs -r grep -lE '(^|[^A-Za-z_])(_|ngettext)\(' \
        | grep -v '/i18n\.py$')
    return $missing
}

translations_are_complete() {
    local workdir language statistics incomplete=0
    make_workdir || return 1
    workdir=$WORKDIR
    xgettext --files-from=po/POTFILES.in --from-code=UTF-8 \
        --keyword=_ --keyword=ngettext:1,2 --package-name=custicon \
        --output="$workdir/custicon.pot" 2>/dev/null
    while IFS= read -r language; do
        [ -n "$language" ] || continue
        msgmerge --quiet --no-fuzzy-matching "po/$language.po" "$workdir/custicon.pot" \
            --output-file="$workdir/$language.po"
        statistics=$(msgfmt --check --statistics --output-file=/dev/null "$workdir/$language.po" 2>&1) || incomplete=1
        printf '%s: %s\n' "$language" "$statistics"
        if grep -qE 'non traduit|untranslated|approximati|fuzzy' <<<"$statistics"; then
            incomplete=1
        fi
    done < po/LINGUAS
    return $incomplete
}

french_guillemets_use_non_breaking_spaces() {
    local found
    found=$(grep -nF -e '« ' -e ' »' po/fr.po)
    if [ -n "$found" ]; then
        printf 'po/fr.po: use a non-breaking space inside « » on these lines:\n%s\n' "$found"
        return 1
    fi
}

french_typography_is_consistent() {
    local found
    found=$(grep -nP '^msgstr(\[\d\])? ".*(\w\x27\w| [:?!;](\s|\\n|"|$))' po/fr.po)
    if [ -n "$found" ]; then
        printf 'po/fr.po: use a typographic apostrophe and a non-breaking space before : ? ! ; on these lines:\n%s\n' "$found"
        return 1
    fi
}

with_session_bus() {
    if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && command -v dbus-run-session >/dev/null; then
        dbus-run-session -- "$@"
    else
        "$@"
    fi
}

unit_tests() {
    CUSTICON_REQUIRE_METADATA=1 with_session_bus python3 -W ignore -m unittest discover -s tests -t .
}

interface_tests() {
    if ! command -v gtk4-broadwayd >/dev/null; then
        printf 'gtk4-broadwayd is missing: it is needed for the interface tests\n'
        return 1
    fi
    local display=":$((200 + RANDOM % 700))"
    local runtime="${XDG_RUNTIME_DIR:-}" own_runtime=""
    if [[ -z "$runtime" ]]; then
        make_workdir || return 1
        own_runtime=$WORKDIR
        chmod 700 "$own_runtime"
        runtime="$own_runtime"
    fi
    XDG_RUNTIME_DIR="$runtime" gtk4-broadwayd "$display" >/dev/null 2>&1 &
    local server=$!
    sleep 1
    XDG_RUNTIME_DIR="$runtime" GDK_BACKEND=broadway BROADWAY_DISPLAY="$display" \
        CUSTICON_REQUIRE_METADATA=1 CUSTICON_REQUIRE_DISPLAY=1 with_session_bus \
        python3 -W ignore -m unittest tests.test_ui_smoke tests.test_website tests.test_symbolic tests.test_windowstate
    local status=$?
    kill "$server" 2>/dev/null
    return $status
}

step "No comments in the code" no_comments_in_sources
step "Plain hyphens only" no_long_dashes
step "Versions match" versions_match
step "Desktop entry" validate_desktop_file
step "AppStream metainfo" validate_metainfo
step "Flatpak manifest" validate_manifest
step "Flatpak repository files" flatpak_repository_files_match
step "Translatable files are listed" translatable_files_are_listed
step "Translations are complete" translations_are_complete
step "French guillemets" french_guillemets_use_non_breaking_spaces
step "French typography" french_typography_is_consistent
step "Unit tests" unit_tests
step "Interface tests" interface_tests

printf '\n'
if [ ${#failures[@]} -eq 0 ]; then
    printf '\033[1;32m✅ All checks passed.\033[0m\n'
else
    printf '\033[1;31m❌ To fix: %s\033[0m\n' "${failures[*]}"
    exit 1
fi
