# Setting up the Savvy Android app

The phone app talks to a small server on your PC (`src/app_server.py`). The tray
starts it for you as "App server", next to the Signal bot and the scheduler. Its
log is `app_server.log` in the logs folder (tray → Open logs folder).

You do these steps once.

## 1. Tailscale on the PC and the phone

The app reaches the PC over Tailscale, which works from anywhere (home Wi-Fi,
mobile data, campus) and encrypts the connection. Nothing is opened to the
internet.

1. Install Tailscale on the PC (<https://tailscale.com/download>) and sign in.
2. Install the Tailscale app on the phone and sign in with the same account.
3. Find the PC's Tailscale address: run `tailscale ip -4` on the PC (it looks
   like `100.x.y.z`), or use its MagicDNS name from the Tailscale admin page
   (like `my-pc.tailnet-name.ts.net`).

The address you'll type into the app is `http://<that address>:8765`, for
example `http://100.101.102.103:8765`.

## 2. Let the server listen on Tailscale

By default the server only listens on `127.0.0.1`, which the phone can't reach.
Add these to `credentials/config.json`:

```json
"app_api_host": "100.101.102.103",
"app_api_port": 8765
```

Use the PC's own Tailscale IP from step 1. That way the server answers only
over Tailscale, not on your home or campus network. `"0.0.0.0"` also works
(every network the PC is on), but then anyone on the same Wi-Fi can reach the
pairing screen, so prefer the Tailscale IP. Every request except pairing needs a
paired phone's token either way.

Then restart services from the tray (or restart the tray). Check from the
phone's browser: `http://<address>:8765/v1/ping` should show
`{"ok":true,"name":"Savvy","version":"1"}`.

If the PC's firewall blocks it, allow TCP port 8765 on the `tailscale0`
interface (Linux: `sudo ufw allow in on tailscale0 to any port 8765 proto tcp`).

## 3. Google sign-in on the phone

