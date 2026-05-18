#!/usr/bin/env bash
set -e
test -f "$1"
echo "energy=-3.45" > result.out
echo "shared=$(cat "$1")" >> result.out
