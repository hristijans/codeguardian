import aio_pika
import pytest
from aiormq.exceptions import ChannelPreconditionFailed

from src.queue import QUEUE_NAME, JobPublisher, PublishError
from src.schemas import ReviewJobMessage


def make_message(**changes) -> ReviewJobMessage:
    fields = {
        "job_id": "job-1",
        "repo_name": "example-org/example-repo",
        "pr_number": 1,
        "head_sha": "0" * 40,
    }
    fields.update(changes)
    return ReviewJobMessage(**fields)


def test_queue_name_matches_the_design():
    assert QUEUE_NAME == "pr_analysis_queue"


@pytest.mark.asyncio
async def test_declared_queue_is_durable(test_queue, settings):
    # RabbitMQ refuses to redeclare a queue with different settings, so a
    # non-durable declaration failing proves the broker holds it as durable.
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    try:
        channel = await connection.channel()
        with pytest.raises(ChannelPreconditionFailed):
            await channel.declare_queue(test_queue.name, durable=False)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_published_message_reaches_the_queue(rabbit_channel, test_queue):
    publisher = JobPublisher(rabbit_channel, test_queue.name)
    sent = make_message()

    await publisher.publish(sent)

    received = await test_queue.get(timeout=5)
    await received.ack()
    assert ReviewJobMessage.model_validate_json(received.body) == sent


@pytest.mark.asyncio
async def test_published_message_is_persistent_json(rabbit_channel, test_queue):
    publisher = JobPublisher(rabbit_channel, test_queue.name)

    await publisher.publish(make_message(job_id="job-42"))

    received = await test_queue.get(timeout=5)
    await received.ack()
    assert received.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert received.content_type == "application/json"
    assert received.message_id == "job-42"


@pytest.mark.asyncio
async def test_publishing_to_a_missing_queue_raises(rabbit_channel):
    # The broker returns the message rather than failing the publish, so the
    # publisher has to notice. Otherwise the job would be lost silently.
    publisher = JobPublisher(rabbit_channel, "test-queue-that-does-not-exist")

    with pytest.raises(PublishError):
        await publisher.publish(make_message())
