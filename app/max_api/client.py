"""Низкоуровневый клиент актуального MAX API v2."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class MaxAPIError(Exception):
    """Ошибка при обращении к MAX API."""


class MaxAPIClient:
    BASE_URL = "https://platform-api2.max.ru"

    def __init__(
        self,
        token: str,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries

        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                )

                if response.status_code == 429:
                    if attempt == self.max_retries - 1:
                        raise MaxAPIError("MAX API rate limit exceeded")

                    await asyncio.sleep(2 ** attempt)
                    continue

                if response.status_code >= 500:
                    if attempt == self.max_retries - 1:
                        raise MaxAPIError(
                            f"MAX API server error: {response.status_code}"
                        )

                    await asyncio.sleep(2 ** attempt)
                    continue

                if response.status_code >= 400:
                    raise MaxAPIError(
                        f"MAX API error {response.status_code}: "
                        f"{response.text}"
                    )

                return response.json()

            except httpx.RequestError as exc:
                if attempt == self.max_retries - 1:
                    raise MaxAPIError(
                        f"MAX API connection error: {exc}"
                    ) from exc

                await asyncio.sleep(2 ** attempt)

        raise MaxAPIError("MAX API request failed")

    async def get_me(self) -> dict[str, Any]:
        """Получить информацию о боте."""
        return await self._request("GET", "/me")

    async def get_message(
        self,
        message_id: str,
    ) -> dict[str, Any]:
        """Получить одно сообщение по ID."""
        return await self._request(
            "GET",
            f"/messages/{message_id}",
        )

    async def get_messages(
        self,
        chat_id: int,
        *,
        from_timestamp: int | None = None,
        to_timestamp: int | None = None,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Получить сообщения чата.

        MAX принимает from/to в Unix timestamp
        в миллисекундах.
        """

        params: dict[str, Any] = {
            "chat_id": chat_id,
            "count": min(max(count, 1), 100),
        }

        if from_timestamp is not None:
            params["from"] = from_timestamp

        if to_timestamp is not None:
            params["to"] = to_timestamp

        result = await self._request(
            "GET",
            "/messages",
            params=params,
        )

        return result.get("messages", [])

    async def send_message(
        self,
        chat_id: int,
        text: str,
    ) -> dict[str, Any]:
        """Отправить текстовое сообщение в чат."""

        return await self._request(
            "POST",
            "/messages",
            params={"chat_id": chat_id},
            json={
                "text": text,
            },
        )