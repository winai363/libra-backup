"""scripts/make_pin_image.py — one fresh Pin image per article, drawn from the
book's own cover. Rendering reads the real cover files; nothing under data/ is
written (the output directory is redirected to tmp_path)."""
import json
import sys
from pathlib import Path

import pytest

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

import make_pin_image  # noqa: E402

LONG_TITLE = ("Cómo empezar una tarea aburrida cuando todo te distrae: "
              "la regla de los dos primeros minutos en la mesa")


@pytest.mark.parametrize("slug", sorted(make_pin_image.BOOKS))
def test_renders_a_vertical_pin_for_each_book(slug):
    pin = make_pin_image.render({"id": f"{slug}-sample", "target_slug": slug,
                                 "title": LONG_TITLE})
    assert pin.size == (1000, 1500)


def test_two_articles_of_one_book_get_different_images():
    first = make_pin_image.render({"id": "adhd-a", "target_slug": "adhd-adults-workbook-es",
                                   "title": "Primera idea práctica para el día"})
    second = make_pin_image.render({"id": "adhd-b", "target_slug": "adhd-adults-workbook-es",
                                    "title": "Segunda idea práctica para la semana"})
    assert first.tobytes() != second.tobytes()


def test_refuses_an_article_whose_image_url_is_not_its_own_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(make_pin_image, "PINS_DIR", tmp_path / "pins")
    article = tmp_path / "a.json"
    article.write_text(json.dumps({"id": "adhd-x", "target_slug": "adhd-adults-workbook-es",
                                   "title": "Una idea", "image_url": "/libra/api/books/x/cover"}))
    with pytest.raises(ValueError):
        make_pin_image.make(article)
    assert not (tmp_path / "pins").exists()


def test_writes_the_pin_named_after_the_article(tmp_path, monkeypatch):
    monkeypatch.setattr(make_pin_image, "PINS_DIR", tmp_path / "pins")
    article = tmp_path / "a.json"
    article.write_text(json.dumps({"id": "adhd-x", "target_slug": "adhd-adults-workbook-es",
                                   "title": "Una idea práctica",
                                   "image_url": "/libra/growth/pins/adhd-x.jpg"}))
    assert make_pin_image.make(article) == tmp_path / "pins" / "adhd-x.jpg"
    assert (tmp_path / "pins" / "adhd-x.jpg").read_bytes()[:2] == b"\xff\xd8"
