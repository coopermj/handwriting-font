from ingest_segment.svg_strokes import RawStroke, parse_svg_strokes

# two polylines: one near-black (ink), one light gray (template rule)
SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <path d="M 10,10 L 20,20 L 30,15" stroke="#111111" fill="none"/>
  <path d="M 0,50 L 100,50" stroke="#cccccc" fill="none"/>
</svg>"""


def test_parse_returns_strokes_with_points_and_color(tmp_path):
    svg_path = tmp_path / "page.svg"
    svg_path.write_text(SVG, encoding="utf-8")

    strokes = parse_svg_strokes(svg_path)

    assert len(strokes) == 2
    assert all(isinstance(s, RawStroke) for s in strokes)
    dark = [s for s in strokes if s.is_dark(threshold=0.5)]
    assert len(dark) == 1
    # points are sampled in order along the path; endpoints land near the path ends
    pts = dark[0].points
    assert len(pts) >= 2
    assert abs(pts[0][0] - 10) < 2 and abs(pts[0][1] - 10) < 2
