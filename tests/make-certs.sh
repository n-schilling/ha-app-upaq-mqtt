#!/bin/bash
# Makes a throwaway CA and a broker certificate for "localhost" for the TLS
# tests against a real broker (tests/mosquitto.conf, listener 8883).
#
# Usage: tests/make-certs.sh <dir>
set -euo pipefail
dir=$1
mkdir -p "$dir"
cd "$dir"
# The extensions Python's strict certificate check asks for
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=UPAQ test CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" \
    -addext "subjectKeyIdentifier=hash" -keyout ca.key -out ca.pem 2> /dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" \
    -keyout server.key -out server.csr 2> /dev/null
printf '%s\n' subjectAltName=DNS:localhost basicConstraints=CA:FALSE \
    authorityKeyIdentifier=keyid subjectKeyIdentifier=hash extendedKeyUsage=serverAuth > server.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 2 \
    -extfile server.ext -out server.pem 2> /dev/null
# The broker runs as its own user in the container
chmod 644 server.key
rm -f server.csr server.ext ca.srl
