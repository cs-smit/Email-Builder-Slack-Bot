import io

from PIL import Image

from emailbot import uploads


def png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def test_two_designs_and_their_images():
    files = [("LEADS.png", png(800, 3500)), ("hero.png", png(1280, 946)), ("MEMBERS.png", png(800, 2900)),
             ("logo.png", png(300, 80))]
    designs, assets = uploads.split_designs(files)
    assert [n for n, _ in designs] == ["LEADS.png", "MEMBERS.png"]
    assert [n for n, _ in assets] == ["hero.png", "logo.png"]


def test_no_tall_design_falls_back_to_biggest():
    designs, assets = uploads.split_designs([("small.png", png(200, 100)), ("wide.png", png(1200, 900))])
    assert [n for n, _ in designs] == ["wide.png"] and [n for n, _ in assets] == ["small.png"]


def test_prepare_uses_the_named_design(tmp_path):
    files = [("A.png", png(800, 3000)), ("B.png", png(800, 2000))]
    info = uploads.prepare(files, tmp_path, design_name="B.png")
    assert info["name"] == "B" and [a["file"] for a in info["assets"]] == ["a.png"]
