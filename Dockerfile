FROM python:3.9

WORKDIR /weaverest
COPY . .
RUN pip install -r requirements.txt

ENTRYPOINT ["/weaverest/weaverest.py"]
