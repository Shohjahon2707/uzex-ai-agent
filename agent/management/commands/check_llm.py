"""Проверяет ключ, адрес, модель и обмен инструментами, не используя базу CSV."""
import json

from django.core.management.base import BaseCommand, CommandError

from agent.llm import assistant_tool_message, create_client, describe_error, get_config, key_pool


CHECK_TOOL = {
    "type": "function",
    "function": {
        "name": "connection_check",
        "description": "Проверка подключения. Вызови один раз без аргументов.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


class Command(BaseCommand):
    help = "Проверить LLM API; --list-models выводит доступные идентификаторы моделей. Ключ не выводится."

    def add_arguments(self, parser):
        parser.add_argument("--list-models", action="store_true", help="Получить список моделей у провайдера")
        parser.add_argument("--key", type=int, default=1, help="Номер ключа в настроенном пуле, начиная с 1")
        parser.add_argument("--show-rotation", action="store_true", help="Показать порядок ключей без запросов к API")

    def handle(self, *args, **options):
        try:
            config = get_config(key_index=options["key"] - 1)
            self.stdout.write(f"API: {config.base_url}\nМодель: {config.model}\nКлюч: {config.key_name} (скрыт)")
            if options["show_rotation"]:
                labels = [name for name, _ in key_pool()]
                self.stdout.write("Порядок: " + " → ".join(labels + labels))
                self.stdout.write("Это локальная проверка настроек без обращения к провайдеру.")
                return
            with create_client(timeout=20, config=config) as client:
                if options["list_models"]:
                    for model in client.models.list():
                        self.stdout.write(model.id)
                    return
                messages = [{"role": "user", "content": "Вызови connection_check, затем ответь OK."}]
                reply = client.chat.completions.create(
                    model=config.model, messages=messages, tools=[CHECK_TOOL], tool_choice="auto",
                ).choices[0].message
                if not reply.tool_calls:
                    raise CommandError("API ответил, но модель не вызвала проверочный инструмент в режиме auto. Повтори проверку или проверь поддержку tool calling у модели.")
                messages.append(assistant_tool_message(reply))
                for tc in reply.tool_calls:
                    if tc.function.name != "connection_check":
                        raise CommandError("Модель вернула неизвестный инструмент вместо connection_check.")
                    json.loads(tc.function.arguments or "{}")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": '{"ok":true}'})
                final = client.chat.completions.create(model=config.model, messages=messages).choices[0].message
                if not final.content or final.tool_calls:
                    raise CommandError("Инструмент вызван, но итоговый текст модели не получен.")
            self.stdout.write(self.style.SUCCESS("OK: API, модель и обмен инструментами работают. Можно запускать чат."))
        except CommandError:
            raise
        except Exception as exc:
            error = describe_error(exc)
            raise CommandError(f"{error['message']}\nКод: {error['code']}\nДетали: {error['detail']}") from None
