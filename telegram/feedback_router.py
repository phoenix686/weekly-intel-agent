import logging

logger = logging.getLogger(__name__)


def handle_feedback(message: dict) -> None:
    """Route Telegram replies that are not approval responses.
    Daily feedback path is not yet built — logging only until it is."""
    logger.info(
        f"feedback_router: unrouted reply "
        f"(message_id={message.get('message_id')}): "
        f"{(message.get('text') or '')[:100]}"
    )
