import re
import asyncio
import logging
import aiohttp
import signal
import sys
from datetime import datetime
from pyrogram.enums import ParseMode
from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ID = "29954197"
API_HASH = "4ea7a4f028bed2a8077c65085dddc9c4"
PHONE_NUMBER = "+254112011036"
SOURCE_GROUPS = [-1002557527694]
TARGET_CHANNELS = [-1002649461790]

POLLING_INTERVAL = 2
MESSAGE_BATCH_SIZE = 100
MAX_WORKERS = 100
SEND_DELAY = 2
PROCESS_DELAY = 0.5
BIN_TIMEOUT = 10
MAX_CONCURRENT_CARDS = 50
MAX_PROCESSED_MESSAGES = 10000

user = Client(
    "cc_monitor_user",
    api_id=API_ID,
    api_hash=API_HASH,
    phone_number=PHONE_NUMBER,
    workers=MAX_WORKERS
)

is_running = True
last_processed_message_ids = {group_id: None for group_id in SOURCE_GROUPS}
processed_messages = set()
processed_cards = set()
stats = {
    'messages_processed': 0,
    'cards_found': 0,
    'cards_sent': 0,
    'cards_duplicated': 0,
    'errors': 0,
    'start_time': None,
    'last_speed_check': None,
    'cards_per_second': 0,
    'bin_lookups_success': 0,
    'bin_lookups_failed': 0
}

bin_cache = {}
card_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CARDS)

class CustomBinClient:
    def __init__(self, timeout=BIN_TIMEOUT):
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.inflight = {}

    async def get_bin_info(self, bin_number):
        bin_number = bin_number.strip()
        if len(bin_number) < 6:
            bin_number = bin_number.ljust(6, '0')
        else:
            bin_number = bin_number[:6]

        if bin_number in bin_cache:
            return bin_cache[bin_number]

        async with self.lock:
            if bin_number in bin_cache:
                return bin_cache[bin_number]
            if bin_number in self.inflight:
                await self.inflight[bin_number]
                return bin_cache.get(bin_number)

            future = asyncio.get_event_loop().create_future()
            self.inflight[bin_number] = future

        try:
            data = await self._fetch_custom_api(bin_number)
            if data:
                bin_cache[bin_number] = data
                stats['bin_lookups_success'] += 1
            else:
                bin_cache[bin_number] = None
                stats['bin_lookups_failed'] += 1
            future.set_result(data)
            return data
        except Exception as e:
            logger.error(f"Error fetching BIN {bin_number} from custom API: {e}")
            future.set_result(None)
            stats['bin_lookups_failed'] += 1
            return None
        finally:
            async with self.lock:
                self.inflight.pop(bin_number, None)

    async def _fetch_custom_api(self, bin_number):
        url = f"https://bins.antipublic.cc/bins/{bin_number}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        j = await resp.json()
                        return {
                            'scheme': j.get('scheme', 'UNKNOWN').upper(),
                            'type': j.get('type', 'UNKNOWN').upper(),
                            'brand': j.get('brand', 'UNKNOWN').upper(),
                            'bank': j.get('bank', 'UNKNOWN BANK'),
                            'country_name': j.get('country_name', 'UNKNOWN'),
                            'country_flag': j.get('country_flag', '🌍'),
                            'country_code': j.get('country_code', 'XX')
                        }
                    else:
                        logger.debug(f"Custom BIN API returned status {resp.status} for {bin_number}")
                        return None
        except Exception as e:
            logger.error(f"Exception on custom BIN API call: {e}")
            return None

bin_client = CustomBinClient()

