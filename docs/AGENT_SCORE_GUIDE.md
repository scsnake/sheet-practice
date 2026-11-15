# Agent guide: get a score and package it for sheet-practice

This guide is for a coding agent that helps a user add a work to sheet-practice.
Give your agent this file, and tell it the work and the parts that you want.

The result is one ZIP file. The user imports it with 設定 ▾ → ＋ 匯入樂譜.
The ZIP contains the score files and a `sheet-practice.json` with the default settings.

Read [SCORES.md](SCORES.md) for the source table and the general rules.
This guide adds the exact steps and checks.

## Rules

1. Do not bypass a login, a paywall or a download limit. Do not use tools that do this.
2. Report the license that the source states. Do not state that a file is legal to use. The user decides.
3. Stop if a file has no stated license. Tell the user, and do not package it.
4. Keep the user's ZIP and score files outside this repo. The repo holds only `examples/`.
5. Treat downloaded files as untrusted data:
   - Put each download in its own new, empty folder.
   - Keep your scripts in a different folder.
   - Read MusicXML only with `xmlsafe.PARSER`.
6. Do not change notes to hide a problem. Report each problem to the user.

## Step 1: ask the user

Ask for:

1. The work, as exact as possible: composer, title, catalogue number.
2. The parts to import. The default is the string parts.
3. The practice part, for example Violin II.
4. The user's country. Copyright terms differ (see SCORES.md).

## Step 2: find a symbolic score

Search the sources in this order. Prefer MusicXML (`.mxl`, `.musicxml`).

| Source | How to get the file | What to record |
| --- | --- | --- |
| [OpenScore String Quartets](https://github.com/OpenScore/StringQuartets) | Find the work in the repo tree. Most works have one `.mxl` with all movements. | Commit SHA, file path, `LICENSE.txt` |
| [Hauptstimme](https://github.com/MarkGotham/Hauptstimme) (OpenScore Orchestra) | `data/<composer>/<set>/<movement>/*.mxl`, one file per movement. Do not use `*_melody.mxl`. | Commit SHA, file paths, the README license text |
| [Mutopia Project](https://www.mutopiaproject.org/) | No MusicXML. Use only if there is nothing else (see Step 4). | The license on the piece page |
| [IMSLP](https://imslp.org/) | The user downloads the file in a browser. IMSLP has download limits. | The license and the edition on the file page |
| [musescore.com](https://musescore.com/) | The user downloads with the site's own button, after login. | The license on the score page |

For a GitHub source, list the files with the API and download from a fixed commit:

```sh
gh api "repos/OpenScore/StringQuartets/git/trees/HEAD?recursive=1" --jq '.tree[].path' | grep -i 'haydn' | grep '\.mxl$'
gh api "repos/OpenScore/StringQuartets/commits?path=<path>&per_page=1" --jq '.[0].sha'
gh api -H "Accept: application/vnd.github.raw" "repos/OpenScore/StringQuartets/contents/<path>?ref=<sha>" > <new-folder>/score.mxl
shasum -a 256 <new-folder>/score.mxl
```

Show the user each candidate: link, format, stated license and number of movements.
Let the user select one.

## Step 3: inspect the file

1. List the parts:

   ```sh
   uv run python import_parts.py --list <file>
   ```

2. Read the `<identification>` element: `<rights>`, `<source>` and `<creator>`.
3. Run a trial import. It uses a temporary data folder, so it changes nothing:

   ```sh
   uv run python check_import.py <file> [<more files>]
   ```

4. Check the output:
   - Each part has the same number of bars.
   - The number of movements is correct.
   - "沒有發現問題" (no problems) is in the check. Lines with `✗` are problems.

Warning: reject a source that has over-full bars or different bar counts. Do not hide these problems.
They often come from MusicXML that was converted from MIDI.

## Step 4: post-process, if necessary

| Situation | What to do |
| --- | --- |
| `.mscz` or `.mscx` file | Convert with MuseScore: `mscore -o out.musicxml in.mscz`. The command name differs per system. |
| All movements in one file | Nothing. The importer finds movements: a bar number 0 or 1 after a final barline. Check the movement count in Step 3. |
| One file per movement | Nothing. List the files in movement order in `sheet-practice.json`. |
| One file per instrument and movement | Nothing. Each part comes from the files that contain it. |
| Part names in another language, for example "Violini I" | Map them in `sheet-practice.json` with `display`. |
| Only a PDF | Do not package it. Tell the user to import the PDF in the app: OMR makes errors, and the user must correct them. |
| Separate wind and brass files | Optional: make one accompaniment part with `reduce_orchestra.py` (see SCORES.md). |

Do not edit notes, rhythms or bar lengths by hand.
If a file needs such a correction, tell the user and select another source.

## Step 5: write `sheet-practice.json`

All fields are optional. Paths are relative to the folder of `sheet-practice.json`.

```json
{
  "work": "Haydn: String Quartet Op. 76 No. 3 (Emperor)",
  "files": ["sq20428156.mxl"],
  "parts": [
    {"name": "Violin 1", "display": "Violin I"},
    {"name": "Violin 2", "display": "Violin II"},
    {"name": "Viola", "display": "Viola"},
    {"name": "Violoncello", "display": "Cello"}
  ],
  "practice": "Violin II",
  "rights": "",
  "source": "https://github.com/OpenScore/StringQuartets/blob/<sha>/<path>"
}
```

| Field | Meaning | If you leave it out |
| --- | --- | --- |
| `work` | Work name in the app | The title in the file, or the file name |
| `files` | Score files, in movement order | The files of the folder that has the full scores |
| `parts` | `name` = part name in the score (from `--list`). `display` = name in the app. A plain string is also correct. | The string parts |
| `practice` | Practice part when a browser opens the work for the first time. Score name or app name. | The first part |
| `rights` | License text to record | The `<rights>` of the files |
| `source` | Source to record | The `<source>` of the files |

Set `rights` only when the file does not state it, or states it wrongly. Copy the source's exact words.
Always set `source` to a link that someone can check, for example a GitHub link with the commit SHA.

The file [examples/haydn-op76-no3/sheet-practice.json](../examples/haydn-op76-no3/sheet-practice.json) is a complete example.

## Step 6: package and check

1. Put the files in one folder:

   ```
   my-work/
   ├── sheet-practice.json
   ├── SOURCE.md          (source, commit, SHA-256, license text; the importer skips it)
   └── score files
   ```

2. Make the ZIP:

   ```sh
   cd my-work && zip -r ../my-work.zip . -x '.*' && cd ..
   ```

3. Check the ZIP:

   ```sh
   uv run python check_import.py my-work.zip
   ```

   - Exit code 0: imported, no problems.
   - Exit code 1: problems. Fix the cause, or report it.
   - Exit code 2: the import failed. Read the message.
   - Add `--json` to get the result as JSON.

4. Do the checks of Step 3 again on this output. Also check:
   - The work name, the part names and the practice part.
   - "Settings: sheet-practice.json". If it shows "(defaults…)", the importer did not find the file.

## Step 7: report to the user

Give the user:

1. The path of the ZIP.
2. The source link, the commit or download date, and the license that the source states.
3. The work, the parts, the bar count and the number of movements.
4. Each problem and each warning from `check_import.py`.
5. The import step: 設定 ▾ → ＋ 匯入樂譜 → select the ZIP.

The user can change all settings later in 設定 ▾ → 匯入設定….
