#!/usr/bin/env python3
"""quartex: convert a LaTeX source package into a Quarto document and render it to one HTML file.

Usage:
    python3 quartex.py PACKAGE.zip OUTDIR [--main FILE] [--title TEXT] [--no-render]

Steps:
  1. Unpack the zip (or copy a folder) into OUTDIR/source, checking for unsafe paths.
  2. Find the main .tex file (or use --main) and flatten \\input / \\include.
  3. Convert EPS figures to SVG (Ghostscript + pdftocairo); copy PNG/JPG/PDF-free images.
  4. Rename labels to Quarto prefixes (fig-, tbl-, eq-, sec-) and rewrite \\ref/\\eqref to match.
  5. Run Pandoc (LaTeX reader -> Markdown) with lua/quarto_filter.lua.
  6. Post-process the Markdown: equation labels, leftovers, Quarto YAML header.
  7. Render with quarto (unless --no-render) and write conversion-report.md.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILTER = HERE / "quarto_filter.lua"


def log(msg):
    print(f"[quartex] {msg}", file=sys.stderr)


def require(tool):
    if shutil.which(tool) is None:
        raise SystemExit(f"Required tool not found on PATH: {tool}")


# ---------------------------------------------------------------- 1. unpack

def unpack(src: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    elif zipfile.is_zipfile(src):
        root = dest.resolve()
        with zipfile.ZipFile(src) as z:
            for member in z.infolist():
                target = (dest / member.filename).resolve()
                if root not in target.parents and target != root:
                    raise SystemExit(f"Unsafe path in zip: {member.filename}")
            z.extractall(dest)
    else:
        raise SystemExit("Input must be a .zip file or a folder.")
    return dest


# ---------------------------------------------------------------- title, author and theme

# Bootswatch themes that Quarto's HTML output accepts. "default" means Quarto's own theme.
THEMES = ["default", "cerulean", "cosmo", "darkly", "flatly", "journal", "litera", "lumen", "lux",
          "materia", "minty", "morph", "pulse", "quartz", "sandstone", "simplex", "sketchy", "slate",
          "solar", "spacelab", "superhero", "united", "vapor", "yeti", "zephyr"]
DEFAULT_THEME = "cosmo"
DEFAULT_AFFILIATION = "Durham University"


def theme_lines(theme: str) -> list[str]:
    """The html 'theme:' key, or nothing for Quarto's default theme."""
    return [] if theme == "default" else [f"    theme: {theme}"]


def _matching_brace(s: str, open_at: int) -> int | None:
    depth = 0
    for i in range(open_at, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def command_arg(tex: str, cmd: str) -> str | None:
    """Braced argument of the first \\cmd{...}, with balanced braces. None if absent."""
    m = re.search(r"\\" + cmd + r"\s*(?:\[[^\]]*\])?\s*\{", tex)
    if not m:
        return None
    end = _matching_brace(tex, m.end() - 1)
    return None if end is None else tex[m.end():end]


def drop_command(s: str, cmd: str) -> str:
    """Remove every \\cmd{...} (balanced braces), e.g. \\thanks{...}."""
    while True:
        m = re.search(r"\\" + cmd + r"\s*(?:\[[^\]]*\])?\s*\{", s)
        if not m:
            return s
        end = _matching_brace(s, m.end() - 1)
        if end is None:
            return s
        s = s[:m.start()] + s[end + 1:]


LETTER_COMMANDS = {"AA": "Å", "aa": "å", "AE": "Æ", "ae": "æ", "OE": "Œ", "oe": "œ",
                   "O": "Ø", "o": "ø", "L": "Ł", "l": "ł", "ss": "ß"}
ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
           "=": "\u0304", ".": "\u0307"}


def clean_latex(s: str, line_break: str = " ") -> str:
    """Plain text from a LaTeX fragment such as a title or an author name."""
    s = drop_command(s, "thanks")
    s = re.sub(r"\\texorpdfstring\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\\\(\[[^\]]*\])?", line_break, s)      # line breaks
    s = s.replace("\\&", "&").replace("~", " ").replace("$", "")
    # Accents: \'e, \"{o}, \c{c} and letters such as \AA and \o
    s = re.sub(r"\\([`'^\"~=.])\s*\{?([A-Za-z])\}?",
               lambda m: unicodedata.normalize("NFC", m.group(2) + ACCENTS[m.group(1)]), s)
    s = re.sub(r"\\(AA|aa|AE|ae|OE|oe|O|o|L|l|ss)(?![A-Za-z])(?:\{\}|\s+)?",
               lambda m: LETTER_COMMANDS[m.group(1)], s)
    for _ in range(5):                                    # \textbf{x} -> x, nested formatting
        s = re.sub(r"\\[A-Za-z]+\*?\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[A-Za-z]+\*?", "", s)                  # bare commands such as \LaTeX
    s = re.sub(r"\\[,;:! ]", " ", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def option_arg(tex: str, key: str) -> str | None:
    """Value in braces of a key=value option, e.g. pdftitle={...} in \\hypersetup."""
    m = re.search(r"\b" + key + r"\s*=\s*\{", tex)
    if not m:
        return None
    end = _matching_brace(tex, m.end() - 1)
    return None if end is None else tex[m.end():end]


def author_lines(author: str | None, affiliation: str = "", indent: str = "") -> list[str]:
    """YAML for the author. With an affiliation, Quarto shows it under the name."""
    if not author:
        return []
    if not affiliation:
        return [f"{indent}author: {json.dumps(author)}"]
    return [f"{indent}author:", f"{indent}  - name: {json.dumps(author)}",
            f"{indent}    affiliations:", f"{indent}      - name: {json.dumps(affiliation)}"]


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]


