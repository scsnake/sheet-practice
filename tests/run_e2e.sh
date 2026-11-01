#!/bin/sh
# Run the e2e tests on the synthetic demo works, in a temporary data folder.
#   tests/run_e2e.sh                     # all tests
#   tests/run_e2e.sh goto_bar skip_solo  # only these tests
# First use: uv sync --group dev && uv run playwright install chromium
cd "$(dirname "$0")/.." || exit 2
if curl -s -o /dev/null http://localhost:8777/; then
  echo "Port 8777 is in use. Stop that server first: the tests must not run on your own data." >&2
  exit 2
fi
uv run --group dev python tests/test_reduce_orchestra.py || exit 2
SP_DATA_DIR=$(mktemp -d)
export SP_DATA_DIR
uv run --group dev python tests/fixtures/make_demo.py || exit 2
uv run --group dev uvicorn server:app --port 8777 --log-level warning &
server=$!
trap 'kill $server 2>/dev/null; rm -rf "$SP_DATA_DIR"' EXIT INT TERM
tries=0
until curl -s -o /dev/null http://localhost:8777/; do
  tries=$((tries + 1))
  [ "$tries" -gt 120 ] && { echo "The server did not start." >&2; exit 2; }
  sleep 0.5
done
if [ $# -eq 0 ]; then
  set -- $(ls tests/e2e/*.py | xargs -n1 basename | sed 's/\.py$//' | grep -v '^common$')
fi
failed=""
for name in "$@"; do
  echo "=== $name"
  out=$(uv run --group dev python "tests/e2e/$name.py" 2>&1)
  code=$?
  printf '%s\n' "$out"
  if [ "$code" -ne 0 ] || printf '%s\n' "$out" | grep -q '^FAIL'; then
    failed="$failed $name"
  fi
done
if [ -n "$failed" ]; then
  echo "FAILED:$failed"
  exit 1
fi
echo "All tests passed."
