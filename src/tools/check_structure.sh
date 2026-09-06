#!/bin/sh
# Pre-submission structure check (COL334 A2 §7.1, roadmap §11).
# Run from the submission root:   sh src/tools/check_structure.sh
#
# A zip that fails the automated structure check costs 10% and buys exactly one
# correction, so this exists to fail loudly on your machine instead.

FAIL=0
WARN=0

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; WARN=$((WARN + 1)); }

echo "Submission structure check"
echo "-------------------------------------------------------------"

# ---- required launchers -------------------------------------------------
for f in server/run-server client/run-trader client/run-market-data ; do
    if [ ! -f "$f" ]; then
        bad "$f is missing (MANDATORY, §7.2)"
        continue
    fi
    ok "$f exists"

    if [ -x "$f" ]; then
        ok "$f is executable"
    else
        bad "$f is not executable  -> chmod +x $f"
    fi

    # exec is mandatory: without it, experiment.py's killpg hits the wrapper
    # and orphans the real process.
    if grep -q '^[[:space:]]*exec[[:space:]]' "$f"; then
        ok "$f uses exec"
    else
        bad "$f does not use exec (§6.0.2 requires it)"
    fi

    # Passing arguments through is mandatory.
    if grep -q '"\$@"' "$f"; then
        ok "$f forwards \"\$@\""
    else
        bad "$f does not forward \"\$@\""
    fi

    # A single CRLF makes #!/bin/sh fail with "bad interpreter". Editing on
    # macOS and running on FreeBSD makes this a live risk.
    if od -c "$f" 2>/dev/null | grep -q '\\r'; then
        bad "$f has CRLF line endings -> run: perl -pi -e 's/\r\n/\n/' $f"
    else
        ok "$f has LF line endings"
    fi
done

# ---- required tree ------------------------------------------------------
[ -d src ]         && ok "src/ exists"        || bad "src/ is missing"
[ -n "$(ls -A src 2>/dev/null)" ] && ok "src/ is non-empty" \
                                  || bad "src/ is empty"
[ -f README.md ]   && ok "README.md exists"   || bad "README.md is missing (§7.4)"
[ -f report.pdf ]  && ok "report.pdf exists"  || warn "report.pdf not present yet (§8)"

# ---- launcher targets actually resolve ---------------------------------
for pair in "server/run-server:src/server.py" \
            "client/run-trader:src/trader.py" \
            "client/run-market-data:src/market_data.py" ; do
    L=$(echo "$pair" | cut -d: -f1)
    T=$(echo "$pair" | cut -d: -f2)
    if [ -f "$T" ]; then
        ok "$L target $T exists"
    else
        bad "$L points at $T, which is missing"
    fi
done

# ---- junk that must not be shipped ------------------------------------
if find . -name '.DS_Store' -o -name '__MACOSX' 2>/dev/null | grep -q . ; then
    warn "macOS junk present (.DS_Store / __MACOSX) -- make_zip.sh excludes it"
else
    ok "no macOS junk"
fi
if find . -name '__pycache__' -type d 2>/dev/null | grep -q . ; then
    warn "__pycache__ present -- make_zip.sh excludes it"
else
    ok "no __pycache__"
fi

# ---- python syntax -----------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
    if python3 -m py_compile src/*.py 2>/dev/null; then
        ok "all src/*.py compile"
    else
        bad "a file in src/ has a syntax error"
    fi
    rm -rf src/__pycache__ 2>/dev/null
else
    warn "python3 not found on PATH"
fi

echo "-------------------------------------------------------------"
if [ "$FAIL" -eq 0 ]; then
    echo "PASS  ($WARN warning(s))"
    exit 0
fi
echo "FAIL  $FAIL problem(s), $WARN warning(s)"
exit 1
