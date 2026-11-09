#!/usr/bin/env python3
"""Local web page for QuarTeX.

Runs only on this computer (127.0.0.1). The LaTeX package is uploaded to this machine, converted
here, and the result is downloaded back. Nothing is sent to any other server.

Run:  python3 webapp.py            (then use the page that opens, or go to the address it prints)
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import check_setup
import quartex

HERE = Path(__file__).resolve().parent
CONVERTER = HERE / "quartex.py"
JOBS = Path.home() / "QuarTeX-jobs"
MAX_UPLOAD = 300 * 1024 * 1024  # 300 MB
STATIC_FILES = {
    "/logo.png": ("logo.png", "image/png"),
    "/favicon-32.png": ("favicon-32.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
JOB_STATE = {}  # job id -> "running" | "done" | "failed"
LOCK = threading.Lock()


def download_name(out_dir):
    """File name for the download: the notes' title, read from the generated Quarto project."""
    for fname, pattern in (("_quarto.yml", r'^  title: "(.*)"\s*$'), ("document.qmd", r'^title: "(.*)"\s*$')):
        path = out_dir / fname
        if path.exists():
            m = re.search(pattern, path.read_text(encoding="utf-8", errors="replace"), re.M)
            if m and m.group(1).strip():
                title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", m.group(1).replace('\\"', '"'))
                title = re.sub(r"\s+", " ", title).strip()[:100].strip()
                if title:
                    return f"{title}_converted.zip"
    return "converted-notes.zip"


def safe_name(value):
    """A plain file name: no folders, and only letters, digits, dots, dashes, underscores and spaces."""
    base = Path((value or "").replace("\\", "/")).name
    keep = "".join(ch for ch in base if ch.isalnum() or ch in "._- ")
    keep = keep.strip(" .") or "main.tex"
    return keep[:120]


def clean_text(value, limit=200):
    """Keep only plain printable text for the title and author fields."""
    return "".join(ch for ch in (value or "").strip() if ch.isprintable())[:limit]


REVIEW_NOTE = """Before you publish these notes, please review them in Quarto:

- Check that every figure has alt text that describes what it shows. The converter only
  guesses alt text from the file name, so it is often unhelpful.
- Check that the maths, tables and cross-references display correctly.
- Check the callout boxes and the order of the sections.
"""


def run_job(job_id, job_dir, options):
    log_path = job_dir / "log.txt"
    out_dir = job_dir / "output"
    cmd = [sys.executable, str(CONVERTER), str(job_dir / "package.zip"), str(out_dir)]
    if options["title"]:
        cmd += ["--title", options["title"]]
    if options["author"]:
        cmd += ["--author", options["author"]]
    cmd += ["--affiliation", options["affiliation"]]
    if options["mode"] != "auto":
        cmd += ["--mode", options["mode"]]
    cmd += ["--theme", options["theme"]]
    if options["no_pdf"]:
        cmd += ["--no-pdf"]
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=job_dir, env=env)
    state = "failed"
    if proc.returncode == 0 and out_dir.exists():
        (out_dir / "REVIEW_BEFORE_PUBLISHING.txt").write_text(REVIEW_NOTE, encoding="utf-8")
        result = job_dir / "result.zip"
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out_dir.rglob("*")):
                if path.is_file() and ".quarto" not in path.relative_to(out_dir).parts:
                    zf.write(path, path.relative_to(out_dir).as_posix())
        state = "done"
    with LOCK:
        JOB_STATE[job_id] = state


def tail(path, chars=6000):
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-chars:]


