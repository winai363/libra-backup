"""Small, fail-closed Search Console adapter for Libra's public guide prefix."""
from __future__ import annotations

from datetime import date


VERIFIED_PERMISSIONS = {"siteOwner", "siteFullUser", "siteRestrictedUser"}
WRITE_PERMISSIONS = {"siteOwner", "siteFullUser"}


def property_status(entries: list[dict], site: str) -> dict:
    entry = next((row for row in entries if row.get("siteUrl") == site), None)
    if entry is None:
        return {"status": "NOT_CONFIGURED", "permission": None, "property": site}
    permission = entry.get("permissionLevel")
    if permission == "siteUnverifiedUser":
        return {"status": "PENDING_VERIFICATION", "permission": permission, "property": site}
    if permission not in VERIFIED_PERMISSIONS:
        return {"status": "PERMISSION_UNKNOWN", "permission": permission, "property": site}
    return {"status": "CONNECTED", "permission": permission, "property": site}


def _metrics(rows: list[dict]) -> dict:
    row = rows[0] if rows else {}
    return {
        "clicks": int(row.get("clicks", 0)),
        "impressions": int(row.get("impressions", 0)),
        "ctr": float(row.get("ctr", 0.0)),
        "position": float(row["position"]) if row.get("position") is not None else None,
    }


def collect_search_console(
    client,
    site: str,
    sitemap_url: str,
    *,
    start: date,
    end: date,
    ensure_sitemap: bool = False,
    submission_state: dict | None = None,
    before_submit=None,
    query_metrics: bool = True,
) -> dict:
    state = property_status(client.sites().list().execute().get("siteEntry", []), site)
    if state["status"] != "CONNECTED":
        return state

    sitemap_rows = client.sitemaps().list(siteUrl=site).execute().get("sitemap", [])
    sitemap_present = any(row.get("path") == sitemap_url for row in sitemap_rows)
    sitemap_state = "PRESENT" if sitemap_present else "MISSING"
    if ensure_sitemap and not sitemap_present:
        if state["permission"] in WRITE_PERMISSIONS:
            attempt_state = (submission_state or {}).get("state")
            if attempt_state in {"ATTEMPTED", "SUBMITTED"}:
                sitemap_state = "SUBMISSION_PENDING_CONFIRMATION"
            else:
                if before_submit is not None:
                    before_submit()
                client.sitemaps().submit(siteUrl=site, feedpath=sitemap_url).execute()
                if submission_state is not None:
                    submission_state["state"] = "SUBMITTED"
                sitemap_state = "SUBMITTED"
        else:
            sitemap_state = "READ_ONLY_PERMISSION"

    if not query_metrics:
        return {
            **state,
            "sitemap": sitemap_state,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "metrics": {"status": "PENDING_FINAL_WINDOW"},
        }

    coverage = client.searchanalytics().query(
        siteUrl=site,
        body={
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["date"],
            "dataState": "all",
            "rowLimit": 25000,
        },
    ).execute()
    first_incomplete = (coverage.get("metadata") or {}).get("firstIncompleteDate")
    if first_incomplete is not None:
        try:
            complete = date.fromisoformat(first_incomplete) > end
        except (TypeError, ValueError):
            complete = False
        if not complete:
            return {
                **state,
                "sitemap": sitemap_state,
                "window": {"start": start.isoformat(), "end": end.isoformat()},
                "metrics": {
                    "status": "PENDING_FINAL_DATA",
                    "first_incomplete_date": first_incomplete,
                },
            }

    response = client.searchanalytics().query(
        siteUrl=site,
        body={"startDate": start.isoformat(), "endDate": end.isoformat(), "dataState": "final"},
    ).execute()
    return {
        **state,
        "sitemap": sitemap_state,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "metrics": _metrics(response.get("rows", [])),
    }


def recommendation_for(search_console: dict, traffic: dict) -> str:
    if search_console.get("status") == "PENDING_VERIFICATION":
        return "VERIFY_PROPERTY"
    if search_console.get("status") != "CONNECTED":
        return "KEEP_MEASURING"
    metrics = search_console.get("metrics") or {}
    if metrics.get("status") in {"PENDING_FINAL_WINDOW", "PENDING_FINAL_DATA"} or not isinstance(metrics.get("impressions"), int):
        return "KEEP_MEASURING"
    if metrics.get("impressions") == 0:
        return "CHECK_INDEXING"
    if float(metrics.get("ctr") or 0.0) < 0.02:
        return "IMPROVE_SEARCH_SNIPPETS"
    if traffic.get("amazon_outbound") == 0:
        return "IMPROVE_AMAZON_CTA"
    return "KEEP_MEASURING"
