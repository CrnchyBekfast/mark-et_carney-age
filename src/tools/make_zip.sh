#!/bin/sh
# Build the submission zip with the exactly-correct filename (COL334 A2 §7.1).
#
#   sh src/tools/make_zip.sh 2024CS10388 <partner-roll>
#
# Naming rules the automated checker is strict about:
#   * both roll numbers, exactly as they appear on the LMS
#   * capital letters, no spaces, no hyphens
#   * ALPHABETICALLY SORTED order, regardless of who uploads
#   -> A2_<first>_<second>.zip
#
# This uses an ALLOWLIST of paths rather than excluding junk, which is the
# safer direction: nothing unexpected can leak into the archive.

set -e

if [ $# -ne 2 ]; then
    echo "usage: sh src/tools/make_zip.sh <roll1> <roll2>" >&2
    echo "   eg: sh src/tools/make_zip.sh 2024CS10388 2024CS10999" >&2
    exit 2
fi

R1=$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')
R2=$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')

# alphabetical order, regardless of argument order
SORTED=$(printf '%s\n%s\n' "$R1" "$R2" | sort)
A=$(printf '%s' "$SORTED" | sed -n 1p)
B=$(printf '%s' "$SORTED" | sed -n 2p)
ZIP="A2_${A}_${B}.zip"

echo "==> structure check"
sh src/tools/check_structure.sh || {
    echo "Refusing to build $ZIP while the structure check fails." >&2
    exit 1
}

echo
echo "==> cleaning generated files"
rm -rf src/__pycache__ src/tools/__pycache__ 2>/dev/null || true
find . -name '.DS_Store' -delete 2>/dev/null || true

echo
echo "==> building $ZIP"
rm -f "$ZIP"
# Allowlist. report.pdf is added only if it exists, so the script is usable
# before the report is finished.
SET="server client src README.md"
[ -f Makefile ]   && SET="$SET Makefile"
[ -f report.pdf ] && SET="$SET report.pdf"
# shellcheck disable=SC2086
zip -r -q "$ZIP" $SET \
    -x '*.DS_Store' -x '*__MACOSX*' -x '*__pycache__*' -x '*.pyc'

echo
echo "==> contents of $ZIP"
unzip -l "$ZIP"

echo
if [ ! -f report.pdf ]; then
    echo "REMINDER: report.pdf is not in this archive yet."
fi
echo "Expected tree (§7.1):"
echo "  server/run-server"
echo "  client/run-trader"
echo "  client/run-market-data"
echo "  src/<sources>"
echo "  README.md"
echo "  report.pdf"
echo
echo "Built: $ZIP"