def extract_metadata(tex: str) -> dict:
    """Title and author from LaTeX source. Author names are split on \\and; text after a \\\\ in an
    author block is usually the affiliation, so only the part before it is kept."""
    tex = "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in tex.splitlines())
    out = {"title": "", "author": "", "affiliation": ""}
    title = command_arg(tex, "title")
    if title:
        out["title"] = clean_latex(title)
    author = command_arg(tex, "author")
    if author:
        names = []
        for part in re.split(r"\\and\b", drop_command(author, "thanks")):
            pieces = re.split(r"\\\\(?:\[[^\]]*\])?", part)
            name = clean_latex(pieces[0])
            if name:
                names.append(name)
            # The first line after a \\ is the affiliation (an email address is not one).
            if not out["affiliation"] and len(pieces) > 1:
                line = clean_latex(pieces[1])
                if line and "@" not in line:
                    out["affiliation"] = line
        out["author"] = join_names(names)
    # Many packages set no \title or \author and only give pdftitle/pdfauthor in \hypersetup.
    # These are the fallback; the user can edit them in the form.
    if not out["title"] and option_arg(tex, "pdftitle"):
        out["title"] = clean_latex(option_arg(tex, "pdftitle"))
    if not out["author"] and option_arg(tex, "pdfauthor"):
        out["author"] = clean_latex(option_arg(tex, "pdfauthor"))
    return out


def read_metadata(package: Path, workdir: Path) -> dict:
    """Title and author from an uploaded .tex file or a .zip package. Nothing is converted."""
    if zipfile.is_zipfile(package):
        root = unpack(package, workdir)
        main_tex = find_main(root, None)
        try:
            text = flatten(main_tex, root, base=main_tex.parent)
        except Exception:
            text = main_tex.read_text(encoding="utf-8", errors="replace")
    else:
        text = package.read_text(encoding="utf-8", errors="replace")
    return extract_metadata(text)


def find_main(root: Path, explicit: str | None) -> Path:
    if explicit:
        p = root / explicit
        if not p.exists():
            raise SystemExit(f"--main file not found: {explicit}")
        return p
    candidates = []
    for p in root.rglob("*.tex"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "\\documentclass" in text and "\\begin{document}" in text:
            candidates.append(p)
    if len(candidates) != 1:
        names = ", ".join(str(c.relative_to(root)) for c in candidates) or "none"
        raise SystemExit(f"Could not pick a single main file (found: {names}). Use --main.")
    return candidates[0]


# ---------------------------------------------------------------- 2. flatten

INPUT_RE = re.compile(r"\\(?:input|include)(?:\s*\{([^}]+)\}|[ \t]+([^\s%{}\\]+))")


def find_in(dirs: list[Path], root: Path, rel: str) -> Path | None:
    """Look in each folder in turn (the including file's folder, then the main file's folder,
    then the package root), then fall back to a case-insensitive search of the package."""
    for d in dirs:
        p = d / rel
        if p.is_file():
            return p
    return find_file(root, rel)


CHAPTER_CMD_RE = re.compile(r"^\s*\\chapter\b", re.M)
SECTION_CMD_RE = re.compile(r"\\section\*?\{([^}]*)\}")


def as_chapter(body: str, p: Path) -> str:
    """A lecture pulled into a main file that has no \\chapter commands is a chapter of its own.
    Its title is its first \\section heading (which is then removed), or else the file name."""
    m = SECTION_CMD_RE.search(body)
    if m:
        title = m.group(1).strip()
        body = body[:m.start()] + body[m.end():]
    else:
        title = p.stem.replace("_", " ")
    log(f"lecture file {p.name} becomes chapter: {title}")
    return "\\chapter{" + title + "}\n" + body


def flatten(path: Path, root: Path, depth: int = 0, base: Path | None = None,
            lectures: bool | None = None) -> str:
    if depth > 10:
        raise SystemExit("\\input nesting too deep (possible loop).")
    base = base or root
    text = path.read_text(encoding="utf-8", errors="replace")
    if lectures is None:   # decided once, from the main file
        lectures = depth == 0 and not CHAPTER_CMD_RE.search(text)
    out = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("%"):
            out.append(line)
            continue

        def repl(m):
            name = (m.group(1) or m.group(2)).strip()
            rel = name if name.endswith(".tex") else name + ".tex"
            p = find_in([path.parent, base, root], root, rel)
            if p is None:
                log(f"WARNING: include not found, left as is: {name}")
                return m.group(0)
            body = flatten(p, root, depth + 1, base, lectures=False)
            if lectures and not CHAPTER_CMD_RE.search(body):
                body = as_chapter(body, p)
            return "\n" + body + "\n"

        out.append(INPUT_RE.sub(repl, line))
    return "".join(out)


# ---------------------------------------------------------------- 3. images

INCLUDE_RE = re.compile(r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}")


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")


def eps_to_svg(eps: Path, svg: Path):
    require("gs")
    require("pdftocairo")
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "figure.pdf"
        subprocess.run(
            ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
             "-dEPSCrop", f"-sOutputFile={pdf}", str(eps)],
            check=True,
        )
        subprocess.run(["pdftocairo", "-svg", str(pdf), str(svg)], check=True)


