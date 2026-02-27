# NoonYar Server Side

Backend service for the NoonYar bakery queue and ticketing platform.

This project exposes a FastAPI API for customers, bakery operators, and admins, and coordinates queue operations through PostgreSQL, Redis, RabbitMQ/Celery, and MQTT-connected bakery hardware.

## Highlights

- **FastAPI-based REST API** for authentication, customer flows, bakery management, and admin operations.
- **Real-time hardware integration** via MQTT (ticket display/print/call workflows).
- **Asynchronous background processing** with Celery workers and periodic jobs.
- **Persistent data layer** with PostgreSQL + SQLAlchemy + Alembic migrations.
- **Operational caching/state** in Redis (including token blacklisting and queue helpers).
- **Production-friendly container setup** with Docker Compose.

## Architecture Overview

Core components and responsibilities:

- `application/server_side.py`
  - FastAPI app entrypoint.
  - Registers routers (`/auth`, `/hc`, `/manage`, `/admin`, public user endpoints).
  - Configures CORS, JWT middleware, Redis connection, MQTT background handler, and APScheduler jobs.
- `application/tasks.py`
  - Celery app and tasks for queue/ticket operations, periodic dispatching, and admin notifications.
- `application/mqtt_client.py`
  - MQTT subscribe/publish logic for bakery hardware communication.
- `application/crud.py`, `application/models.py`, `application/schemas.py`
  - Data access, ORM models, and API schemas.
- `application/setting.py`
  - Typed configuration loaded from environment variables.

## Main API Areas

- **Authentication** (`/auth`)
  - Sign-up, OTP flow, token issue/refresh, logout.
- **Hardware Communication** (`/hc`)
  - New ticket creation, serving tickets, wait-list operations, hardware initialization.
- **Bakery Management** (`/manage`)
  - Bakery and bread configuration, urgent queue handling, queue maintenance, upcoming customer controls.
- **Admin** (`/admin`)
  - Admin bootstrap and management endpoints.
- **Public/User Endpoints**
  - Queue status, token-based ticket lookup, and rating endpoints.

Interactive API documentation is available at:

- `GET /docs` (Swagger UI)
- `GET /redoc` (ReDoc)

## Tech Stack

- **Language**: Python 3.11
- **Web Framework**: FastAPI + Uvicorn
- **Database**: PostgreSQL + SQLAlchemy + Alembic
- **Cache / State**: Redis
- **Message Broker / Queue**: RabbitMQ + Celery
- **Hardware Messaging**: MQTT (`aiomqtt`)
- **Scheduling**: APScheduler + Celery beat
- **Reverse Proxy (deployment)**: Nginx

## Prerequisites

For local/containerized development:

- Docker + Docker Compose
- (Optional, non-container workflow) Python 3.11

## Environment Variables

This service is configured entirely through environment variables (via `.env` in Compose).

Required/important variables include:

- **Database**
  - `DATABASE_URL`
- **JWT/Auth**
  - `ACCESS_TOKEN_SECRET_KEY`
  - `REFRESH_TOKEN_SECRET_KEY`
  - `ALGORITHM`
  - `SIGN_UP_TEMPORARY_TOKEN_EXP_MIN`
  - `ACCESS_TOKEN_EXP_MIN`
  - `REFRESH_TOKEN_EXP_MIN`
- **Telegram Notifications**
  - `TELEGRAM_TOKEN`
  - `TELEGRAM_CHAT_ID`
  - `ERR_THREAD_ID`
  - `NEW_USER_THREAD_ID`
  - `INFO_THREAD_ID`
  - `BAKERY_TICKET_THREAD_ID`
  - `RATE_THREAD_ID`
  - `HARDWARE_CLIENT_ERROR_THREAD_ID`
  - `TELEGRAM_PROXY_URL` (optional)
- **SMS**
  - `SMS_KEY`
- **MQTT**
  - `MQTT_BROKER_HOST`
  - `MQTT_BROKER_PORT`
  - `MQTT_PUBLISH_TIMEOUT_S` (optional, defaults in code)
- **Redis**
  - `REDIS_URL`
- **Celery**
  - `CELERY_BROKER_URL`
  - `ENABLE_AUTO_DISPATCH_READY_TICKETS` (optional flag)

> Tip: create a `.env` file in the project root and provide values for all required keys before startup.

## Running with Docker Compose (Recommended)

Start all services:

```bash
docker compose up -d
```

Core services in `docker-compose.yml`:

- `web` (FastAPI app)
- `worker` (Celery worker)
- `beat` (Celery beat scheduler)
- `db` (PostgreSQL)
- `redis`
- `rabbitmq`
- `migrate` (one-off Alembic migration runner)
- optional deployment services: `frontend`, `nginx`, `watchtower`, `redisinsight`

Check logs:

```bash
docker compose logs -f web
```

Stop services:

```bash
docker compose down
```

## Running Without Docker (Advanced)

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Export environment variables (or use a compatible `.env` loader).
3. Run migrations:

   ```bash
   alembic upgrade head
   ```

4. Start API server:

   ```bash
   uvicorn application.server_side:app --host 0.0.0.0 --port 80
   ```

5. Start Celery worker:

   ```bash
   celery -A application.tasks worker --loglevel=info --pool=threads --concurrency=4
   ```

6. Start Celery beat:

   ```bash
   celery -A application.tasks beat --loglevel=info
   ```

## Database Migrations

Alembic is configured in `alembic.ini` and `alembic/`.

Common commands:

```bash
alembic upgrade head
alembic downgrade -1
```

## Project Structure

```text
application/
  admin/                   # Admin-related endpoints
  bakery/                  # Bakery management and hardware communication endpoints
  helpers/                 # Utility/helper modules
  user/                    # Authentication and user-facing endpoints
  auth.py                  # Token/cookie/auth helper logic
  crud.py                  # Database operations
  database.py              # SQLAlchemy engine/session setup
  models.py                # ORM models
  mqtt_client.py           # MQTT connection + publish/subscribe handling
  schemas.py               # Pydantic schemas
  server_side.py           # FastAPI app entrypoint
  setting.py               # Environment-driven app settings
  tasks.py                 # Celery tasks

alembic/                   # Database migrations
docker-compose.yml         # Full container stack
Dockerfile                 # Application container build
```

## Operational Notes

- The API middleware validates access/refresh tokens and can mint a new access token from refresh cookies.
- MQTT and Redis connections are initialized during app lifespan startup.
- Scheduler jobs initialize daily bakery queue data and periodically adjust bread timing configurations.
- Error events are reported to Telegram via Celery tasks.

## Troubleshooting

- **App fails at startup with config errors**: verify all required environment variables are set.
- **Worker tasks not executing**: ensure RabbitMQ is healthy and `CELERY_BROKER_URL` is correct.
- **Realtime hardware events missing**: validate MQTT broker reachability (`MQTT_BROKER_HOST`, `MQTT_BROKER_PORT`) and topic subscriptions.
- **Auth issues**: check JWT secret variables and cookie/domain settings in your deployment environment.

## License

No license file is currently included in this repository. Add one if you plan public distribution.
