# QuarTeX

QuarTeX converts a LaTeX lecture package into Quarto notes. The output is a single web page or a multi-page book with a sidebar, and each one comes with a PDF download.

It runs entirely on your own computer. Nothing is uploaded to another server, so your source
material stays local.

By converting the LaTeX notes to Quarto, you can edit and render the notes in tools like Positron and R Studio, giving you Overleaf like functionality for updating and maintaining your notes in future.

## Who it is for

Academic staff who have lecture notes written in LaTeX and want to publish them as accessible
web notes. The tool can be used by anyone, whether it's academics themselves, or people who support them such as learning technologists and developers.

While the app needs to be launched from the command line, it has a web page that opens in your browser.

## Requirements

- **Python 3.10 or newer.** On a Mac, check with `python3 --version`.
- **Pandoc 3.x**, on your PATH.
- **Quarto 1.6 or newer**, on your PATH.
- **Ghostscript** (`gs`), only if your source has EPS figures, and **Poppler** (`pdftocairo`), only if
  it has PDF or EPS figures. Without them, those figures are listed in the conversion report and left
  out, and the rest of the notes still convert. Installing Ghostscript may need administrator rights.
- **Node.js** (optional), only for the `--check-math` option.

No LaTeX installation is needed. The PDF is made with Quarto's Typst engine.

## Quick start

1. Download the code from this repository and unzip it.
2. Open Terminal (Mac) or PowerShell (Windows) and navigate to the QuarTeX folder.
- using Terminal or PowerShell, type `cd ` (with a space after `cd`) and paste in the location path wrapped in quotes, and press Return. Your command should look something like: cd "/Users/Username/Downloads/QuarTeX"
2. Check your setup (once). This lists anything missing and the command to install it:
- python3 check_setup.py      # Mac
- py check_setup.py           # Windows
3. Start the converter:
- python3 webapp.py           # Mac
- py webapp.py               # Windows
- Your browser opens the converter page. Then:
4. Choose the .zip of your LaTeX source (or a single .tex file). The title, author and
   affiliation are read from the source and filled in. You can change them.
5. Choose the output: Automatic (a book if the source has more than one chapter), Single page
   or Book.
6. Choose a theme. Quarto's theme comparison page is linked from the form.
7. Click **Convert**. A long package can take several minutes.
8. Download the converted notes. The file is named after the title, with `_converted` added,
   for example `Introduction to Circuits_converted.zip`.
9. To preview the notes, to into the **_book** folder and open the **index.html** file in your browser.
10. To edit the notes, open the entire folder in [Positron](https://positron.posit.co/ "a free IDE purpose-built for data science"), [R Studio](https://posit.co/products/open-source/rstudio "The trusted IDE for R data scientists"), or another editor of choice.

For guidance on editing the files, visit the [Quarto guidance](https://quarto.org/docs/guide/ "Comprehensive guide to using Quarto. If you are just starting out, you may want to explore the tutorials to learn the basics.").

## Command-line use

    python3 quartex.py PACKAGE OUTDIR [--main FILE] [--title TEXT] [--author NAME]
        [--affiliation TEXT] [--theme NAME] [--mode auto|book|single]
        [--no-pdf] [--no-render] [--check-math]

- `PACKAGE` is the .zip or folder containing the source. `OUTDIR` is where the output goes.
- `--affiliation` defaults to Durham University. Use `--affiliation ""` for none.
- `--theme` defaults to `cosmo`. Use `default` for Quarto's own theme.
- `--check-math` typesets every equation with MathJax to find ones that fail. It needs Node.js
  and `npm install mathjax-full@3` in this folder.

## What you get

- **Single page:** `document.html`, a self-contained page with its figures embedded, plus
  `document.pdf` and `document.qmd` (the Quarto source).
- **Book:** `_quarto.yml`, one `.qmd` per chapter, and `_book/`, the rendered site. The PDF is
  in `_book/` beside `index.html`.
- **`conversion-report.md`:** what converted, Quarto warnings, and any maths that failed.
- **`math-failures.json`:** only with `--check-math`.

## What it handles

- Flattening of `\input` and `\include`.
- EPS and PDF figures converted to SVG (PDF: first page only). File names are matched without
  regard to case, and extension-less paths are found.
- Labels renamed to Quarto prefixes (`fig-`, `tbl-`, `eq-`, `sec-`), with `\ref`, `\eqref` and
  "Fig./Eq./Table" wording converted to Quarto cross-references. Chapter-based numbering is kept.
- Subfigure references such as `Fig. \ref{X}b`.
- Equation labels attached so Quarto numbers them.
- Theorem-style environments (`challenge_env`, `problem_env`, `solution_env`) turned into
  numbered callouts.
- `\resizebox` tables, which Pandoc would otherwise drop silently.
- Biblatex `.bib` files used directly, with all chapters' bibliographies merged into one list.
- Title, author and affiliation read from `\title`, `\author` and `\hypersetup`.
- The PDF has the title and author in the header and "© Durham University", the year and page
  numbers in the footer. The book PDF has a link in the sidebar, and so does the single page.

## Known limits

- **Internet access when converting:** Quarto downloads each theme's Google font to embed it
  in the HTML. If that download is blocked, the notes can come out without their theme styling.
  Check the output on a machine with internet access.
- **Internet access when reading:** MathJax and the Quarto stylesheet load from a CDN when the
  page is opened, so maths needs an internet connection to display.
- Problem and solution numbering is taken from chapter order, so it may differ from the original
  if the source numbers problems in an unusual way.
- Each `\printbibliography` is replaced by one combined list at the end, not one per chapter.
- `\includegraphics` trim and width options are dropped. Images are sized to the page.
- Some learning platforms strip JavaScript from uploaded HTML. Check the output in the
  destination, for example by testing that MathJax and the page scripts run after upload.
- Large packages produce large HTML files, because every figure is embedded.
- Tested on a Mac, and on Windows with a basic package. Check packages with more complex figures or
  maths on Windows before relying on them.

## Licence and credits

© Ross Parker, Durham University, 2026. 

QuarTeX source code is released under the **MIT License** (see `LICENSE`), and documentation/educational materials are shared under **Creative Commons Attribution (CC BY)**. You are free to use, adapt, and share it, including for commercial purposes, as long as the licence notices are kept.

##How it was made

The ideas, requirements, design decisions and testing are the author's own based on years of experience of authoring LaTeX code and converting it to accessible HTML.

The code was written with the help of an AI assistant (Claude, by Anthropic), working
under the author's direction. The author reviewed and tested the result.

LaTeX notes used in early testing was conducted with permission of the authors. Note, the tool runs locally meaning that the source LaTeX notes do not need to be uploaded to AI models for the conversion.
