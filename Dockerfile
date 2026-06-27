FROM python:3.13-alpine

WORKDIR /app

RUN pip install redis --no-cache-dir

COPY dashboard.py .
COPY new_fetcher.py .
COPY ip2region/ ./ip2region/
COPY data/ ./data/
COPY static/ ./static/

EXPOSE 5050

CMD ["python3", "dashboard.py"]