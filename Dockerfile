FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

COPY *.py ./
COPY data/ ./data/
COPY static/ ./static/

EXPOSE 5050

CMD ["python3", "dashboard.py"]
