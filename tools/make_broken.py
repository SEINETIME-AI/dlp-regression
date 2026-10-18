"""Writes .docx files the agent cannot read: password-protected and corrupt."""
import io
import os
import tempfile

from msoffcrypto.format.ooxml import OOXMLFile

from make_docx import write_docx

PASSWORD = "uat-password"


def write_password_docx(path: str, paragraphs: list[str]):
    """Encrypted the way Word's "Encrypt with Password" saves it: an OLE container, not a zip."""
    with tempfile.TemporaryDirectory() as folder:
        plain = os.path.join(folder, "plain.docx")
        write_docx(plain, paragraphs)
        with open(plain, "rb") as source, open(path, "wb") as target:
            OOXMLFile(source).encrypt(PASSWORD, target)


def write_corrupt_docx(path: str, paragraphs: list[str]):
    """A real .docx with its zip central directory cut off, so no zip reader can open it."""
    buffer = io.BytesIO()
    write_docx(buffer, paragraphs)
    data = buffer.getvalue()
    with open(path, "wb") as target:
        target.write(data[: len(data) // 2])
