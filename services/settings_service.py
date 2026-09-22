"""Сервис сохранения настроек периода консолидации для каждого чата."""

from  schemas.settings import ChatSettings


class SettingsService:
    """Изолирует бизнес-правила настроек от Telegram/MAX handlers и DB."""

    def __init__(self, repository):
        self.repository = repository

    def get(self, chat_id):
        """Получить текущую настройку конкретного чата."""
        return self.repository.get_settings(chat_id)

    def set_interval(self, chat_id, interval, custom_days=None):
        """Сохранить период, выбранный пользователем кнопкой."""
        if interval not in {"daily", "weekly", "custom"}:
            raise ValueError("Unsupported interval")
        if interval == "custom" and (custom_days is None or not 1 <= custom_days <= 30):
            raise ValueError("custom_days must be between 1 and 30")

        settings = self.repository.get_settings(chat_id)
        settings.enabled = True
        settings.interval_type = interval
        settings.custom_days = custom_days
        self.repository.save_settings(settings)
        return settings
