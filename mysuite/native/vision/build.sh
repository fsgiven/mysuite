#!/bin/sh
# Builds the mysuite-vision helper (macOS Vision: OCR + barcode/QR reading; needs the Xcode Command Line Tools,
# `xcode-select --install`). Usage: ./build.sh [install-dir]   e.g. ./build.sh /opt/homebrew/bin
set -e
cd "$(dirname "$0")"
swiftc -O main.swift -o mysuite-vision
if [ -n "$1" ]; then
    mkdir -p "$1"
    mv mysuite-vision "$1/mysuite-vision"
    echo "built and installed to $1/mysuite-vision"
else
    echo "built ./mysuite-vision — copy it onto your PATH, e.g.: cp mysuite-vision /opt/homebrew/bin/"
fi
