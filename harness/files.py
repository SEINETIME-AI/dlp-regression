import re
import zipfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

_PROPERTY = re.compile(r'<property[^>]*name="(nextdlp_[A-Za-z]+)"[^>]*>\s*<vt:lpwstr>(.*?)</vt:lpwstr>', re.S | re.I)

# Formats the agent writes its label into; for anything else the result is read
# from the backend instead.
LABELED_SUFFIXES = {".docx", ".xlsx", ".pptx", ".pdf"}


# Office matches custom property names without case; so does the agent.
_CANONICAL = {name.lower(): name for name in ("NextDLP_Secrecy", "NextDLP_Domains", "NextDLP_ClassifiedAt")}


def _label(values: dict) -> dict | None:
    values = {_CANONICAL.get(key.lower(), key): value for key, value in values.items()}
    if not values.get("NextDLP_Secrecy"):
        return None
    domains = [d.strip() for d in (values.get("NextDLP_Domains") or "").split(",") if d.strip()]
    return {"secrecy": values["NextDLP_Secrecy"], "domains": sorted(domains),
            "classified_at": values.get("NextDLP_ClassifiedAt", "")}


def embedded_label(path: Path) -> dict | None:
    """The NextDLP label the agent wrote into an OOXML or PDF file, or None."""
    if path.suffix.lower() == ".pdf":
        try:
            info = PdfReader(path).metadata or {}
        except PdfReadError:  # another format renamed .pdf: it cannot carry a label
            return None
        return _label({key.lstrip("/"): str(value) for key, value in info.items()})
    try:
        with zipfile.ZipFile(path) as archive:
            if "docProps/custom.xml" not in archive.namelist():
                return None
            xml = archive.read("docProps/custom.xml").decode("utf-8")
    except zipfile.BadZipFile:  # password-protected (OLE) or broken: it cannot carry a label
        return None
    return _label(dict(_PROPERTY.findall(xml)))
