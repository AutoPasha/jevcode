"""What the web layer calls."""

from paging import paginate, pages


def list_page(rows, page):
    return {"rows": paginate(rows, page), "pages": pages(rows), "page": page}
