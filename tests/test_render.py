import json

from PIL import Image

from emailbot import render


def test_placeholders_for_img_and_background(tmp_path):
    (tmp_path / "email.html").write_text(
        '<img src="images/MISSING-hero.png" width="640" height="300" alt="">'
        '<td background="images/MISSING-bg.jpg" data-placeholder="600x200"></td>')
    made = render.make_placeholders(tmp_path)
    assert sorted(made) == ["MISSING-bg.jpg", "MISSING-hero.png"]
    assert Image.open(tmp_path / "images" / "MISSING-hero.png").size == (1280, 600)
    assert Image.open(tmp_path / "images" / "MISSING-bg.jpg").size == (1200, 400)


def test_crops_cut_and_trim(tmp_path):
    design = Image.new("RGB", (400, 400), "white")
    design.paste(Image.new("RGB", (40, 20), "black"), (100, 100))  # a "logo" at 50,50 in 1x pixels
    design.save(tmp_path / "design-full.png")
    (tmp_path / "design.json").write_text(json.dumps({"scale": 2}))
    (tmp_path / "crops.json").write_text(json.dumps([
        {"file": "logo.png", "x": 40, "y": 40, "w": 50, "h": 30, "trim": True},
        {"file": "../evil.png", "x": 0, "y": 0, "w": 10, "h": 10},
        {"file": "outside.png", "x": 500, "y": 500, "w": 10, "h": 10},
    ]))
    made, problems = render.apply_crops(tmp_path)
    # "../evil.png" is reduced to its file name, so it can only land inside images/.
    assert made == ["logo.png", "evil.png"]
    assert (tmp_path / "images" / "evil.png").exists()
    assert len(problems) == 1  # the box outside the design
    w, h = Image.open(tmp_path / "images" / "logo.png").size
    assert (w, h) == (48, 28)  # 40x20 logo + 2 design-px (4 file-px) margin each side
    assert not (tmp_path.parent / "evil.png").exists()
