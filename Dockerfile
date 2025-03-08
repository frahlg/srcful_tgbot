FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create data directory
RUN mkdir -p /app/data /app/logs

# Expose Telegram API port and Facebook Messenger webhook port
EXPOSE 3001

COPY *.py ./

CMD ["python", "-u", "main.py"]
