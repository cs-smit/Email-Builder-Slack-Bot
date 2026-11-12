import json

from emailbot import config, library


def test_parse_manifest_formats():
    js = 'window.GALLERY = {"templates": [{"file": "a.html"}]};'
    assert library._parse_manifest(js)["templates"][0]["file"] == "a.html"
    assert library._parse_manifest('[{"file": "b.html"}]')["templates"][0]["file"] == "b.html"


def test_library_dir_scan_and_matching(tmp_path, monkeypatch):
    lib = tmp_path / "templates"
    (lib / "promos").mkdir(parents=True)
    (lib / "welcome.html").write_text("<html><title>Welcome</title><h1>Welcome aboard</h1><p>Start your free trial today</p></html>")
    (lib / "promos" / "sale.html").write_text("<html><title>Summer sale</title><h1>Summer sale</h1><p>Save 30% on all plans this weekend</p></html>")
    (lib / ".hidden.html").write_text("<p>ignored</p>")
    monkeypatch.setattr(config, "LIBRARY_DIR", str(lib))
    monkeypatch.setattr(library, "FILES_DIR", tmp_path / "cache" / "files")
    monkeypatch.setattr(library, "MANIFEST_CACHE", tmp_path / "cache" / "manifest.json")
    (tmp_path / "cache").mkdir()

    templates = library.sync()
    assert sorted(t.file for t in templates) == ["promos/sale.html", "welcome.html"]
    best = library.find_matches("Big summer sale: save on every plan", templates=templates, top=2)
    assert best[0][0].file == "promos/sale.html"
    hinted = library.find_matches("Big summer sale", hint="base it on welcome.html", templates=templates, top=1)
    assert hinted[0][0].file == "welcome.html"
