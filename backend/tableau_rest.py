"""Tableau REST: PAT sign-in, workbook resolve, datasource listing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from backend.config import env, has_tableau_creds, httpx_verify, require_env

DEFAULT_REST_VERSION = "3.27"
LUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


@dataclass
class WorkbookSummary:
    id: str
    name: str
    content_url: Optional[str] = None
    project_name: Optional[str] = None

    def to_api_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.content_url:
            d["contentUrl"] = self.content_url
        if self.project_name:
            d["projectName"] = self.project_name
        return d


def _server_base() -> str:
    return require_env("TABLEAU_SERVER").rstrip("/")


def _rest_version() -> str:
    return env("TABLEAU_REST_API_VERSION") or DEFAULT_REST_VERSION


def _site_content_url() -> str:
    return env("TABLEAU_SITE_NAME")


def _client() -> httpx.Client:
    return httpx.Client(verify=httpx_verify(), timeout=120.0)


def sign_in_pat() -> tuple[str, str]:
    url = f"{_server_base()}/api/{_rest_version()}/auth/signin"
    with _client() as client:
        res = client.post(
            url,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "credentials": {
                    "site": {"contentUrl": _site_content_url()},
                    "personalAccessTokenName": require_env("TABLEAU_PAT_NAME"),
                    "personalAccessTokenSecret": require_env("TABLEAU_PAT_VALUE"),
                }
            },
        )
    if not res.is_success:
        raise RuntimeError(f"Tableau sign-in failed ({res.status_code}): {res.text[:500]}")
    j = res.json()
    token = j.get("credentials", {}).get("token")
    site_id = j.get("credentials", {}).get("site", {}).get("id") or ""
    if not token:
        raise RuntimeError("Tableau sign-in returned no token")
    return token, site_id


def probe_tableau_sign_in() -> dict[str, Any]:
    if not has_tableau_creds():
        return {
            "tableauSignInOk": False,
            "tableauHint": "Set TABLEAU_SERVER, TABLEAU_PAT_NAME, TABLEAU_PAT_VALUE in .env",
        }
    try:
        sign_in_pat()
        return {"tableauSignInOk": True}
    except Exception as e:
        return {
            "tableauSignInOk": False,
            "tableauSignInError": str(e)[:300],
            "tableauHint": "Regenerate PAT and confirm TABLEAU_SITE_NAME matches the site content URL.",
        }


def _parse_workbook(raw: dict[str, Any]) -> WorkbookSummary | None:
    wid = raw.get("id")
    name = raw.get("name")
    if not isinstance(wid, str) or not wid or not isinstance(name, str) or not name:
        return None
    project = raw.get("project") if isinstance(raw.get("project"), dict) else None
    return WorkbookSummary(
        id=wid,
        name=name,
        content_url=raw.get("contentUrl") if isinstance(raw.get("contentUrl"), str) else None,
        project_name=project.get("name") if project and isinstance(project.get("name"), str) else None,
    )


def list_workbooks(token: str, site_id: str, *, page_size: int = 100) -> list[WorkbookSummary]:
    out: list[WorkbookSummary] = []
    page = 1
    while True:
        url = (
            f"{_server_base()}/api/{_rest_version()}/sites/{site_id}/workbooks"
            f"?pageSize={page_size}&pageNumber={page}"
        )
        with _client() as client:
            res = client.get(url, headers={"Accept": "application/json", "X-Tableau-Auth": token})
        if not res.is_success:
            raise RuntimeError(f"List workbooks failed ({res.status_code}): {res.text[:400]}")
        body = res.json()
        rows = body.get("workbooks", {}).get("workbook") or []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if isinstance(row, dict):
                wb = _parse_workbook(row)
                if wb:
                    out.append(wb)
        pagination = body.get("pagination") or {}
        total = int(pagination.get("totalAvailable") or len(out))
        if len(out) >= total or not rows:
            break
        page += 1
        if page > 50:
            break
    out.sort(key=lambda w: w.name.casefold())
    return out


def resolve_workbook(
    *,
    workbook_id: str | None = None,
    name: str | None = None,
    content_url: str | None = None,
    project_name: str | None = None,
) -> WorkbookSummary:
    token, site_id = sign_in_pat()
    workbooks = list_workbooks(token, site_id)

    if workbook_id:
        wid = workbook_id.strip().casefold()
        for wb in workbooks:
            if wb.id.casefold() == wid:
                return wb

    if content_url:
        cu = content_url.strip().casefold()
        for wb in workbooks:
            if wb.content_url and wb.content_url.casefold() == cu:
                return wb
        for wb in workbooks:
            if wb.name.casefold() == cu:
                return wb

    if name:
        nm = name.strip().casefold()
        matches = [wb for wb in workbooks if wb.name.casefold() == nm]
        if project_name:
            pn = project_name.strip().casefold()
            proj = [wb for wb in matches if (wb.project_name or "").casefold() == pn]
            if proj:
                return proj[0]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return matches[0]
        # slug-like name guesses
        slug = re.sub(r"[^a-zA-Z0-9]", "", name)
        for wb in workbooks:
            if wb.content_url and re.sub(r"[^a-zA-Z0-9]", "", wb.content_url).casefold() == slug.casefold():
                return wb

    detail = "Could not resolve workbook"
    if content_url:
        detail += f" contentUrl={content_url!r}"
    if name:
        detail += f" name={name!r}"
    raise RuntimeError(detail)


def list_workbook_connections(workbook_id: str) -> list[dict[str, Any]]:
    token, site_id = sign_in_pat()
    url = (
        f"{_server_base()}/api/{_rest_version()}/sites/{site_id}"
        f"/workbooks/{workbook_id}/connections"
    )
    with _client() as client:
        res = client.get(url, headers={"Accept": "application/json", "X-Tableau-Auth": token})
    if not res.is_success:
        raise RuntimeError(f"List connections failed ({res.status_code}): {res.text[:400]}")
    body = res.json()
    rows = body.get("connections", {}).get("connection") or []
    if isinstance(rows, dict):
        rows = [rows]
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "id": row.get("id") or "",
                "type": row.get("type") or "",
                "serverAddress": row.get("serverAddress") or "",
                "userName": row.get("userName") or "",
                "datasourceId": (row.get("datasource") or {}).get("id")
                if isinstance(row.get("datasource"), dict)
                else "",
                "datasourceName": (row.get("datasource") or {}).get("name")
                if isinstance(row.get("datasource"), dict)
                else row.get("dbname") or "",
            }
        )
    return out


def list_published_datasources(*, page_size: int = 100) -> list[dict[str, Any]]:
    token, site_id = sign_in_pat()
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"{_server_base()}/api/{_rest_version()}/sites/{site_id}/datasources"
            f"?pageSize={page_size}&pageNumber={page}"
        )
        with _client() as client:
            res = client.get(url, headers={"Accept": "application/json", "X-Tableau-Auth": token})
        if not res.is_success:
            raise RuntimeError(f"List datasources failed ({res.status_code}): {res.text[:400]}")
        body = res.json()
        rows = body.get("datasources", {}).get("datasource") or []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            project = row.get("project") if isinstance(row.get("project"), dict) else {}
            out.append(
                {
                    "id": row.get("id") or "",
                    "name": row.get("name") or "",
                    "contentUrl": row.get("contentUrl") or "",
                    "type": row.get("type") or "",
                    "projectName": project.get("name") or "",
                    "isPublished": True,
                }
            )
        pagination = body.get("pagination") or {}
        total = int(pagination.get("totalAvailable") or len(out))
        if len(out) >= total or not rows:
            break
        page += 1
        if page > 50:
            break
    out.sort(key=lambda d: (d.get("name") or "").casefold())
    return out


FIELD_FRAGMENT = """
  __typename
  name
  ... on ColumnField { dataType }
  ... on CalculatedField { formula }
