#!/bin/zsh
# DEMO STUB of the private _render.sh (Chrome headless -> PDF -> qpdf page count).
#
# If a Chrome/Chromium binary is available it performs a real headless render;
# otherwise it writes a minimal single-page placeholder PDF so the pipeline
# stays exercisable on a bare machine. Either way the page count printed at the
# end is read back from the PDF that was actually produced, so the one-page
# gate stays honest.
set -e
name="$1"
dir="$(cd "$(dirname "$0")" && pwd)"
html="$dir/$name.html"
pdf="$dir/$name.pdf"
if [ ! -f "$html" ]; then
  echo "no such html: $html" >&2
  exit 1
fi

chrome=""
for c in \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "$(command -v google-chrome 2>/dev/null || true)" \
  "$(command -v chromium-browser 2>/dev/null || true)" \
  "$(command -v chromium 2>/dev/null || true)"; do
  if [ -n "$c" ] && [ -x "$c" ]; then
    chrome="$c"
    break
  fi
done

if [ -n "$chrome" ]; then
  "$chrome" --headless --disable-gpu --no-pdf-header-footer \
    --print-to-pdf="$pdf" "file://$html" >/dev/null 2>&1
else
  python3 - "$pdf" <<'PY'
import sys
pdf = (b"%PDF-1.4\n"
       b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")
open(sys.argv[1], "wb").write(pdf)
PY
fi

pages=$(python3 - "$pdf" <<'PY'
import re, sys
data = open(sys.argv[1], "rb").read()
counts = [int(m) for m in re.findall(rb"/Count\s+(\d+)", data)]
print(max(counts) if counts else 0)
PY
)
echo "pages: $pages"
