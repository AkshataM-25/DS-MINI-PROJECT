import pika


def connection_parameters(
    host: str,
    user: str,
    password: str,
    **options,
) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(
        host=host,
        credentials=pika.PlainCredentials(user, password),
        **options,
    )
