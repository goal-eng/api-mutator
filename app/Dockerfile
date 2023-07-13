ARG BASE_IMAGE=python:3.8-alpine


FROM $BASE_IMAGE AS base-image

WORKDIR /root/src/
ENV PATH=/root/.local/bin:$PATH

RUN pip3 install --user --no-cache-dir gunicorn==19.9.0

COPY ./src/requirements.txt requirements.txt

RUN grep psycopg2 requirements.txt && apk add --no-cache postgresql-dev gcc python3-dev musl-dev || true
RUN grep git:\/\/ requirements.txt && apk add --no-cache git || true
RUN pip3 install --user --no-cache-dir -r requirements.txt

COPY ./src/ /root/src/

RUN python3 -m compileall -b -f -q /root/
RUN ENV_FILL_MISSING_VALUES=1 SECRET_KEY=dummy DATABASE_URL= python3 manage.py collectstatic --no-input --clear


FROM $BASE_IMAGE AS secondary-image

WORKDIR /root/src/
ENV PYTHONUNBUFFERED=1
ENV PATH=/root/.local/bin:$PATH

COPY --from=base-image /root/ /root/

RUN grep psycopg2 requirements.txt && apk add --no-cache libpq || true
EXPOSE 8000

CMD ["gunicorn", "--workers=1", "--bind=0.0.0.0:8000", "src.wsgi:application"]
