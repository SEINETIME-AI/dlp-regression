"""A real SMB share on this machine for a suite whose data source is a network
folder: a local account the agent signs in with, and a hidden share over the
run's work folder. Needs an elevated prompt; the UAT runs as administrator."""
import ctypes
import os
import secrets
import subprocess
from pathlib import Path

USER = "uat-smb"
SHARE_PREFIX = "uat-smb-"


def _powershell(script: str, **values: str) -> str:
    # Values travel as environment variables, so a password never appears on a command line.
    env = {**os.environ, **{f"UAT_{key.upper()}": value for key, value in values.items()}}
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                             "$ErrorActionPreference = 'Stop'; " + script],
                            capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"powershell failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


class SmbShare:
    def __init__(self, machine: str, suite: str, folder: Path):
        self.machine = machine
        self.name = f"{SHARE_PREFIX}{suite}$"
        self.folder = folder
        self.username = f"{machine}\\{USER}"
        # Complexity rules want every character class; the random part makes it unique per run.
        self.password = secrets.token_urlsafe(18) + "Aa1!"

    @property
    def unc(self) -> str:
        return f"\\\\{self.machine}\\{self.name}"

    def create(self):
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError("the smb suite creates a local account and a share: run as administrator")
        remove_leftovers()
        self.folder.mkdir(parents=True, exist_ok=True)
        _powershell(
            "$password = ConvertTo-SecureString $env:UAT_PASSWORD -AsPlainText -Force;"
            "New-LocalUser -Name $env:UAT_USER -Password $password -PasswordNeverExpires"
            " -Description 'NextDLP UAT SMB source, removed after the run' | Out-Null;"
            "New-SmbShare -Name $env:UAT_SHARE -Path $env:UAT_FOLDER -FullAccess $env:UAT_USER | Out-Null;"
            "icacls $env:UAT_FOLDER /grant \"${env:UAT_USER}:(OI)(CI)M\" | Out-Null",
            user=USER, password=self.password, share=self.name, folder=str(self.folder))

    def to_unc(self, local: Path) -> str:
        """The path the agent and the backend see for a file in the shared folder."""
        return self.unc + "\\" + str(local.relative_to(self.folder)) if local != self.folder else self.unc


def remove_leftovers() -> list[str]:
    """Removes every UAT share and the UAT account, also those of an interrupted run."""
    removed = _powershell(
        "Get-SmbShare | Where-Object Name -like \"$env:UAT_PREFIX*\" | ForEach-Object {"
        " Remove-SmbShare -Name $_.Name -Force; \"share $($_.Name)\" };"
        "if (Get-LocalUser -Name $env:UAT_USER -ErrorAction SilentlyContinue) {"
        " Remove-LocalUser -Name $env:UAT_USER; \"account $env:UAT_USER\" }",
        prefix=SHARE_PREFIX, user=USER)
    return [line for line in removed.splitlines() if line]
