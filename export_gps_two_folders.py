#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ExifTags
from staticmap import StaticMap, CircleMarker, Line

GPSINFO_TAG = 34853  # EXIF tag ID for "GPSInfo"


@dataclass
class PhotoPoint:
    path: Path
    dt: datetime
    lat: float
    lon: float


def _to_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        if isinstance(x, tuple) and len(x) == 2:
            num, den = x
            return float(num) / float(den) if den else float("nan")
        return float("nan")


def _dms_to_decimal(dms: Any, ref: str) -> float:
    deg = _to_float(dms[0])
    minute = _to_float(dms[1])
    sec = _to_float(dms[2])
    dec = deg + (minute / 60.0) + (sec / 3600.0)
    if ref in ("S", "W"):
        dec = -dec
    return dec


def _parse_exif_datetime(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def extract_gps_and_dt(image_path: Path) -> Tuple[Optional[datetime], Optional[float], Optional[float]]:
    with Image.open(image_path) as img:
        exif = img.getexif()
        if not exif:
            return None, None, None

        dt = _parse_exif_datetime(exif.get(36867)) or _parse_exif_datetime(exif.get(306))

        try:
            gps_ifd = exif.get_ifd(GPSINFO_TAG)
        except Exception:
            gps_ifd = None

        if not gps_ifd:
            return dt, None, None

        gps: Dict[str, Any] = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

        lat = gps.get("GPSLatitude")
        lat_ref = gps.get("GPSLatitudeRef")
        lon = gps.get("GPSLongitude")
        lon_ref = gps.get("GPSLongitudeRef")

        if not (lat and lat_ref and lon and lon_ref):
            return dt, None, None

        lat_dd = _dms_to_decimal(lat, str(lat_ref))
        lon_dd = _dms_to_decimal(lon, str(lon_ref))

        if math.isnan(lat_dd) or math.isnan(lon_dd):
            return dt, None, None

        return dt, lat_dd, lon_dd


def iter_images(folder: Path, recursive: bool) -> Iterable[Path]:
    exts = {".jpg", ".jpeg", ".JPG", ".JPEG"}

    def is_skipped(p: Path) -> bool:
        name = p.name.upper()
        # Skip panoramas and burst shots
        return name.startswith("PANO_") or "BURST" in name

    if recursive:
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix in exts and not is_skipped(p):
                yield p
    else:
        for p in folder.iterdir():
            if p.is_file() and p.suffix in exts and not is_skipped(p):
                yield p


def collect_points(folder: Path, recursive: bool) -> Tuple[List[PhotoPoint], int]:
    points: List[PhotoPoint] = []
    skipped = 0

    for img_path in iter_images(folder, recursive):
        try:
            dt, lat, lon = extract_gps_and_dt(img_path)
        except Exception:
            skipped += 1
            continue

        if dt is None or lat is None or lon is None:
            skipped += 1
            continue

        points.append(PhotoPoint(path=img_path, dt=dt, lat=lat, lon=lon))

    points.sort(key=lambda p: p.dt)
    return points, skipped


def safe_stem(p: Path) -> str:
    s = p.stem
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in s)


def render_map_image(
    out_path: Path,
    center_lat: float,
    center_lon: float,
    tour_coords: Optional[List[Tuple[float, float]]],
    point_coords: Tuple[float, float],
    width_px: int,
    height_px: int,
    zoom: int,
    draw_line: bool,
) -> None:
    m = StaticMap(width_px, height_px, url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

    # Full tour polyline (optional)
    if draw_line and tour_coords and len(tour_coords) >= 2:
        line = Line([(lon, lat) for lat, lon in tour_coords], "#1f77b4", 3)
        m.add_line(line)

    # Marker for this photo
    lat, lon = point_coords
    marker = CircleMarker((lon, lat), "#d62728", 12)
    m.add_marker(marker)

    img = m.render(zoom=zoom, center=(center_lon, center_lat))
    img.save(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export one static map PNG per selected photo; draw the tour line from another folder."
    )

    ap.add_argument("track_folder", nargs="?", default=".", help="Folder to scan for the tour track (default: .)")
    ap.add_argument("--track-recursive", action="store_true", help="Scan track folder recursively")

    ap.add_argument(
        "--photos",
        default=None,
        help="Folder containing the selected/good photos to render maps for (default: same as track_folder)",
    )
    ap.add_argument("--photos-recursive", action="store_true", help="Scan photos folder recursively")

    ap.add_argument("--out", default="maps_out", help="Output folder for map images")
    ap.add_argument("--width", type=int, default=700, help="Map image width in px")
    ap.add_argument("--height", type=int, default=1000, help="Map image height in px")
    ap.add_argument("--zoom", type=int, default=12, help="Map zoom level (typical: 10-15)")

    ap.add_argument(
        "--line",
        choices=["none", "full"],
        default="full",
        help="Draw tour line: none | full",
    )
    ap.add_argument(
        "--center",
        choices=["photo", "tour"],
        default="photo",
        help="Map centering: photo (center on each photo) | tour (fixed center of entire tour)",
    )

    args = ap.parse_args()

    track_folder = Path(args.track_folder).resolve()
    photos_folder = Path(args.photos).resolve() if args.photos else track_folder

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Tour points (blue line)
    tour_points, tour_skipped = collect_points(track_folder, args.track_recursive)
    print(f"Track folder: {track_folder}")
    print(f"Tour points (GPS+dt): {len(tour_points)}")
    print(f"Track skipped (no GPS/dt/unreadable): {tour_skipped}")

    if not tour_points and args.line == "full":
        print("No tour points found; cannot draw full tour line.")
        return 0

    tour_coords = [(p.lat, p.lon) for p in tour_points] if tour_points else None

    # Selected photos (red points)
    selected_points, selected_skipped = collect_points(photos_folder, args.photos_recursive)
    print(f"Photos folder: {photos_folder}")
    print(f"Selected photos (GPS+dt): {len(selected_points)}")
    print(f"Photos skipped (no GPS/dt/unreadable): {selected_skipped}")

    if not selected_points:
        print("No selected GPS+datetime photos found. Nothing to render.")
        return 0

    # Fixed tour center if requested
    if args.center == "tour" and tour_coords:
        avg_lat = sum(lat for lat, _ in tour_coords) / len(tour_coords)
        avg_lon = sum(lon for _, lon in tour_coords) / len(tour_coords)
        tour_center = (avg_lat, avg_lon)
    else:
        tour_center = None

    draw_line = args.line == "full"

    for i, p in enumerate(selected_points, start=1):
        if tour_center is not None:
            center_lat, center_lon = tour_center
        else:
            center_lat, center_lon = p.lat, p.lon

        ts = p.dt.strftime("%Y%m%d_%H%M%S")
        out_name = f"{ts}__{safe_stem(p.path)}__map_{args.width}x{args.height}.png"
        out_path = out_dir / out_name

        render_map_image(
            out_path=out_path,
            center_lat=center_lat,
            center_lon=center_lon,
            tour_coords=tour_coords,
            point_coords=(p.lat, p.lon),
            width_px=args.width,
            height_px=args.height,
            zoom=args.zoom,
            draw_line=draw_line,
        )

        if i % 25 == 0 or i == len(selected_points):
            print(f"Rendered {i}/{len(selected_points)}")

    print(f"Done. Output folder: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