def find_file(root: Path, src: str) -> Path | None:
    """Exact match first, then case-insensitive (sources often differ in case from file names)."""
    p = root / src
    if p.exists():
        return p
    wanted = src.lower()
    # LaTeX lets \includegraphics omit the extension, so also try the usual image types.
    wanted_forms = {wanted} | {wanted + ext for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")}
    for candidate in root.rglob("*"):
        if candidate.is_file() and str(candidate.relative_to(root)).lower() in wanted_forms:
            return candidate
    return None


def read_group(text: str, pos: int) -> tuple[str, int]:
    """Return the contents of the {...} group starting at text[pos] and the index after it."""
    if pos >= len(text) or text[pos] != "{":
        raise ValueError("expected {")
    depth = 0
    for i in range(pos, len(text)):
        if text[i] == "{" and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif text[i] == "}" and (i == 0 or text[i - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return text[pos + 1:i], i + 1
    raise ValueError("unbalanced braces")


LIST_OPTIONS_RE = re.compile(r"\\begin\{(description|itemize|enumerate)\}\s*\[[^\]]*\]")


def strip_list_options(text: str) -> str:
    """Remove options such as \\begin{description}[leftmargin=3em,style=nextline].
    Pandoc drops the whole list (and its \\item[...] terms, e.g. symbol definitions) when they are present."""
    return LIST_OPTIONS_RE.sub(lambda m: f"\\begin{{{m.group(1)}}}", text)


REMOVED_EMPTY_DISPLAYS = []  # labels (or "unlabelled") of display maths removed because it was empty
DISPLAY_ENV_RE = re.compile(r"(\\begin\{(equation|align|gather|multline|eqnarray)\*?\})(.*?)(\\end\{\2\*?\})", re.S)
DISPLAY_BRACKET_RE = re.compile(r"(\\\[)(.*?)(\\\])", re.S)


def _clean_display_body(body: str) -> str:
    """Remove source that Pandoc and Quarto cannot typeset: TeX comments, layout spacing
    (\\vspace, \\hspace, ~ and the spacing after a line break) so only the maths is left."""
    body = "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in body.split("\n"))
    body = re.sub(r"\\vspace\*?\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\hspace\*?\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\\\\s*\[[^\]]*\]", "", body)
    return body.replace("~", " ")


def clean_display_maths(text: str) -> str:
    """Tidy display equations before conversion. An equation with nothing left after removing its
    comments is dropped, and the drop is recorded so it can be checked in the report."""
    def label_of(raw):
        m = re.search(r"\\label\{([^}]*)\}", raw)
        return m.group(1) if m else "unlabelled"

    def has_maths(cleaned):
        # A label on its own is not maths: an equation with only a label would render as an empty block.
        without_labels = re.sub(r"\\label\{[^}]*\}", "", cleaned)
        return bool(without_labels.strip()) and without_labels.strip() != "\\\\"

    def env_repl(m):
        cleaned = _clean_display_body(m.group(3))
        if not has_maths(cleaned):
            REMOVED_EMPTY_DISPLAYS.append(label_of(m.group(3)))
            return ""
        if m.group(2) in ("equation", "equation*"):
            # Pandoc reads \\[...\\] reliably, but leaves \\begin{equation} as raw text inside
            # text lines and tables, so unwrap plain equations.
            return "\\[" + cleaned.strip() + "\\]"
        return m.group(1) + cleaned + m.group(4)

    def bracket_repl(m):
        cleaned = _clean_display_body(m.group(2))
        if not has_maths(cleaned):
            REMOVED_EMPTY_DISPLAYS.append(label_of(m.group(2)))
            return ""
        return m.group(1) + cleaned + m.group(3)

    text = DISPLAY_ENV_RE.sub(env_repl, text)
    text = DISPLAY_BRACKET_RE.sub(bracket_repl, text)
    return text


def normalise_math_macros(text: str) -> str:
    """Macros Quarto's Typst maths converter does not understand; the whole equation then falls back
    to raw text in the PDF. \\textnormal -> \\text does the same job in MathJax and Typst.
    \\nonumber only matters for numbering, and unlabelled display maths is already unnumbered."""
    text = clean_display_maths(text)
    # wrapfigure is not something Pandoc or Quarto can label, so treat it as an ordinary figure
    # (\label inside it is then kept as a cross-reference target).
    text = re.sub(r"\\begin\{wrapfigure\}\s*(\[[^\]]*\])?\s*\{[^{}]*\}\s*\{[^{}]*\}", r"\\begin{figure}", text)
    text = text.replace("\\end{wrapfigure}", "\\end{figure}")
    text = re.sub(r"\\textnormal\b", r"\\text", text)
    text = re.sub(r"\\nonumber\b", "", text)
    # align-type environments inside paragraphs are passed through by Pandoc as raw LaTeX, which
    # swallows the closing ::: of the enclosing callout. As display maths they convert cleanly.
    text = re.sub(r"\\begin\{(?:align|gather|multline|eqnarray)\*?\}", r"\\[\\begin{aligned}", text)
    text = re.sub(r"\\end\{(?:align|gather|multline|eqnarray)\*?\}", r"\\end{aligned}\\]", text)
    return text


# ---------------------------------------------------------------- shaded box macros



def header_text() -> str:
    return "<style>img, svg {max-width: 100%; height: auto;}\n.callout {margin: 1.2em 0;}</style>\n"


def find_box_macros(text: str) -> dict:
    """Macros whose definition draws a coloured box (\\colorbox{...}). Maps name -> colour names,
    outer colour first, then the inner box's colour if there is one."""
    boxes = {}
    for m in re.finditer(r"\\newcommand\*?\{\\(\w+)\}(?:\[\d\])?\s*", text):
        pos = m.end()
        if pos >= len(text) or text[pos] != "{":
            continue
        try:
            body, _ = read_group(text, pos)
        except ValueError:
            continue
        colours = re.findall(r"\\colorbox\{(\w+)\}", body)
        if colours:
            boxes[m.group(1)] = colours
    return boxes


def apply_box_macros(text: str, boxes: dict) -> str:
    """\\name{content} becomes \\begin{box_name}...\\end{box_name}. Pandoc turns that into a div with
    class box_name, and quarto_filter.lua turns it into a Quarto callout (its type and title are set
    there). A second, inner colour is only the box's background, so it is not needed."""
    for name, colours in boxes.items():
        pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z])\s*")
        out, i = [], 0
        while True:
            m = pat.search(text, i)
            if not m:
                out.append(text[i:])
                break
            pos = m.end()
            try:
                if pos < len(text) and text[pos] == "{":
                    content, end = read_group(text, pos)
                else:
                    raise ValueError("no argument")
            except ValueError:
                out.append(text[i:m.end()])
                i = m.end()
                continue
            out.append(text[i:m.start()])
            out.append(f"\\begin{{box_{name}}}" + content + f"\\end{{box_{name}}}")
            i = end
        text = "".join(out)
    return text


def unwrap_resizebox(text: str) -> str:
    """\\resizebox{w}{h}{<table>} hides the table from Pandoc, which then drops it. Keep only <table>."""
    out, i = [], 0
    while True:
        j = text.find("\\resizebox", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        try:
            k = j + len("\\resizebox")
            _, k = read_group(text, k)          # width
            _, k = read_group(text, k)          # height
            body, k = read_group(text, k)       # content
            out.append(body)
            i = k
        except ValueError:
            out.append(text[j:j + len("\\resizebox")])
            i = j + len("\\resizebox")
    return "".join(out)


TRAILING_BREAK_RE = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\{[^}]+\})\s*\\\\")


