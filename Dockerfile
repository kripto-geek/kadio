FROM python:3.13

WORKDIR /kadio

COPY . .

RUN pip install -r requirements.txt

EXPOSE 5000

CMD ["gunicorn", "app:app"]
