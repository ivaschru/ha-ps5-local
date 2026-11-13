"""Configuration keys and defaults."""

from datetime import timedelta

DOMAIN = "ps5_local"
CONF_CONSOLE_ID = "console_id"
CONF_PAIRING = "pairing"
CONF_ACCOUNT_ID = "account_id"
CONF_REDIRECT_URL = "redirect_url"
CONF_PIN = "pin"
CONF_PASSCODE = "passcode"
POLL_INTERVAL = timedelta(seconds=15)
