#!/bin/sh
cd "$(dirname "$0")" && exec uv run uvicorn server:app --port 8777 "$@"
