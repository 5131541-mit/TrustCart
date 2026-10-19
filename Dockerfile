FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DB=/data/trustcart.db MODEL_DIR=/data/models DATASET_DIR=/data/dataset COOKIE_SECURE=1
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
