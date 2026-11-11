# A fixed tag and digest, so every build starts from the same image; without a
# build.yaml the Supervisor passes no BUILD_FROM. Dependabot proposes new ones.
FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0@sha256:31e73797b8c9a0ba62d33f0f46f81174b808fb42bdcb2a902cb0e8c3efdfd8fc

SHELL ["/bin/ash", "-o", "pipefail", "-c"]

COPY requirements.txt /tmp/
# Every package pinned with its hashes, so a later upload to PyPI cannot slip in
RUN pip3 install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && addgroup -S upaq \
    && adduser -S -D -H -G upaq -h /nonexistent -s /sbin/nologin upaq

COPY rootfs /
RUN python3 -m compileall -q /usr/lib/upaq_bridge

ARG BUILD_ARCH BUILD_DATE BUILD_DESCRIPTION BUILD_NAME BUILD_REF BUILD_REPOSITORY BUILD_VERSION
# For the origin of the MQTT discovery
ENV APP_VERSION=${BUILD_VERSION}
# s6-overlay: warnings and errors only, no per-service start/stop info lines
ENV S6_VERBOSITY=1
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION}
