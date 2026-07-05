from PIL import Image, ImageDraw

import cover_generator


def test_fit_caps_title_depth_at_max_lines():
    img = Image.new("RGB", (400, 400), "white")
    draw = ImageDraw.Draw(img)
    text = " ".join(["ExtremelyLongTitleSegment"] * 20)

    _font, lines, _size, _lh, _lead = cover_generator._fit(
        draw,
        text,
        cover_generator.FONT_SANS_BOLD,
        None,
        0,
        False,
        maxw=180,
        max_h=80,
        start=48,
        min_s=24,
        max_lines=3,
    )

    assert len(lines) <= 3
    assert lines[-1].endswith("…")
