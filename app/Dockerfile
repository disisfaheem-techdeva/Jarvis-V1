FROM python:3.12-slim
WORKDIR /srv
# ffmpeg/ffprobe: check, fast-start and trim post videos (app/videos.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /srv/app
ENV PYTHONPATH=/srv
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
