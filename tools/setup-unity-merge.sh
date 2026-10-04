#!/usr/bin/env sh
set -eu
: "${UNITY_EDITOR:?Set UNITY_EDITOR to the selected Unity executable}"
merge="$(dirname "$UNITY_EDITOR")/../Tools/UnityYAMLMerge"
test -x "$merge"
git config --local merge.unityyamlmerge.name 'Unity Smart Merge'
git config --local merge.unityyamlmerge.driver "\"$merge\" merge -p %O %B %A %A"
git config --local merge.unityyamlmerge.recursive binary
