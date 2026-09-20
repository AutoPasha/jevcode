"""A tiny CSV line parser, written by hand because the task needs one field quirk."""


def parse_line(line):
    """Split one CSV line into fields, honouring double quotes.

    >>> parse_line('a,"b,c",d')
    ['a', 'b,c', 'd']
    """
    fields, current, quoted = [], [], False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    fields.append("".join(current))
    return fields
