#!/usr/bin/env python3
"""
Bulk-import FIT files into Garmin Connect.

  * Workout FIT files (structured workouts)  -> added to your Workouts library
  * Activity FIT files (recorded runs, rides) -> uploaded as activities

Usage:
    pip install garminconnect fitparse
    python garmin_fit_import.py                      # import everything in ./fit_files
    python garmin_fit_import.py --dry-run            # preview, no upload
    python garmin_fit_import.py --only workout       # skip activities
    python garmin_fit_import.py /other/folder        # use a different folder
    python garmin_fit_import.py fit_files/run.fit    # import a single file

Login: set GARMIN_EMAIL / GARMIN_PASSWORD env vars, or you'll be prompted.
Tokens are cached in ~/.garminconnect so you only log in (and do MFA) once.
"""

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

from fitparse import FitFile

TOKEN_STORE = os.path.expanduser("~/.garminconnect")
DEFAULT_FOLDER = Path(__file__).resolve().parent / "fit_files"

# ---------------------------------------------------------------- Garmin IDs
SPORTS = {
    "running": 1, "cycling": 2, "swimming": 4, "training": 5,
    "fitness_equipment": 6, "walking": 3, "hiking": 3, "generic": 3,
}
STEP_TYPES = {  # FIT "intensity" -> Garmin step type
    "warmup": (1, "warmup"), "cooldown": (2, "cooldown"),
    "active": (3, "interval"), "interval": (3, "interval"),
    "recovery": (4, "recovery"), "rest": (5, "rest"),
}
COND = {
    "lap": (1, "lap.button"), "time": (2, "time"),
    "distance": (3, "distance"), "calories": (4, "calories"),
    "iterations": (7, "iterations"),
}
TARGET = {
    "none": (1, "no.target"), "power": (2, "power.zone"),
    "cadence": (3, "cadence"), "hr": (4, "heart.rate.zone"),
    "pace": (6, "pace.zone"),
}


def fit_type(path: Path) -> str:
    """Return 'workout', 'activity', or something else from the file_id message."""
    for msg in FitFile(str(path)).get_messages("file_id"):
        return str(msg.get_value("type"))
    return "unknown"


# ---------------------------------------------------- FIT workout -> Garmin JSON
def _end_condition(s: dict) -> dict:
    dt = s.get("duration_type")
    if dt == "time":
        cid, key = COND["time"]
        val = s.get("duration_time") or (s.get("duration_value") or 0) / 1000
    elif dt == "distance":
        cid, key = COND["distance"]
        val = s.get("duration_distance") or (s.get("duration_value") or 0) / 100
    elif dt == "calories":
        cid, key = COND["calories"]
        val = s.get("duration_calories") or s.get("duration_value")
    else:  # open / lap button / anything unsupported
        cid, key = COND["lap"]
        val = None
    out = {"endCondition": {"conditionTypeId": cid, "conditionTypeKey": key}}
    if val is not None:
        out["endConditionValue"] = float(val)
    return out


def _target(s: dict) -> dict:
    tt = s.get("target_type")
    lo_hi = None
    zone = None
    if tt == "speed":
        key = "pace"
        lo = s.get("custom_target_speed_low") or 0
        hi = s.get("custom_target_speed_high") or 0
        if lo or hi:
            lo_hi = (lo, hi)                       # m/s
    elif tt == "heart_rate":
        key = "hr"
        lo = s.get("custom_target_heart_rate_low") or 0
        hi = s.get("custom_target_heart_rate_high") or 0
        if lo or hi:
            # FIT: values > 100 are bpm + 100; <= 100 are % of max HR (not mapped)
            lo_hi = (lo - 100 if lo > 100 else lo, hi - 100 if hi > 100 else hi)
        else:
            zone = s.get("target_hr_zone") or s.get("target_value")
    elif tt == "power":
        key = "power"
        lo = s.get("custom_target_power_low") or 0
        hi = s.get("custom_target_power_high") or 0
        if lo or hi:
            lo_hi = (lo - 1000 if lo > 1000 else lo, hi - 1000 if hi > 1000 else hi)
        else:
            zone = s.get("target_power_zone") or s.get("target_value")
    elif tt == "cadence":
        key = "cadence"
        lo_hi = (s.get("custom_target_cadence_low") or 0,
                 s.get("custom_target_cadence_high") or 0)
    else:
        key = "none"

    tid, tkey = TARGET[key]
    out = {"targetType": {"workoutTargetTypeId": tid, "workoutTargetTypeKey": tkey}}
    if lo_hi:
        out["targetValueOne"], out["targetValueTwo"] = float(lo_hi[0]), float(lo_hi[1])
    elif zone:
        out["zoneNumber"] = int(zone)
    return out


def _exec_step(s: dict) -> dict:
    intensity = s.get("intensity") or "active"
    if isinstance(intensity, int):  # newer FIT enum values fitparse doesn't name
        intensity = {0: "active", 1: "rest", 2: "warmup", 3: "cooldown",
                     4: "recovery", 5: "interval"}.get(intensity, "active")
    sid, skey = STEP_TYPES.get(str(intensity), (3, "interval"))
    step = {
        "type": "ExecutableStepDTO",
        "stepType": {"stepTypeId": sid, "stepTypeKey": skey},
        **_end_condition(s),
        **_target(s),
    }
    if s.get("notes"):
        step["description"] = s["notes"]
    elif s.get("wkt_step_name"):
        step["description"] = s["wkt_step_name"]
    return step


