# OS AI Assistant — Windows desktop app

An installable Windows app that answers questions in Dari, Pashto and English about your own Excel and CSV files. It needs no Telegram account, no Google account, no server and no administrator rights.

## Install

1. Download **OS-AI-Assistant-Setup.exe** from the repository's **Releases** page, from the release named **OS AI Assistant (latest)**.
   - Before the first release exists, use the build from GitHub instead: go to **Actions → Desktop App**, open the latest successful run, and download **os-ai-assistant-windows-installer**.
2. Run it and click **Next** until it finishes.
   - Windows may show "Windows protected your PC", because the app is not yet code-signed. Click **More info → Run anyway**.
3. The app opens in your web browser. It runs only on your computer, at `127.0.0.1`.

## First use

1. **Settings:** choose an AI service, enter the model name and your API key, and click **Save**.
   - **Free option:** OpenRouter. Create a key at openrouter.ai, then under **Models** choose one whose name ends in `:free`.
   - **No key:** Ollama or LM Studio running on the same computer.
   - The key is encrypted for your Windows account (Windows DPAPI) and never shown again.
2. Click **Data folder**. This opens `Documents\OS AI Assistant Data`.
3. Copy your Excel (`.xlsx`) or CSV files into it. The first row of each sheet must hold the column names, and each sheet becomes a table.
4. Click **Refresh**, then ask a question such as "مجموع مصارف چقدر است؟".

## Daily use

- Start it from the **Start menu** or the **desktop shortcut**. If it is already running, the browser just opens again.
- **Quit** (top right) stops it.
- **Uninstall:** use Settings → Apps → OS AI Assistant. Your settings and data folder are kept.

## Privacy

- The numbers in your files are read and calculated on your computer.
- Only your question and the column names are sent to the AI service you chose. Cell values are never sent.
- Answers show the file and sheet they came from.

## Where things are

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\OS AI Assistant\` |
| Settings, encrypted key, audit log | `%LOCALAPPDATA%\OS AI Assistant\` |
| Your files | `Documents\OS AI Assistant Data\` |

## Build (developers)

`.github/workflows/desktop-app.yml` does the build. It runs the unit tests, builds `OS-AI-Assistant.exe` with PyInstaller, and smoke-tests it. It then compiles the per-user Inno Setup installer, installs it silently on a Windows runner, smoke-tests the installed app, uninstalls it, and publishes to the `desktop-latest` release on every push to `main`.

Local run from source:

```bash
python -m osai.desktop
```
