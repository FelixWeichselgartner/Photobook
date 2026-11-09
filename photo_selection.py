"""Select marked photos for a configured photobook chapter."""

import os
import sqlite3


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def selected_good_images(config: dict, folder_path: str, valid_exts: tuple[str, ...]):
    """Return ordered database selections for the configured folder, or None."""
    database = config.get("good_database")
    if not database:
        return None
    good_folder = config.get("good_folder")
    if not good_folder:
        raise ValueError("good_folder must be set when good_database is configured")
    if _normalized(folder_path) != _normalized(good_folder):
        return None
    if not os.path.isfile(database):
        raise FileNotFoundError(f"Good-photo database not found: {database}")

    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            """SELECT good_copy_path FROM images WHERE good = 1
               ORDER BY CASE WHEN capture_date IS NULL OR capture_date = '' THEN 1 ELSE 0 END,
                        capture_date ASC, path ASC"""
        ).fetchall()

    if not rows:
        raise ValueError("No photos are marked good in the configured database")
    result = []
    seen = set()
    for (path,) in rows:
        if not path or _normalized(os.path.dirname(path)) != _normalized(good_folder):
            raise ValueError(f"Good photo has no copy in {good_folder}: {path}")
        if not path.lower().endswith(valid_exts):
            raise ValueError(f"Unsupported good-photo format: {path}")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Good photo copy is missing: {path}")
        key = _normalized(path)
        if key in seen:
            raise ValueError(f"Duplicate good-photo copy in database: {path}")
        seen.add(key)
        result.append(path)
    return result
