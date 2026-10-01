#!/bin/sh
# Builds the mysuite-cutout helper binary (macOS Vision framework — requires
# macOS 14+ and the Xcode Command Line Tools, `xcode-select --install`).
#
# Usage: ./build.sh [install-dir]
# With no argument, the binary is left in this directory — copy or symlink
# it into a directory already on your PATH (e.g. /opt/homebrew/bin on Apple
# Silicon, /usr/local/bin on Intel) so mysuite can find it, the same way
# Homebrew already puts rsvg-convert/gs/magick on PATH. Passing a directory
# builds straight into it instead, e.g.: ./build.sh /opt/homebrew/bin

set -e
cd "$(dirname "$0")"

swiftc -O main.swift -o mysuite-cutout

if [ -n "$1" ]; then
    mkdir -p "$1"
    mv mysuite-cutout "$1/mysuite-cutout"
    echo "built and installed to $1/mysuite-cutout"
else
    echo "built ./mysuite-cutout — copy or symlink it into a directory on your PATH, e.g.:"
    echo "  cp mysuite-cutout /opt/homebrew/bin/"
fi