def convert_images(text: str, root: Path, outdir: Path, base: Path | None = None) -> str:
    figs = outdir / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    seen = {}
    failures = []
    # "\includegraphics{x}\\" starts a new line, which Pandoc reads as inline HTML instead of a figure.
    text = TRAILING_BREAK_RE.sub(r"\1", text)

    def repl(m):
        src = m.group(2).strip()          # options (trim, width) are dropped; HTML sizes images responsively
        p = find_in([base or root], root, src)
        if p is None:
            failures.append(src)
            return m.group(0)
        if str(p.relative_to(root)) != src:
            log(f"note: '{src}' matched '{p.relative_to(root)}' (case differs)")
        if src in seen:
            return f"\\includegraphics{{figs/{seen[src]}}}"
        stem = safe(Path(src).stem)
        if p.suffix.lower() == ".pdf":
            # A web page cannot show a PDF figure inline, so it becomes SVG (first page only).
            name = stem + ".svg"
            if shutil.which("pdftocairo") is None:
                failures.append(f"{src} (not converted: Poppler (pdftocairo) is not installed)")
                return m.group(0)
            try:
                subprocess.run(["pdftocairo", "-svg", "-f", "1", "-l", "1", str(p), str(figs / name)],
                               check=True)
            except subprocess.CalledProcessError as e:
                failures.append(f"{src} (conversion failed: {e})")
                return m.group(0)
        elif p.suffix.lower() == ".eps":
            name = stem + ".svg"
            # A missing tool is reported against this figure; the rest of the conversion carries on.
            if shutil.which("gs") is None or shutil.which("pdftocairo") is None:
                failures.append(f"{src} (not converted: Ghostscript and Poppler are needed for EPS figures)")
                return m.group(0)
            try:
                eps_to_svg(p, figs / name)
            except subprocess.CalledProcessError as e:
                failures.append(f"{src} (conversion failed: {e})")
                return m.group(0)
        else:
            name = stem + p.suffix.lower()
            shutil.copy(p, figs / name)
        seen[src] = name
        return f"\\includegraphics{{figs/{name}}}"

    text = INCLUDE_RE.sub(repl, text)
    log(f"figures converted: {len(seen)}")
    for f in failures:
        log(f"WARNING: figure problem: {f}")
    return text


# ---------------------------------------------------------------- 4. labels and references

REF_WORD = (r"(?:Figs?\.?|Figures?|Tables?|Tab\.|Eqs?\.|Equations?|Sections?|Secs?\.|"
            r"Chapters?|Chap\.)\s*~?\s*")


def prefix_for(key: str) -> str | None:
    if key.startswith(("Fig", "fig")):
        return "fig-"
    if key.startswith(("tab", "Tab")):
        return "tbl-"
    if key.startswith(("eq", "Eq")):
        return "eq-"
    if key.startswith(("sec", "Sec", "chap", "Chap")):
        return "sec-"
    return None


def rewrite_labels(text: str) -> tuple[str, dict]:
    """Rename labels to Quarto prefixes and turn \\ref/\\eqref into @-references.

    Labels built from \\arabic counters (problem/solution macros) are left for the
    Lua filter to remove, because they cannot resolve statically.
    """
    labels = {}

    def new_label(key):
        if "arabic" in key:
            return None
        p = prefix_for(key)
        if p is None:
            return None
        s = safe(key)
        labels[key] = s if s.startswith(p) else p + s   # avoid "eq-eq-newton" for \label{eq:newton}
        return labels[key]

    # Collect labels first so references can be resolved in one pass.
    for key in re.findall(r"\\label\{([^}]+)\}", text):
        new_label(key)

    def label_repl(m):
        key = m.group(1)
        new = labels.get(key)
        return f"\\label{{{new}}}" if new else m.group(0)

    text = re.sub(r"\\label\{([^}]+)\}", label_repl, text)

    def eqref_repl(m):
        key = m.group(1)
        return f"@{labels[key]}" if key in labels else m.group(0)

    # "Eq.~\eqref{X}" -> "@eq-X": drop the typed word, Quarto supplies "Equation N" (avoids "Eq. Equation (1.8)").
    text = re.sub(r"Eqs?\.(?:~|\s)*(?=\\eqref\{)", "", text)
    text = re.sub(r"\\eqref\{([^}]+)\}", eqref_repl, text)

    # "Fig. \ref{X}" -> "@fig-X": drop the typed word, Quarto supplies "Figure N".
    def ref_repl(m):
        key = m.group(1)
        return f"@{labels[key]}" if key in labels else m.group(0)

    text = re.sub(REF_WORD + r"\\ref\{([^}]+)\}", ref_repl, text)
    text = re.sub(r"\\ref\{([^}]+)\}", ref_repl, text)
    log(f"labels renamed: {len(labels)}")
    return text, labels


# ---------------------------------------------------------------- 5. pandoc

def run_pandoc(tex: Path, out_md: Path, book: bool = False):
    require("pandoc")
    cmd = ["pandoc", str(tex), "-f", "latex", "-t", "markdown", "--wrap=none",
           "--lua-filter", str(FILTER), "-o", str(out_md)]
    env = dict(os.environ, QUARTEX_BOOK="1" if book else "0")
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise SystemExit(f"pandoc failed:\n{res.stderr}")
    if res.stderr.strip():
        for line in res.stderr.strip().splitlines():
            log(f"pandoc: {line}")


# ---------------------------------------------------------------- 6. markdown post-processing

EQ_LABEL_RE = re.compile(r"\$\$((?:(?!\$\$).)*?)\\label\{(eq-[^}]+)\}((?:(?!\$\$).)*?)\$\$", re.S)


DISPLAY_RE = re.compile(r"\$\$(.*?)\$\$", re.S)
BLANK_IN_MATH_RE = re.compile(r"\n[ \t]*\n")


def join_split_display_math(md: str) -> tuple[str, int]:
    """A blank line inside $$...$$ ends the paragraph in Markdown, which breaks the maths.
    Blank lines are removed from inside display blocks; the count is reported."""
    count = 0

    def repl(m):
        nonlocal count
        body = m.group(1)
        new, n = BLANK_IN_MATH_RE.subn("\n", body)
        count += n
        return "$$" + new + "$$"

    return DISPLAY_RE.sub(repl, md), count