def extract_credit_cards(text):
    if not text:
        return []

    pattern = r'(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})'
    matches = re.findall(pattern, text)
    cards = []
    for match in matches:
        card_num, month, year, cvv = match
        card_num = re.sub(r'\D', '', card_num)
        if len(card_num) < 13 or len(card_num) > 19:
            continue
        try:
            m = int(month)
            if m < 1 or m > 12:
                continue
            y = year[-2:] if len(year) == 4 else year
            if len(y) != 2:
                continue
            if len(cvv) < 3 or len(cvv) > 4:
                continue
        except:
            continue
        cards.append(f"{card_num}|{month.zfill(2)}|{y}|{cvv}")
    return list(dict.fromkeys(cards))

def format_card_message(cc_data, bin_info):
    bin_number = cc_data.split('|')[0][:6]
    scheme = bin_info.get('scheme', 'UNKNOWN') if bin_info else 'UNKNOWN'
    card_type = bin_info.get('type', 'UNKNOWN') if bin_info else 'UNKNOWN'
    brand = bin_info.get('brand', 'UNKNOWN') if bin_info else 'UNKNOWN'
    bank = bin_info.get('bank', 'UNKNOWN BANK') if bin_info else f"{brand} BANK"
    country_name = bin_info.get('country_name', 'UNKNOWN') if bin_info else 'UNKNOWN'
    country_flag = bin_info.get('country_flag', '🌍') if bin_info else '🌍'

    if scheme == 'UNKNOWN':
        type_str = f"{card_type} - {brand}"
    else:
        type_str = f"{scheme} - {card_type} - {brand}"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""[ϟ] 𝐀𝐩𝐩𝐫𝐨𝐯𝐞𝐝 𝐒𝐜𝐫𝐚𝐩𝐩𝐞𝐫
