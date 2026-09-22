"""Вспомогательная загрузка вложений с гарантированной очисткой временного файла."""

import tempfile
from pathlib import Path


async def download_attachment(
    message,
    attachment_type: str,
    max_bytes: int,
) -> bytes:

    attachment = await message.get_attachment(
        attachment_type
    )

    if attachment is None:
        raise ValueError(
            "Аудиовложение не найдено."
        )

    fd, filename = tempfile.mkstemp(
        suffix=".audio"
    )

    Path(filename).unlink(
        missing_ok=True
    )

    path = Path(filename)

    try:

        await attachment.download(
            path
        )

        file_size = path.stat().st_size

        if file_size > max_bytes:
            raise ValueError(
                "Аудиофайл слишком большой."
            )

        return path.read_bytes()

    finally:

        path.unlink(
            missing_ok=True
        )