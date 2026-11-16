# Hosted mode: the app as a static site

The hosted mode runs the app with no server. A static host, for example Cloudflare Pages, serves only the code.
Each visitor imports ready packages in their own browser. The works stay in that browser (IndexedDB).

The hosted mode does no conversion. An agent or a person prepares and checks each work first, and exports a ready package.
The browser only shows the score, plays it, and saves note corrections.

## What the hosted mode can do

| Feature | Local app (`./run.sh`) | Hosted mode |
| --- | --- | --- |
| Show and play a score, all practice settings | Yes | Yes |
| Import a ready package (`check_import.py --export`) | Yes | Yes |
| Import MusicXML, MXL or a score ZIP | Yes | No: export a ready package first |
| PDF and OMR | Yes | No |
| 匯入設定 (change the parts, files and names) | Yes | No: change them before the export |
| Correct notes (修正音符) | Yes: saved in the part files | Yes: the edited score is saved in the browser as it is |
| Download the MusicXML | Yes | Yes: the current score, with your corrections |
| Where the works are | The data folder (`SP_DATA_DIR`) | This browser only |

## Make a ready package

1. Prepare and check the work. [AGENT_SCORE_GUIDE.md](AGENT_SCORE_GUIDE.md) has the steps.
2. Export the package:

   ```sh
   uv run python check_import.py my-work.zip --export my-work-ready.zip
   ```

   For a work that is already in your data folder:

   ```sh
   SP_DATA_DIR=<data folder> uv run python export_ready.py <work-slug> -o my-work-ready.zip
   ```

3. Give the ZIP to the user. The ZIP contains the score, so share it only as the score's license permits.

The package contains:

| File | Content |
| --- | --- |
| `sheet-practice-ready.json` | Work name, parts, default settings, and the import summary (check report and alignment check) |
| `score.musicxml` | The merged score of all parts |
| `timeline.json` | The playback timeline, so the browser does not have to compute it |
| `sheet-practice.json` | Settings for the local app, so `./run.sh` can import the same ZIP |

## Build and publish the site

1. Build the site:

   ```sh
   uv run python build_static.py
   ```

   The site is in `dist/`. It also contains the example of `examples/` as a ready package.

2. Try it on your computer:

   ```sh
   uv run python -m http.server 8780 --bind 127.0.0.1 --directory dist
   ```

   Then open <http://127.0.0.1:8780/>.

Warning: the host gives every file in `dist/` to everyone. Never put a ready package or a score in `dist/`.

3. Publish `dist/` on a static host. For example, with Cloudflare Pages:

   ```sh
   npx wrangler pages deploy dist
   ```

Any static host is correct. The site uses only relative paths, so it also works in a subfolder.

## Use the site

1. Open the site.
2. Drag the ready package onto the page, or click ＋ 匯入樂譜 and select it.
3. The summary shows the check and the alignment result from the package.

To see or delete the works, open 設定 ▾ → 管理.
If you import the same package again after you corrected notes, the app asks first. A "yes" removes your corrections.

## Limits

- The works are only in this browser. Another browser or another computer needs its own import.
- Clearing the site data in the browser deletes the works. The app asks the browser to keep the data (`navigator.storage.persist()`), but the browser decides.
- The browser must support `DecompressionStream("deflate-raw")`: Chrome 103, Safari 16.4 and Firefox 113, or later.
- After a note correction, the next load computes the timeline in the browser once. Then the app saves it.
