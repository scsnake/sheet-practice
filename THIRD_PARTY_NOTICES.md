# Third-party notices

sheet-practice is licensed under AGPL-3.0-or-later (see `LICENSE`).
The project includes or uses the third-party components below.
This file does not change the license of any component.

## Files in `static/vendor/`

| File | Component | License |
| --- | --- | --- |
| `opensheetmusicdisplay.min.js` | [OpenSheetMusicDisplay](https://github.com/opensheetmusicdisplay/opensheetmusicdisplay) 1.9.0 | BSD-3-Clause |
| `WebAudioFontPlayer.js` | [WebAudioFont](https://github.com/surikov/webaudiofont) engine 3.0.04 | GPL-3.0 |
| `*_FluidR3_GM_sf2_file.js` | Instrument data from [webaudiofontdata](https://github.com/surikov/webaudiofontdata), made from FluidR3 GM | MIT (both notices are below) |

The OSMD file refers to `opensheetmusicdisplay.min.js.LICENSE.txt`.
The npm package 1.9.0 does not contain that file, so this repo cannot include it.

## Python dependencies

`uv.lock` lists all Python dependencies. These two have the strongest conditions:

| Package | License | Note |
| --- | --- | --- |
| [homr](https://github.com/liebharc/homr) | AGPL-3.0 | `homr --init` downloads the model weights. This repo does not contain them. Read homr's `LICENSE_FAQ.md` for the weights and training data. |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | AGPL-3.0 or Artifex commercial | Used only to read PDF pages. |

## License texts

### OpenSheetMusicDisplay (BSD-3-Clause)

```text
Copyright 2019 PhonicScore

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### webaudiofontdata (MIT)

```text
MIT License

Copyright (c) 2017 Srgy Surkv

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Fluid (R3) GM SoundFont (MIT)

```text
Copyright (c) 2000-2002, 2008 Frank Wen <getfrank@gmail.com>

Fluid was constructed in part from samples found in the public
domain that I [Frank Wen] edited/cleaned/remixed/programmed and
largely from recordings of my own and in conjunction with the
people below who helped along the way: Suren M. Seron,
Scott Hanan, Steve Aupperle, Chris Gillman, Alex Taubr,
Chris Prola, Andrew Klenk, Winfried Hubbe, Dylan, Tim, Gort,
Uros Katic, Ethan Winer (http://www.ethanwiner.com)

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
```

The text above comes from the Debian copyright record for this sound font.
