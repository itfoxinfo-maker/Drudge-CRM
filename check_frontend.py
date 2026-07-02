#!/usr/bin/env python3
"""Static guard rails for the browser layer — the piece test_api.py can't reach.

Catches the frontend/i18n/route drift that unit tests miss:
  1. i18n keys used as t("literal") in app.js but defined in NEITHER language
     (they'd render as the raw key to the user — e.g. a quote showing the
     literal text "valid_until").
  2. en/ar dictionary drift (a key defined in one language but not the other).
  3. Fully-static frontend API paths (API.get/post/put/del) that match no server
     route — a typo'd or renamed endpoint that would 404 at runtime.

Standard library only. Run:  python3 check_frontend.py   (exits non-zero on any
problem, so it can gate CI).

Only unambiguous cases are checked: dynamic keys built by concatenation
(t("role_" + r)) and API paths with interpolation/concatenation are skipped
rather than guessed at, so the check never raises a false alarm.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def _strip_js(src):
    """Remove // and /* */ comments and string/template literals so a key regex
    can't trip over a colon or identifier that lives inside a value or comment."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    src = re.sub(r'"(?:[^"\\]|\\.)*"', '""', src)
    src = re.sub(r"'(?:[^'\\]|\\.)*'", "''", src)
    src = re.sub(r"`(?:[^`\\]|\\.)*`", "``", src)
    return src


def _lang_keys(i18n_src, lang):
    """The set of keys defined in the en/ar object literal of TRANSLATIONS."""
    m = re.search(r"\b%s\s*:\s*\{" % lang, i18n_src)
    if not m:
        return set()
    i, depth, start = m.end(), 1, m.end()
    while i < len(i18n_src) and depth:
        if i18n_src[i] == "{":
            depth += 1
        elif i18n_src[i] == "}":
            depth -= 1
        i += 1
    block = _strip_js(i18n_src[start:i - 1])
    return set(re.findall(r"([A-Za-z_]\w*)\s*:", block))


def check_i18n(problems):
    i18n = _read("static", "js", "i18n.js")
    app = _read("static", "js", "app.js")
    en, ar = _lang_keys(i18n, "en"), _lang_keys(i18n, "ar")

    # 1. static, single-literal t("key") calls — the closing ) must follow the
    #    string, which excludes dynamic t("prefix_" + x) concatenations.
    used = set(re.findall(r"""\bt\(\s*['"]([A-Za-z_]\w*)['"]\s*\)""", app))
    for k in sorted(k for k in used if k not in en and k not in ar):
        problems.append(f'i18n: t("{k}") used in app.js but defined in NEITHER language')

    # 2. en/ar parity
    for k in sorted(en - ar):
        problems.append(f'i18n: key "{k}" defined in en but MISSING in ar')
    for k in sorted(ar - en):
        problems.append(f'i18n: key "{k}" defined in ar but MISSING in en')

    return len(en), len(ar)


def check_routes(problems):
    import server  # importing registers ROUTES; no side effects (main() is guarded)
    routes = [(m, rx) for m, rx, _fn, _auth in server.ROUTES]
    app = _read("static", "js", "app.js")
    verb = {"get": "GET", "post": "POST", "put": "PUT", "del": "DELETE"}

    # capture the method, the first path literal, and whether it is concatenated
    # (a trailing +) — concatenated / interpolated paths can't be resolved here.
    calls = re.findall(r"""API\.(get|post|put|del)\(\s*['"`]([^'"`]*)['"`]\s*(\+)?""", app)
    checked = 0
    for v, path, concat in calls:
        if not path.startswith("/") or concat or "${" in path or path.endswith("/"):
            continue  # dynamic / concatenated — skip rather than guess
        method = verb[v]
        full = "/api" + path.split("?", 1)[0]
        if not any(m == method and rx.match(full) for m, rx in routes):
            problems.append(f"route: {method} {full} matches no server route")
        checked += 1
    return checked


def main():
    problems = []
    en, ar = check_i18n(problems)
    n = check_routes(problems)
    print(f"i18n: {en} en keys / {ar} ar keys")
    print(f"routes: {n} static frontend API paths checked")
    if problems:
        print("\n\033[31mFAIL\033[0m — %d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("\n\033[32mPASS\033[0m — frontend/i18n/routes are consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
