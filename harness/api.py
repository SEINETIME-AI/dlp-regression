"""Thin admin client for the NextDLP backend. Used for waiting, checking and
cleanup; everything an admin would do is done through the UI instead."""
import json
import urllib.error
import urllib.parse
import urllib.request


class ApiError(RuntimeError):
    def __init__(self, method, path, status, body):
        super().__init__(f"{method} {path} -> {status}: {body[:300]}")
        self.status = status


class Api:
    def __init__(self, base_url: str, user: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.token = self.call("POST", "/api/auth/login", {"username": user, "password": password})["token"]

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json; charset=utf-8")
        if getattr(self, "token", None):
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise ApiError(method, path, error.code, error.read().decode("utf-8", "replace")) from None
        return json.loads(text) if text else None

    def get(self, path: str, **query):
        if query:
            path += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        return self.call("GET", path)

    def items(self, path: str, **query) -> list:
        result = self.get(path, **query)
        return result.get("items", []) if isinstance(result, dict) else (result or [])

    # ── Lookups ──────────────────────────────────────────────────────────────
    def machine(self, hostname: str) -> dict:
        for machine in self.items("/api/machines"):
            if machine["name"].upper() == hostname.upper():
                return machine
        raise LookupError(f"machine {hostname} is not enrolled")

    def document_type(self, code: str):
        return next((t for t in self.items("/api/document-types") if t["code"] == code), None)

    def secrecy_label(self, code: str):
        return next((s for s in self.items("/api/taxonomy/secrecy-labels") if s["code"] == code), None)

    def documents(self, department: str) -> list:
        return self.items("/api/documents", department=department, limit=200)

    def department_policies(self, department: str) -> list[dict]:
        return [p for p in self.items("/api/policies") if p.get("department") == department]

    # ── Cleanup (idempotent) ─────────────────────────────────────────────────
    def purge_documents(self, path_prefix: str) -> int:
        """Deletes the inventory rows (and their events) under a folder."""
        query = urllib.parse.urlencode({"path_prefix": path_prefix})
        return self.call("DELETE", f"/api/documents?{query}")["deleted"]

    def delete_document_type(self, code: str):
        self.call("DELETE", f"/api/document-types/{urllib.parse.quote(code)}")

    def delete_secrecy_label(self, code: str):
        self.call("DELETE", f"/api/taxonomy/secrecy-labels/{urllib.parse.quote(code)}")

    def delete_uat_objects(self, prefix: str) -> list[str]:
        """Removes only data sources and policies the UAT created (name starts with prefix)."""
        removed = []
        for source in self.items("/api/data-sources"):
            if source["name"].startswith(prefix):
                self.call("DELETE", f"/api/data-sources/{source['id']}")
                removed.append(f"data source {source['name']}")
        for policy in self.items("/api/policies"):
            if policy["name"].startswith(prefix):
                if policy.get("is_active"):
                    self.call("PATCH", f"/api/policies/{policy['id']}/deactivate")
                self.call("DELETE", f"/api/policies/{policy['id']}")
                removed.append(f"policy {policy['name']}")
        return removed
