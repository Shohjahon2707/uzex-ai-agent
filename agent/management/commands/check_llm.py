"""Проверяет ключ, адрес, модель и обмен инструментами, не используя базу CSV."""
import json

from django.core.management.base import BaseCommand, CommandError

from agent.llm import create_client, describe_error, get_config


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

    def handle(self, *args, **options):
        try:
            config = get_config()
            self.stdout.write(f"API: {config.base_url}\nМодель: {config.model}\nКлюч: задан (скрыт)")
            with create_client(timeout=20, config=config) as client:
                if options["list_models"]:
                    for model in client.models.list():
                        self.stdout.write(model.id)
                    return
                messages = [{"role": "user", "content": "Вызови connection_check, затем ответь OK."}]
                reply = client.chat.completions.create(
                    model=config.model, messages=messages, tools=[CHECK_TOOL], tool_choice="required",
                ).choices[0].message
                if not reply.tool_calls:
                    raise CommandError("API ответил, но модель не вызвала инструмент. Выбери модель с tool calling.")
                messages.append({"role": "assistant", "content": reply.content,
                                 "tool_calls": [{"id": tc.id, "type": "function", "function": {
                                     "name": tc.function.name, "arguments": tc.function.arguments,
                                 }} for tc in reply.tool_calls]})
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
