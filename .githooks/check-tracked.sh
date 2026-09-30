#!/bin/sh
# Checks that no confidential path, PDF or image is tracked (plan §11 F0).
# Usage, from the repo root: sh .githooks/check-tracked.sh     Exit 1 if anything is found.
. "$(dirname "$0")/guard-lib.sh"

tracked=$(git ls-files -z | tr '\0' '\n' | grep -c .)
blocked=$(git ls-files -z | tr '\0' '\n' | guard_count_blocked_paths)
pdf_content=$(git ls-files -z | tr '\0' '\n' | grep -E -v "$GUARD_ALLOW" | sed 's/^/:/' | guard_count_pdf_content)

echo "tracked files: $tracked; confidential/PDF/image paths: $blocked; files with PDF content: $pdf_content"
[ "$blocked" -eq 0 ] && [ "$pdf_content" -eq 0 ]
