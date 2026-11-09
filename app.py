import os
import json
import re
import sqlite3
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from flask import Flask, render_template, request, redirect, url_for, send_file, abort, jsonify, flash
from PIL import Image

APP_DIR = Path(__file__).resolve().parent


def configured_data_paths() -> tuple[Path, Path]:
    """Use an environment override or the local, untracked configuration."""
    override = os.environ.get("PHOTOBOOK_DATA_DIR")
    if override:
        directory = Path(override).expanduser()
        return directory / "app.db", directory / "good_photobook"

    config_path = APP_DIR / "config.json"
    config = {}
    if config_path.is_file():
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)

    def configured_path(key: str, default: Path) -> Path:
        value = config.get(key)
        if not value:
            return default
        path = Path(value).expanduser()
        return path if path.is_absolute() else APP_DIR / path

    database = configured_path("good_database", APP_DIR / "app.db")
    good_folder = configured_path("good_folder", database.parent / "good_photobook")
    return database, good_folder


DB_PATH, GOOD_OUTPUT_DIR = configured_data_paths()

# Allowed image extensions:
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

# If True, hidden files/dirs (starting with .) are skipped
SKIP_HIDDEN = True

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"  # set a real secret for production


# -----------------------------
# Database helpers
# -----------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                root TEXT NOT NULL,
                relpath TEXT NOT NULL,
                ext TEXT NOT NULL,
                size_bytes INTEGER,
                mtime REAL,
                good INTEGER NOT NULL DEFAULT 0,
                good_copy_path TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_images_good ON images(good);
            """
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(images)")}
        if "capture_date" not in columns:
            conn.execute("ALTER TABLE images ADD COLUMN capture_date TEXT")

        # Refresh existing dates once when date extraction logic changes.
        date_version = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'capture_date_version'"
        ).fetchone()
        if date_version is None or date_version["value"] != "2":
            rows = conn.execute("SELECT id, path, mtime FROM images").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE images SET capture_date = ? WHERE id = ?",
                    (image_capture_date(row["path"], row["mtime"]), row["id"]),
                )
            conn.execute(
                "INSERT INTO app_meta (key, value) VALUES ('capture_date_version', '2') "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            )


def image_capture_date(path: str, fallback_mtime: Optional[float]) -> str:
    """Return an ISO sortable capture date, falling back to file modification time."""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            exif_ifd = exif.get_ifd(34665) if hasattr(exif, "get_ifd") else {}
            for raw in (exif_ifd.get(36867), exif_ifd.get(36868), exif.get(36867), exif.get(36868), exif.get(306)):
                if raw:
                    try:
                        return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").isoformat(sep=" ")
                    except ValueError:
                        continue
    except (OSError, ValueError):
        pass

    if fallback_mtime is not None:
        try:
            return datetime.fromtimestamp(float(fallback_mtime)).isoformat(sep=" ", timespec="seconds")
        except (OverflowError, OSError, ValueError):
            pass
    return ""


# -----------------------------
# Scanning / path logic
# -----------------------------
def is_hidden_path(p: Path) -> bool:
    if not SKIP_HIDDEN:
        return False
    return any(part.startswith(".") for part in p.parts)


def iter_images_in_roots(roots: Iterable[Path]) -> Iterable[Tuple[Path, Path, Path]]:
    """
    Yields (root, file_path, relpath_to_root) for image files under roots.
    """
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if is_hidden_path(file_path):
                continue

            ext = file_path.suffix.lower()
            if ext in IMAGE_EXTS:
                try:
                    rel = file_path.relative_to(root)
                except ValueError:
                    # Shouldn't happen with rglob, but safe-guard.
                    rel = file_path.name
                yield root, file_path, Path(rel)


def stable_copy_name(root: str, relpath: str, original_name: str) -> str:
    """
    Create a collision-resistant filename for the copy in GOOD_OUTPUT_DIR.
    Keeps original basename for human readability, with a short hash prefix.
    """
    key = f"{root}::{relpath}"
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    # sanitize original_name a bit for filesystem
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("_")
    if not safe_name:
        safe_name = "image"
    return f"{h}__{safe_name}"


def ensure_good_output_dir() -> None:
    GOOD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def upsert_scan(roots: List[Path]) -> int:
    """
    Scan roots and insert any new images into DB.
    Returns count of new images added.
    """
    ensure_good_output_dir()

    added = 0
    with db() as conn:
        for root, file_path, rel in iter_images_in_roots(roots):
            try:
                stat = file_path.stat()
            except OSError:
                continue

            capture_date = image_capture_date(str(file_path), stat.st_mtime)
            row = conn.execute("SELECT id FROM images WHERE path = ?", (str(file_path),)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO images (path, root, relpath, ext, size_bytes, mtime, good, good_copy_path, capture_date)
                    VALUES (?, ?, ?, ?, ?, ?, 0, NULL, ?)
                    """,
                    (
                        str(file_path),
                        str(root),
                        str(rel),
                        file_path.suffix.lower(),
                        int(stat.st_size),
                        float(stat.st_mtime),
                        capture_date,
                    ),
                )
                added += 1
            else:
                # Update size/mtime in case file changed (but do not change good state)
                conn.execute(
                    """
                    UPDATE images
                    SET size_bytes = ?, mtime = ?, capture_date = ?
                    WHERE path = ?
                    """,
                    (int(stat.st_size), float(stat.st_mtime), capture_date, str(file_path)),
                )
    return added


