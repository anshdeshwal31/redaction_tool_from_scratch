# Shared rules for the pre-commit and pre-push hooks and check-tracked.sh (plan §2.3).
# A path is blocked when it is under a confidential folder or is a PDF or image,
# unless it is a generated synthetic fixture. Only counts are ever printed:
# the confidential file names themselves contain PII.

GUARD_ALLOW='^fixtures/synthetic/'
GUARD_CONFIDENTIAL='^(golden_dataset_docs|golden_dataset|data|runs|exports)/'
GUARD_BINARY='\.(pdf|png|jpe?g|tiff?|bmp|gif|webp|heic)$'

# Reads paths (one per line) on stdin; prints how many are blocked.
guard_count_blocked_paths() {
  grep -E -v "$GUARD_ALLOW" | grep -E -i -c -e "$GUARD_CONFIDENTIAL" -e "$GUARD_BINARY"
}

# Reads git blob specs (":path" for the index, "<commit>:path") on stdin;
# prints how many start with the PDF signature, whatever the file is named.
guard_count_pdf_content() {
  n=0
  while IFS= read -r spec; do
    head5=$(git cat-file blob "$spec" 2>/dev/null | head -c 5 | tr -d '\000')
    [ "$head5" = "%PDF-" ] && n=$((n + 1))
  done
  echo "$n"
}
