import os
import re
import sqlite3
import hashlib
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from flask import Flask, render_template, request, redirect, url_for, send_file, abort, jsonify, flash

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "app.db"

# Where "good" images are copied to:
GOOD_OUTPUT_DIR = APP_DIR / "good_photobook"

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

            row = conn.execute("SELECT id FROM images WHERE path = ?", (str(file_path),)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO images (path, root, relpath, ext, size_bytes, mtime, good, good_copy_path)
                    VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (
                        str(file_path),
                        str(root),
                        str(rel),
                        file_path.suffix.lower(),
                        int(stat.st_size),
                        float(stat.st_mtime),
                    ),
                )
                added += 1
            else:
                # Update size/mtime in case file changed (but do not change good state)
                conn.execute(
                    """
                    UPDATE images
                    SET size_bytes = ?, mtime = ?
                    WHERE path = ?
                    """,
                    (int(stat.st_size), float(stat.st_mtime), str(file_path)),
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
            SELECT id, path, root, relpath, good, good_copy_path, size_bytes, mtime
            FROM images
            ORDER BY path ASC
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
            SELECT id, path, root, relpath, good_copy_path
            FROM images
            WHERE good = 1
            ORDER BY path ASC
            """
        ).fetchall()
    return render_template("index.html", total=None, good=len(rows), output_dir=str(GOOD_OUTPUT_DIR), good_rows=rows)


if __name__ == "__main__":
    init_db()
    ensure_good_output_dir()
    app.run(host="0.0.0.0", port=5000, debug=True)
