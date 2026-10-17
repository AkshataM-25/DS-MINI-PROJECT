
FROM python:3.12-slim

WORKDIR /app

COPY aggregator/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY common/ /app/common/
COPY aggregator/aggregator.py /app/aggregator.py

CMD ["python", "aggregator.py"]
