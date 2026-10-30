from datetime import date

from libra_search_console import (
    collect_search_console,
    property_status,
    recommendation_for,
)


SITE = "https://newton-winai-klinprasom.incomeinclick.in.th/libra/"
SITEMAP = f"{SITE}sitemap.xml"


class Request:
    def __init__(self, result=None):
        self.result = result if result is not None else {}

    def execute(self):
        return self.result


class FakeSites:
    def __init__(self, entries):
        self.entries = entries

    def list(self):
        return Request({"siteEntry": self.entries})


class FakeSitemaps:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.submitted = []

    def list(self, **kwargs):
        return Request({"sitemap": self.rows})

    def submit(self, **kwargs):
        self.submitted.append(kwargs)
        return Request()


class FakeAnalytics:
    def __init__(self, rows=None, responses=None):
        self.rows = rows or []
        self.responses = list(responses or [])
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self.responses:
            return Request(self.responses.pop(0))
        return Request({"rows": self.rows})


class FakeClient:
    def __init__(self, permission, *, sitemaps=None, rows=None, responses=None):
        self._sites = FakeSites([{"siteUrl": SITE, "permissionLevel": permission}])
        self._sitemaps = FakeSitemaps(sitemaps)
        self._analytics = FakeAnalytics(rows, responses)

    def sites(self):
        return self._sites

    def sitemaps(self):
        return self._sitemaps

    def searchanalytics(self):
        return self._analytics


def test_unverified_property_never_submits_or_queries():
    client = FakeClient("siteUnverifiedUser")

    result = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 4),
        ensure_sitemap=True,
    )

    assert result == {
        "status": "PENDING_VERIFICATION",
        "permission": "siteUnverifiedUser",
        "property": SITE,
    }
    assert client._sitemaps.submitted == []
    assert client._analytics.queries == []


def test_verified_property_submits_missing_sitemap_once_and_reads_metrics():
    client = FakeClient(
        "siteFullUser",
        rows=[{"clicks": 2, "impressions": 50, "ctr": 0.04, "position": 13.5}],
    )

    result = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 4),
        ensure_sitemap=True,
    )

    assert client._sitemaps.submitted == [{"siteUrl": SITE, "feedpath": SITEMAP}]
    assert result["status"] == "CONNECTED"
    assert result["sitemap"] == "SUBMITTED"
    assert result["metrics"] == {
        "clicks": 2,
        "impressions": 50,
        "ctr": 0.04,
        "position": 13.5,
    }


def test_existing_sitemap_is_not_submitted_again():
    client = FakeClient(
        "siteOwner",
        sitemaps=[{"path": SITEMAP}],
        rows=[],
    )

    result = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 4),
        ensure_sitemap=True,
    )

    assert client._sitemaps.submitted == []
    assert result["sitemap"] == "PRESENT"
    assert result["metrics"]["impressions"] == 0


def test_property_status_distinguishes_absent_pending_and_verified():
    assert property_status([], SITE)["status"] == "NOT_CONFIGURED"
    assert property_status(
        [{"siteUrl": SITE, "permissionLevel": "siteUnverifiedUser"}], SITE
    )["status"] == "PENDING_VERIFICATION"
    assert property_status(
        [{"siteUrl": SITE, "permissionLevel": "siteRestrictedUser"}], SITE
    )["status"] == "CONNECTED"


def test_unknown_permission_fails_closed_without_provider_reads():
    for permission in (None, "", "futurePermission"):
        client = FakeClient(permission)
        result = collect_search_console(
            client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 5),
            ensure_sitemap=True,
        )
        assert result["status"] == "PERMISSION_UNKNOWN"
        assert client._sitemaps.submitted == []
        assert client._analytics.queries == []


def test_submission_attempt_is_durable_when_provider_list_is_stale():
    client = FakeClient("siteFullUser")
    submission = {}

    def claim():
        submission["state"] = "ATTEMPTED"

    first = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 5),
        ensure_sitemap=True, submission_state=submission, before_submit=claim,
    )
    second = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 5),
        ensure_sitemap=True, submission_state=submission, before_submit=claim,
    )

    assert first["sitemap"] == "SUBMITTED"
    assert second["sitemap"] == "SUBMISSION_PENDING_CONFIRMATION"
    assert len(client._sitemaps.submitted) == 1


def test_metrics_are_not_queried_before_the_final_window_is_available():
    client = FakeClient("siteFullUser")

    result = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 5),
        query_metrics=False,
    )

    assert result["metrics"] == {"status": "PENDING_FINAL_WINDOW"}
    assert client._analytics.queries == []


def test_incomplete_provider_window_stays_pending_instead_of_becoming_zero():
    client = FakeClient(
        "siteFullUser",
        responses=[{
            "metadata": {"firstIncompleteDate": "2026-11-05"},
            "rows": [{"keys": ["2026-11-05"], "impressions": 0, "clicks": 0}],
        }],
    )

    result = collect_search_console(
        client, SITE, SITEMAP, start=date(2026, 10, 30), end=date(2026, 11, 5),
    )

    assert result["metrics"] == {
        "status": "PENDING_FINAL_DATA",
        "first_incomplete_date": "2026-11-05",
    }
    assert len(client._analytics.queries) == 1
    assert client._analytics.queries[0]["body"]["dimensions"] == ["date"]
    assert client._analytics.queries[0]["body"]["dataState"] == "all"


def test_recommendation_uses_observed_funnel_without_inventing_zeroes():
    assert recommendation_for({"status": "PENDING_VERIFICATION"}, {}) == "VERIFY_PROPERTY"
    assert recommendation_for({"status": "CONNECTED", "metrics": {"impressions": 0}}, {}) == "CHECK_INDEXING"
    assert recommendation_for(
        {"status": "CONNECTED", "metrics": {"impressions": 100, "ctr": 0.01}},
        {"amazon_outbound": 0},
    ) == "IMPROVE_SEARCH_SNIPPETS"
    assert recommendation_for(
        {"status": "CONNECTED", "metrics": {"impressions": 100, "ctr": 0.05}},
        {"amazon_outbound": 0},
    ) == "IMPROVE_AMAZON_CTA"
    assert recommendation_for({"status": "UNKNOWN"}, {}) == "KEEP_MEASURING"