def fix_equation_labels(md: str) -> str:
    """Quarto numbers a display equation when '{#eq-...}' follows the closing $$ on a paragraph of its own.
    Labelled equations that sit mid-sentence are moved into their own paragraph."""
    def repl(m):
        body = m.group(1) + m.group(3)
        return f"\n\n$${body}$$ {{#{m.group(2)}}}\n\n"
    return EQ_LABEL_RE.sub(repl, md)


CLOSING_FENCE_RE = re.compile(r"^:{3,}\s*$")
TRAILING_FENCE_RE = re.compile(r"^(.*\S)\s+(:{3,})\s*$")


def fix_closing_fences(md: str) -> str:
    """A closing ::: must sit on its own line, after a blank line. Where it has been run on to the
    end of a line of text (or follows a paragraph directly), Pandoc reads it as text, so the box
    never closes. Each such closing fence is moved onto its own line."""
    out = []
    for line in md.split("\n"):
        if line.lstrip().startswith(":::") and line != line.lstrip():
            # A callout indented inside a list item breaks as soon as its content starts at column 0
            # (e.g. display maths), so its fences are moved to column 0. The box then sits after the list.
            if out and out[-1].strip() != "":
                out.append("")
            line = line.lstrip()
        if not line.lstrip().startswith(":::"):
            m = TRAILING_FENCE_RE.match(line)
            if m:
                out.append(m.group(1))
                out.append("")
                out.append(m.group(2))
                continue
        if CLOSING_FENCE_RE.match(line) and out and out[-1].strip() != "":
            out.append("")
        out.append(line)
    return "\n".join(out)


DISPLAY_LABELLED_RE = re.compile(r"\$\$(.*?)\$\$\s*\{#(eq-[^}\s]+)\}", re.S)
INNER_EQ_LABEL_RE = re.compile(r"\\label\{(eq-[^}\s]+)\}")


def fix_inner_equation_labels(md: str) -> str:
    """A \\label{eq-X} inside a multi-line display (one labelled equation with several lines) cannot be
    referenced by Quarto. Such labels are removed, and references to them point to the whole equation."""
    mapping = {}

    def repl(m):
        body, eq_id = m.group(1), m.group(2)
        for inner in INNER_EQ_LABEL_RE.findall(body):
            if inner != eq_id:
                mapping[inner] = eq_id
        body = INNER_EQ_LABEL_RE.sub("", body)
        return f"$${body}$$ {{#{eq_id}}}"

    md = DISPLAY_LABELLED_RE.sub(repl, md)
    for old, new in mapping.items():
        md = re.sub(r"@" + re.escape(old) + r"(?![\w-])", "@" + new, md)
    return md


def unescape_refs(md: str) -> str:
    """Pandoc escapes '@' before cross-reference keys ('\\@fig-x'), which Quarto then shows as literal text."""
    return re.sub(r"\\@((?:fig|tbl|eq|sec)-)", r"@\1", md)


def separate_suffixes(md: str, labels) -> str:
    """'Fig. \\ref{X}b' becomes '@fig-X' followed by 'b' in the Markdown. Quarto would read
    'fig-Xb' as one label, so an invisible HTML comment ends the reference first.
    A run that is itself a label (e.g. '@eq-x-R1' when 'eq-x-R1' exists) is left alone."""
    known = set(labels)
    by_len = sorted(known, key=len, reverse=True)

    def repl(m):
        run = m.group(1)
        if run in known:
            return m.group(0)
        for label in by_len:
            if run.startswith(label):
                return "@" + label + "<!-- -->" + run[len(label):]
        return m.group(0)

    return re.sub(r"@((?:fig|tbl|eq|sec)-[A-Za-z0-9_-]+)", repl, md)


PDF_FORMAT_LINES = ["  typst:", "    toc: true", "    number-sections: true", "    papersize: a4",
                    "    include-in-header: page-setup.typ"]
PAGE_SETUP_FILE = "page-setup.typ"


def write_page_setup(outdir: Path, title: str, author: str | None) -> None:
    """Typst page header (title, author) and footer (copyright and year, page number) for the PDF.
    The year is worked out by Typst when the PDF is built, so it is always the current year."""
    title_lit = json.dumps(title, ensure_ascii=False)
    right = f"#{json.dumps(author, ensure_ascii=False)}" if author else ""
    text = (
        "#set page(\n"
        "  header: context {\n"
        "    set text(size: 8.5pt, fill: luma(90))\n"
        f"    grid(columns: (1fr, auto), [#{title_lit}], align(right)[{right}])\n"
        "    line(length: 100%, stroke: 0.4pt + luma(180))\n"
        "  },\n"
        "  footer: context {\n"
        "    set text(size: 8.5pt, fill: luma(90))\n"
        "    grid(columns: (1fr, auto), [© Durham University #datetime.today().year()],\n"
        "      align(right)[#counter(page).display(\"1 / 1\", both: true)])\n"
        "  },\n"
        ")\n"
    )
    (outdir / PAGE_SETUP_FILE).write_text(text, encoding="utf-8")


def pdf_slug(title: str) -> str:
    """File name of the book's PDF, derived from the title (used for the PDF and the link to it)."""
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "notes"





HTML_FIGURE_RE = re.compile(r'<figure id="([^"]+)">(.*?)</figure>', re.S)