The app signs in to Google natively and hands the PC a one-time code; the PC
swaps it for a login and saves it just like a browser sign-in. That needs two
more OAuth clients in the same Google Cloud project as your existing
`credentials/credentials.json`
(<https://console.cloud.google.com/apis/credentials>).

### 3a. The Web client (used by the PC)

1. Create credentials → OAuth client ID → Application type **Web application**.
   Name it "Savvy app server". No redirect URIs or origins are needed.
2. Download the JSON and save it as `credentials/web_client.json`. It must have a
   top-level `"web"` key. Keep it private: it holds the client secret.

### 3b. The Android client (identifies the app)

1. Create credentials → OAuth client ID → Application type **Android**.
2. Package name: `com.spirolife.savvy`.
3. SHA-1 certificate fingerprint: the fingerprint of the key the app is signed
   with. For a debug build:

   ```sh
   keytool -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey \
       -storepass android -keypass android
   ```

   or run `android/build.sh signingReport` and copy the `SHA1` line for the
   `debug` variant.

   **The fingerprint must stay the same between builds.** `android/build.sh`
   builds in a throwaway container, and Android's tools make a new random debug
   keystore in the container's home folder whenever there isn't one, so each
   build can get a different SHA-1 and Google sign-in then fails with a
   "developer error". Use a fixed keystore that lives in the project instead
   (the app build is set up for this; see "Installing the app" below), and
   register *that* keystore's SHA-1 here. Keep the keystore out of git.

Nothing is downloaded for the Android client; Google matches the app by package
name and fingerprint.

### 3c. Test users

The project's OAuth consent screen is in "Testing" mode, so only listed users can
sign in. OAuth consent screen → Audience (or Test users) → add **both** of your
Google accounts (personal and school), if they aren't there already.

## 4. Pair the phone

1. On the PC: tray → **Pair phone…**. (Or run
   `python src/app_server.py --pair` from the project folder.) You get a
   6-digit code. It works once, for 10 minutes.
2. In the app, enter the server address from step 1 and the code.

Five wrong codes in 10 minutes cancel the code; make a new one.

To unpair every phone (a lost phone, say): `python src/app_server.py --revoke-all`,
then pair again on the phones you still have.

## Installing the app

The app is published as GitHub Releases of this repo, and
[Obtainium](https://github.com/ImranR98/Obtainium) on the phone installs it from
there and offers each new release as an update.

### On the phone (once)

1. Install Obtainium from its GitHub releases page or F-Droid.
2. In Obtainium: **Add App**, paste `https://github.com/Spirolife/Savvy`, tap
   **Add**, then **Install**. Allow Obtainium to install apps when Android asks.
   If Savvy is already installed from an APK, this replaces it and keeps your
   pairing.
3. From then on, Obtainium shows **Update** when there's a new release (it checks
   in the background and notifies you, or pull down to check now).

### Publishing an update (on the PC)

Commit the change, then:

```sh
android/release.sh                  # notes list the commits since the last release
android/release.sh "What changed"   # or write the notes yourself
```

It runs the app's tests, builds the APK, tags the commit `v0.1.<commit count>`,
pushes just that tag, and uploads `Savvy-<version>.apk` to a new release. The
version comes from the commit count, so it goes up with every commit and the
phone accepts it as an update. The release is public, like the repo; the APK
holds no keys and only works after pairing with your PC.

### Building by hand

`android/build.sh` builds `android/app/build/outputs/apk/debug/app-debug.apk` in a
container (no JDK or Android SDK needed on the PC); `android/build.sh
testDebugUnitTest` runs the tests. Install it with `adb install -r <apk>` or by
copying it to the phone.

### The signing key

Every build is signed with `android/.android-home/debug.keystore` (gitignored).
**Back it up.** Google sign-in is registered to its SHA-1 (step 3b above), and
Android only installs an update signed with the same key as the installed app.
If the folder is missing, a build makes a new key, and then sign-in fails and the
phone refuses the update until you uninstall. `release.sh` refuses to run
without it.

## Notifications (Firebase)

Everything Savvy used to text you on Signal (morning and evening prompts,
check-ins, the weekly review, reminders, meal plans, rest-day warnings, memory
checks, service and Google sign-in alerts) now goes through `src/notify.py`,
which:

1. keeps it in `memory/notifications.db` (the last 500), where the app picks it up;
2. pushes it to the phone with Firebase Cloud Messaging, if you set that up below;
3. still sends it on Signal while **Signal fallback** is on (app → Settings, or
   `"signal_fallback": false` in `credentials/config.json`). It's on by default,
   so nothing changes until you've seen the app's notifications arrive and turn
   it off.

The quiet hours and the daily limit apply to every channel, exactly as before.

**Without Firebase** the app checks the PC for new notifications every 15
minutes (Android may stretch that when the phone is idle) and every time you
open it. That's enough for check-ins, but a reminder can arrive up to ~15
minutes late. Firebase makes them arrive within seconds, and it's free.

Android 13 and later ask once, after pairing, whether Savvy may show
notifications. If you said no, turn them on in Android Settings → Apps → Savvy →
Notifications. There you can also tune the three channels: **Check-ins and
plans**, **Reminders** (pops up on screen), and **Alerts**. Check-ins have a
**Reply** button: your answer goes to Savvy as a chat message and its reply
appears in the same notification. (Anything that needs your OK, like sending an
email, is declined from there; open the app to approve it.)

### Setting up Firebase (optional)

1. Go to <https://console.firebase.google.com>, **Add project**, and pick your
   existing Google Cloud project (the one with `credentials/credentials.json`),
   or create a new one. Google Analytics isn't needed.
2. **The phone side.** In the project: Add app → Android. Package name
   `com.spirolife.savvy`; the nickname and SHA-1 are optional. Download
   `google-services.json` and save it as `android/app/google-services.json`.
   Rebuild and reinstall the app (the build turns on Firebase only when that
   file exists). Keep it out of git.
3. **The PC side.** Project settings (gear icon) → Service accounts →
   **Generate new private key**. Save the downloaded file as
   `credentials/firebase_service_account.json` and make it private
   (`chmod 600 credentials/firebase_service_account.json`). This key can act on
   the whole Firebase project, so treat it like a password.
4. Restart services from the tray. Open the app once; it registers for push by
   itself (its token is kept in `credentials/app_fcm_tokens.json`).
5. Test it: `.venv/bin/python src/liveness.py test phone`. The phone should show
   "Savvy liveness test" within a few seconds (and Signal gets it too while the
   fallback is on).

If push stops working, the app still polls, so nothing is lost. The scheduler
log says "FCM unavailable" or "FCM error" when the PC side is the problem.
Unpairing a phone stops pushes to it.