"""


def fetch_datasource_fields(identifier: str) -> dict[str, Any]:
    id_val = identifier.strip()
    if not id_val:
        raise ValueError("identifier is empty")
    token, _ = sign_in_pat()
    by_luid = bool(LUID_RE.match(id_val))
    if by_luid:
        filt = "luid: $id"
        var_type = "String!"
        variables: dict[str, Any] = {"id": id_val}
    else:
        filt = "name: $id"
        var_type = "String!"
        variables = {"id": id_val}

    query = f"""
    query Fields($id: {var_type}) {{
      publishedDatasources(filter: {{ {filt} }}) {{
        name
        luid
        fieldsConnection(first: 500, permissionMode: OBFUSCATE_RESULTS) {{
          nodes {{ {FIELD_FRAGMENT} }}
        }}
      }}
    }}"""
    gql_url = f"{_server_base()}/api/metadata/graphql"
    with _client() as client:
        res = client.post(
            gql_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Tableau-Auth": token,
            },
            json={"query": query, "variables": variables},
        )
    body = res.json() if res.content else {}
    if not res.is_success:
        raise RuntimeError(f"Metadata GraphQL HTTP {res.status_code}: {str(body)[:500]}")
    rows = body.get("data", {}).get("publishedDatasources") or []
    matches: list[dict[str, Any]] = []
    for ds in rows:
        nodes = ((ds.get("fieldsConnection") or {}).get("nodes")) or []
        fields = []
        for rf in nodes:
            if not isinstance(rf, dict) or not rf.get("name"):
                continue
            fields.append(
                {
                    "name": rf["name"],
                    "typename": rf.get("__typename") or "Field",
                    "dataType": rf.get("dataType"),
                    "formula": rf.get("formula"),
                }
            )
        matches.append({"name": ds.get("name") or "", "luid": ds.get("luid") or "", "fields": fields})
    return {
        "identifier": id_val,
        "matchedBy": "luid" if by_luid else "name",
        "matches": matches,
        "graphqlErrors": body.get("errors"),
    }
