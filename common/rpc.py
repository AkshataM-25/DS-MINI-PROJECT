import logging
import xmlrpc.client


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float = 3.0):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


def call_aggregator(host: str, method: str, *args, timeout: float = 3.0):
    with xmlrpc.client.ServerProxy(
        f"http://{host}:9000",
        allow_none=True,
        transport=TimeoutTransport(timeout),
    ) as proxy:
        logging.info("XML-RPC request method=%s host=%s", method, host)
        try:
            return getattr(proxy, method)(*args)
        except (OSError, xmlrpc.client.Error) as error:
            logging.error(
                "XML-RPC failed method=%s host=%s error=%s",
                method,
                host,
                type(error).__name__,
            )
            raise
