import json
import zipfile

from PIL import Image

from emailbot import package


def make_job(tmp_path, html):
    (tmp_path / "images").mkdir()
    for name in ("logo.png", "photo.png", "unused.png"):
        Image.new("RGB", (20, 10), "red").save(tmp_path / "images" / name)
    (tmp_path / "email.html").write_text(html)
    return tmp_path


HTML = ('<img src="images/logo.png"><td background="images/photo.png" style="background:url(\'images/photo.png\')">'
        '<img src="images/MISSING-hero.png"><img src="images/nope.png">{{unsubscribe}}')


def test_check_reports_missing_unused_and_tags(tmp_path):
    job = make_job(tmp_path, HTML)
    (tmp_path / "base.html").write_text("{{unsubscribe}} {{{EmailPreferenceCenter}}}")
    r = package.check(job, tmp_path / "base.html")
    assert r.used == ["logo.png", "photo.png", "MISSING-hero.png", "nope.png"]
    assert r.unused == ["unused.png"]
    assert r.lost_tags == ["{{{EmailPreferenceCenter}}}"]
    lines = "\n".join(r.lines())
    assert "nope.png" in lines and "MISSING-hero.png" in lines


def test_hosted_images_and_base_url():
    assert package.normalise_base_url("<https://b.s3.amazonaws.com/f/x/logo.png|logo>") == "https://b.s3.amazonaws.com/f/x/"
    assert package.normalise_base_url("https://cdn.example.com/folder") == "https://cdn.example.com/folder/"
    out = package.with_hosted_images(HTML, "https://cdn.example.com/f/")
    assert "images/" not in out.replace("MISSING", "")[:0] + out.split("{{")[0].replace("cdn.example.com/f/", "")
    assert out.count("https://cdn.example.com/f/photo.png") == 2


def test_package_zip_contains_html_and_used_images(tmp_path):
    job = make_job(tmp_path, '<img src="images/logo.png"><img src="images/photo.png">')
    names = zipfile.ZipFile(package.package_zip(job, "welcome")).namelist()
    assert sorted(names) == ["welcome/images/logo.png", "welcome/images/photo.png", "welcome/welcome.html"]


def test_netlify_comment_is_removed():
    from emailbot.library import clean
    html = "<html><head>\n<!-- This site is hosted on Netlify. https://netlify.new -->\n<meta></head></html>"
    assert "netlify" not in clean(html).lower()