━━━━━━━━━━━━━
[ϟ] 𝗖𝗖 - <code>{cc_data}</code>
[ϟ] 𝗦𝘁𝗮𝘁𝘂𝘀 : APPROVED ✅
[ϟ] 𝗚𝗮𝘁𝗲 - Stripe Auth
━━━━━━━━━━━━━
[ϟ] 𝗕𝗶𝗻 : {bin_number}
[ϟ] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆 : {country_name} {country_flag}
[ϟ] 𝗜𝘀𝘀𝘂𝗲𝗿 : {bank}
[ϟ] 𝗧𝘆𝗽𝗲 : {type_str}
━━━━━━━━━━━━━
[ϟ] 𝗧𝗶𝗺𝗲 : {timestamp}
[ϟ] 𝗦𝗰𝗿𝗮𝗽𝗽𝗲𝗱 𝗕𝘆 : @cmchkbot"""

async def send_message_with_delay(formatted_message, cc_data):
    card_hash = cc_data.split('|')[0]
    if card_hash in processed_cards:
        logger.info(f"Duplicate card detected, skipping: {cc_data[:12]}***")
        stats['cards_duplicated'] += 1
        return

    processed_cards.add(card_hash)
    if len(processed_cards) > 10000:
        old = list(processed_cards)
        processed_cards.clear()
        processed_cards.update(old[-5000:])

    for i, channel_id in enumerate(TARGET_CHANNELS):
        while True:
            try:
                await user.send_message(chat_id=channel_id, text=formatted_message, parse_mode=ParseMode.HTML)
                logger.info(f"Sent card {cc_data[:12]}*** to channel {channel_id}")
                stats['cards_sent'] += 1
                break
            except FloodWait as e:
                logger.warning(f"Flood wait of {e.x} seconds for channel {channel_id}")
                await asyncio.sleep(e.x)
            except Exception as e:
                logger.error(f"Error sending message to channel {channel_id}: {e}")
                stats['errors'] += 1
                break

        if i < len(TARGET_CHANNELS) - 1:
            await asyncio.sleep(SEND_DELAY)

async def process_card(cc_data):
    async with card_semaphore:
        try:
            bin_number_raw = cc_data.split('|')[0]
            bin_number = bin_number_raw[:6] if len(bin_number_raw) >=6 else bin_number_raw.ljust(6, '0')
            bin_info = await bin_client.get_bin_info(bin_number)

            formatted_message = format_card_message(cc_data, bin_info)
            await send_message_with_delay(formatted_message, cc_data)
            await asyncio.sleep(PROCESS_DELAY)
        except Exception as e:
            logger.error(f"Error processing card {cc_data}: {e}")
            stats['errors'] += 1

async def process_message(message):
    if message.id in processed_messages:
        return
    processed_messages.add(message.id)
    stats['messages_processed'] += 1

    if len(processed_messages) > MAX_PROCESSED_MESSAGES:
        old = list(processed_messages)
        processed_messages.clear()
        processed_messages.update(old[-5000:])

    text = message.text or message.caption
    if not text:
        return

    cards = extract_credit_cards(text)
    if not cards:
        return

    stats['cards_found'] += len(cards)
    for card in cards:
        await process_card(card)

async def poll_messages():
    global last_processed_message_ids, is_running

    accessible_groups = []
    for group_id in SOURCE_GROUPS:
        try:
            chat = await user.get_chat(group_id)
            logger.info(f"Access OK for group {chat.title} ({group_id})")
            accessible_groups.append(group_id)
            async for message in user.get_chat_history(group_id, limit=1):
                last_processed_message_ids[group_id] = message.id
                break
        except Exception as e:
            logger.error(f"Cannot access group {group_id}, skipping: {e}")

    if not accessible_groups:
        logger.error("No accessible groups. Exiting polling.")
        return

    while is_running:
        try:
            for group_id in accessible_groups:
                last_id = last_processed_message_ids.get(group_id)
                new_messages = []

                try:
                    async for message in user.get_chat_history(group_id, limit=MESSAGE_BATCH_SIZE):
                        if last_id and message.id <= last_id:
                            break
                        new_messages.append(message)
                except ValueError as ve:
                    if "Peer id invalid" in str(ve):
                        logger.warning(f"Peer id invalid for group {group_id}, retrying after delay")
                        await asyncio.sleep(5)
                        continue
                    else:
                        raise

                new_messages.reverse()
                if new_messages:
                    logger.info(f"Found {len(new_messages)} new messages in group {group_id}")
                    for msg in new_messages:
                        await process_message(msg)
                        last_processed_message_ids[group_id] = max(last_processed_message_ids[group_id] or 0, msg.id)
                        await asyncio.sleep(0.1)
                else:
                    logger.info(f"No new messages in group {group_id}")

            await asyncio.sleep(POLLING_INTERVAL)

        except Exception as e:
            logger.error(f"Polling error: {e}")
            stats['errors'] += 1
            await asyncio.sleep(5)

@user.on_message(filters.chat(SOURCE_GROUPS))
async def realtime_handler(client, message):
    asyncio.create_task(process_message(message))

async def print_stats():
    while is_running:
        await asyncio.sleep(120)
        uptime = datetime.now() - stats['start_time'] if stats['start_time'] else 0
        logger.info(f"Uptime: {uptime}")
        logger.info(f"Messages Processed: {stats['messages_processed']}")
        logger.info(f"Cards Found: {stats['cards_found']}")
        logger.info(f"Cards Sent: {stats['cards_sent']}")
        logger.info(f"Duplicates Skipped: {stats['cards_duplicated']}")
        logger.info(f"BIN Lookup Success: {stats['bin_lookups_success']}")
        logger.info(f"BIN Lookup Failed: {stats['bin_lookups_failed']}")
        logger.info(f"Errors: {stats['errors']}")

def signal_handler(sig, frame):
    global is_running
    logger.info(f"Signal {sig} received, shutting down.")
    is_running = False

async def main():
    global is_running
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    stats['start_time'] = datetime.now()
    await user.start()
    logger.info("User client started.")

    poll_task = asyncio.create_task(poll_messages())
    stats_task = asyncio.create_task(print_stats())

    await idle()

    poll_task.cancel()
    stats_task.cancel()
    await asyncio.gather(poll_task, stats_task, return_exceptions=True)

    await user.stop()
    logger.info("User client stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
