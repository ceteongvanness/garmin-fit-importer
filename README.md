# garmin-fit-importer

Bulk-import a folder of `.fit` files into **Garmin Connect** with one command.

- **Workout FIT files** (structured sessions with steps) are converted and added to your **Workouts library**, ready to send to your watch or put on your calendar.
- **Activity FIT files** (recorded runs, rides, swims) are uploaded as **activities**.

The script reads each file's header to see what kind it is, so you can mix both kinds in one folder.

## Features

- Recursive folder scan for `.fit` files
- Detects workout and activity files automatically
- Converts FIT workout steps to Garmin Connect's workout format, including:
  - Warm-up, interval, recovery, rest and cool-down steps
  - Time, distance, calorie and lap-button step durations
  - Repeat blocks (e.g. 5 × 1 km)
  - Pace, heart rate (bpm or zone), power and cadence targets
- `--dry-run` mode prints the converted workout JSON without uploading anything
- Caches login tokens, so you sign in (and do MFA) only once
- Skips duplicates already in Garmin Connect

## Requirements

- Python 3.10+
- A Garmin Connect account

```bash
pip install garminconnect fitparse
```

## Usage

```bash
# Preview: parse everything, upload nothing
python garmin_fit_import.py ~/fit_files --dry-run

# Import all workouts and activities
python garmin_fit_import.py ~/fit_files

# Import only workouts (or only activities)
python garmin_fit_import.py ~/fit_files --only workout
python garmin_fit_import.py ~/fit_files --only activity

# Wait longer between uploads (default 1 second)
python garmin_fit_import.py ~/fit_files --delay 3
```

### Options

| Option | Description |
|---|---|
| `folder` | Folder containing `.fit` files (searched recursively) |
| `--dry-run` | Parse and show results without uploading |
| `--only {workout,activity}` | Import only one kind of file |
| `--delay SECONDS` | Pause between uploads (default `1.0`) |

## Authentication

On the first run you'll be asked for your Garmin email, password and MFA code (if MFA is enabled). For unattended runs you can set environment variables instead:

```bash
export GARMIN_EMAIL="you@example.com"
export GARMIN_PASSWORD="your-password"
```

After a successful login, tokens are saved to `~/.garminconnect` and reused on later runs. Delete that folder to sign out.

> Never commit your credentials or the `~/.garminconnect` token folder to Git.

## Example output

```
Found 3 FIT file(s)

✓ intervals.fit -> workout '5x1k intervals' (id 123456789)
✓ tempo.fit -> workout 'Tempo 8k' (id 123456790)
- morning_run.fit: already in Garmin Connect

Done: 2 imported, 1 skipped, 0 failed
```

## Known limitations

- Uses the unofficial [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library. If Garmin changes its login or API, run `pip install -U garminconnect`.
- Heart-rate targets given as a **percentage of max HR** are not converted. Targets in bpm or HR zones work.
- Multi-sport workouts are imported as a single segment.
- Strength workouts with exercise names, sets and weights are not mapped yet.

## Tip: watch only, no code

If you only need the workouts on your watch and not in Garmin Connect, connect the watch by USB and copy the workout `.fit` files into `GARMIN/NewFiles`. The watch imports them when you disconnect it.

## Disclaimer

This project is not affiliated with or endorsed by Garmin. Use it at your own risk, and keep upload rates reasonable.

## License

MIT
