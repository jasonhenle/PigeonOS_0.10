Pi setup — metadata and TMDb artwork
====================================

Pigeon on Raspberry Pi needs the same secrets and pairing data as on your Mac.
The Pi install does NOT copy these automatically unless you put files here.

Option A — copy from Mac (recommended)
--------------------------------------
On your Mac (where Pigeon already works), copy the whole state folder to the Pi:

  scp -r ~/.pigeon_0_6 pi@YOUR_PI_IP:~/.pigeon_0_6

Replace pi@YOUR_PI_IP with your Pi user and address.

This folder includes:
  tmdb_api_key or tmdb_read_token  — TMDb artwork search
  pyatv_credentials                — Apple TV pairing
  state.json                       — saved locations and devices

Option B — place files here before Install-Pigeon
-------------------------------------------------
Put one-line secret files in this installer/setup/ folder (same names as above), then run
Install-Pigeon (in the parent installer/ folder). The installer copies them into ~/.pigeon_0_6 on first install only.

  installer/setup/tmdb_api_key      — from https://www.themoviedb.org/settings/api
  installer/setup/tmdb_read_token   — optional JWT read token (instead of api key)
  installer/setup/pyatv_credentials — optional; usually easier to pair on the Pi in-app

Option C — configure on the Pi
------------------------------
1. TMDb: create ~/.pigeon_0_6/tmdb_api_key with your API key on one line.
2. Apple TV: in Pigeon, open Find device, pair your Apple TV (pyatv).
3. Re-pairing on the Pi is required unless you copy pyatv_credentials from the Mac.

Logs
----
After launch, check ~/.pigeon_0_6/pigeon.log on the Pi for metadata/TMDb errors.

Manual GitHub update (latin-1 / Updates button error)
-----------------------------------------------------
If in-app Updates fails with a latin-1 or U+202F encoding error, run once on the Pi:

  rm -f ~/.pigeon_0_6/github_update_token
  curl -fsSL -o /tmp/pi_update.sh \
    "https://raw.githubusercontent.com/jasonhenle/PigeonOS_0.10/main/PigeonOS_0.11_Development/Pigeon/installer/pi_update_from_github.sh"
  bash /tmp/pi_update.sh

If Pigeon is already installed, you can also run:
  bash ~/Pigeon_*/installer/pi_update_from_github.sh
