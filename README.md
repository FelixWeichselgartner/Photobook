# Commands

uv run python export_gps_img.py . --recursive --width 400 --height 1200 --zoom 5 --line full --center tour

uv run python export_gps_img.py . --recursive --width 400 --height 1200 --zoom 12 --line full --center photo

uv run python export_gps_img.py "path" --recursive --width 400 --height 1200 --zoom 12 --line full --center photo

python export_gps_two_folders.py "path" --track-recursive --photos ./path --photos-recursive --width 400 --height 1200 --zoom 12 --line full --center photo

uv run .\photobook_gps.py .\config_gps.json  
