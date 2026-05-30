"""
garmin_sync.py
Runs every morning via GitHub Actions.
Pulls data from Garmin Connect and saves to Firebase Firestore.
Data appears automatically on the F&F Tracker home screen.
"""

import os
import json
import datetime
from garminconnect import Garmin
import firebase_admin
from firebase_admin import credentials, firestore

# ── CONFIG ────────────────────────────────────────────────────
GARMIN_EMAIL    = os.environ['GARMIN_EMAIL']
GARMIN_PASSWORD = os.environ['GARMIN_PASSWORD']
SERVICE_ACCOUNT = os.environ['FIREBASE_SERVICE_ACCOUNT']
TODAY           = datetime.date.today().isoformat()

# ── FIREBASE INIT ─────────────────────────────────────────────
sa_dict = json.loads(SERVICE_ACCOUNT)
cred    = credentials.Certificate(sa_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# ── GARMIN CONNECT ────────────────────────────────────────────
print(f"Connecting to Garmin Connect for {GARMIN_EMAIL}...")
client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
client.login()
print("Connected successfully.")

# ── FETCH DATA ────────────────────────────────────────────────
data = {}

# 1. Resting Heart Rate
try:
    rhr_data = client.get_rhr_day(TODAY)
    if rhr_data and 'restingHeartRate' in rhr_data:
        data['restingHR'] = rhr_data['restingHeartRate']
        print(f"RHR: {data['restingHR']} bpm")
    else:
        # Try stats
        stats = client.get_stats(TODAY)
        if stats and 'restingHeartRate' in stats:
            data['restingHR'] = stats['restingHeartRate']
            print(f"RHR (from stats): {data['restingHR']} bpm")
except Exception as e:
    print(f"RHR fetch failed: {e}")

# 2. Sleep
try:
    sleep = client.get_sleep_data(TODAY)
    if sleep and 'dailySleepDTO' in sleep:
        sleep_seconds = sleep['dailySleepDTO'].get('sleepTimeSeconds', 0)
        data['sleepHours'] = round(sleep_seconds / 3600, 1)
        data['sleepScore'] = sleep['dailySleepDTO'].get('sleepScore', None)
        print(f"Sleep: {data['sleepHours']} hours, score: {data.get('sleepScore')}")
except Exception as e:
    print(f"Sleep fetch failed: {e}")

# 3. Body Battery
try:
    body_battery = client.get_body_battery(TODAY)
    if body_battery and len(body_battery) > 0:
        bb_readings = body_battery[0].get('bodyBatteryValuesArray', [])
        if bb_readings:
            # Most recent reading at sync time — last entry in the array
            # Each entry is [timestamp, value]; filter out any null values
            valid = [r for r in bb_readings if r and len(r) > 1 and r[1] is not None]
            if valid:
                data['bodyBattery'] = valid[-1][1]
                print(f"Body Battery (latest at sync): {data['bodyBattery']}")
except Exception as e:
    print(f"Body Battery fetch failed: {e}")

# 4. Steps
try:
    steps_data = client.get_steps_data(TODAY)
    if steps_data:
        total_steps = sum(s.get('steps', 0) for s in steps_data if s.get('steps'))
        data['steps'] = total_steps
        print(f"Steps: {data['steps']}")
except Exception as e:
    print(f"Steps fetch failed: {e}")

# 5. Last Activity / Run Zone Data
try:
    activities = client.get_activities(0, 1)  # most recent activity
    if activities and len(activities) > 0:
        latest = activities[0]
        activity_type = latest.get('activityType', {}).get('typeKey', '')
        print(f"Latest activity: {latest.get('activityName')} ({activity_type})")

        # Only pull zone data for runs
        if 'running' in activity_type.lower() or 'run' in activity_type.lower():
            activity_id = latest['activityId']
            details = client.get_activity(activity_id)

            # Heart rate zones
            hr_zones = details.get('heartRateZones', [])
            if hr_zones and len(hr_zones) >= 5:
                # Convert seconds to minutes
                data['z1'] = round(hr_zones[0].get('secsInZone', 0) / 60)
                data['z2'] = round(hr_zones[1].get('secsInZone', 0) / 60)
                data['z3'] = round(hr_zones[2].get('secsInZone', 0) / 60)
                data['z4'] = round(hr_zones[3].get('secsInZone', 0) / 60)
                data['z5'] = round(hr_zones[4].get('secsInZone', 0) / 60)
                print(f"Zones (min): Z1={data['z1']} Z2={data['z2']} Z3={data['z3']} Z4={data['z4']} Z5={data['z5']}")

            # Run summary
            data['lastRunName']     = latest.get('activityName', 'Run')
            data['lastRunDistance'] = round(latest.get('distance', 0) / 1000, 2)  # km
            data['lastRunDate']     = latest.get('startTimeLocal', TODAY)[:10]
            print(f"Last run: {data['lastRunDistance']}km on {data['lastRunDate']}")

except Exception as e:
    print(f"Activity fetch failed: {e}")

# ── SAVE TO FIREBASE ──────────────────────────────────────────
data['date']      = TODAY
data['updatedAt'] = datetime.datetime.now().isoformat()
data['source']    = 'garmin_auto_sync'

# Merge with any existing data for today (don't overwrite manual entries)
existing_ref = db.collection('readiness').document(TODAY)
existing     = existing_ref.get()
if existing.exists:
    existing_data = existing.to_dict()
    # Only update fields we successfully fetched
    merged = {**existing_data, **{k: v for k, v in data.items() if v is not None}}
    existing_ref.set(merged)
    print(f"Updated existing readiness document for {TODAY}")
else:
    existing_ref.set(data)
    print(f"Created new readiness document for {TODAY}")

print("\n✅ Garmin sync complete!")
print(json.dumps(data, indent=2, default=str))
