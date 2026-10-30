# 🚀 Securo — Render Deployment Guide (PaaS)

This guide provides step-by-step instructions for deploying the **Securo** application on the **Render** platform using Docker containers (Frontend and Backend), a PostgreSQL Database (via Supabase), and a Redis Key-Value instance.

---

## 🏗️ Deployment Architecture

```mermaid
graph TD
    Client[📱/💻 Browser / Client] -->|HTTPS Requests| Frontend[🎨 Frontend Service<br/>Nginx Container]
    Frontend -->|Reverse Proxy /api/| Backend[⚙️ Backend Service<br/>FastAPI / Uvicorn]
    Backend -->|Async DB Connections| Database[(🗄️ PostgreSQL Database<br/>Supabase / Render)]
    Backend -->|Cache & Session Data| Redis[(⚡ Redis Instance<br/>Render Key-Value)]

```

---

## 1. Configure the Database (Supabase)

Supabase will act as your cloud PostgreSQL server to store your transactions securely.

1. Create a free account on [Supabase](https://supabase.com/) and click to start a **New Project**.
2. ⚠️ **Crucial Password Step:** While filling out your project details, click on the **Generate a password** link under the *Database password* field. **Copy this generated password and save it in a secure file immediately**. You will strictly need it later to build your `DATABASE_URL` environment variable for Render.
3. 🌍 **Select Your Region:** On this same creation screen, make sure to choose the closest hosting **Region** to your target users (for example, *South America (São Paulo)*) or match it closely with your Render web service location to avoid network latency.
4. 🔗 **Get the Connection String (Session Pooler):** Once your project is fully provisioned and you are inside the main dashboard:
* Go to **Project Settings** (gear icon) > **Database**.
* Scroll down to the **Connection string** section.
* Select the **Pooler** tab and set the Mode to **Session** (This prevents `Network is unreachable` errors on cloud environments like Render).
* Copy the connection string provided.
* ⚠️ **Important Formatting Notice:** This string will look like `postgresql://postgres.[your-project-id]:[YOUR-PASSWORD]@...`. Before pasting it into Render, you must:
1. Replace `[YOUR-PASSWORD]` with the actual password you saved in **Step 2**. 
If your password contains special characters (like `@`, `#`, `/`, `?`, or `%`), ensure it is percent-encoded (URL-encoded, e.g., `@` becomes `%40`) so SQLAlchemy parses the URL correctly.
2. Add `+asyncpg` right after `postgresql` to match the application's required asynchronous driver format (transforming it into `postgresql+asyncpg://postgres...`).




---

## 2. Deploy the Redis Instance (Render)

The backend requires a Redis instance for task queuing and caching. We will use Render's native, fully-managed Key Value service.

1. On the Render Dashboard, click **New +** and select **Key Value**.
2. Configure the service with the following settings:
* **Name:** `securo-redis`
* **Plan:** `Free`


3. Click **Create Key Value**.
4. Once provisioned, locate the **Connections** section on the dashboard and copy the **Internal Key Value URL**. *(It will look like `redis://red-xxxxxxxxxxxxxxxxxxxx:...`). This is the exact string you will paste into the `REDIS_URL` environment variable for the backend in Step 3.*

---

## 3. Host the Backend (Render)

Now we deploy the core engine of the application, connecting it to our cloud Database and Redis container.

1. On the Render Dashboard, click **New +** and select **Web Service**.
2. Connect your GitHub repository fork.
3. Configure the service with the following settings:
* **Name:** `securo-backend`
* **Region:** (Must match your other services)
* **Branch:** `main` (or your active branch)
* **Dockerfile Path:** `./backend/Dockerfile`
* **Docker Build Context Directory:** `./backend/`
* **Instance Type:** `Free`


4. ⚙️ **Configure Lifecycle Command (Crucial Step):**
* Scroll down to the **Deploy** section.
* Locate the **Docker Command** field (which overrides the default Dockerfile CMD).
* Type exactly: `bash ./start.sh`


5. Add the following **Environment Variables** in the service settings:

| Key | Description / Example |
| --- | --- |
| `BACKEND_URL` | `https://your-backend-service.onrender.com` *(Public URL generated for this backend)* |
| `FRONTEND_URL` | `https://your-frontend-service.onrender.com` *(Public URL generated for the frontend)* |
| `DATABASE_URL` | `postgresql+asyncpg://postgres.[id]:[percent_encoded_pass]@host:5432/postgres` *(Configured in Step 1)* | 
| `REDIS_URL` | `redis://...` *(Internal URL retrieved in Step 2)* |
| `SECRET_KEY` | Generate via terminal: `openssl rand -hex 32` |
| `OIDC_ENABLED` | `true` |
| `OIDC_PROVIDER_NAME` | `Google` |
| `OIDC_CLIENT_ID` | `your-client-id.apps.googleusercontent.com` |
| `OIDC_CLIENT_SECRET` | `your-google-client-secret` |
| `OIDC_DISCOVERY_URL` | `https://accounts.google.com/.well-known/openid-configuration` |
| `OIDC_AUTO_REGISTER` | `true` |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `true` |
| `OIDC_EXISTING_USER_LINK_MODE` | `verified_email` |
| `OIDC_SCOPES` | `openid email profile` |
| `PLUGGY_CLIENT_ID` | `your-pluggy-client-id` |
| `PLUGGY_CLIENT_SECRET` | `your-pluggy-client-secret` |
| `TRUSTED_PROXY_HOPS` | `1` | (Optional: Number of reverse proxy hops sitting in front of the backend. Recommended: `1`) |

> ℹ️ **Security & Network Notice – TRUSTED_PROXY_HOPS:**
> Set `TRUSTED_PROXY_HOPS` to match the exact number of upstream reverse proxies handling traffic before the backend service (set to `1` when placed behind the Nginx frontend container). 
> - **If set too low:** The application will fail to resolve the real client IP address, logging internal proxy IPs instead.
> - **If set too high:** The application trusts spoofed `X-Forwarded-For` headers from untrusted upstream clients.

> **Security Notice – Backend Network Isolation:**
> For maximum security, deploy the backend as a **Private Service** behind the Nginx frontend reverse proxy so it remains isolated from the public internet. If deployed as a public Web Service (e.g., using Render's free plan), direct access to the backend API bypasses the frontend Nginx layer, meaning no `TRUSTED_PROXY_HOPS` value is fully safe against direct header spoofing.

> **Security Notice – Registration:**
> The `REGISTRATION_ENABLED` environment variable sets the initial registration state upon deployment until modified by an administrator in the UI settings.

> **Important – File Attachments Storage:**
> Note that Render's free tier does not support persistent storage volumes or native S3 object storage. Uploaded file attachments stored on the local container filesystem are ephemeral and will be lost whenever the backend container restarts or redeploys.
---

## 4. Host the Frontend (Render)

Deploy the Frontend Web Service to serve the application user interface.

1. On the Render Dashboard, click **New +** and select **Web Service**.
2. Connect your GitHub repository fork.
3. Configure the service with the following settings:
* **Name:** `securo-frontend`
* **Region:** (Choose the same region as your other services)
* **Branch:** `main` (or your active branch)
* **Dockerfile Path:** `./frontend/Dockerfile`
* **Docker Build Context Directory:** `./frontend`
* **Instance Type:** `Free`


4. ⚠️ **Environment Variables (Preventing Error 508):**
* Add the following environment variables under the **Environment** tab:


| Key | Example Value | Description |
| --- | --- | --- |
| **`PROXY_HOST`** | **`your-backend-service.onrender.com`** | **Required:** Pure backend domain (without `https://` or trailing slashes) to prevent Nginx proxy looping. |
| `BACKEND_URL` | `https://your-backend-service.onrender.com` | Full public URL of the backend. |
| `PROXY_SSL_VERIFY` | 	on | (Enforces strict SSL certificate verification between Nginx and the backend; works out-of-the-box in cloud environments) | 
| `PORT` | Container listening port injected by PaaS health check | `8080` (or leave default on Render) |

5. Click **Deploy Web Service**.

---

> 🔒 **Security Notice:** Once you have completed initial setup and created your admin account, navigate to **Admin -> Settings** in the UI to turn off public registrations (the application seeds this setting in the database on first run, so modifying environment variables alone will not disable it).

## ⚠️ Note on Background Workers (Celery)

On Render's **Free Tier**, Web Services go into spin-down (sleep mode) after 15 minutes of inactivity and only wake up upon receiving incoming HTTP requests.

Because background workers (Celery Worker & Celery Beat) rely on continuous uptime and scheduled execution:

* **Celery Worker/Beat services are omitted** in this free PaaS guide.
* **Impact:** Scheduled background tasks (such as automatic daily bank sync, automated recurring transactions, automated FX rates, and background asset valuations) will not run automatically in the background.
* Manual triggers or upgrading to a paid instance with continuous uptime are required if automated background synchronization is desired.

---

## 🔍 Troubleshooting

* **`508 Loop Detected` Error on Frontend:**
Ensure that the `PROXY_HOST` environment variable in the frontend is set **without** the `https://` prefix (e.g., `your-backend-service.onrender.com`).
* **`invalid number of arguments in "proxy_set_header"` Error:**
Occurs when `PROXY_HOST` is left completely empty. A valid hostname or IP is required to render the Nginx template.
* **`start.sh` Permission Denied Error:**
Verify that the `backend/start.sh` file has execution permissions in Git (`chmod +x backend/start.sh`).
