# V1 bot setup guide (owner)

Follow these steps once. Every secret goes **only** into files under `/etc/osai/` on the server. Never paste a secret into chat, email or GitHub.

Total time: about 1 hour. Expected cost: the server only (about $10–20/month).

## Step 1 — Rent a server (10 min)

1. Create an account at a cheap cloud host, such as Hetzner, DigitalOcean or Contabo.
2. Create a server with **Ubuntu 24.04**, **4 GB RAM** and the smallest disk offered.
3. Note its IP address. Log in from your computer:
   ```bash
   ssh root@YOUR_SERVER_IP
   ```
4. Install Docker and Git:
   ```bash
   apt update && apt install -y docker.io docker-compose-v2 git
   ```

## Step 2 — Create the Telegram bot (5 min)

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Choose a name (for example "Company Data Assistant") and a username ending in `bot`.
3. BotFather replies with a **token** that looks like `1234567890:AA...`. Keep it private; you'll use it in Step 5.

## Step 3 — Connect the AI model (5 min)

The bot works with many providers (see `deploy/bot/bot.env.example`). The default is **OpenRouter free models**:

1. Sign up at **openrouter.ai**.
2. Go to **Keys** and create an API key. Keep it private.
3. **Don't add credit.** With no credit, nothing can ever be charged.
4. Go to **Models**, filter for **free** models, and pick one that supports tools/JSON (for example a Llama, Qwen or Gemini free model). Copy its exact name, which ends in `:free`.
5. In OpenRouter's **privacy settings**, check whether the free model's provider may store prompts. Real company numbers pass through it (see `V1_PLAN.md` D-1).

To use a different provider later, change the settings in `/etc/osai/bot.env` (Step 5). No code changes are needed.

## Step 4 — Give the bot read-only access to Google Sheets (20 min)

1. Open **console.cloud.google.com** and create a project named "osai-bot".
2. Go to **APIs & Services → Library** and enable the **Google Drive API** and the **Google Sheets API**.
3. Go to **IAM & Admin → Service Accounts → Create service account**. Name it "osai-reader" and grant it no roles.
4. Open the new service account, go to **Keys → Add key → JSON**, and download the file.
5. Copy the service account's email (it looks like `osai-reader@osai-bot.iam.gserviceaccount.com`).
6. In Google Drive, right-click each folder the bot may read, choose **Share**, paste that email, and set it to **Viewer**.
7. Upload the JSON key to the server from your computer:
   ```bash
   ssh root@YOUR_SERVER_IP "mkdir -p /etc/osai && chmod 700 /etc/osai"
   scp downloaded-key.json root@YOUR_SERVER_IP:/etc/osai/google-service-account.json
   ```
8. Finally, delete the downloaded key from your computer.

**Native Google Sheets work best.** Plain `.xlsx` files in Drive also work.

## Step 5 — Configure and start (15 min)

On the server:

```bash
cd /opt && git clone https://github.com/Hashmatullahzaher/AI-OP-Tele-bot.git osai && cd osai
cp deploy/bot/bot.env.example /etc/osai/bot.env
cp deploy/bot/catalog.example.json /etc/osai/catalog.json
chmod 600 /etc/osai/*
nano /etc/osai/bot.env
```

In `bot.env`, fill in `OSAI_TELEGRAM_BOT_TOKEN`, `OSAI_LLM_API_KEY` and `OSAI_LLM_MODEL`.

Then edit `catalog.json` (`nano /etc/osai/catalog.json`) to describe your sheets:
- `file_id`: the long ID in the sheet's link, `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
- `parent_id`: the ID of the Drive folder that contains the file (from the folder's link).
- `sheet_name`: the tab name.
- `columns`: the header row, written exactly as it appears in the sheet.
- `description`: one sentence saying what the sheet contains. This helps the AI choose the right table.

Start the bot:

```bash
docker compose -f deploy/bot/compose.yml run --rm bot check   # validates configuration
docker compose -f deploy/bot/compose.yml up -d --build
docker compose -f deploy/bot/compose.yml logs -f              # Ctrl+C to stop watching
```

## Step 6 — Add yourself as CEO

1. In Telegram, open your bot and send `/start`.
2. It replies that you are not allowed yet and shows **your Telegram ID**.
3. Put that number in `/etc/osai/catalog.json` under `users` → `telegram_user_id`, with `"role": "ceo"`.
4. Restart the bot:
   ```bash
   docker compose -f deploy/bot/compose.yml restart
   ```
5. Send `/start` again, then ask a question in Dari, Pashto or English.

## Updating

```bash
cd /opt/osai && git pull && docker compose -f deploy/bot/compose.yml up -d --build
```

## If something goes wrong

- **"busy" replies:** the free model hit its rate limit or is down. Wait, or pick another `:free` model in `bot.env`.
- **"not available" replies:** the folder is not shared with the service account email, or `file_id`/`parent_id` is wrong.
- **"could not answer" replies:** the question didn't match a table. Improve that sheet's `description` and `columns` in `catalog.json`.
- Send the output of `docker compose -f deploy/bot/compose.yml logs --tail 100` to the project lead **after checking it contains no keys**. The bot never logs keys.
