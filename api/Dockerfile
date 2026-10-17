
FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api/main.py /app/main.py
COPY api/static/ /app/static/
COPY common/ /app/common/
COPY tools/ /app/tools/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
