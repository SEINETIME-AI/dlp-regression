import re
import zipfile
from pathlib import Path

_PROPERTY = re.compile(r'<property[^>]*name="(NextDLP_[A-Za-z]+)"[^>]*>\s*<vt:lpwstr>(.*?)</vt:lpwstr>', re.S)


def embedded_label(path: Path) -> dict | None:
    """The NextDLP label the agent wrote into an OOXML file, or None."""
    with zipfile.ZipFile(path) as archive:
        if "docProps/custom.xml" not in archive.namelist():
            return None
        xml = archive.read("docProps/custom.xml").decode("utf-8")
    values = dict(_PROPERTY.findall(xml))
    if "NextDLP_Secrecy" not in values:
        return None
    domains = [d.strip() for d in values.get("NextDLP_Domains", "").split(",") if d.strip()]
    return {"secrecy": values["NextDLP_Secrecy"], "domains": sorted(domains)}
