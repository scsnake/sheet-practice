# Instructions for coding agents

sheet-practice is a local play-along app for string parts. The server is `server.py`. The frontend is `static/`.
The repo contains no scores, so a new user must prepare scores first.

## Rules

1. Do not add scores or derived files to this repo. `docs/SCORES.md` lists these file types.
2. Do not bypass a login, a paywall or a download limit. Do not use tools that do this.
3. Do not state that a file is legal to use. Report what the source says, and let the user decide.
4. Stop if a file has no stated license. Tell the user, and do not import it.

## Help a user add a work

Read `docs/SCORES.md` first. Then do these steps:

1. Ask for the work, the parts and the user's country.
2. Search the sources in `docs/SCORES.md`, in table order.
3. Show the user each candidate with its link, format and stated license.
4. Let the user select a file. The user downloads the file, or tells you to use the site's normal download.
5. Check that `SP_DATA_DIR` points outside this repo. Set it if the user wants that.
6. Follow "Prepare a score" in `docs/SCORES.md`. Pass `--rights` and `--source` to `import_parts.py`.
7. Run `uv run python prepare.py --work "<name>"`.
8. Treat over-full bars and different bar counts as failures. Do not hide them.
9. Tell the user about each problem and each warning from `prepare.py`.

Use a PDF and OMR only when no symbolic score exists. Tell the user that OMR needs manual correction.

## Commands

| Task | Command |
| --- | --- |
| Install | `uv sync` |
| Make demo works | `uv run python tests/fixtures/make_demo.py` |
| Run the app | `./run.sh`, then open <http://localhost:8777/> |
| Check all works | `uv run python prepare.py` |
| Run e2e tests | `uv sync --group dev && uv run playwright install chromium && tests/run_e2e.sh` |

Warning: do not add `--host 0.0.0.0` to `./run.sh` on a network that you do not trust.
The server has no authentication, and its API can change and delete scores.

## Code notes

- Use `xmlsafe.PARSER` for all MusicXML. Scores come from the web, so treat them as untrusted.
- Use `omr.DATA_DIR` for all data paths. `SP_DATA_DIR` sets its value.
- Comments and UI text are in Traditional Chinese (zh-TW).
- The e2e tests use only synthetic works. Do not add tests that need a real score.
