import re
import zipfile
from pathlib import Path

from pypdf import PdfReader

_PROPERTY = re.compile(r'<property[^>]*name="(NextDLP_[A-Za-z]+)"[^>]*>\s*<vt:lpwstr>(.*?)</vt:lpwstr>', re.S)

# Formats the agent writes its label into; for anything else the result is read
# from the backend instead.
LABELED_SUFFIXES = {".docx", ".xlsx", ".pptx", ".pdf"}


def _label(values: dict) -> dict | None:
    if not values.get("NextDLP_Secrecy"):
        return None
    domains = [d.strip() for d in (values.get("NextDLP_Domains") or "").split(",") if d.strip()]
    return {"secrecy": values["NextDLP_Secrecy"], "domains": sorted(domains)}


def embedded_label(path: Path) -> dict | None:
    """The NextDLP label the agent wrote into an OOXML or PDF file, or None."""
    if path.suffix.lower() == ".pdf":
        info = PdfReader(path).metadata or {}
        return _label({key.lstrip("/"): str(value) for key, value in info.items()})
    with zipfile.ZipFile(path) as archive:
        if "docProps/custom.xml" not in archive.namelist():
            return None
        xml = archive.read("docProps/custom.xml").decode("utf-8")
    return _label(dict(_PROPERTY.findall(xml)))
