FROM python:3.9

WORKDIR /weaverest
COPY . .
RUN pip install -r requirements.txt
ENV PYTHONPATH="${PYTHONPATH}:/weaverest"

ENTRYPOINT ["python", "-m", "weaverest"]
