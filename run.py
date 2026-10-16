import logging

from coachbot.app import build_app
from coachbot.telegram import TelegramBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    coach, settings, database = build_app()
    TelegramBot(settings.telegram_token, settings.telegram_allowed_users, coach, database).run()


if __name__ == "__main__":
    main()
