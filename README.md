# Vidmahi

Vidmahi is a responsive, installable web assistant with optional OpenRouter/OpenAI-powered chat and an optional laptop companion. Core commands work without an AI API key. The same web app works in desktop browsers and Android Chrome. The laptop companion runs locally and can receive confirmed, allow-listed actions from a paired web app.

## Features

- ChatGPT-inspired responsive chat interface and installable PWA.
- Typed chat, browser speech input/output, and wake phrases: **“hey mahi,” “hey kittu,” “hey vidmahi,” “keettuu,” and “hello mahi.”**
- Optional OpenRouter (preferred) or OpenAI chat responses, time/date, weather, riddles, web searches, YouTube, WhatsApp, and ChatGPT.
- Optional laptop agent for opening supported apps/URLs, browser shortcuts, battery status, screenshots, locking, sleep, shutdown, and restart.
- Every laptop action is queued and requires an explicit confirmation in the web app. The laptop agent only accepts known action types; it never runs model-generated shell commands.
- Separate access and laptop-agent tokens.

## Local setup (Windows)

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment, then install the web requirements:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and replace the three token/secret placeholders with long, unique random values. For open-ended AI chat, set `OPENROUTER_API_KEY` and optionally `OPENROUTER_MODEL` (default `openai/gpt-4.1`) on the server. `OPENAI_API_KEY` remains an optional fallback. Without either provider, built-in time/date, weather, riddles, web searches, and confirmed laptop actions remain available. Never put provider keys in the Android app or share/commit `.env`.
4. Start the server:

   ```powershell
   python main.py
   ```

5. Open `http://127.0.0.1:5000` and enter `VIDMAHI_ACCESS_TOKEN`.
6. To enable laptop actions, install the companion dependencies and start the agent in a second terminal:

   ```powershell
   pip install -r requirements-agent.txt
   python agent.py
   ```

The browser will ask permission before using the microphone. Browser speech recognition requires a supported browser; Chrome and Edge are recommended. Typed chat remains available if speech recognition is unavailable. Wake-word detection is active only while the microphone button is on, as required by browser permission and battery restrictions.

## Render deployment

Deploy this repository using `render.yaml`. AI chat is optional; set `OPENROUTER_API_KEY` in the Render dashboard to enable the preferred provider, or `OPENAI_API_KEY` for the fallback. Render generates the other secrets and stores the SQLite database on its persistent disk. For remote laptop actions, copy the generated `VIDMAHI_AGENT_TOKEN` into the laptop's local `.env`, set `VIDMAHI_SERVER_URL` to the deployed HTTPS URL, install `requirements-agent.txt`, and run `python agent.py`.

Copy the generated `VIDMAHI_ACCESS_TOKEN` from Render's environment settings into the browser when prompted. Keep both generated tokens private. Use HTTPS for deployed use; browser microphone access is restricted to secure contexts.

## Laptop agent limits

- App-specific paths can be overridden using `CHROME_PATH` and `VSCODE_PATH`.
- Lock, sleep, shutdown, and restart are implemented for Windows; other operating systems report that those actions are unsupported.
- Screenshot files are saved locally to `Pictures\Vidmahi-screenshot.png`; they are not uploaded to the cloud.
- Keep the laptop agent running and online to receive remote requests. It polls the configured server over HTTPS; the cloud server does not directly connect to the laptop.
- Review every confirmation before accepting it. Revoking the agent token in the server configuration disables remote commands.

## Development checks

Run the focused Python tests with:

```powershell
python -m unittest discover -s tests -v
```

## Native Android application

The `android` directory contains the Kotlin and Jetpack Compose application. It is an additional client: the Flask service, PWA, and optional Windows laptop agent remain separate and continue to work. Android-only controls run on the phone and do not use the laptop agent.

The Android build script locates the Android SDK and Android Studio's bundled JDK when they are not on `PATH`. From the project root, build a debug APK with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\android\scripts\build-android.ps1
```

The APK is written to `android\app\build\outputs\apk\debug\app-debug.apk`. Open the `android` directory in Android Studio to run or install it. A USB-connected, developer-authorized Android device is required for device testing; no emulator or connected device is assumed by the build.

The Android assistant does not require this Flask service for phone actions. For optional intelligent chat without maintaining a server, enter your own OpenRouter API key in the app's Settings and choose a model (default `openai/gpt-4.1`). The key is encrypted at rest using Android Keystore and sent directly from the phone to OpenRouter over HTTPS when you chat. It is not bundled into the APK, but a key stored on a phone cannot be treated as secret against a rooted or compromised device; OpenRouter receives the prompts and its usage may be billable. You can instead use the optional self-hosted Flask server by entering its HTTPS URL and `VIDMAHI_ACCESS_TOKEN` in the same settings. Leave both server fields blank when not using that option.

An on-device language model could remove the external AI dependency, but no model/runtime is bundled: models require substantial storage and memory, and their speed, language support, and quality depend on the phone. Android device actions, private notes, and settings remain on-device regardless of AI configuration.

Android limits background microphone use to a user-started foreground service and the operating system's permissions and battery policies. Wake listening is opt-in and only starts when Android provides an on-device recognizer; it is best-effort, not a dedicated always-on keyword-spotting engine, and the wake audio stays on-device. Use the in-app microphone button when local recognition is unavailable; Android's selected speech service may use network recognition for tap-to-speak. Notification reading and notification quick replies require user-granted Notification access and can expose only content/actions Android publishes. Calls open the system dialer for a final user action. Screen capture and recording require confirmation plus Android's MediaProjection prompt each session; recordings contain video only. Storage cleanup hands off to Android Storage settings so the user reviews and chooses items; Vidmahi does not delete other apps' files.

See [android/FEATURE_STATUS.md](./android/FEATURE_STATUS.md) for the implemented, limited, and unimplemented feature checklist.
