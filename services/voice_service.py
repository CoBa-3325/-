"""Получение аудио из MAX, распознавание и очистка временных файлов."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import tempfile
from pathlib import Path
from typing import Any

import aiohttp
try:
    import truststore
except ImportError:  # optional; ssl.create_default_context remains verified TLS
    truststore = None

from  ai.speechkit import SpeechKitTranscriber

logger = logging.getLogger(__name__)


class VoiceService:
    """Преобразует MAX audio attachment в текст контекста."""

    def __init__(
        self,
        transcriber: SpeechKitTranscriber,
        max_bytes: int,
        bot_token: str,
        download_timeout: float = 60.0,
        download_retries: int = 2,
        ca_bundle: str | None = None,
    ):
        self.transcriber = transcriber
        self.max_bytes = max_bytes
        self.bot_token = bot_token
        self.download_timeout = download_timeout
        self.download_retries = max(1, download_retries)
        self.ca_bundle = ca_bundle or os.getenv("MAX_CA_BUNDLE") or None

    @staticmethod
    def _get(obj: Any, name: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(name, default)

        return getattr(obj, name, default)

    def find_audio_attachment(self, message: Any) -> Any | None:
        """Найти audio attachment внутри сообщения MAX."""
        body = self._get(message, "body")
        attachments = self._get(body, "attachments", []) or []

        for attachment in attachments:
            if self._get(attachment, "type") == "audio":
                return attachment

        return None

    def _ssl_context(self) -> ssl.SSLContext:
        """
        Создать проверяемый TLS-контекст для загрузки файлов MAX.

        При наличии MAX_CA_BUNDLE используется явно указанный PEM bundle.

        Если MAX_CA_BUNDLE не указан, truststore заставляет Python
        использовать системное хранилище сертификатов Windows/Linux/macOS.

        SSL-проверка НЕ отключается.
        """

        if self.ca_bundle:
            bundle = Path(self.ca_bundle)

            if not bundle.is_file():
                raise RuntimeError(
                    f"MAX_CA_BUNDLE points to a missing file: {bundle}"
                )

            return ssl.create_default_context(
                cafile=str(bundle),
            )

        if truststore is not None:
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return ssl.create_default_context()

    async def _download_url(self, url: str) -> bytes:
        """Скачать аудиофайл с URL MAX с проверкой TLS."""

        headers = {
            "Authorization": self.bot_token,
        }

        timeout = aiohttp.ClientTimeout(
            total=self.download_timeout,
        )

        ssl_context = self._ssl_context()

        connector = aiohttp.TCPConnector(
            ssl=ssl_context,
        )

        last_error: Exception | None = None

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            raise_for_status=True,
        ) as session:

            for attempt in range(
                1,
                self.download_retries + 1,
            ):
                try:
                    async with session.get(
                        url,
                        headers=headers,
                    ) as response:

                        data = await response.read()

                    if len(data) > self.max_bytes:
                        raise ValueError(
                            "Аудиофайл слишком большой."
                        )

                    return data

                except aiohttp.ClientConnectorCertificateError as exc:
                    host = (
                        url.split("/", 3)[2]
                        if "://" in url
                        else url
                    )

                    logger.error(
                        "VOICE_FILE_TLS_FAILED "
                        "host=%s attempt=%s/%s error=%s",
                        host,
                        attempt,
                        self.download_retries,
                        exc,
                    )

                    raise RuntimeError(
                        "Не удалось установить доверенное "
                        "HTTPS-соединение с MAX. "
                        "Проверьте сертификаты в системном "
                        "хранилище или задайте MAX_CA_BUNDLE."
                    ) from exc

                except (
                    aiohttp.ClientConnectionError,
                    asyncio.TimeoutError,
                ) as exc:

                    last_error = exc

                    if attempt < self.download_retries:
                        logger.warning(
                            "VOICE_FILE_DOWNLOAD_RETRY "
                            "attempt=%s/%s error=%s",
                            attempt,
                            self.download_retries,
                            exc,
                        )

                        continue

                    raise RuntimeError(
                        "Не удалось скачать аудиофайл из MAX."
                    ) from exc

        raise RuntimeError(
            "Не удалось скачать аудиофайл из MAX."
        ) from last_error

    async def process_voice_message(
        self,
        message: Any,
        attachment_type: str = "audio",
    ) -> str:
        """
        Получить голосовое сообщение MAX,
        скачать его и передать в SpeechKit.
        """

        logger.info(
            "VOICE_MESSAGE_RECEIVED"
        )

        attachment = self.find_audio_attachment(
            message
        )

        if (
            attachment is None
            and hasattr(message, "get_attachment")
        ):
            attachment = await message.get_attachment(
                attachment_type
            )

        if attachment is None:
            logger.error(
                "VOICE_FILE_REQUESTED "
                "failed: attachment not found"
            )

            raise ValueError(
                "Аудиовложение не найдено."
            )

        logger.info(
            "VOICE_FILE_REQUESTED"
        )

        temp_path: Path | None = None

        try:
            payload = (
                self._get(
                    attachment,
                    "payload",
                    {},
                )
                or {}
            )

            url = (
                self._get(
                    payload,
                    "url",
                )
                or self._get(
                    attachment,
                    "url",
                )
            )

            # -------------------------------------------------
            # Вариант 1. MAX предоставил URL
            # -------------------------------------------------

            if url:

                try:
                    data = await self._download_url(
                        url
                    )

                except Exception:
                    logger.exception(
                        "VOICE_FILE_DOWNLOAD_FAILED"
                    )
                    raise

                logger.info(
                    "VOICE_FILE_DOWNLOADED bytes=%s",
                    len(data),
                )

            # -------------------------------------------------
            # Вариант 2. MAX предоставил downloader
            # -------------------------------------------------

            elif hasattr(
                attachment,
                "download",
            ):

                fd, temp = tempfile.mkstemp(
                    suffix=".audio"
                )

                os.close(fd)

                Path(temp).unlink(
                    missing_ok=True
                )

                temp_path = Path(temp)

                await attachment.download(
                    temp_path
                )

                size = temp_path.stat().st_size

                if size > self.max_bytes:
                    raise ValueError(
                        "Аудиофайл слишком большой."
                    )

                data = temp_path.read_bytes()

                logger.info(
                    "VOICE_FILE_DOWNLOADED bytes=%s",
                    len(data),
                )

            # -------------------------------------------------
            # Ни URL, ни downloader
            # -------------------------------------------------

            else:
                raise ValueError(
                    "MAX не предоставил URL "
                    "или downloader для аудио."
                )

            # -------------------------------------------------
            # Распознавание
            # -------------------------------------------------

            logger.info(
                "VOICE_TRANSCRIPTION_STARTED"
            )

            try:
                text = (
                    await self.transcriber.transcribe(
                        data
                    )
                ).strip()

            except Exception:
                logger.exception(
                    "VOICE_TRANSCRIPTION_FAILED"
                )
                raise

            if not text:
                raise ValueError(
                    "SpeechKit вернул пустую расшифровку."
                )

            logger.info(
                "VOICE_TRANSCRIPTION_SUCCESS chars=%s",
                len(text),
            )

            return text

        except Exception:
            logger.exception(
                "VOICE_PROCESSING_FAILED"
            )
            raise

        finally:
            if temp_path is not None:
                temp_path.unlink(
                    missing_ok=True
                )

                logger.info(
                    "VOICE_TEMP_FILE_DELETED path=%s",
                    temp_path.name,
                )