PAGE = """<!doctype html>
<html lang="en-GB"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>QuarTeX converter</title>
<link rel="icon" href="/favicon-32.png" type="image/png" sizes="32x32">
<link rel="apple-touch-icon" href="/apple-touch-icon.png" sizes="180x180">
<style>
:root { color-scheme: light dark; --bg:#ffffff; --fg:#1d1d1f; --muted:#4a4f57; --line:#767b84; --accent:#0f4c9a; --accent-text:#ffffff;
  --ok:#1a5c1f; --bad:#8f1626; --code-bg:#f0f0f2; --link:#0f4c9a; --focus:#0f4c9a; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1c1c1e; --fg:#f5f5f5; --muted:#d0d2d6; --line:#9ca1aa; --accent:#9cc2ff; --accent-text:#06142b;
    --ok:#9ff0a8; --bad:#ffb3b3; --code-bg:#2c2c2e; --link:#a9ccff; --focus:#9cc2ff; }
}
html, body { background: var(--bg); color: var(--fg); }
body { font-family: system-ui, sans-serif; margin: 0; padding: 16px; max-width: 760px; margin-inline: auto; line-height: 1.5; font-size: 1.05rem; }
h1 { font-size: 1.6rem; margin: 8px 0 4px; }
.brand { display: flex; align-items: center; gap: 14px; margin-bottom: 12px; }
.brand .logo { flex: none; width: 56px; height: 56px; }
.brand .note { margin: 2px 0 0; }
.brand h1 { margin: 6px 0 0; }
h2 { font-size: 1.2rem; margin: 0; padding: 0 6px; }
.note { color: var(--muted); margin: 4px 0 16px; }
fieldset { border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px 16px; margin: 0 0 16px; min-width: 0; }
legend { padding: 0 6px; }
label { display: block; margin: 12px 0 4px; font-weight: 600; }
.hint { color: var(--muted); font-size: .95rem; margin: 2px 0 0; }
input[type=text], select { width: 100%; box-sizing: border-box; min-height: 44px; padding: 8px 10px; font-size: 1rem; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg); }
input[type=file] { font-size: 1rem; color: var(--fg); min-height: 44px; max-width: 100%; }
input::placeholder { color: var(--muted); opacity: 1; }
.check { display: flex; align-items: center; gap: 12px; margin-top: 14px; min-height: 44px; font-weight: 600; cursor: pointer; }
.check input { width: 24px; height: 24px; margin: 0; accent-color: var(--accent); flex: none; }
button { font-size: 1.05rem; min-height: 44px; min-width: 44px; padding: 10px 20px; border-radius: 6px; border: 2px solid var(--accent); background: var(--accent); color: var(--accent-text); cursor: pointer; font-weight: 700; }
button:disabled { background: var(--bg); color: var(--muted); border-color: var(--muted); cursor: not-allowed; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
pre { background: var(--code-bg); color: var(--fg); padding: 12px; border-radius: 6px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .95rem; max-height: 340px; overflow: auto; }
.setup-bad { color: var(--bad); font-weight: 600; }
.setup-ok { color: var(--ok); }
#status { margin-top: 18px; }
#status p { margin: 6px 0; }
a { color: var(--link); }
a.download { display: inline-block; margin-top: 10px; font-weight: 700; font-size: 1.1rem; padding: 10px 0; min-height: 44px; }
.review { border: 2px solid var(--accent); border-radius: 6px; background: var(--code-bg); padding: 12px 16px; margin: 16px 0; }
.review ul { margin: 6px 0 0; padding-left: 20px; }
.skip { position: absolute; left: -9999px; }
.skip:focus { left: 16px; top: 16px; background: var(--bg); color: var(--fg); padding: 10px 14px; min-height: 44px; box-sizing: border-box; z-index: 10; }
</style></head>
<body>
<a class="skip" href="#main">Skip to the converter</a>
<header class="brand">
  <img class="logo" src="/logo.png" alt="" width="56" height="56">
  <div>
    <h1>QuarTeX converter</h1>
    <p class="note">Runs on this computer only. Your files are not uploaded to any other server.</p>
  </div>
</header>
<main id="main">

<fieldset aria-labelledby="setup-title">
  <legend><h2 id="setup-title">Setup</h2></legend>
  <pre id="setup">Checking the setup…</pre>
</fieldset>

<form id="form" aria-describedby="form-note">
  <fieldset>
    <legend><h2>Package</h2></legend>
    <p id="form-note" class="hint">Fields marked as optional can be left empty. Press Convert when you have chosen a file.</p>
    <label for="zip">LaTeX source</label>
    <input type="file" id="zip" accept=".zip,.tex" required aria-describedby="zip-hint zip-error">
    <p id="zip-hint" class="hint">A .zip package, or a single .tex file that contains everything it needs.</p>
    <p id="zip-error" class="setup-bad" role="alert" hidden></p>

    <div id="meta" hidden>
      <p id="meta-note" class="hint" role="status"></p>
      <label for="title">Title (optional)</label>
      <input type="text" id="title" autocomplete="off" placeholder="For example, Introduction to Circuits">

      <label for="author">Author (optional)</label>
      <input type="text" id="author" autocomplete="off" placeholder="For example, Dr Jane Smith">

      <label for="affiliation">Affiliation (optional)</label>
      <input type="text" id="affiliation" autocomplete="off" placeholder="For example, Durham University">
    </div>

    <label for="mode">Output</label>
    <select id="mode" aria-describedby="mode-hint">
      <option value="auto">Automatic (a book if there are several chapters)</option>
      <option value="book">Multi-page book</option>
      <option value="single">Single page</option>
    </select>
    <p id="mode-hint" class="hint">Automatic chooses a book when the source has more than one chapter.</p>

    <label for="theme">Theme</label>
    <select id="theme" aria-describedby="theme-hint">
      <option value="default">Quarto default</option>
      <option value="cerulean">Cerulean</option>
      <option value="cosmo" selected>Cosmo (QuarTeX default)</option>
      <option value="darkly">Darkly</option>
      <option value="flatly">Flatly</option>
      <option value="journal">Journal</option>
      <option value="litera">Litera</option>
      <option value="lumen">Lumen</option>
      <option value="lux">Lux</option>
      <option value="materia">Materia</option>
      <option value="minty">Minty</option>
      <option value="morph">Morph</option>
      <option value="pulse">Pulse</option>
      <option value="quartz">Quartz</option>
      <option value="sandstone">Sandstone</option>
      <option value="simplex">Simplex</option>
      <option value="sketchy">Sketchy</option>
      <option value="slate">Slate</option>
      <option value="solar">Solar</option>
      <option value="spacelab">Spacelab</option>
      <option value="superhero">Superhero</option>
      <option value="united">United</option>
      <option value="vapor">Vapor</option>
      <option value="yeti">Yeti</option>
      <option value="zephyr">Zephyr</option>
    </select>
    <p id="theme-hint" class="hint">Not sure which to choose? <a href="https://quarto.org/docs/output-formats/html-themes.html" target="_blank" rel="noopener">Compare the Quarto themes on the Quarto website (opens in a new tab)</a>.</p>

    <label class="check" for="nopdf"><input type="checkbox" id="nopdf"> Skip the PDF download</label>
  </fieldset>
  <button type="submit" id="go">Convert</button>
</form>

<div id="status">
  <p id="state" role="status"></p>
  <div id="result"></div>
</div>

</main>

<script>
const setupEl = document.getElementById('setup');
const goBtn = document.getElementById('go');
const stateEl = document.getElementById('state');
const resultEl = document.getElementById('result');
const zipInput = document.getElementById('zip');
const zipError = document.getElementById('zip-error');
let setupOk = false;

function esc(s) { return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function setError(message) {
  zipError.textContent = message;
  zipError.hidden = !message;
  if (message) { zipInput.setAttribute('aria-invalid', 'true'); } else { zipInput.removeAttribute('aria-invalid'); }
}

fetch('/api/setup').then(r => r.json()).then(d => {
  setupOk = d.ok;
  setupEl.textContent = d.lines.join('\\n') + (d.ok ? '' : '\\n\\nInstall the missing items, then reload this page.');
  setupEl.className = d.ok ? 'setup-ok' : 'setup-bad';
  goBtn.disabled = !d.ok;
}).catch(() => {
  setupEl.textContent = 'Could not check the setup. Reload the page, or restart the converter.';
  setupEl.className = 'setup-bad';
  goBtn.disabled = true;
});

// Title and author appear once a source file is chosen, and are read from that file.
// A value is replaced on a new file only if the person has not typed their own.
const metaEl = document.getElementById('meta');
const metaNote = document.getElementById('meta-note');
const titleInput = document.getElementById('title');
const authorInput = document.getElementById('author');
const affiliationInput = document.getElementById('affiliation');
const autoFilled = {title: null, author: null, affiliation: null};
let inspectSeq = 0;

function fillIfAuto(input, key, value) {
  if (!input.value.trim() || input.value === autoFilled[key]) {
    input.value = value;
    autoFilled[key] = value;
  }
}

zipInput.addEventListener('change', async () => {
  setError('');
  const file = zipInput.files[0];
  metaEl.hidden = !file;
  const seq = ++inspectSeq;
  if (!file) { metaNote.textContent = ''; return; }
  metaNote.textContent = 'Reading the title and author from ' + file.name + '…';
  try {
    const resp = await fetch('/api/inspect?name=' + encodeURIComponent(file.name), {
      method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file });
    const d = await resp.json();
    if (seq !== inspectSeq) return;
    fillIfAuto(titleInput, 'title', d.title || '');
    fillIfAuto(authorInput, 'author', d.author || '');
    fillIfAuto(affiliationInput, 'affiliation', d.affiliation || 'Durham University');
    metaNote.textContent = (d.title || d.author)
      ? 'Title and author were read from the source file. Change them if you need to.'
      : 'No title or author was found in the source file. Type them here if you want them.';
  } catch (err) {
    if (seq === inspectSeq) metaNote.textContent = 'Could not read the title and author. Type them here if you want them.';
  }
});

const themeSel = document.getElementById('theme');

document.getElementById('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = zipInput.files[0];
  if (!file) { setError('Choose a .zip or .tex file first.'); zipInput.focus(); return; }
  setError('');
  goBtn.disabled = true;
  resultEl.innerHTML = '';
  stateEl.textContent = 'Uploading ' + file.name + '…';
  const q = new URLSearchParams({
    title: document.getElementById('title').value,
    author: document.getElementById('author').value,
    affiliation: affiliationInput.value,
    mode: document.getElementById('mode').value,
    theme: themeSel.value,
    no_pdf: document.getElementById('nopdf').checked ? '1' : '0',
    name: file.name,
  });
  let data;
  try {
    const resp = await fetch('/api/convert?' + q.toString(), {
      method: 'POST', headers: {'Content-Type': 'application/zip'}, body: file });
    data = await resp.json();
    if (!resp.ok) {
      setError(data.error || 'The upload failed.');
      stateEl.textContent = '';
      goBtn.disabled = !setupOk;
      zipInput.focus();
      return;
    }
  } catch (err) {
    setError('The upload failed. Check that the converter is still running.');
    stateEl.textContent = '';
    goBtn.disabled = !setupOk;
    return;
  }
  poll(data.job);
});

function poll(job) {
  stateEl.textContent = 'Converting. This can take a few minutes for a long package.';
  const logBox = document.createElement('pre');
  logBox.id = 'log';
  logBox.setAttribute('aria-label', 'Conversion log');
  logBox.tabIndex = 0;
  resultEl.innerHTML = '';
  resultEl.appendChild(logBox);
  const timer = setInterval(async () => {
    let d;
    try { d = await (await fetch('/api/status/' + job)).json(); } catch (err) { return; }
    logBox.textContent = d.log;
    if (d.state === 'running') return;
    clearInterval(timer);
    goBtn.disabled = !setupOk;
    if (d.state === 'done') {
      stateEl.textContent = 'Conversion finished. Read the steps below before you use the notes.';
      resultEl.innerHTML =
        '<a class="download" href="/api/result/' + job + '">Download the converted notes</a>' +
        '<section class="review" aria-labelledby="review-title"><h2 id="review-title">Notes converted: what to do now</h2>' +
        '<ul>' +
        '<li>Download the converted notes and unzip the folder.</li>' +
        '<li><strong>The conversion is automated and may contain errors.</strong> Check every page before you share the notes.</li>' +
        '<li>Alt text will need to be added to figures to improve accessibility. The converter only guesses at it from the file name.</li>' +
        '<li>Check that the maths, tables, cross-references, callout boxes and section order display correctly.</li>' +
        '<li>To preview the notes, open <code>_book/index.html</code> in your browser if the output is a book, or <code>document.html</code> if it is a single page.</li>' +
        '<li>To edit the notes, use a tool such as Positron or RStudio. You will need to install Quarto first, from <a href="https://quarto.org/" target="_blank" rel="noopener">quarto.org (opens in a new tab)</a>.</li>' +
        '<li>If you change the Quarto source files, render the notes again so the HTML is updated.</li>' +
        '<li>For more information on using Quarto, visit <a href="https://quarto.org/" target="_blank" rel="noopener">quarto.org (opens in a new tab)</a>.</li>' +
        '</ul></section>' +
        '<details><summary>Conversion log</summary><pre>' + esc(d.log) + '</pre></details>';
    } else {
      stateEl.textContent = 'The conversion failed. The log below shows why.';
      resultEl.innerHTML = '<pre>' + esc(d.log) + '</pre>';
    }
  }, 2000);
}
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "QuarTeX-local"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        # Refuse requests that arrive under another host name (a guard against web pages
        # that try to reach this local server through the browser).
        host = self.headers.get("Host", "")
        return host.startswith("127.0.0.1:") or host.startswith("localhost:") or host in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._host_ok():
            return self._send_json(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path in STATIC_FILES:
            filename, ctype = STATIC_FILES[url.path]
            data = (HERE / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        elif url.path == "/api/setup":
            ok, lines = check_setup.check()
            self._send_json(200, {"ok": ok, "lines": lines})
        elif url.path.startswith("/api/status/"):
            job = url.path.rsplit("/", 1)[-1]
            job_dir = JOBS / job
            if not job_dir.is_dir():
                return self._send_json(404, {"error": "no such job"})
            with LOCK:
                state = JOB_STATE.get(job, "running")
            self._send_json(200, {"state": state, "log": tail(job_dir / "log.txt")})
        elif url.path.startswith("/api/result/"):
            job = url.path.rsplit("/", 1)[-1]
            result = JOBS / job / "result.zip"
            if not result.exists():
                return self._send_json(404, {"error": "no result"})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(result.stat().st_size))
            name = download_name(JOBS / job / "output")
            # filename* carries the real title (UTF-8); the plain filename is an ASCII fallback.
            fallback = re.sub(r"[^A-Za-z0-9 ._-]", "", name).strip() or "converted-notes.zip"
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}")
            self.end_headers()
            with open(result, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile)
        else:
            self._send_json(404, {"error": "not found"})

    def _inspect(self):
        """Read the title and author from an uploaded file. Nothing is converted or kept."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            return self._send_json(400, {"error": "No usable file received."})
        JOBS.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="inspect-", dir=JOBS))
        try:
            upload = work / "upload"
            remaining = length
            with open(upload, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            meta = quartex.read_metadata(upload, work / "unpacked")
            return self._send_json(200, meta)
        except (Exception, SystemExit):
            # A file that cannot be read is not an error for the form: the fields simply stay empty.
            return self._send_json(200, {"title": "", "author": ""})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def do_POST(self):
        if not self._host_ok():
            return self._send_json(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path == "/api/inspect":
            return self._inspect()
        if url.path != "/api/convert":
            return self._send_json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return self._send_json(400, {"error": "No file received."})
        if length > MAX_UPLOAD:
            return self._send_json(413, {"error": "That file is larger than 300 MB."})
        ok, _ = check_setup.check()
        if not ok:
            return self._send_json(400, {"error": "Setup is incomplete. Run check_setup.py and install the missing tools."})
        params = parse_qs(url.query)
        options = {
            "title": clean_text(params.get("title", [""])[0]),
            "author": clean_text(params.get("author", [""])[0]),
            "mode": params.get("mode", ["auto"])[0] if params.get("mode", ["auto"])[0] in ("auto", "book", "single") else "auto",
            "theme": params.get("theme", [quartex.DEFAULT_THEME])[0] if params.get("theme", [""])[0] in quartex.THEMES else quartex.DEFAULT_THEME,
            # Only a missing parameter gets the default; an empty one means no affiliation.
            "affiliation": clean_text(params["affiliation"][0]) if "affiliation" in params else quartex.DEFAULT_AFFILIATION,
            "no_pdf": params.get("no_pdf", ["0"])[0] == "1",
        }
        job_id = uuid.uuid4().hex
        job_dir = JOBS / job_id
        job_dir.mkdir(parents=True)
        name = safe_name(params.get("name", ["package.zip"])[0])
        is_tex = name.lower().endswith(".tex")
        upload = job_dir / ("upload.tex" if is_tex else "package.zip")
        remaining = length
        with open(upload, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if is_tex:
            # One .tex file: wrap it in a zip so the converter reads it the same way as a package.
            try:
                text = upload.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                shutil.rmtree(job_dir, ignore_errors=True)
                return self._send_json(400, {"error": "That .tex file is not plain UTF-8 text. Save it as UTF-8 and try again."})
            if "\\begin{document}" not in text and "\\documentclass" not in text:
                shutil.rmtree(job_dir, ignore_errors=True)
                return self._send_json(400, {"error": "That .tex file has no \\documentclass or \\begin{document}, so it does not look like a complete LaTeX file."})
            with zipfile.ZipFile(job_dir / "package.zip", "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(name, text)
            upload.unlink()
        elif not zipfile.is_zipfile(upload):
            shutil.rmtree(job_dir, ignore_errors=True)
            return self._send_json(400, {"error": "Please choose a .zip or .tex file."})
        else:
            upload.rename(job_dir / "package.zip")
        with LOCK:
            JOB_STATE[job_id] = "running"
        threading.Thread(target=run_job, args=(job_id, job_dir, options), daemon=True).start()
        self._send_json(202, {"job": job_id})


def main():
    JOBS.mkdir(parents=True, exist_ok=True)
    port = 8765
    server = None
    while server is None:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            port += 1
    url = f"http://127.0.0.1:{port}/"
    print(f"QuarTeX converter running at {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
