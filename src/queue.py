import aio_pika
from aio_pika.abc import AbstractChannel, AbstractQueue
from pamqp.commands import Basic

from src.schemas import ReviewJobMessage

QUEUE_NAME = "pr_analysis_queue"


class PublishError(Exception):
    """The broker didn't confirm that it accepted a message."""


async def declare_queue(
    channel: AbstractChannel, name: str = QUEUE_NAME
) -> AbstractQueue:
    # Declaring is idempotent: it creates the queue if it's missing and does
    # nothing if it already exists with the same settings. Both the API and
    # the worker declare it, so neither depends on the other starting first.
    # `durable=True` keeps the queue itself across a broker restart.
    return await channel.declare_queue(name, durable=True)


class JobPublisher:
    """Publishes review jobs to a RabbitMQ queue.

    Messages go through the default exchange, which routes each message to the
    queue whose name equals the routing key.
    """

    def __init__(self, channel: AbstractChannel, queue_name: str = QUEUE_NAME) -> None:
        self._channel = channel
        self._queue_name = queue_name

    async def publish(self, message: ReviewJobMessage) -> None:
        amqp_message = aio_pika.Message(
            body=message.model_dump_json().encode(),
            content_type="application/json",
            # PERSISTENT asks the broker to write the message to disk, so it
            # survives a restart. It only helps if the queue is durable too.
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message.job_id,
        )
        # The channel uses publisher confirms, so this waits for the broker's
        # answer. `Basic.Ack` means it took the message. A message that no
        # queue can take comes back as a returned message instead of an error,
        # so anything other than an ack is treated as a failure.
        result = await self._channel.default_exchange.publish(
            amqp_message, routing_key=self._queue_name
        )
        if not isinstance(result, Basic.Ack):
            raise PublishError(
                f"Broker did not accept job {message.job_id} for {self._queue_name}"
            )
