# PestCare Agent — Android app

A thin native wrapper that loads the live PestCare CRM web app in a full-screen
WebView. Because it loads the site from the server, **every change deployed to
the CRM appears instantly in the app — no rebuild and no reinstall.**

Built for field agents to start visits and fill in reports easily, with camera
photo upload, GPS, file attachments, pull-to-refresh and back-button support.

## Getting the APK (cloud build — no Android Studio needed)

1. Push this repo to GitHub (already the case).
2. On GitHub go to **Actions → "Build Android APK" → Run workflow** (or it runs
   automatically when anything under `android/` changes).
3. When it finishes, download **`pestcare-agent-apk`** from the run's *Artifacts*,
   or grab `pestcare-agent.apk` from the **`android-latest`** prerelease under
   *Releases*.

## Installing on an agent's phone (sideload)

1. Copy `pestcare-agent.apk` to the phone (or open the Releases link on it).
2. Open the file; Android will ask to allow installing from this source — allow it.
3. Open **PestCare Agent** and log in.

> It is a *debug-signed* APK, which is fine for sideloading. For Play Store
> distribution you'd switch to a release keystore.

## Changing the server address later (no reinstall)

The app loads `http://95.216.189.8:8000` by default. To point it somewhere else
(e.g. an HTTPS domain), **long-press the top-left corner** of the screen, enter
the new URL and Save. The setting is remembered on the device.

## Notes / recommendations

- **HTTPS is recommended.** Over plain HTTP, logins are not encrypted and the
  in-browser camera (`getUserMedia`) and offline service worker are unavailable.
  Photo upload still works (it uses the native file/camera picker). Point a domain
  at the server and install free HTTPS (Caddy/Let's Encrypt), then update the URL
  in the app — no reinstall needed.
- To build locally instead: open the `android/` folder in Android Studio and run
  **Build → Build APK**.

## Project layout

```
android/
  app/src/main/AndroidManifest.xml      permissions + activity + file provider
  app/src/main/java/.../MainActivity.java  WebView wrapper logic
  app/src/main/res/                      layout, theme, icon, file paths
  build.gradle, settings.gradle          Gradle config (AGP 8.5, Gradle 8.7)
```
