"""RADAR 0.8 background entrypoint (called by launchd at 08:00 / 20:00).

Legacy Watch code is retained elsewhere.  The default scheduled surface now
runs every enabled Radar and persists an honest Run Report even on failures.
"""
import sys
from datetime import datetime


def main() -> None:
    from .radar import list_radars, run_radar
    results = []
    for radar in list_radars():
        if not radar.get("enabled"):
            continue
        try:
            summary = run_radar(radar["id"])
            results.append({"radar": radar["name"], **summary})
        except Exception as exc:
            results.append({"radar": radar["name"], "error": str(exc)})
    print(f"[radar {datetime.now().isoformat()}] {results}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 2)[0])
    main()