def _html_to_md(fragment: str) -> str:
    """Pandoc's HTML reader turns the text inside a figure back into Markdown."""
    if not fragment.strip():
        return ""
    res = subprocess.run(["pandoc", "-f", "html", "-t", "markdown", "--wrap=none", "--quiet"],
                         input=fragment, capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else fragment.strip()


def fix_html_figures(md: str) -> str:
    """Pandoc writes some figures as raw <figure id=...> HTML. Quarto 1.6 cannot cross-reference
    those (\"Unable to resolve crossref\"), so turn each into a Markdown figure with the same label.
    Any text that was inside the figure (from a minipage) is kept after it."""
    def repl(m):
        fig_id, body = m.group(1), m.group(2)
        img = re.search(r'<img src="([^"]+)"', body)
        if not img:
            return m.group(0)
        cap_m = re.search(r"<figcaption>(.*?)</figcaption>", body, re.S)
        caption_html = cap_m.group(1) if cap_m else ""
        rest = re.sub(r'<img[^>]*/?>|<figcaption>.*?</figcaption>', "", body, flags=re.S)
        caption = _html_to_md(caption_html).replace("\n", " ").replace("]", "\\]")
        alt = re.sub(r"<[^>]+>", "", caption_html).strip().replace('"', "'")[:200] or fig_id
        out = f'![{caption}]({img.group(1)}){{#{fig_id} fig-alt="{alt}"}}'
        extra = _html_to_md(rest)
        return out + ("\n\n" + extra if extra else "")
    md = HTML_FIGURE_RE.sub(repl, md)
    # Consecutive figures on adjacent lines form one paragraph, so Quarto does not number them.
    return re.sub(r"(\{#fig-[^}]*\})\n(?=!\[)", r"\1\n\n", md)


def write_qmd(md: str, title: str, outdir: Path, bibs: list[str], pdf: bool = True,
              author: str | None = None, theme: str = DEFAULT_THEME, affiliation: str = "") -> Path:
    md = fix_html_figures(md)
    if pdf:
        write_page_setup(outdir, title, author)
    header = ["---", f'title: "{title}"', *author_lines(author, affiliation)]
    if bibs:
        header.append("bibliography: " + bibs[0] if len(bibs) == 1 else "bibliography:")
        if len(bibs) > 1:
            header += [f"  - {b}" for b in bibs]
    header += [
        "link-citations: true",
        "format:",
        "  html:",
        "    embed-resources: true",
        *theme_lines(theme),
        "    number-sections: true",
        "    toc: true",
        "    toc-location: right",
        "    toc-depth: 3",
        "    toc-title: On this page",
        "    include-in-header: header.html",
    ]
    if pdf:
        header += PDF_FORMAT_LINES
    header += [
        "crossref:",
        "  chapters: true",
        "---",
        "",
    ]
    (outdir / "header.html").write_text(
        header_text(), encoding="utf-8")
    qmd = outdir / "document.qmd"
    link = ""  # the PDF appears in Quarto's "Other Formats" list (typst format), not in the body
    qmd.write_text("\n".join(header) + link + md, encoding="utf-8")
    return qmd


def count_chapters(tex: str) -> int:
    """Count \\chapter commands in the uncommented LaTeX source."""
    lines = [l for l in tex.splitlines() if not l.lstrip().startswith("%")]
    return len(re.findall(r"\\chapter\*?[\[{]", "\n".join(lines)))


# ---------------------------------------------------------------- 6b. book project

FENCE_RE = re.compile(r"^(```|~~~)")
CHAPTER_RE = re.compile(r"^# (?!#)(.*)$")


def split_chapters(md: str) -> tuple[str, list[tuple[str, str]]]:
    """Split Markdown at top-level headings (LaTeX \\chapter). Lines inside code fences are kept."""
    front, chapters, current = [], [], None
    in_fence = False
    for line in md.splitlines(keepends=True):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if not in_fence:
            m = CHAPTER_RE.match(line.rstrip("\n"))
            if m:
                current = (m.group(1).strip(), [line])
                chapters.append(current)
                continue
        if current is None:
            front.append(line)
        else:
            current[1].append(line)
    return "".join(front), [(t, "".join(body)) for t, body in chapters]


def write_book(md: str, title: str, author: str | None, outdir: Path, bibs: list[str], pdf: bool = True,
               theme: str = DEFAULT_THEME, affiliation: str = "") -> list[str]:
    md = fix_html_figures(md)
    front, chapters = split_chapters(md)
    if not chapters:
        raise SystemExit("No chapters found (no top-level headings), so no book can be built.")
    # Quarto always needs index.qmd as the book's home page, and it is listed (and numbered) in the
    # sidebar. So the first chapter is the home page: its heading comes first, then any cover
    # material (logo, names, citation) from before it, then the rest of the chapter.
    files = ["index.qmd"]
    first_title, first_body = chapters[0]
    heading, _, rest = first_body.partition("\n")
    cover = front.strip()
    index_text = heading + "\n\n" + (cover + "\n\n" if cover else "") + rest
    (outdir / "index.qmd").write_text(index_text, encoding="utf-8")
    log(f"chapter 1: {first_title} (also the book's home page)")
    for i, (ctitle, body) in enumerate(chapters[1:], 2):
        name = f"chapter-{i:02d}.qmd"
        (outdir / name).write_text(body, encoding="utf-8")
        files.append(name)
        log(f"chapter {i}: {ctitle}")
    # No separate references page: each chapter gets its own list, as in the source (refsection=chapter).

    (outdir / "header.html").write_text(
        header_text(), encoding="utf-8")
    lines = ["project:", "  type: book", "  output-dir: _book", "", "book:",
             f"  title: {json.dumps(title)}"]
    # A blank author is needed when there is none: the PDF template fails without one.
    lines += author_lines(author, affiliation, indent="  ") or ['  author: " "']
    if pdf:
        # Quarto shows a PDF icon beside the book title in the sidebar. render_book removes this
        # line again if the PDF is not produced, so the icon never points at a missing file.
        lines.append("  downloads: [pdf]")
    lines.append("  chapters:")
    lines += [f"    - {f}" for f in files]
    if bibs:   # an empty "bibliography:" key makes Quarto reject the project
        lines += ["", "bibliography:"] + [f"  - {b}" for b in bibs]
        lines += ["link-citations: true"]
    lines += ["crossref:", "  chapters: true", "", "format:", "  html:",
              *theme_lines(theme), "    number-sections: true", "    toc: true",
              "    toc-location: right", "    toc-depth: 3", "    toc-title: On this page",
              "    include-in-header: header.html"]
    # No Typst format here: Quarto does not support PDF output in book projects (the PDF is built
    # from a single combined document in build_print_pdf).
    lines.append("")
    (outdir / "_quarto.yml").write_text("\n".join(lines), encoding="utf-8")
    return files


def pdf_error_lines(args: list[str], cwd: Path) -> list[str]:
    """Run the PDF (Typst) step on its own and return its error lines, so the report says why no PDF."""
    res = subprocess.run(["quarto", "render", *args, "--to", "typst"], cwd=cwd,
                         capture_output=True, text=True)
    lines = [l.strip() for l in (res.stdout + res.stderr).splitlines()
             if "error" in l.lower() and "kernel" not in l.lower()]
    return lines[:15] or ["Typst step gave no error message; see the Terminal output"]


def build_print_pdf(outdir: Path, title: str, author: str | None, bibs: list[str],
                    affiliation: str = "") -> list[str]:
    """Book projects cannot output Typst PDF, so the chapters are joined into one document and rendered
    to PDF. The PDF is moved into _book beside index.html. Returns error lines if it fails."""
    parts = [outdir / "index.qmd"] + sorted(outdir.glob("chapter-*.qmd"))
    fm = ["---", f"title: {json.dumps(title)}"]
    fm += author_lines(author, affiliation)
    if bibs:
        fm += ["bibliography:"] + [f"  - {b}" for b in bibs] + ["link-citations: true"]
    write_page_setup(outdir, title, author)
    fm += ["crossref:", "  chapters: true", "format:", "  typst:", "    toc: true",
           "    number-sections: true", "    papersize: a4",
           f"    include-in-header: {PAGE_SETUP_FILE}", "---", ""]
    body = "\n\n".join(p.read_text(encoding="utf-8") for p in parts)
    name = "print-edition.qmd"
    (outdir / name).write_text("\n".join(fm) + "\n" + body, encoding="utf-8")
    res = subprocess.run(["quarto", "render", name, "--to", "typst"], cwd=outdir,
                         capture_output=True, text=True)
    made = outdir / "print-edition.pdf"
    errors = []
    if made.exists():
        shutil.move(made, outdir / f"{pdf_slug(title)}.pdf")
    else:
        errors = [l.strip() for l in (res.stdout + res.stderr).splitlines()
                  if "error" in l.lower() and "kernel" not in l.lower()][:15]
        errors = errors or ["Typst step gave no error message; see the Terminal output"]
    (outdir / name).unlink(missing_ok=True)
    shutil.rmtree(outdir / "print-edition_files", ignore_errors=True)
    return errors


def render_book(outdir: Path, pdf: bool = True, title: str = "", author: str | None = None,
                bibs: list[str] | None = None, affiliation: str = "") -> tuple[bool, list[str]]:
    require("quarto")
    # Book projects have no PDF format, so the PDF is built separately, first, so that the
    # sidebar download setting is only kept when the PDF exists.
    warnings = []
    if pdf:
        errors = build_print_pdf(outdir, title, author, bibs or [], affiliation)
        if errors:
            warnings += ["PDF NOT PRODUCED. Typst error:"] + errors
            config = outdir / "_quarto.yml"
            config.write_text("\n".join(l for l in config.read_text(encoding="utf-8").splitlines()
                                         if l.strip() != "downloads: [pdf]") + "\n", encoding="utf-8")
    res = subprocess.run(["quarto", "render"], cwd=outdir, capture_output=True, text=True)
    # Quarto clears _book when it renders, so the PDF is moved in afterwards.
    pdf_file = outdir / f"{pdf_slug(title)}.pdf"
    if pdf and pdf_file.exists():
        (outdir / "_book").mkdir(exist_ok=True)
        shutil.move(pdf_file, outdir / "_book" / pdf_file.name)
    warnings += [l for l in (res.stdout + res.stderr).splitlines()
                 if any(key in l for key in ("WARNING", "ERROR", "FATAL", "Error", "error"))
                 and "Kernel is running" not in l]
    return res.returncode == 0, warnings


# ---------------------------------------------------------------- 7. render and report

def _quarto_warnings(res) -> list[str]:
    return [l for l in (res.stdout + res.stderr).splitlines()
            if any(key in l for key in ("WARNING", "ERROR", "FATAL", "Error", "error"))
            and "Kernel is running" not in l]


def render(qmd: Path, pdf: bool = True) -> tuple[bool, list[str]]:
    """Render the web page and (optionally) the PDF in separate steps. A PDF failure must not
    stop the web page from being finished, and the PDF link is removed when there is no PDF."""
    require("quarto")
    warnings = []
    if pdf:
        res_pdf = subprocess.run(["quarto", "render", qmd.name, "--to", "typst"],
                                 cwd=qmd.parent, capture_output=True, text=True)
        warnings += _quarto_warnings(res_pdf)
        if not qmd.with_suffix(".pdf").exists():
            warnings += ["PDF NOT PRODUCED. Typst error:"] + pdf_error_lines([qmd.name], qmd.parent)
            # Quarto still lists a failed PDF under "Other Formats", as a link to a missing file,
            # so the Typst format is taken out of the page before the web page is rendered.
            text = qmd.read_text(encoding="utf-8")
            qmd.write_text(text.replace("\n".join(PDF_FORMAT_LINES) + "\n", ""), encoding="utf-8")
    res = subprocess.run(["quarto", "render", qmd.name, "--to", "html"],
                         cwd=qmd.parent, capture_output=True, text=True)
    warnings += _quarto_warnings(res)
    html_ok = res.returncode == 0 and qmd.with_suffix(".html").exists()
    return html_ok, warnings


def leftovers(md: str) -> dict:
    return {
        "raw_html_embeds": md.count("<embed"),
        "counter_links_left": md.count("arabic"),
        "latex_labels_left": len(re.findall(r"\\label\{", md)),
        "unconverted_refs": len(re.findall(r"\\@(?:fig|tbl|eq|sec)-", md)),
        "latex_refs_left": len(re.findall(r"\\(?:ref|eqref)\{", md)),
    }


def check_math(qmd: Path) -> tuple[int | None, list[str]]:
    """Typeset every maths block with MathJax (needs node and `npm install` in this folder)."""
    script = HERE / "check_math.mjs"
    if shutil.which("node") is None or not (HERE / "node_modules" / "mathjax-full").exists():
        return None, ["maths check skipped: needs node, and `npm install mathjax-full@3` in the tool folder"]
    res = subprocess.run(["node", str(script), str(qmd), str(qmd.parent / "math-failures.json")],
                         capture_output=True, text=True, timeout=900)
    lines = (res.stdout + res.stderr).strip().splitlines()
    failures = next((int(l.split(":")[1]) for l in lines if l.startswith("failures:")), None)
    return failures, lines


def write_report(outdir: Path, md: str, labels: dict, render_ok: bool, warnings: list[str],
                 math_result):
    lo = leftovers(md)
    lines = ["# Conversion report", ""]
    lines.append(f"- Render succeeded: {'yes' if render_ok else 'NO'}")
    lines.append(f"- Labels converted to Quarto cross-references: {len(labels)}")
    lines.append(f"- Figures: {md.count('{#fig-')}, tables: {md.count('{#tbl-')}, labelled equations: {md.count('{#eq-')}")
    lines.append(f"- Callouts (challenge, problem, worked solution): {md.count('callout-note') + md.count('callout-important') + md.count('callout-tip')}")
    lines.append(f"- Raw HTML image embeds left: {lo['raw_html_embeds']}")
    lines.append(f"- Counter-based links left (\\arabic): {lo['counter_links_left']}")
    lines.append(f"- LaTeX labels left in output: {lo['latex_labels_left']}")
    lines.append(f"- References not converted to Quarto form: {lo['unconverted_refs'] + lo['latex_refs_left']}")
    if math_result is not None:
        n, detail = math_result
        lines.append(f"- Maths blocks failing MathJax compilation: {n if n is not None else 'not checked'}")
    if warnings:
        lines += ["", "## Quarto warnings", ""] + [f"- {w}" for w in warnings[:200]]
    if math_result is not None and math_result[1]:
        lines += ["", "## Maths check", ""] + [f"    {l}" for l in math_result[1][:40]]
    (outdir / "conversion-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", type=Path, help="zip file or folder containing the LaTeX source")
    ap.add_argument("outdir", type=Path, help="output folder")
    ap.add_argument("--main", help="main .tex file, relative to the package root")
    ap.add_argument("--title", help="document title (default: main file name)")
    ap.add_argument("--no-render", action="store_true", help="write the .qmd but do not run quarto")
    ap.add_argument("--check-math", action="store_true", help="typeset all maths with MathJax (needs node)")
    ap.add_argument("--no-pdf", action="store_true", help="do not also produce a PDF download")
    ap.add_argument("--mode", choices=["auto", "book", "single"], default="auto",
                    help="auto (default): a book if the source has two or more chapters, otherwise a single page")
    ap.add_argument("--author", help="author name for the book front page")
    ap.add_argument("--affiliation", default=DEFAULT_AFFILIATION,
                    help="affiliation shown under the author (default: Durham University; '' for none)")
    ap.add_argument("--theme", choices=THEMES, default=DEFAULT_THEME,
                    help="Quarto HTML theme (default: cosmo; 'default' is Quarto's own theme)")
    args = ap.parse_args()

    outdir = args.outdir.resolve()
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)

    root = unpack(args.package.resolve(), outdir / "source")
    main_tex = find_main(root, args.main)
    log(f"main file: {main_tex.relative_to(root)}")

    text = flatten(main_tex, root, base=main_tex.parent)
    text = strip_list_options(text)
    text = normalise_math_macros(text)
    # Shaded box macros (e.g. \ebox, \derivation) become Quarto callouts; see quarto_filter.lua.
    package_src = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                            for p in root.rglob("*") if p.suffix.lower() in (".tex", ".sty"))
    boxes = find_box_macros(package_src)
    for name in boxes:
        uses = len(re.findall(r"\\" + re.escape(name) + r"(?![A-Za-z])", text))
        log(f"shaded box macro \\{name}: {uses} uses")
    text = apply_box_macros(text, boxes)
    text = unwrap_resizebox(text)
    chapters = count_chapters(text)
    if args.mode == "auto":
        book_mode = chapters >= 2
    else:
        book_mode = args.mode == "book"
    log(f"chapters in source: {chapters}; layout: {'book' if book_mode else 'single page'}")
    text = convert_images(text, root, outdir, base=main_tex.parent)
    text, labels = rewrite_labels(text)

    work_tex = outdir / "document.tex"
    work_tex.write_text(text, encoding="utf-8")

    md_path = outdir / "pandoc-output.md"
    run_pandoc(work_tex, md_path, book=book_mode)
    md = md_path.read_text(encoding="utf-8")
    md = unescape_refs(md)
    md = fix_closing_fences(md)
    md = fix_equation_labels(md)
    md = fix_inner_equation_labels(md)
    md, split_fixed = join_split_display_math(md)
    log(f"blank lines removed inside display maths: {split_fixed}")
    md = separate_suffixes(md, labels.values())

    bibs = sorted(p.name for p in root.glob("*.bib"))
    for b in bibs:
        shutil.copy(root / b, outdir / b)

    title = args.title or main_tex.stem.replace("_", " ")
    if book_mode:
        write_book(md, title, args.author, outdir, bibs, pdf=not args.no_pdf, theme=args.theme,
                   affiliation=args.affiliation)
        log(f"wrote Quarto book project in {outdir}")
        render_ok, warnings = (False, [])
        if not args.no_render:
            render_ok, warnings = render_book(outdir, pdf=not args.no_pdf, title=title,
                                              author=args.author, bibs=bibs, affiliation=args.affiliation)
            log("render " + ("succeeded" if render_ok else "FAILED"))
            if not render_ok:
                for line in warnings[:30]:
                    log("  " + line.strip())
        qmd = md_path
    else:
        qmd = write_qmd(md, title, outdir, bibs, pdf=not args.no_pdf, author=args.author, theme=args.theme,
                        affiliation=args.affiliation)
        log(f"wrote {qmd}")
        render_ok, warnings = (False, [])
        if not args.no_render:
            render_ok, warnings = render(qmd, pdf=not args.no_pdf)
            log("render " + ("succeeded" if render_ok else "FAILED"))
            if not render_ok:
                for line in warnings[:30]:
                    log("  " + line.strip())
    for label in REMOVED_EMPTY_DISPLAYS:
        log(f"removed empty display maths ({label}): nothing left after removing comments; check references to it")
    math_result = None
    if args.check_math:
        math_result = check_math(qmd)
        log(f"maths check: {math_result[0]} failing blocks")
    write_report(outdir, md, labels, render_ok, warnings, math_result)
    log(f"report: {outdir / 'conversion-report.md'}")


if __name__ == "__main__":
    main()
