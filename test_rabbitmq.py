
import pika
import json

RABBITMQ_HOST = "localhost"
QUEUE_NAME = "test_queue"


def main():
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST)
    )
    try:
        channel = connection.channel()

        channel.queue_declare(queue=QUEUE_NAME, durable=True)

        message = {
            "job_id": "job-test",
            "task_id": "task-001",
            "message": "Hello from SimilarDocs",
        }

        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        print("Message sent successfully.")

        method, properties, body = channel.basic_get(
            queue=QUEUE_NAME,
            auto_ack=False,
        )
        if method:
            print("Message received:", json.loads(body))
            channel.basic_ack(delivery_tag=method.delivery_tag)
        else:
            raise RuntimeError("No message received from RabbitMQ.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
