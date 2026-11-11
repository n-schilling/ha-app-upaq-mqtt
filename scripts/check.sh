#!/bin/bash
# Runs locally what the CI workflow runs, so a push only confirms what already
# passed here: the changelog check, shellcheck, ruff, the unit tests and the
# tests against a real Mosquitto broker, the image build and its start without
# options. Keep the steps in step with .github/workflows/ci.yaml.
#
# Usage: scripts/check.sh [--no-broker] [--no-image]
#
# Needs uv for the Python environment (.venv) and Docker for the broker and
# the image (built for the architecture of this machine; CI builds amd64 and
# aarch64). shellcheck and ruff come from PATH, else from a container image or
# uvx. Only the app configuration linter (frenck/action-addon-linter) runs in
# CI alone.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

readonly IMAGE=upaq-mqtt:check
readonly BROKER=upaq-mqtt-check-broker
# Keep in step with ci.yaml
readonly RUFF_VERSION=0.17.3
readonly TEST_PACKAGES=(pytest==9.1.1 pytest-aiohttp==1.1.1)
readonly SHELLCHECK_IMAGE=koalaman/shellcheck:v0.11.0
readonly MOSQUITTO_IMAGE=eclipse-mosquitto:2.1.2-alpine@sha256:75d6662dee52c947dc9dd9c29f1d8d7ffef78ddaf38001caa09755681e8bbc98

no_broker=false
no_image=false
for arg in "$@"; do
    case "$arg" in
        --no-broker) no_broker=true ;;
        --no-image) no_image=true ;;
        *) echo "unknown option $arg" >&2; exit 2 ;;
    esac
done

docker=true
if ! docker info > /dev/null 2>&1; then
    docker=false
fi

failed=()
skipped=()

run() { # run <name> <command...>
    local name=$1
    shift
    echo "==> $name"
    if "$@"; then
        echo "    ok: $name"
    else
        echo "    FAILED: $name"
        failed+=("$name")
    fi
    echo
}

version=$(sed -n 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' upaq_mqtt/config.yaml)

shell_scripts=(
    upaq_mqtt/rootfs/etc/s6-overlay/s6-rc.d/bridge/run
    upaq_mqtt/rootfs/etc/s6-overlay/s6-rc.d/bridge/finish
    scripts/check.sh
    tests/make-certs.sh
)
shellcheck_run() {
    if command -v shellcheck > /dev/null; then
        shellcheck --shell=bash --severity=warning "${shell_scripts[@]}"
    elif $docker; then
        docker run --rm -v "$PWD":/mnt:ro -w /mnt "$SHELLCHECK_IMAGE" \
            --shell=bash --severity=warning "${shell_scripts[@]}"
    else
        echo "neither shellcheck nor Docker" >&2
        return 1
    fi
}
ruff_run() {
    if command -v ruff > /dev/null && [[ "$(ruff --version)" == "ruff $RUFF_VERSION" ]]; then
        ruff check upaq_mqtt/rootfs/usr/lib/upaq_bridge tests
    else
        uvx "ruff==$RUFF_VERSION" check upaq_mqtt/rootfs/usr/lib/upaq_bridge tests
    fi
}
venv() {
    [[ -x .venv/bin/python ]] || uv venv -q -p 3.14 .venv || return 1
    uv pip install -q -p .venv --require-hashes -r upaq_mqtt/requirements.txt &&
        uv pip install -q -p .venv "${TEST_PACKAGES[@]}"
}
unit_tests() {
    if $no_broker || ! $docker; then
        .venv/bin/pytest -q
        return
    fi
    docker rm -f "$BROKER" > /dev/null 2>&1
    local certs
    certs=$(mktemp -d) || return 1
    tests/make-certs.sh "$certs" || return 1
    # Free ports of its own, so a broker already running here does not matter
    docker run -d --name "$BROKER" -p 127.0.0.1::1883 -p 127.0.0.1::8883 \
        -v "$PWD/tests/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
        -v "$certs:/mosquitto/certs:ro" "$MOSQUITTO_IMAGE" > /dev/null || return 1
    local port tls_port
    port=$(docker port "$BROKER" 1883/tcp | head -n 1 | sed 's/.*://')
    tls_port=$(docker port "$BROKER" 8883/tcp | head -n 1 | sed 's/.*://')
    sleep 1
    MQTT_TEST_HOST=127.0.0.1 MQTT_TEST_PORT="$port" MQTT_TEST_TLS_PORT="$tls_port" \
        MQTT_TEST_CERTS="$certs" .venv/bin/pytest -q
    local result=$?
    ((result)) && docker logs "$BROKER"
    docker rm -f "$BROKER" > /dev/null
    rm -rf "$certs"
    return "$result"
}
start_without_options() {
    # The bridge loads as its own user, as the s6 service starts it, finds no
    # options and says what to set
    local out
    out=$(docker run --rm --network none --user upaq -e PYTHONPATH=/usr/lib \
        --entrypoint python3 "$IMAGE" -m upaq_bridge 2>&1)
    echo "$out"
    grep -q '/data/options.json not readable' <<< "$out"
}

run "Changelog has the version $version" bash -c \
    '.github/scripts/changelog-section.sh upaq_mqtt/CHANGELOG.md "$1" > /dev/null' _ "$version"
run "Shell scripts" shellcheck_run
run "Python" ruff_run
run "Test environment" venv
if $no_broker || ! $docker; then
    skipped+=("Tests against a real MQTT broker")
fi
run "Unit tests" unit_tests
if $no_image || ! $docker; then
    skipped+=("Image build and start")
else
    run "Build" docker build -q --build-arg "BUILD_VERSION=$version" -t "$IMAGE" upaq_mqtt
    run "Start without options" start_without_options
fi

echo "===================="
# The ${arr[@]+"${arr[@]}"} form keeps an empty array from tripping `set -u` in the bash 3.2 of macOS
for name in ${skipped[@]+"${skipped[@]}"}; do
    echo "skipped: $name"
done
if ((${#failed[@]})); then
    echo "FAILED: ${failed[*]}"
    exit 1
fi
echo "All checks passed."
