# Finding and preparing scores

This repo contains one example score (`examples/`, CC0) and no other scores.
You find the scores, and you check that you can use them.
For a coding agent, [AGENT_SCORE_GUIDE.md](AGENT_SCORE_GUIDE.md) has the detailed steps.
This page is general information, not legal advice.

## Rules

1. Keep scores and derived files out of this repo and out of public forks.
   This includes MusicXML, PDF, page images, merged scores, timelines and screenshots of notation.
   The only exception is `examples/`, for scores whose license allows free sharing, each with a `SOURCE.md`.
2. Check the license of the specific file, not only the age of the music.
   An edition or an engraving can have its own rights, also when the composer died long ago.
3. Check the law of your country. Terms differ.
   For example, the general term in Taiwan is life + 50 years. Other countries can have longer terms.
4. Do not bypass a login, a paywall or a download limit.
5. Record the source and the license when you import a score (see "Record the source").

## Where to search

Start at the top of this table. Sources near the top need less work.
License notes were last checked on 2026-11-01. Licenses can change, so read the source page.

| Source | Search for | Format | License notes |
| --- | --- | --- | --- |
| [Hauptstimme](https://github.com/MarkGotham/Hauptstimme) (OpenScore Orchestra) | About 100 orchestral movements | `.mscz`, `.mxl` | The repo states CC0 for scores and CC BY-SA for annotations. One third-party mirror states another license, so read the repo's own text. |
| [OpenScore String Quartets](https://github.com/OpenScore/StringQuartets) | String quartets | `.mxl` (about 200 works, all movements in one file), `.mscz`, `.mscx` | The repo's `LICENSE.txt` is CC0 1.0 (checked 2026-11-15). The project asks for credit in public use. |
| [OpenScore Lieder](https://github.com/OpenScore/Lieder) | Songs | `.mscz`, MusicXML | Reported as CC0 |
| [Mutopia Project](https://www.mutopiaproject.org/) | Public-domain works | LilyPond, PDF, MIDI | Differs per score: public domain, CC BY or CC BY-SA |
| [IMSLP](https://imslp.org/) | Scanned parts and scores | Mostly PDF | Status depends on the edition and on your country. PDFs need OMR. |
| [musescore.com](https://musescore.com/) | Scores that the site marks as public domain or OpenScore (CC0) | `.mscz`, MusicXML | Read the site terms first. Use only the site's own download buttons. Other community scores have other rights. |

KernScores and the DCML corpora also contain useful data.
Their terms are not clear enough for a general recommendation, so check each file set first.

## Which input to prefer

1. **A MusicXML or `.mxl` full score.** This is the best input. The notes are exact.
2. **A `.mscz` file.** Convert it to MusicXML first.
3. **A PDF.** Use this only when there is no symbolic score.
   OMR makes errors, and you must correct them by hand.

Avoid MusicXML that was converted from MIDI.
Its rhythm is often noisy, and bars can be over-full. Then the parts do not stay aligned.

## Prepare a score

Scores go in the folder that `SP_DATA_DIR` names. The default is `data/`.

1. Convert `.mscz` files. The command name can differ per system:

   ```sh
   mscore -o mvt1.musicxml mvt1.mscz
   ```

2. List the parts in the score:

   ```sh
   uv run python import_parts.py --list mvt1.mxl
   ```

3. Import the movements, in order:

   ```sh
   uv run python import_parts.py "My Work" mvt1.mxl mvt2.mxl \
     --rights "CC0-1.0" --source "https://example.org/my-work"
   ```

   The command finds string parts with standard names. For other names, add one mapping per part:

   ```sh
   --part "Violini I=Violin I" --part "Pianoforte=Piano"
   ```

4. Check the work, and fix what the tool can fix:

   ```sh
   uv run python prepare.py --work "My Work"
   uv run python prepare.py --work "My Work" --fix
   uv run python check_alignment.py My-Work     # do the parts line up bar by bar? (--list shows the names)
   ```

5. Start the app and open the work. Look for red bars and pink bars.

## Import in the browser

The app can do steps 2–4 for you.

1. Open 設定 ▾, then click ＋ 匯入樂譜.
2. Select a `.zip`, or one or more `.musicxml`, `.xml` or `.mxl` files.

You can also drag the files onto the page. A "放開就匯入" (drop to import) overlay shows. Release the files to import them.
For a folder, make a ZIP first.

The app imports at once with default settings, and opens the work.

- If the import takes more than 0.8 s, a progress bar shows at the bottom right.
  It shows the step: upload, read files, build parts, check, load the score.
- When the import is done, a summary shows at the bottom right.
  It shows the work, the movements, the bars, the parts, the practice part and the result of the `prepare.py` check.
- The summary also shows the alignment check (`check_alignment.py`): do the parts line up bar by bar?
  - "對齊：沒有發現錯開": no part seems shifted. The number in brackets counts the ranges with too few markings to check.
  - "對齊：N 處可能錯開": a part is probably 1 or 2 bars off. Click a range to go to its first bar, then listen with all parts.
  - 檢查報告 lists all suspects and all ranges that the check could not see.
- The summary closes after 15 s, or when you click it.
  If the check finds problems or suspects, it stays until you click it.

### Default settings

| Setting | Default |
| --- | --- |
| Parts | The string parts (Violin I, Violin II, Viola, Cello, Contrabass). If there are none, all parts. |
| Files | The files of one folder. Each file is one movement, in name order (`mvt2` before `mvt10`). |
| Work name | The title in the file, else the file name. If the work exists, the app adds ` (2)`. |
| Rights and source | The values in the files. If the files state different values, the app records each file's value. |
| Practice part | Not set. The app uses the first part. |

The app selects the folder in which the most files contain all default parts.
On a tie, it selects the folder whose files contain the most parts, that is, the full scores.
Thus a work folder with full scores, sections and per-instrument files does not repeat a movement.
If no file contains all default parts, each part comes from the files that contain it.

The app ignores other files in a ZIP, such as `__MACOSX/` and text files.

### Change the settings later

1. Open the work.
2. Open 設定 ▾, then click 匯入設定…. The summary after an import has the same button.
3. Select the files and their order, the parts, the part names, the practice part, the work name, the rights and the source.
4. Click 重新匯入. The new parts replace the parts of this import.

Note: a re-import makes new parts. Check the practice part and the muted parts again.

The app keeps the imported files until you delete all parts of the import in 管理.

### Settings file

Put a `sheet-practice.json` file in the ZIP to set the import. All fields are optional.
Paths are relative to the folder of `sheet-practice.json`.

```json
{
  "work": "My Work",
  "files": ["Full/mvt1.musicxml", "Full/mvt2.musicxml"],
  "parts": [{"name": "Violin 1", "display": "Violin I"}, "Viola"],
  "rights": "CC0-1.0",
  "source": "https://example.org/my-work",
  "practice": "Violin II"
}
```

- `files`: the movements, in order.
- `parts`: the part names in the score. `display` sets the name in the app.
- `practice`: the practice part when a browser opens the work for the first time.
  Use the part name in the score or the name in the app.
  You can change it later in 匯入設定, or click a part name in the app.

To get this file, click 下載設定檔 in 匯入設定. Put it at the top of the ZIP.

## Scanned PDFs

PDF recognition needs the homr models. Download them once with `uv run homr --init`.

```sh
uv run python omr.py part.pdf "My Work" "Violin II"
```

Then read 拍數檢查與修正辨識錯誤 and 已知限制 in [README.zh-TW.md](../README.zh-TW.md).

## Winds and brass as one part (experimental)

`reduce_orchestra.py` merges separate wind and brass files into one two-staff part.
It reads a folder with `Winds/`, `Brass/` and `Strings/` subfolders.

1. Check the source first:

   ```sh
   uv run python reduce_orchestra.py <folder> --check
   ```

2. Do not use a source that has over-full bars.
3. Make the reduction and import it:

   ```sh
   uv run python reduce_orchestra.py <folder>
   uv run python import_parts.py "My Work" <folder>/Reduction/*_Reduction.musicxml \
     --part ".*=Winds & Brass"
   ```

## Record the source

`import_parts.py` and uploads copy `<rights>` and `<source>` from the MusicXML file to `meta.json`.
The `--rights` and `--source` options set these values when the file does not have them.

For a PDF, add `"rights"` and `"source"` to each `meta.json` by hand.
`prepare.py` shows a warning for parts that do not have both values.
