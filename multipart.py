"""Minimal multipart/form-data parser (standard library only).

Parses a raw request body into form fields and files. Good enough for the
single-file photo uploads this CRM performs.
"""


def parse_multipart(body: bytes, content_type: str):
    """Return (fields: dict[str,str], files: list[dict])."""
    fields, files = {}, []
    if "boundary=" not in content_type:
        return fields, files
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    delim = ("--" + boundary).encode()
    parts = body.split(delim)
    for part in parts:
        # Strip only the framing CRLF around the part — NOT every trailing
        # \r/\n byte (strip(b"\r\n") would corrupt binary files whose content
        # happens to end in 0x0A/0x0D).
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, data = part.split(b"\r\n\r\n", 1)
        headers = {}
        for line in raw_headers.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower().decode()] = v.strip().decode(errors="replace")
        disp = headers.get("content-disposition", "")
        name = _param(disp, "name")
        filename = _param(disp, "filename")
        if filename:
            files.append({
                "field": name,
                "filename": filename,
                "content_type": headers.get("content-type", "application/octet-stream"),
                "data": data,
            })
        elif name:
            fields[name] = data.decode(errors="replace")
    return fields, files


def _param(header_value: str, key: str):
    for chunk in header_value.split(";"):
        chunk = chunk.strip()
        if chunk.startswith(key + "="):
            return chunk[len(key) + 1:].strip().strip('"')
    return None
