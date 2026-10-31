FROM python:3.13-slim
WORKDIR /app
# pillow + pillow-heif are only needed to convert HEIC photos from the inbox
RUN pip install --no-cache-dir pillow==11.3.0 pillow-heif==1.1.1
COPY server.py inbox.py ./
COPY static ./static
RUN useradd --system --no-create-home tagger && mkdir /data && chown tagger /data
USER tagger
ENV PYTHONUNBUFFERED=1 PORT=8080 INBOX_STATE_DIR=/data
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'])"
CMD ["python", "server.py"]
