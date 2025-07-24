from pyrogram import Client, filters
from pyrogram.enums import ParseMode
import re
import asyncio
from datetime import datetime
import httpx

API_ID = 29954197
API_HASH = "4ea7a4f028bed2a8077c65085dddc9c4"
BOT_TOKEN = "8408338657:AAFX15Ei4hvX6Bijnu0STRF5JRwQe20J-_U"

# Group IDs
TARGET_GROUP_IDS = [-1002557527694]   # 👁 Whitelisted scraping groups
YOUR_GROUP_ID = -1002649461790        # 📤 Your group to send formatted results

# Max concurrent workers
MAX_WORKERS = 5
semaphore = asyncio.Semaphore(MAX_WORKERS)

# Regex: match full CC format
cc_pattern = re.compile(
    r"\b(?:4[0-9]{12}(?:[0-9]{3})?"               # Visa
    r"|5[1-5][0-9]{14}"                           # MasterCard
    r"|3[47][0-9]{13}"                            # Amex
    r"|6(?:011|5[0-9]{2})[0-9]{12})"              # Discover
    r"\|(?:0[1-9]|1[0-2])"                        # MM
    r"\|(?:[0-9]{2}|20[2-9][0-9])"                # YY or YYYY
    r"\|[0-9]{3,4}\b"                             # CVV
)

def extract_credit_cards(text):
    matches = cc_pattern.findall(text)
    unique_cards = list(dict.fromkeys(matches))  # Deduplicate
    return unique_cards

async def get_bin_info(bin_number):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"https://bins.antipublic.cc/bins/{bin_number}")
            return r.json() if r.status_code == 200 else None
    except Exception:
        return None

async def format_card_message(cc_data):
    bin_number = cc_data.split("|")[0][:6]
    bin_info = await get_bin_info(bin_number)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not bin_info:
        bin_info = {
            "country": "Unknown",
            "bank": "Unknown",
            "scheme": "Unknown",
            "type": "Unknown",
            "brand": "Unknown",
            "emoji": "❓"
        }

    return f"""━━━━━━━━━━━━━
[ϟ] 𝗖𝗖 - <code>{cc_data}</code> 
[ϟ] 𝗦𝘁𝗮𝘁𝘂𝘀 : APPROVED ✅
[ϟ] 𝗚𝗮𝘁𝗲 - Stripe Auth
━━━━━━━━━━━━━
[ϟ] 𝗕𝗶𝗻 : {bin_number}
[ϟ] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆 : {bin_info.get("country", "Unknown")} {bin_info.get("emoji", "🌐")}
[ϟ] 𝗜𝘀𝘀𝘂𝗲𝗿 : {bin_info.get("bank", "Unknown")}
[ϟ] 𝗧𝘆𝗽𝗲 : {bin_info.get("scheme", "Unknown")} - {bin_info.get("type", "Unknown")} - {bin_info.get("brand", "Unknown")}
━━━━━━━━━━━━━
[ϟ] 𝗧𝗶𝗺𝗲 : {timestamp}
[ϟ] 𝗦𝗰𝗿𝗮𝗽𝗽𝗲𝗱 𝗕𝘆 : @yourbot"""

bot = Client("scrape_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@bot.on_message(filters.text)
async def handle_message(_, message):
    if message.chat.id not in TARGET_GROUP_IDS:
        return  # Ignore if not from approved groups

    text = message.text or message.caption
    if not text:
        return

    cards = extract_credit_cards(text)
    if not cards:
        return

    async with semaphore:
        for cc in cards:
            try:
                msg = await format_card_message(cc)
                await bot.send_message(YOUR_GROUP_ID, msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                print(f"Error sending message: {e}")

print("✅ Bot running using bot token and filtering allowed groups only...")
bot.run()