def fit_workout_to_json(path: Path) -> dict:
    fit = FitFile(str(path))
    wkt = next(fit.get_messages("workout"), None)
    wkt = wkt.get_values() if wkt else {}
    raw = [m.get_values() for m in fit.get_messages("workout_step")]
    for i, s in enumerate(raw):
        s.setdefault("message_index", i)

    sport = str(wkt.get("sport") or "running")
    sport_id = SPORTS.get(sport, 3)
    sport_key = {1: "running", 2: "cycling", 3: "other", 4: "swimming",
                 5: "strength_training", 6: "cardio_training"}[sport_id]

    # FIT repeats are a step that says "go back to step N, X times".
    # Garmin JSON wants nested RepeatGroupDTOs, so fold them back in.
    steps: list[tuple[int, dict]] = []  # (fit message_index, garmin step)
    for s in raw:
        if s.get("duration_type") in ("repeat_until_steps_cmplt", "repetition"):
            back_to = s.get("duration_step") or s.get("duration_value") or 0
            reps = int(s.get("repeat_steps") or s.get("target_value") or 1)
            inner = [st for idx, st in steps if idx >= back_to]
            steps = [(idx, st) for idx, st in steps if idx < back_to]
            group = {
                "type": "RepeatGroupDTO",
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                "numberOfIterations": reps,
                "smartRepeat": False,
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
                "endConditionValue": float(reps),
                "workoutSteps": inner,
            }
            steps.append((back_to, group))
        else:
            steps.append((s["message_index"], _exec_step(s)))

    counter = [0]

    def number(lst):
        for st in lst:
            counter[0] += 1
            st["stepOrder"] = counter[0]
            if st["type"] == "RepeatGroupDTO":
                number(st["workoutSteps"])

    top = [st for _, st in steps]
    number(top)

    sport_obj = {"sportTypeId": sport_id, "sportTypeKey": sport_key}
    return {
        "workoutName": str(wkt.get("wkt_name") or path.stem)[:80],
        "sportType": sport_obj,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport_obj,
            "workoutSteps": top,
        }],
    }


# ------------------------------------------------------------------ Garmin login
def connect():
    from garminconnect import Garmin

    try:  # reuse cached tokens if we have them
        api = Garmin()
        api.login(TOKEN_STORE)
        return api
    except Exception:
        pass
    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ")
    password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")
    api = Garmin(email, password, prompt_mfa=lambda: input("MFA code: "))
    api.login(TOKEN_STORE)
    return api


# -------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Import FIT files into Garmin Connect")
    ap.add_argument("folder", type=Path, nargs="?", default=DEFAULT_FOLDER,
                    help="a .fit file, or a folder of .fit files searched recursively "
                         "(default: fit_files next to this script)")
    ap.add_argument("--only", choices=["workout", "activity"], help="import only this kind")
    ap.add_argument("--dry-run", action="store_true", help="parse and show, don't upload")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between uploads")
    args = ap.parse_args()

    target = Path(os.path.expandvars(os.path.expanduser(str(args.folder))))
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(p for p in target.rglob("*") if p.suffix.lower() == ".fit")
    elif target == DEFAULT_FOLDER:
        target.mkdir(parents=True, exist_ok=True)
        sys.exit(f"Created {target} - put your .fit files in it and run again.")
    else:
        sys.exit(f"Not found: {target}")
    if not files:
        sys.exit(f"No .fit files found in {target}")
    print(f"Found {len(files)} FIT file(s)\n")

    api = None if args.dry_run else connect()
    ok = fail = skipped = 0

    for p in files:
        try:
            kind = fit_type(p)
        except Exception as e:
            print(f"✗ {p.name}: can't read FIT file ({e})")
            fail += 1
            continue
        if kind not in ("workout", "activity") or (args.only and kind != args.only):
            print(f"- {p.name}: skipped ({kind})")
            skipped += 1
            continue

        try:
            if kind == "workout":
                payload = fit_workout_to_json(p)
                if args.dry_run:
                    print(f"• {p.name} -> workout '{payload['workoutName']}'")
                    print(json.dumps(payload, indent=2))
                else:
                    res = api.upload_workout(payload)
                    print(f"✓ {p.name} -> workout '{payload['workoutName']}' "
                          f"(id {res.get('workoutId')})")
            else:
                if args.dry_run:
                    print(f"• {p.name} -> activity upload")
                else:
                    api.upload_activity(str(p))
                    print(f"✓ {p.name} -> activity uploaded")
            ok += 1
        except Exception as e:
            msg = str(e)
            if "409" in msg or "Duplicate" in msg:
                print(f"- {p.name}: already in Garmin Connect")
                skipped += 1
            else:
                print(f"✗ {p.name}: {msg}")
                fail += 1
        if not args.dry_run:
            time.sleep(args.delay)

    print(f"\nDone: {ok} imported, {skipped} skipped, {fail} failed")


if __name__ == "__main__":
    main()