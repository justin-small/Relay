#!/usr/bin/env python3
"""Build reference.docx: the Word styles the DOCX build is poured into.

Starts from Pandoc's own default reference document and changes only what the
guide needs:

- Note / Tip / Warning paragraph styles for the callout boxes (callouts.lua
  tags each box with one of them);
- a sans-serif body and blue headings, to match the PDF;
- a footer with the page number, since Pandoc's default has none.

    python3 make_reference.py <pandoc> <out.docx>
"""
from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from io import BytesIO

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CALLOUTS = {
    # style name: (fill, border colour)
    "Note": ("E8F0FE", "2F6DF6"),
    "Tip": ("E8F5EC", "1E8E3E"),
    "Warning": ("FDECEA", "D93025"),
}


def callout_style(name: str, fill: str, border: str) -> str:
    return f"""
<w:style w:type="paragraph" w:customStyle="1" w:styleId="{name}">
  <w:name w:val="{name}"/>
  <w:basedOn w:val="BodyText"/>
  <w:qFormat/>
  <w:pPr>
    <w:pBdr><w:left w:val="single" w:sz="24" w:space="8" w:color="{border}"/></w:pBdr>
    <w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>
    <w:spacing w:before="0" w:after="0"/>
    <w:ind w:left="240" w:right="120"/>
  </w:pPr>
</w:style>"""


FOOTER = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="{W}" xmlns:r="{R}">
  <w:p>
    <w:pPr><w:jc w:val="center"/></w:pPr>
    <w:r><w:fldChar w:fldCharType="begin"/></w:r>
    <w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r>
    <w:r><w:t>1</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
</w:ftr>"""


def main() -> None:
    pandoc, out = sys.argv[1], sys.argv[2]
    subprocess.run(
        [pandoc, "-o", out, "--print-default-data-file", "reference.docx"], check=True,
    )
    with open(out, "rb") as fh:
        src = fh.read()
    zin = zipfile.ZipFile(BytesIO(src))
    files = {n: zin.read(n) for n in zin.namelist()}

    styles = files["word/styles.xml"].decode()
    # Body and heading fonts.
    styles = re.sub(r"<w:rFonts [^>]*/>", '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>', styles)
    # Styles that name no font inherit it from the document defaults.
    if "<w:rPrDefault><w:rPr>" in styles and "rPrDefault><w:rPr><w:rFonts" not in styles:
        styles = styles.replace(
            "<w:rPrDefault><w:rPr>",
            '<w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>', 1)
    styles = styles.replace('w:color w:val="4F81BD"', 'w:color w:val="1D2330"')
    styles = styles.replace('w:color w:val="345A8A"', 'w:color w:val="1D2330"')
    extra = "".join(callout_style(n, f, b) for n, (f, b) in CALLOUTS.items())
    styles = styles.replace("</w:styles>", extra + "\n</w:styles>")
    files["word/styles.xml"] = styles.encode()

    # Footer part, its relationship, its content type, and a reference to it
    # from the document's section properties.
    files["word/footer1.xml"] = FOOTER.encode()
    rels = files["word/_rels/document.xml.rels"].decode()
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="rIdGuideFooter" Type="{R}/footer" Target="footer1.xml"/></Relationships>',
    )
    files["word/_rels/document.xml.rels"] = rels.encode()
    ct = files["[Content_Types].xml"].decode()
    if "footer1.xml" not in ct:
        ct = ct.replace(
            "</Types>",
            '<Override PartName="/word/footer1.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>',
        )
    files["[Content_Types].xml"] = ct.encode()
    doc = files["word/document.xml"].decode()
    doc = re.sub(
        r"<w:sectPr([^>]*)>",
        lambda m: f'<w:sectPr{m.group(1)}><w:footerReference w:type="default" r:id="rIdGuideFooter"/>',
        doc, count=1,
    )
    if 'xmlns:r=' not in doc.split(">", 2)[1]:
        doc = doc.replace("<w:document ", f'<w:document xmlns:r="{R}" ', 1)
    files["word/document.xml"] = doc.encode()

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)


if __name__ == "__main__":
    main()
