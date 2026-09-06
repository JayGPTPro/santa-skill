#!/usr/bin/env bash
# The Santa Skill environment check. Prints OK or MISSING with the exact fix line.
# Exit 0 = ready. Anything else = fix the MISSING lines first.

PASS=0; FAIL=0
ok()   { printf "  OK       %s\n" "$1"; PASS=$((PASS+1)); }
miss() { printf "  MISSING  %s\n           fix: %s\n" "$1" "$2"; FAIL=$((FAIL+1)); }

echo "santa-skill: environment check"
echo

# 1. python3
if command -v python3 >/dev/null 2>&1; then
  ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"
else
  miss "python3" "macOS: brew install python3 | Windows: winget install Python.Python.3.12 | Linux: apt install python3"
fi

# 2. openai package
if python3 -c "import openai" >/dev/null 2>&1; then
  ok "openai package $(python3 -c 'import openai;print(openai.__version__)' 2>/dev/null)"
else
  miss "openai python package" "python3 -m pip install openai   (if pip refuses: python3 -m pip install openai --break-system-packages)"
fi

# 3. Pillow (sizes, thumbnails for the contact sheet)
if python3 -c "import PIL" >/dev/null 2>&1; then
  ok "Pillow"
else
  miss "Pillow python package" "python3 -m pip install pillow"
fi

# 4. curl (Amazon fetch)
if command -v curl >/dev/null 2>&1; then
  ok "curl"
else
  miss "curl" "macOS ships it. Linux: apt install curl. Windows 10+: ships it, or winget install cURL.cURL"
fi

# 5. OpenAI key: env, else ~/Downloads/Claude/.env
KEY="${OPENAI_API_KEY:-}"
if [ -z "$KEY" ] && [ -f "$HOME/Downloads/Claude/.env" ]; then
  KEY=$(grep -E '^OPENAI_API_KEY=' "$HOME/Downloads/Claude/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
if [ -n "$KEY" ]; then
  ok "OPENAI_API_KEY (${KEY:0:7}...)"
else
  miss "OPENAI_API_KEY" "export OPENAI_API_KEY=sk-...   (get one at platform.openai.com/api-keys; gpt-image-2 needs a verified org: platform.openai.com/settings/organization/general)"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "Ready. $PASS checks passed."
  exit 0
else
  echo "$FAIL missing. Run the fix lines above, then run this check again."
  exit 1
fi
