import os
import asyncio
import logging
from typing import Dict, Any

try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Bot = None

class TelegramAlert:
    def __init__(self, config: Dict[str, Any] = None):
        # Use config if provided, otherwise fall back to environment variables
        if config:
            self.telegram_token = config.get('bot_token', '')
            self.telegram_chat_id = config.get('chat_id', '')
        else:
            self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
            self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')  # Updated to unified naming
        
        self.logger = logging.getLogger(__name__)
        self.enabled = self._validate_config()

    def _validate_config(self):
        if not TELEGRAM_AVAILABLE:
            self.logger.warning("python-telegram-bot is not installed. Telegram alerts disabled.")
            return False
        if not self.telegram_token or not self.telegram_chat_id:
            self.logger.warning("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID for Telegram alerts.")
            return False
        token_str = str(self.telegram_token).strip()
        chat_str = str(self.telegram_chat_id).strip()
        if token_str.startswith("${") or chat_str.startswith("${") or token_str == "":
            self.logger.info("Telegram alert keys are unconfigured placeholders; skipping Telegram backend.")
            return False
        return True

    async def _send_telegram_message(self, messages):
        bot = Bot(token=str(self.telegram_token))
        text = '\n'.join(messages)
        async with bot:
            await bot.send_message(text=text, chat_id=str(self.telegram_chat_id), parse_mode="HTML")

    async def _send_telegram_video(self, video_path: str, caption: str = ""):
        for attempt in range(3):
            try:
                bot = Bot(token=str(self.telegram_token))
                async with bot:
                    with open(video_path, 'rb') as video_file:
                        await bot.send_video(
                            chat_id=str(self.telegram_chat_id),
                            video=video_file,
                            caption=caption,
                            parse_mode="HTML",
                            write_timeout=120,  # Increase write timeout
                            read_timeout=120   # Increase read timeout
                        )
                return True
            except Exception as e:
                self.logger.error(f"Attempt {attempt+1}/3: Error sending Telegram video: {e}")
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        return False

    def send_incident(self, incident_data: Dict[str, Any]) -> bool:
        if not self.enabled:
            self.logger.warning("Telegram alerts are disabled due to missing configuration")
            return False
        try:
            is_suspicious = incident_data.get('is_suspicious', False)
            event_id = incident_data.get('event_id', 'unknown')
            llm_result = incident_data.get('llm_result', {})
            
            anomaly_type = (
                llm_result.get('anomaly_type')
                or incident_data.get('anomaly_type')
                or ('Suspicious Activity' if is_suspicious else 'Normal Activity')
            )
            severity = (
                llm_result.get('severity')
                or incident_data.get('severity')
                or ('HIGH' if is_suspicious else 'LOW')
            ).upper()
            
            description = incident_data.get('description', 'No description available')
            
            if is_suspicious:
                header = f"🚨 <b>SECURITY ALARM: {anomaly_type.upper()}</b> 🚨"
                self.logger.info(f"Suspicious activity detected for event {event_id}, sending Telegram alert")
            else:
                header = f"ℹ️ <b>Activity Notice: {anomaly_type}</b>"
                self.logger.info(f"Normal activity detected for event {event_id}, sending Telegram notification")
            
            message_text = (
                f"{header}\n\n"
                f"<b>Severity:</b> {severity}\n"
                f"<b>Event ID:</b> <code>{event_id}</code>\n"
                f"<b>Anomaly Type:</b> {anomaly_type}\n\n"
                f"<b>Details:</b>\n{description}"
            )
            
            # Send video file if available
            video_path = llm_result.get('video_path') or incident_data.get('video_path')
            if video_path and os.path.exists(video_path):
                asyncio.run(self._send_telegram_video(video_path, caption=message_text))
            else:
                # Always send text message
                asyncio.run(self._send_telegram_message([message_text]))
                
            return True
        except Exception as e:
            self.logger.error(f"Error sending Telegram alert for event {incident_data.get('event_id')}: {e}")
            return False 