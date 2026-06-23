# PestCare CRM — zero-dependency Python image.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# Persist the SQLite DB and uploaded files outside the image.
VOLUME ["/app/data", "/app/uploads"]

EXPOSE 8000
ENV PORT=8000

# No pip install needed — standard library only.
CMD ["sh", "-c", "python3 server.py ${PORT}"]
