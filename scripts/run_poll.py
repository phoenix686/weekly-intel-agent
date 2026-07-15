from dotenv import load_dotenv
load_dotenv()

from logging_config import setup_logging
setup_logging()

from telegram.polling import poll_once

poll_once()
print("poll_once complete")
