# Your template library

Email Builder never designs from a blank page. It finds the existing emails closest to the new
design and has Claude adapt the best one, so new emails reuse your tested code: the `<head>`,
CSS, Outlook fixes, header, footer, social icons and merge tags.

Point it at your templates in one of two ways.

## A folder of HTML files: `LIBRARY_DIR`

```
LIBRARY_DIR=/path/to/templates
```

Every `.html`/`.htm` file under it (subfolders included, hidden files skipped) is a template.
The title comes from `<title>` (or the file name) and the headline from the first `<h1>`/`<h2>`.
Changes are picked up every time the bot starts a build.

## A website with a manifest: `LIBRARY_URL`

Useful when your templates are already published, e.g. a gallery site on Netlify:

```
LIBRARY_URL=https://your-templates.example.com/
LIBRARY_MANIFEST=gallery/templates.js     # path of the manifest, relative to LIBRARY_URL
```

The manifest lists the templates. Plain JSON works, or a JavaScript file that assigns the
object (the bot reads from the first `{` to the last `}`):

```json
{
  "templates": [
    {"file": "welcome.html", "title": "Welcome", "headline": "Welcome aboard", "modified": "2026-01-05T10:00:00Z"},
    {"file": "promos/sale.html", "title": "Summer sale", "headline": "Save 30%", "modified": "2026-06-01T09:00:00Z"}
  ]
}
```

- `file` (required): path relative to `LIBRARY_URL`.
- `title`, `headline` (optional): help matching.
- `modified` (optional): when it changes, the bot downloads that template again. Others are
  cached in `data/library/`.

A bare list (`[{"file": …}, …]`) also works.

## How a base template is chosen

The design's text (from Figma, or read from the uploaded picture) is compared with every
template's text, and the four closest are given to Claude, which picks the one whose layout fits
best. To choose yourself, name it in the notes: `/email <link> base it on welcome.html`.

Check what it would pick without building anything:

```bash
python -m emailbot.cli match "Some text from the new design" --hint "base it on welcome.html"
```
