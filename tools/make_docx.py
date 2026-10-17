"""Writes a minimal, unlabeled .docx with the given paragraphs.

Usage: python tools/make_docx.py <out.docx> "paragraph" ["paragraph" ...]
"""
import sys
import zipfile
from xml.sax.saxutils import escape

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>')
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>')


def write_docx(path: str, paragraphs: list[str], media: dict[str, bytes] | None = None):
    """`media` adds files under word/media/ (e.g. {"image1.png": png_bytes}); the agent
    OCRs images found there even when the body does not reference them."""
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>")
    content_types = CONTENT_TYPES
    if media:
        content_types = content_types.replace(
            '<Default Extension="xml"', '<Default Extension="png" ContentType="image/png"/><Default Extension="xml"')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document)
        for name, data in (media or {}).items():
            archive.writestr(f"word/media/{name}", data)


if __name__ == "__main__":
    write_docx(sys.argv[1], sys.argv[2:])
