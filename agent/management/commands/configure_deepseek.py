from getpass import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from agent.configuration import configure_deepseek


class Command(BaseCommand):
    help = "Сохранить ключ DeepSeek в локальном .env. Ключ вводится скрыто и не передаётся в аргументах."

    def add_arguments(self, parser):
        parser.add_argument("--model", default="deepseek-v4-pro")

    def handle(self, *args, **options):
        try:
            key = getpass("Вставь API-ключ DeepSeek и нажми Enter (ввод скрыт): ")
            configure_deepseek(settings.BASE_DIR / ".env", key, options["model"])
        except (ValueError, OSError, EOFError):
            raise CommandError("Не удалось сохранить настройки DeepSeek. Проверь ввод ключа и доступ к .env.") from None
        self.stdout.write(self.style.SUCCESS(f"DeepSeek настроен: {options['model']}. Ключ сохранён в .env (скрыт)."))
        self.stdout.write("Перезапусти Django. Проверка API: python manage.py check_llm")
