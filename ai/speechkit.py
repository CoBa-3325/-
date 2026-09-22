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
            "Content-Type": "audio/ogg",
        }

        params = {
            "lang": self.language,
            "format": "oggopus",
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

                response_text = await response.text()

                if response.status >= 400:
                    raise RuntimeError(
                        "Yandex SpeechKit error "
                        f"{response.status}: "
                        f"{response_text}"
                    )

                try:
                    data = await response.json()
                except Exception as exc:
                    raise RuntimeError(
                        "Yandex SpeechKit вернул "
                        f"некорректный JSON: {response_text!r}"
                    ) from exc

        result = data.get("result", "").strip()

        if not result:
            raise RuntimeError(
                "SpeechKit не вернул текст расшифровки."
            )

        return result