# NextShelf for Android

A **Trusted Web Activity**: the app opens https://nextshelfapp.com full screen in the phone's
Chrome, with no browser bar. Same site, same login, and every web change is in the app as soon
as Render deploys it.

- Package: `com.nextshelf.app` · opens `/app#today` · library `androidbrowserhelper` 2.7.4
- Built by GitHub Actions (`.github/workflows/android.yml`) on every push that touches `android/`,
  or by hand from the repo's **Actions** tab → *Android app* → *Run workflow*.
- Each run attaches `NextShelf-debug.apk` (install on a phone to test) and `NextShelf-release.aab`
  (for Google Play).

## The address bar check

Android only hides the browser bar if `https://nextshelfapp.com/.well-known/assetlinks.json`
lists the app's signing key. The site serves that file (`app.py`, `assetlinks()`):
- the committed **debug** key is always listed, so test builds open full screen;
- for **Google Play**, copy the SHA-256 fingerprints from Play Console → *Test and release* →
  *App integrity* (the **app signing key**, plus the **upload key**) into the Render env var
  `ANDROID_CERT_SHA256`, comma-separated. No code change.

## Going to Google Play

1. Google Play developer account (US$25 once).
2. Create an upload key once (keep it safe, never commit it):
   `keytool -genkeypair -v -keystore upload.jks -alias upload -keyalg RSA -keysize 2048 -validity 10000`
3. Add the four GitHub secrets listed at the top of the workflow, then re-run it: the `.aab`
   comes out signed.
4. Play Console → create the app → upload the `.aab` to a testing track. New personal accounts
   must run a closed test before going to production.
5. Put the fingerprints from step 4's *App integrity* page into `ANDROID_CERT_SHA256` on Render.
