FROM python:3.13-slim
WORKDIR /app
# pillow resizes images (and with pillow-heif converts HEIC photos from the inbox);
# reportlab makes moodboard PDFs, with the fonts in fonts/
RUN pip install --no-cache-dir pillow==11.3.0 pillow-heif==1.1.1 reportlab==4.2.5
COPY *.py ./
COPY fonts ./fonts
COPY static ./static
RUN useradd --system --no-create-home tagger && mkdir /data && chown tagger /data
USER tagger
ENV PYTHONUNBUFFERED=1 PORT=8080 TAGGER_BIND=0.0.0.0 TAGGER_DATA_DIR=/data
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'])"
CMD ["python", "server.py"]