# -----------------------------
# Good toggle logic
# -----------------------------
def copy_to_good(image_row: sqlite3.Row) -> str:
    """
    Copy original file into GOOD_OUTPUT_DIR.
    Returns the destination path as string.
    """
    src = Path(image_row["path"])
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Source missing: {src}")

    dest_name = stable_copy_name(image_row["root"], image_row["relpath"], src.name)
    dest = GOOD_OUTPUT_DIR / dest_name

    # Copy with metadata (mtime etc.)
    shutil.copy2(src, dest)

    return str(dest)


def remove_good_copy(copy_path: Optional[str]) -> None:
    """
    Remove the copied file only (never the original).
    """
    if not copy_path:
        return
    p = Path(copy_path)
    try:
        if p.exists() and p.is_file():
            p.unlink()
    except OSError:
        pass


# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    init_db()
    ensure_good_output_dir()

    if request.method == "POST":
        raw = request.form.get("folders", "")
        roots = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            roots.append(Path(line))

        if not roots:
            flash("Please provide at least one folder path.", "error")
            return redirect(url_for("index"))

        added = upsert_scan(roots)
        flash(f"Scan complete. Added {added} new image(s).", "ok")
        return redirect(url_for("review", idx=0))

    # Stats
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM images").fetchone()["c"]
        good = conn.execute("SELECT COUNT(*) AS c FROM images WHERE good = 1").fetchone()["c"]

    return render_template("index.html", total=total, good=good, output_dir=str(GOOD_OUTPUT_DIR))


@app.route("/review/<int:idx>")
def review(idx: int):
    init_db()

    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM images").fetchone()["c"]
        if total == 0:
            flash("No images in database yet. Add folders on the home page.", "error")
            return redirect(url_for("index"))

        idx = max(0, min(idx, total - 1))

        row = conn.execute(
            """
            SELECT id, path, root, relpath, good, good_copy_path, size_bytes, mtime, capture_date
            FROM images
            ORDER BY CASE WHEN capture_date IS NULL OR capture_date = '' THEN 1 ELSE 0 END,
                     capture_date ASC, path ASC
            LIMIT 1 OFFSET ?
            """,
            (idx,),
        ).fetchone()

        if row is None:
            abort(404)

        good_count = conn.execute("SELECT COUNT(*) AS c FROM images WHERE good = 1").fetchone()["c"]

    return render_template(
        "review.html",
        row=row,
        idx=idx,
        total=total,
        good_count=good_count,
    )


@app.route("/image/<int:image_id>")
def serve_image(image_id: int):
    init_db()
    with db() as conn:
        row = conn.execute("SELECT path FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        abort(404)

    p = Path(row["path"])
    if not p.exists() or not p.is_file():
        abort(404)

    # Sends the original image for viewing; does not modify it.
    return send_file(p)


@app.route("/toggle_good", methods=["POST"])
def toggle_good():
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    image_id = data.get("image_id")
    make_good = data.get("good")

    if image_id is None or make_good is None:
        return jsonify({"ok": False, "error": "Missing image_id or good"}), 400

    with db() as conn:
        row = conn.execute(
            "SELECT id, path, root, relpath, good, good_copy_path FROM images WHERE id = ?",
            (int(image_id),),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "Image not found"}), 404

        make_good = bool(make_good)

        if make_good:
            # If already good, no-op
            if int(row["good"]) == 1 and row["good_copy_path"]:
                return jsonify({"ok": True, "good": True, "copy_path": row["good_copy_path"]})

            try:
                dest = copy_to_good(row)
            except Exception as e:
                return jsonify({"ok": False, "error": f"Copy failed: {e}"}), 500

            conn.execute(
                "UPDATE images SET good = 1, good_copy_path = ? WHERE id = ?",
                (dest, int(image_id)),
            )
            return jsonify({"ok": True, "good": True, "copy_path": dest})

        else:
            # Unmark: remove copied file only
            remove_good_copy(row["good_copy_path"])
            conn.execute(
                "UPDATE images SET good = 0, good_copy_path = NULL WHERE id = ?",
                (int(image_id),),
            )
            return jsonify({"ok": True, "good": False})


@app.route("/good")
def list_good():
    init_db()
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, path, root, relpath, good_copy_path, capture_date
            FROM images
            WHERE good = 1
            ORDER BY CASE WHEN capture_date IS NULL OR capture_date = '' THEN 1 ELSE 0 END,
                     capture_date ASC, path ASC
            """
        ).fetchall()
    return render_template("good.html", rows=rows, output_dir=str(GOOD_OUTPUT_DIR))


if __name__ == "__main__":
    init_db()
    ensure_good_output_dir()
    app.run(host="0.0.0.0", port=5000, debug=False)  # debug=True would expose the Werkzeug console to the LAN
