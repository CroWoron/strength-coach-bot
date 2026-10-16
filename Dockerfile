FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

COPY . .
RUN python -m unittest discover -s tests -v

CMD ["python", "run.py"]
