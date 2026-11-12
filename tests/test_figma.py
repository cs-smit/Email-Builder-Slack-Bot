import pytest

from emailbot import figma


def bb(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def text(i, chars="Hello", y=0):
    return {"id": i, "type": "TEXT", "characters": chars, "absoluteBoundingBox": bb(0, y, 100, 20),
            "style": {"fontFamily": "Inter", "fontWeight": 400, "fontSize": 16}, "fills": []}


def test_parse_link_variants():
    link = figma.parse_link("https://www.figma.com/design/AbC123/My-File?node-id=12094-7958&t=x")
    assert (link.file_key, link.node_id) == ("AbC123", "12094:7958")
    assert figma.parse_link("https://www.figma.com/design/KEY/branch/BR/My-File?node-id=1-2").file_key == "BR"
    with pytest.raises(figma.FigmaError):
        figma.parse_link("https://www.figma.com/design/KEY/name")  # no node-id
    with pytest.raises(figma.FigmaError):
        figma.parse_link("https://example.com/design/KEY?node-id=1-2")


def test_pick_assets_photos_icons_and_marked():
    frame = {"id": "0", "type": "FRAME", "name": "Email", "absoluteBoundingBox": bb(0, 0, 600, 2000), "children": [
        {"id": "1", "type": "RECTANGLE", "name": "Hero photo", "fills": [{"type": "IMAGE"}], "absoluteBoundingBox": bb(0, 0, 600, 300)},
        {"id": "2", "type": "INSTANCE", "name": "Logo", "componentId": "c", "absoluteBoundingBox": bb(0, 0, 120, 40),
         "children": [{"id": "3", "type": "VECTOR", "absoluteBoundingBox": bb(0, 0, 120, 40)}]},
        {"id": "4", "type": "FRAME", "name": "Copy", "absoluteBoundingBox": bb(0, 300, 600, 200), "children": [text("5")]},
        {"id": "6", "type": "FRAME", "name": "Hidden", "visible": False, "absoluteBoundingBox": bb(0, 0, 10, 10), "children": []},
        {"id": "7", "type": "FRAME", "name": "Badge", "exportSettings": [{}], "absoluteBoundingBox": bb(0, 500, 100, 100), "children": [text("8")]},
    ]}
    picked = {n["name"]: kind for n, kind in figma.pick_assets(frame)}
    assert picked == {"Hero photo": "photo", "Logo": "graphic", "Badge": "marked"}


def test_whole_email_marked_for_export_is_not_flattened():
    frame = {"id": "0", "type": "SECTION", "absoluteBoundingBox": bb(0, 0, 1700, 4000), "children": [
        {"id": "1", "type": "FRAME", "name": "EMAIL", "exportSettings": [{}], "absoluteBoundingBox": bb(0, 0, 800, 3500), "children": [
            {"id": "2", "type": "RECTANGLE", "name": "Photo", "fills": [{"type": "IMAGE"}], "absoluteBoundingBox": bb(0, 0, 640, 400)},
            text("3", y=500)]}]}
    assert [n["name"] for n, _ in figma.pick_assets(frame)] == ["Photo"]


def test_instances_of_one_component_share_a_file():
    icon = lambda i: {"id": i, "type": "INSTANCE", "name": "Icon/Facebook", "componentId": "fb",
                      "absoluteBoundingBox": bb(0, 0, 32, 32), "children": [{"id": i + "v", "type": "VECTOR", "absoluteBoundingBox": bb(0, 0, 32, 32)}]}
    names = figma._name_assets([(icon("a"), "graphic"), (icon("b"), "graphic")])
    assert names["a"] == names["b"] == "icon-facebook.png"


def test_outline_includes_text_and_styles():
    frame = {"id": "0", "type": "FRAME", "name": "E", "absoluteBoundingBox": bb(0, 0, 600, 100), "children": [text("1", "Line one\nLine two")]}
    out = figma.outline(frame, {})
    assert "| Line one" in out and "| Line two" in out and "font=Inter 400 16px" in out
