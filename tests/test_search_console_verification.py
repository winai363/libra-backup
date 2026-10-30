from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import app as libra_app


def test_libra_root_exposes_google_site_verification_meta():
    response = TestClient(libra_app.app).get("/")

    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    tag = soup.select_one('head meta[name="google-site-verification"]')
    assert tag is not None
    assert tag.get("content")
