# Finding and preparing scores

This repo contains no scores. You find the scores, and you check that you can use them.
This page is general information, not legal advice.

## Rules

1. Keep scores and derived files out of this repo and out of public forks.
   This includes MusicXML, PDF, page images, merged scores, timelines and screenshots of notation.
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
| [OpenScore String Quartets](https://github.com/OpenScore/StringQuartets) | String quartets | `.mscz`, MusicXML | Reported as CC0 |
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
   ```

5. Start the app and open the work. Look for red bars and pink bars.

## Import in the browser

The app can do steps 2–4 for you.

1. Open 設定 ▾, then click ＋ 上傳分譜.
2. Select one or more `.musicxml`, `.xml` or `.mxl` files, or a `.zip` that contains them.
   Each file is one movement.
3. In the next dialog, set the movement order, the parts to import and their names.
4. Fill in 授權 and 來源 if the file does not have them.
5. Click 匯入. The dialog shows the `prepare.py` report, and the work opens.

A part comes only from the files that contain it.
Thus a folder with one file per movement and per instrument also works.
A part that is in fewer files than the others gets fewer bars. The dialog shows a warning.

The app ignores other files in a ZIP, such as `__MACOSX/` and text files.

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
