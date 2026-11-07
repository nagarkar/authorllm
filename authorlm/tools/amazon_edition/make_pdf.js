// Render an HTML build to a 6x9 PDF with Chromium; print page numbers in the footer.
// node make_pdf.js <in.html> <out.pdf> [running-head]
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const [inp, out, head] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('file://' + path.resolve(inp), { waitUntil: 'load' });
  await p.emulateMedia({ media: 'print' });
  await p.pdf({ path: out, width: '6in', height: '9in', printBackground: false, preferCSSPageSize: true,
    displayHeaderFooter: true,
    headerTemplate: `<div style="font-family: 'Bitstream Charter', serif; font-size:8.5pt; width:100%; text-align:center; font-style:italic; color:#000;">${head || ''}</div>`,
    footerTemplate: `<div style="font-family: 'Bitstream Charter', serif; font-size:9pt; width:100%; text-align:center; color:#000;"><span class="pageNumber"></span></div>`,
    margin: { top: '0.75in', bottom: '0.8in', left: '0.75in', right: '0.5in' } });
  await b.close();
  console.log('pdf written', out);
})().catch(e => { console.error(e); process.exit(1); });
