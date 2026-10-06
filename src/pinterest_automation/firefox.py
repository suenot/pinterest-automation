"""Read only ordinary Pinterest cookies from an explicitly selected Firefox profile."""

from pathlib import Path
import sqlite3
import time

from .errors import PublishError

PINTEREST_HOSTS = {"pinterest.com", ".pinterest.com", "www.pinterest.com", ".www.pinterest.com"}


def pinterest_cookies(profile: str) -> list[dict]:
    database = Path(profile).expanduser().resolve() / "cookies.sqlite"
    if not database.is_file():
        raise PublishError("FIREFOX_PROFILE_INVALID", "Firefox profile must contain a readable cookies.sqlite database.")
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            columns = {row[1] for row in connection.execute("PRAGMA table_info(moz_cookies)")}
            required = {"host", "name", "value", "path", "expiry", "isSecure", "isHttpOnly", "sameSite", "originAttributes"}
            if not required <= columns:
                raise ValueError()
            partitioned = "isPartitionedAttributeSet" if "isPartitionedAttributeSet" in columns else "0 AS isPartitionedAttributeSet"
            rows = connection.execute(f"SELECT host,name,value,path,expiry,isSecure,isHttpOnly,sameSite,originAttributes,{partitioned} FROM moz_cookies WHERE host IN (?,?,?,?)", tuple(sorted(PINTEREST_HOSTS))).fetchall()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise PublishError("FIREFOX_PROFILE_INVALID", "Cannot read the Firefox Pinterest cookie database; no Firefox files were changed.") from exc
    cookies = []
    now = time.time()
    for row in rows:
        if row["originAttributes"] or row["isPartitionedAttributeSet"]:
            continue
        expires = row["expiry"]
        # Current Firefox profiles can store milliseconds; Playwright uses seconds.
        if expires > 100_000_000_000:
            expires /= 1000
        if expires and expires <= now:
            continue
        if not row["name"] or not row["value"] or not row["path"].startswith("/"):
            continue
        cookie = {"name": row["name"], "value": row["value"], "domain": row["host"], "path": row["path"],
                  "secure": bool(row["isSecure"]), "httpOnly": bool(row["isHttpOnly"])}
        if expires:
            cookie["expires"] = expires
        if row["sameSite"] in {0, 1, 2}:
            cookie["sameSite"] = {0: "None", 1: "Lax", 2: "Strict"}[row["sameSite"]]
        cookies.append(cookie)
    if not any(cookie["name"] == "_pinterest_sess" for cookie in cookies):
        raise PublishError("FIREFOX_SESSION_MISSING", "Firefox has no unexpired ordinary Pinterest session; sign in to the intended account in the source profile.")
    return cookies
