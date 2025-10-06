FROM python:3.13.7
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
ENV PYTHONUNBUFFERED 1
CMD ["fastapi", "run", "--workers", "5"]
