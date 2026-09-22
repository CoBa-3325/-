from datetime import (
    datetime,
    timezone,
)

from app.database.connection import (
    connect,
)
from app.database.repository import (
    Repository,
)
from app.schemas.message import (
    MessageRecord,
)


def test_message_is_deduplicated():

    connection = connect(
        ":memory:"
    )

    repository = Repository(
        connection
    )

    message = MessageRecord(
        max_message_id="message-1",
        chat_id=100,
        user_id=200,
        user_name="User",
        text="Привет",
        source="text",
        timestamp=datetime.now(
            timezone.utc
        ),
    )

    assert (
        repository.save_message(
            message
        )
        is True
    )

    assert (
        repository.save_message(
            message
        )
        is False
    )