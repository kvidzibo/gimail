"""Parse FETCH metadata structurally, never matching attributes inside flag lists."""
import re

from .errors import GimailError


START = re.compile(rb"^\d+\s+\(")


def fetch_metadata(header):
    """Read top-level UID/FLAGS/RFC822.SIZE, including metadata after literals."""
    error = "Server returned malformed FETCH metadata."
    if not isinstance(header, bytes) or not START.match(header):
        raise GimailError(error)
    root = []
    stack = [root]
    index = 0
    while index < len(header):
        char = header[index:index + 1]
        if char.isspace():
            index += 1
            continue
        if char == b"(":
            value = []
            stack[-1].append(value)
            stack.append(value)
            index += 1
        elif char == b")":
            if len(stack) == 1:
                raise GimailError(error)
            stack.pop()
            index += 1
        else:
            start = index
            if char == b'"':
                index += 1
                while index < len(header) and header[index:index + 1] != b'"':
                    index += 2 if header[index:index + 1] == b"\\" else 1
                if index >= len(header):
                    raise GimailError(error)
                index += 1
            else:
                # A BODY[HEADER.FIELDS (...)] section is one attribute name,
                # despite spaces and parentheses inside its square brackets.
                attribute_name = len(stack) == 2 and len(stack[-1]) % 2 == 0
                while index < len(header) and header[index:index + 1] not in b' \t\r\n()"':
                    if attribute_name and header[index:index + 1] == b"[":
                        index = header.find(b"]", index + 1)
                        if index == -1:
                            raise GimailError(error)
                    index += 1
            stack[-1].append(header[start:index])
    if len(stack) != 1 or len(root) != 2 or not isinstance(root[1], list) or len(root[1]) % 2:
        raise GimailError(error)
    attributes = {}
    for key, value in zip(root[1][::2], root[1][1::2]):
        if not isinstance(key, bytes) or key.upper() in attributes:
            raise GimailError(error)
        attributes[key.upper()] = value
    result = {}
    for key, name in ((b"UID", "uid"), (b"RFC822.SIZE", "size")):
        value = attributes.get(key)
        if value is not None and (
            not isinstance(value, bytes) or len(value) > 10 or not value.isdigit()
            or not (1 if key == b"UID" else 0) <= int(value) <= 4294967295
        ):
            raise GimailError(error)
        result[name] = int(value) if value is not None else None
    flags = attributes.get(b"FLAGS")
    if flags is not None and (not isinstance(flags, list) or any(not isinstance(flag, bytes) for flag in flags)):
        raise GimailError(error)
    result["flags"] = [flag.decode("ascii", "replace") for flag in flags] if flags is not None else None
    return result
