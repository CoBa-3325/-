"""Клиент распознавания речи через Yandex SpeechKit."""

import aiohttp


class SpeechKitTranscriber:
    URL = (
        "https://stt.api.cloud.yandex.net/"
        "speech/v1/stt:recognize"
    )

    def __init__(
        self,
        api_key: str,
        language: str = "ru-RU",
    ):
        self.api_key = api_key
        self.language = language

    async def transcribe(
        self,
        audio_bytes: bytes,
    ) -> str:
        """Отправить аудио в SpeechKit и получить текст."""

        if not audio_bytes:
            raise ValueError(
                "Передан пустой аудиофайл."
            )

        headers = {
            "Authorization": f"Api-Key {self.api_key}",
        }

        params = {
            "lang": self.language,
        }

        timeout = aiohttp.ClientTimeout(
            total=60,
        )

        async with aiohttp.ClientSession(
            timeout=timeout,
        ) as session:

            async with session.post(
                self.URL,
                headers=headers,
                params=params,
                data=audio_bytes,
            ) as response:

                response.raise_for_status()

                data = await response.json()

        result = data.get(
            "result",
            "",
        ).strip()

        if not result:
            raise RuntimeError(
                "SpeechKit не вернул текст расшифровки."
            )

        return result