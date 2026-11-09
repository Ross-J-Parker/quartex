// Typeset every display and inline maths block from a Quarto .qmd with MathJax (TeX input,
// all packages) and report the ones that fail to compile.
import fs from 'fs';
import {mathjax} from 'mathjax-full/js/mathjax.js';
import {TeX} from 'mathjax-full/js/input/tex.js';
import {AllPackages} from 'mathjax-full/js/input/tex/AllPackages.js';
import {liteAdaptor} from 'mathjax-full/js/adaptors/liteAdaptor.js';
import {RegisterHTMLHandler} from 'mathjax-full/js/handlers/html.js';
import {SVG} from 'mathjax-full/js/output/svg.js';

const file = process.argv[2];
let src = fs.readFileSync(file, 'utf8');
// drop YAML header and fenced code, which are not maths
src = src.replace(/^---[\s\S]*?---/, '').replace(/```[\s\S]*?```/g, '');
// Markdown writes a literal bracket as \\[ or \\] (units such as [LT]); these are not maths delimiters.
src = src.replace(/\\\[|\\\]/g, '');

const blocks = [];
const display = /\$\$([\s\S]*?)\$\$/g;
let m;
while ((m = display.exec(src))) blocks.push({kind: 'display', tex: m[1].replace(/\{#eq-[^}]*\}/g, '')});
const noDisplay = src.replace(/\$\$[\s\S]*?\$\$/g, '');
const inline = /(?<!\\)\$([^$\n]+?)(?<!\\)\$/g;
while ((m = inline.exec(noDisplay))) blocks.push({kind: 'inline', tex: m[1]});

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const tex = new TeX({packages: AllPackages.filter(p => p !== 'bussproofs' && p !== 'noundefined')});
const doc = mathjax.document('', {InputJax: tex, OutputJax: new SVG({fontCache: 'none'})});

const failures = [];
for (const b of blocks) {
  let math;
  try {
    math = doc.convert(b.tex, {display: b.kind === 'display'});
  } catch (e) {
    failures.push({...b, err: String(e.message || e).slice(0, 120)});
    continue;
  }
  const html = adaptor.outerHTML(math);
  const errs = html.match(/merror/g);
  if (errs) {
    const msg = (html.match(/data-mml-node="merror"[^>]*>[\s\S]{0,200}/) || [''])[0].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160) || 'merror';
    failures.push({...b, err: msg});
  }
}
console.log(`maths blocks checked: ${blocks.length} (display ${blocks.filter(b=>b.kind==='display').length})`);
console.log(`failures: ${failures.length}`);
const byErr = {};
for (const f of failures) byErr[f.err] = (byErr[f.err] || 0) + 1;
for (const [e, n] of Object.entries(byErr).sort((a, b) => b[1] - a[1]).slice(0, 25)) console.log(n, '|', e);
fs.writeFileSync(process.argv[3] || 'math_failures.json', JSON.stringify(failures, null, 1));
