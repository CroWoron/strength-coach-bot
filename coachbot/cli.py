from __future__ import annotations

from .app import build_app


def main() -> None:
    coach, _settings, _database = build_app()
    print("Силовой тренер запущен. Пиши обычным языком; Ctrl+C — выход.")
    counter = 0
    while True:
        try:
            text = input("Ты: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        counter += 1
        answer = coach.handle_message(
            platform="cli",
            external_user_id="local",
            display_name="Local user",
            event_id=str(counter),
            text=text,
        )
        if answer:
            print(f"Тренер: {answer}")


if __name__ == "__main__":
    main()
