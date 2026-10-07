"""Trusted local recipe for a synthetic static site; never run from a handoff."""
import html
import json


def render(page_bytes, stylesheet):
    page = json.loads(page_bytes)
    if (not isinstance(page, dict) or set(page) != {"title", "body"}
            or not all(isinstance(value, str) for value in page.values())):
        raise ValueError("page must contain only a string title and body")
    title, body = html.escape(page["title"]), html.escape(page["body"])
    document = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<title>{title}</title><link rel="stylesheet" href="style.css">'
        f'</head><body><h1>{title}</h1><p>{body}</p></body></html>\n'
    ).encode("utf-8")
    return {"index.html": document, "style.css": stylesheet}
