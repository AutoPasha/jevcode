"""Cutting a list into pages."""

PAGE = 10


def paginate(rows, page):
    """Return the rows of one page, counting pages from one."""
    start = (page - 1) * PAGE
    return list(rows)[start:start + PAGE]


def pages(rows):
    return (len(rows) + PAGE - 1) // PAGE
