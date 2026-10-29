FROM python:3.13-slim
WORKDIR /app
COPY server.py ./
COPY static ./static
RUN useradd --system --no-create-home tagger
USER tagger
ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'])"
CMD ["python", "server.py"]
