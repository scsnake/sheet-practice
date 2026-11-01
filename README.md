# sheet-practice

A local web app for practicing a string part with the other parts playing along.
It shows the score, plays the parts, and moves a cursor in time.

**This repo contains no scores.** You supply your own. [docs/SCORES.md](docs/SCORES.md) tells you where to search and how to prepare them.

The user interface is in Traditional Chinese. [README.zh-TW.md](README.zh-TW.md) has the full feature guide.

## Features

- Combine separate parts into one multi-part score.
- Play the other parts, and mute or lower your own part.
- Change the speed per movement, loop a section, and use a click track or count-in.
- Skip long rests.
- Read in scroll, horizontal or page mode.
- Import MusicXML or MXL, or recognize PDF parts with OMR ([homr](https://github.com/liebharc/homr)).
- Find bars with the wrong duration, and correct notes in the browser.

The app works offline after installation. It was built for desktop Chrome and iPad.

## Quick start

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

1. Install the dependencies:

   ```sh
   uv sync
   ```

2. Make two synthetic demo works. Their notes come from code, not from a published score:

   ```sh
   uv run python tests/fixtures/make_demo.py
   ```

3. Start the app:

   ```sh
   ./run.sh
   ```

4. Open <http://localhost:8777/>.

## Add your own scores

1. Read [docs/SCORES.md](docs/SCORES.md).
2. If you use a coding agent, tell it to follow [AGENTS.md](AGENTS.md). It can search the listed sources with you and run the preparation steps.

## Keep your scores outside the repo

Set `SP_DATA_DIR` to use another data folder:

```sh
SP_DATA_DIR=~/my-scores ./run.sh
```

The command-line tools use the same setting.

## Security

Warning: keep the server on `localhost` unless you trust the network.

The server has no authentication. Anyone who can reach it can upload, change and delete scores.
Do not put it on the public internet.

## Tests

```sh
uv sync --group dev
uv run playwright install chromium
tests/run_e2e.sh
```

The tests use a temporary data folder and the synthetic demo works. Stop your own server first, because they use port 8777.

`scroll_mode` has a strict timing limit. Its Demo Concerto case can fail at times with `append gap<120ms=NO`.
This is one short visual stall. No note was late in any run.

## License

sheet-practice is licensed under [AGPL-3.0-or-later](LICENSE).
Copyright (C) 2026 scsnake.

[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) lists the bundled components and their licenses.
The license covers the software only. It gives you no rights to any score.
